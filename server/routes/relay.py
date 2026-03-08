"""
BLE Relay management routes.

Relay is server-level (one BLE adapter), so it always uses DEFAULT_SESSION_ID.
"""

import asyncio
import json
import subprocess
import sys
import time

from flask import Blueprint, jsonify, request

from state import get_session, DEFAULT_SESSION_ID
from relay_helpers import (
    bleak_ready,
    ble_backend_preflight,
    build_windows_relay_cmd,
    format_ble_backend_error,
    relay_running,
    relay_script,
    windows_bridge_ready,
)

relay_bp = Blueprint("relay", __name__)


@relay_bp.route("/api/relay/search")
def relay_search():
    keyword = request.args.get("keyword", "").strip().upper()
    timeout = float(request.args.get("timeout", 6))
    timeout = min(max(timeout, 2.0), 20.0)

    if windows_bridge_ready():
        try:
            cmd = build_windows_relay_cmd(keyword or "", scan_only=True, timeout=timeout)
            proc = subprocess.run(
                cmd, cwd=str(relay_script().parent),
                capture_output=True, text=True, timeout=timeout + 12,
            )
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

    if bleak_ready():
        try:
            from bleak import BleakScanner
            devices = asyncio.run(BleakScanner.discover(timeout=timeout))
        except Exception as e:
            return jsonify({"error": format_ble_backend_error(e)}), 503

        results = []
        for d in devices:
            name = (d.name or "").strip()
            if not name:
                continue
            if keyword and keyword not in name.upper():
                continue
            results.append({"name": name, "address": d.address})
        return jsonify({"devices": results})

    return jsonify({"error": "No BLE backend available."}), 503


@relay_bp.route("/api/relay/connect", methods=["POST"])
def relay_connect():
    body = request.get_json(silent=True) or {}
    keyword = (body.get("device_name_keyword") or "C5AB").strip()
    if not keyword:
        return jsonify({"error": "device_name_keyword required"}), 400

    backend_error = ble_backend_preflight()
    if backend_error:
        return jsonify({"error": backend_error}), 503

    sess = get_session(DEFAULT_SESSION_ID)
    with sess.lock:
        if relay_running(sess.relay_process):
            return jsonify({"ok": True, "running": True, "device_name_keyword": sess.relay_device_keyword})

        script = relay_script()
        if not script.exists():
            return jsonify({"error": f"relay script not found: {script}"}), 500

        if windows_bridge_ready():
            cmd = build_windows_relay_cmd(keyword, scan_only=False, timeout=6.0)
        elif bleak_ready():
            cmd = [
                sys.executable, str(script),
                "--device-name", keyword,
                "--flask-url", "http://127.0.0.1:5000/api/pulse",
                "--disable-tcp",
            ]
        else:
            return jsonify({"error": "No BLE backend available for relay connect."}), 503

        sess.relay_process = subprocess.Popen(
            cmd, cwd=str(script.parent),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        sess.relay_device_keyword = keyword
        time.sleep(0.6)

        if not relay_running(sess.relay_process):
            code = sess.relay_process.poll() if sess.relay_process else -1
            detail = ""
            if sess.relay_process and sess.relay_process.stderr:
                raw = sess.relay_process.stderr.read() or b""
                detail = (raw.decode("utf-8", errors="replace").strip().splitlines() or [""])[-1]
            sess.relay_process = None
            sess.relay_device_keyword = None
            msg = f"relay process failed to start (exit={code})"
            if detail:
                msg = f"{msg}: {detail}"
            return jsonify({"error": msg}), 500

    return jsonify({"ok": True, "running": True, "device_name_keyword": keyword})


@relay_bp.route("/api/relay/disconnect", methods=["POST"])
def relay_disconnect():
    sess = get_session(DEFAULT_SESSION_ID)
    with sess.lock:
        if not relay_running(sess.relay_process):
            sess.relay_process = None
            sess.relay_device_keyword = None
            return jsonify({"ok": True, "running": False})
        sess.relay_process.terminate()
        try:
            sess.relay_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            sess.relay_process.kill()
            sess.relay_process.wait(timeout=2)
        sess.relay_process = None
        sess.relay_device_keyword = None
    return jsonify({"ok": True, "running": False})


@relay_bp.route("/api/relay/status")
def relay_status():
    sess = get_session(DEFAULT_SESSION_ID)
    with sess.lock:
        return jsonify({
            "running": relay_running(sess.relay_process),
            "device_name_keyword": sess.relay_device_keyword,
        })
