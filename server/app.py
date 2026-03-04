"""
HeartBeat Study – Flask server
  POST /api/pulse        ← Windows Python script pushes BPM + HRV readings
  GET  /api/stream       ← Browser subscribes via Server-Sent Events
  GET  /                 ← Serves the breathing-guidance frontend

Adaptive breathing logic (MVP):
  - Session length: 10 minutes
  - Base cycle: 10 s (inhale 5 s / exhale 5 s, no hold)
  - If RMSSD drops below a rolling baseline → difficulty detected → cycle = 11 s
  - If RMSSD recovers to baseline → cycle returns to 10 s
"""
import asyncio
import importlib.util
import json
import os
import queue
import shlex
import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from threading import Lock

from flask import Flask, Response, jsonify, render_template, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ── Session ───────────────────────────────────────────────────────────────────
SESSION_DURATION = 10 * 60   # seconds
_session_start: float = time.time()

# ── HRV adaptive state ────────────────────────────────────────────────────────
# RMSSD baseline: rolling median of last N readings
HRV_WINDOW      = 8    # readings kept for baseline
HRV_DROP_RATIO  = 0.80  # if current RMSSD < baseline * ratio → difficulty

_lock          = Lock()
_bpm_window: deque[float]   = deque(maxlen=10)
_hrv_window: deque[float]   = deque(maxlen=HRV_WINDOW)
_latest_bpm:  float         = 0.0
_latest_rmssd: float | None = None
_breath_cycle: float        = 10.0   # current cycle seconds (10 or 11)
_subscribers: list[queue.Queue] = []

# ── Session timer state ───────────────────────────────────────────────────────
_paused:        bool  = False
_pause_time:    float = 0.0   # when the session was paused
_elapsed_before_pause: float = 0.0  # accumulated seconds before current pause
_relay_lock = Lock()
_relay_process: subprocess.Popen | None = None
_relay_device_keyword: str | None = None


def _relay_script() -> Path:
    return Path(__file__).resolve().parents[1] / "ble-relay-server-python" / "main.py"


def _relay_running() -> bool:
    return _relay_process is not None and _relay_process.poll() is None


def _bleak_ready() -> bool:
    return importlib.util.find_spec("bleak") is not None


def _is_wsl() -> bool:
    try:
        return "microsoft" in os.uname().release.lower()
    except Exception:
        return False


def _windows_bridge_ready() -> bool:
    return _is_wsl() and shutil.which("powershell.exe") is not None and shutil.which("wslpath") is not None


def _wsl_to_windows_path(path: Path) -> str:
    return subprocess.check_output(["wslpath", "-w", str(path)], text=True).strip()


def _build_windows_relay_cmd(keyword: str, scan_only: bool = False, timeout: float = 6.0) -> list[str]:
    relay_win = _wsl_to_windows_path(_relay_script())
    safe_keyword = keyword.replace('"', "").strip()
    parts = [
        "py -3",
        shlex.quote(relay_win).replace("'", '"'),
        "--device-name",
        shlex.quote(safe_keyword).replace("'", '"'),
        "--scan-timeout",
        str(timeout),
    ]
    if scan_only:
        parts.append("--scan-only")
    else:
        parts.extend(["--flask-url", '"http://localhost:5000/api/pulse"', "--disable-tcp"])
    cmdline = " ".join(parts)
    return ["powershell.exe", "-NoProfile", "-Command", cmdline]


def _format_ble_backend_error(exc: Exception) -> str:
    text = str(exc)
    if "org.bluez" in text or "DBus.Error.ServiceUnknown" in text:
        return ("BLE backend unavailable in this Linux/WSL runtime (org.bluez missing). "
                "Run relay on Windows host Bluetooth, or enable BlueZ on native Linux.")
    return f"ble search failed: {text}"


def _ble_backend_preflight(timeout: float = 2.0) -> str | None:
    if _windows_bridge_ready():
        return None
    try:
        from bleak import BleakScanner
        asyncio.run(BleakScanner.discover(timeout=timeout))
        return None
    except Exception as e:
        return _format_ble_backend_error(e)


