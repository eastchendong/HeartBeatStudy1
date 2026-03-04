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
import json
import queue
import time
from collections import deque
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True, debug=False)