def _compute_cycle(rmssd: float | None) -> float:
    """Return 11 s when HRV is poor, 10 s otherwise."""
    if rmssd is None or len(_hrv_window) < 3:
        return 10.0
    baseline = sorted(_hrv_window)[len(_hrv_window) // 2]   # median
    if baseline > 0 and rmssd < baseline * HRV_DROP_RATIO:
        return 11.0
    return 10.0


def _elapsed_seconds() -> float:
    """Return total elapsed session seconds, accounting for pauses."""
    if _paused:
        return _elapsed_before_pause
    return _elapsed_before_pause + (time.time() - _session_start)


def _make_payload(bpm: float, rmssd: float | None, cycle: float) -> dict:
    half = round(cycle / 2, 1)
    remaining = max(0.0, SESSION_DURATION - _elapsed_seconds())
    return {
        "bpm":        round(bpm, 1),
        "hrv_rmssd":  round(rmssd, 1) if rmssd is not None else None,
        "total":      cycle,
        "inhale":     half,
        "exhale":     half,
        "session_remaining": round(remaining),
        "paused":     _paused,
    }


def _broadcast(payload: dict):
    data = json.dumps(payload)
    dead = []
    with _lock:
        for q in _subscribers:
            try:
                q.put_nowait(data)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _subscribers.remove(q)


# ── Routes ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/pulse", methods=["POST"])
def receive_pulse():
    global _latest_bpm, _latest_rmssd, _breath_cycle, _session_start
    try:
        body = request.get_json(silent=True)
        if body and "bpm" in body:
            bpm = float(body["bpm"])
        else:
            bpm = float(request.data.decode().strip())
        rmssd = float(body["hrv_rmssd"]) if body and "hrv_rmssd" in body else None
    except (ValueError, TypeError):
        return jsonify({"error": "invalid payload"}), 400

    with _lock:
        _bpm_window.append(bpm)
        avg_bpm = sum(_bpm_window) / len(_bpm_window)
        _latest_bpm = avg_bpm

        if rmssd is not None:
            _hrv_window.append(rmssd)
            _latest_rmssd = rmssd

        _breath_cycle = _compute_cycle(_latest_rmssd)
        paused = _paused

    payload = _make_payload(avg_bpm, _latest_rmssd, _breath_cycle)
    if not paused:
        _broadcast(payload)
    return jsonify(payload), 200


@app.route("/api/status")
def status():
    with _lock:
        avg_bpm = sum(_bpm_window) / len(_bpm_window) if _bpm_window else 0
        cycle   = _breath_cycle
        rmssd   = _latest_rmssd
    return jsonify(_make_payload(avg_bpm, rmssd, cycle))


@app.route("/api/reset", methods=["POST"])
def reset_session():
    global _session_start, _paused, _pause_time, _elapsed_before_pause
    with _lock:
        _session_start = time.time()
        _paused = False
        _pause_time = 0.0
        _elapsed_before_pause = 0.0
        _bpm_window.clear()
        _hrv_window.clear()
    with _lock:
        avg_bpm = sum(_bpm_window) / len(_bpm_window) if _bpm_window else 0
        cycle, rmssd = _breath_cycle, _latest_rmssd
    _broadcast(_make_payload(avg_bpm, rmssd, cycle))
    return jsonify({"ok": True})


@app.route("/api/pause", methods=["POST"])
def pause_session():
    global _paused, _pause_time, _elapsed_before_pause
    with _lock:
        if not _paused:
            _elapsed_before_pause += time.time() - _session_start
            _paused = True
            _pause_time = time.time()
        avg_bpm = sum(_bpm_window) / len(_bpm_window) if _bpm_window else 0
        cycle, rmssd = _breath_cycle, _latest_rmssd
    payload = _make_payload(avg_bpm, rmssd, cycle)
    _broadcast(payload)
    return jsonify(payload)


@app.route("/api/play", methods=["POST"])
def play_session():
    global _paused, _session_start
    with _lock:
        if _paused:
            _session_start = time.time()
            _paused = False
        avg_bpm = sum(_bpm_window) / len(_bpm_window) if _bpm_window else 0
        cycle, rmssd = _breath_cycle, _latest_rmssd
    payload = _make_payload(avg_bpm, rmssd, cycle)
    _broadcast(payload)
    return jsonify(payload)


@app.route("/api/stream")
def stream():
    """Server-Sent Events endpoint – one persistent connection per browser tab."""
    def event_generator():
        q: queue.Queue = queue.Queue(maxsize=20)
        with _lock:
            _subscribers.append(q)
        try:
            with _lock:
                avg_bpm = sum(_bpm_window) / len(_bpm_window) if _bpm_window else 0
                cycle   = _breath_cycle
                rmssd   = _latest_rmssd
            payload = _make_payload(avg_bpm, rmssd, cycle)
            yield f"data: {json.dumps(payload)}\n\n"

            while True:
                try:
                    data = q.get(timeout=20)
                    yield f"data: {data}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"   # keep-alive comment
        finally:
            with _lock:
                try:
                    _subscribers.remove(q)
                except ValueError:
                    pass

    return Response(
        event_generator(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/relay/search")
def relay_search():
    keyword = request.args.get("keyword", "").strip().upper()
    timeout = float(request.args.get("timeout", 6))
    timeout = min(max(timeout, 2.0), 20.0)
    if _windows_bridge_ready():
        try:
            cmd = _build_windows_relay_cmd(keyword or "", scan_only=True, timeout=timeout)
            proc = subprocess.run(cmd, cwd=str(_relay_script().parent), capture_output=True, text=True, timeout=timeout + 12)
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip()
                return jsonify({"error": f"windows relay scan failed: {detail}"}), 503
            payload = []
            for line in reversed((proc.stdout or "").splitlines()):
                line = line.strip()
                if line.startswith("[") or line.startswith("{"):
                    try:
                        payload = json.loads(line)
                        break
                    except Exception:
                        continue
            return jsonify({"devices": payload if isinstance(payload, list) else []})
        except Exception as e:
            return jsonify({"error": f"windows relay scan failed: {e}"}), 503

    if _bleak_ready():
        try:
            from bleak import BleakScanner
            devices = asyncio.run(BleakScanner.discover(timeout=timeout))
        except Exception as e:
            return jsonify({"error": _format_ble_backend_error(e)}), 503

        results = []
        for d in devices:
            name = (d.name or "").strip()
            if not name:
                continue
            if keyword and keyword not in name.upper():
                continue
            results.append({"name": name, "address": d.address})
        return jsonify({"devices": results})

    return jsonify({
        "error": "No BLE backend available. Install bleak+BlueZ on Linux, or use WSL with Windows Python/PowerShell bridge.",
    }), 503


@app.route("/api/relay/connect", methods=["POST"])
def relay_connect():
    global _relay_process, _relay_device_keyword
    body = request.get_json(silent=True) or {}
    keyword = (body.get("device_name_keyword") or "C5AB").strip()
    if not keyword:
        return jsonify({"error": "device_name_keyword required"}), 400
    backend_error = _ble_backend_preflight()
    if backend_error:
        return jsonify({"error": backend_error}), 503

    with _relay_lock:
        if _relay_running():
            return jsonify({"ok": True, "running": True, "device_name_keyword": _relay_device_keyword})

        relay_script = _relay_script()
        if not relay_script.exists():
            return jsonify({"error": f"relay script not found: {relay_script}"}), 500

        if _windows_bridge_ready():
            cmd = _build_windows_relay_cmd(keyword, scan_only=False, timeout=6.0)
        elif _bleak_ready():
            cmd = [
                sys.executable,
                str(relay_script),
                "--device-name", keyword,
                "--flask-url", "http://127.0.0.1:5000/api/pulse",
                "--disable-tcp",
            ]
        else:
            return jsonify({"error": "No BLE backend available for relay connect."}), 503
        _relay_process = subprocess.Popen(
            cmd,
            cwd=str(relay_script.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        _relay_device_keyword = keyword
        time.sleep(0.6)
        if not _relay_running():
            code = _relay_process.poll() if _relay_process else -1
            detail = ""
            if _relay_process and _relay_process.stderr:
                raw = _relay_process.stderr.read() or b""
                if isinstance(raw, bytes):
                    detail = raw.decode("utf-8", errors="replace").strip().splitlines()[-1:] or [""]
                    detail = detail[0]
                else:
                    detail = str(raw).strip().splitlines()[-1:] or [""]
                    detail = detail[0]
            _relay_process = None
            _relay_device_keyword = None
            msg = f"relay process failed to start (exit={code})"
            if detail:
                msg = f"{msg}: {detail}"
            return jsonify({"error": msg}), 500
    return jsonify({"ok": True, "running": True, "device_name_keyword": keyword})


@app.route("/api/relay/disconnect", methods=["POST"])
def relay_disconnect():
    global _relay_process, _relay_device_keyword
    with _relay_lock:
        if not _relay_running():
            _relay_process = None
            _relay_device_keyword = None
            return jsonify({"ok": True, "running": False})
        _relay_process.terminate()
        try:
            _relay_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _relay_process.kill()
            _relay_process.wait(timeout=2)
        _relay_process = None
        _relay_device_keyword = None
    return jsonify({"ok": True, "running": False})


@app.route("/api/relay/status")
def relay_status():
    with _relay_lock:
        return jsonify({
            "running": _relay_running(),
            "device_name_keyword": _relay_device_keyword,
        })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True, debug=False)
