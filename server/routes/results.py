"""
Results-scene routes.

Handles post-training actions: stop, reset, save session data,
and listing past sessions.
"""

import json
import time
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

import state
from routes.utils import get_current_session
from hrv import compute_lf_power, compute_rmssd

results_bp = Blueprint("results", __name__)


@results_bp.route("/api/stop", methods=["POST"])
def stop_session():
    sess = get_current_session()
    with sess.lock:
        sess.session_active = False
        sess.paused = False
    return jsonify({"ok": True})


@results_bp.route("/api/reset", methods=["POST"])
def reset_session():
    sess = get_current_session()
    with sess.lock:
        sess.session_start        = time.time()
        sess.paused               = False
        sess.session_active       = False
        sess.pause_time           = 0.0
        sess.elapsed_before_pause = 0.0
        sess.bpm_window.clear()
        sess.hrv_window.clear()
        sess.rr_intervals.clear()
        sess.rr_timestamps.clear()
        sess.bpm_all.clear()
        sess.latest_rmssd    = None
        sess.latest_lf_power = None
        sess.latest_bpm      = 0.0
        sess.breath_cycle    = float(sess.session_config["breath_cycle"])
    return jsonify({"ok": True})


@results_bp.route("/api/save_session", methods=["POST"])
def save_session():
    sess = get_current_session()
    body = request.get_json(silent=True) or {}
    username = body.get("username", "").strip()

    with sess.lock:
        if not username:
            username = sess.session_config.get("username", "")
        rr_data  = list(sess.rr_intervals)
        bpm_data = list(sess.bpm_all)

    final_rmssd = compute_rmssd(rr_data)
    final_lf    = compute_lf_power(rr_data)

    with sess.lock:
        config_snap = {
            "breath_cycle":    sess.session_config["breath_cycle"],
            "session_duration": sess.session_config["session_duration"],
            "adaptive":        sess.session_config["adaptive"],
        }

    session_record = {
        "id":               str(uuid.uuid4()),
        "session_id":       sess.session_id,
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "username":         username or "anonymous",
        "config":           config_snap,
        "rr_intervals_ms":  rr_data,
        "rr_count":         len(rr_data),
        "bpm_readings":     bpm_data,
        "bpm_max":          round(max(bpm_data), 1) if bpm_data else None,
        "bpm_min":          round(min(bpm_data), 1) if bpm_data else None,
        "bpm_avg":          round(sum(bpm_data) / len(bpm_data), 1) if bpm_data else None,
        "hrv_rmssd":        round(final_rmssd, 2) if final_rmssd is not None else None,
        "lf_power":         round(final_lf, 4) if final_lf is not None else None,
    }

    filename = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{session_record['id'][:8]}.json"
    filepath = state.DATA_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(session_record, f, ensure_ascii=False, indent=2)

    return jsonify({
        "ok":   True,
        "file": str(filepath.name),
        "summary": {
            "bpm_max":   session_record["bpm_max"],
            "bpm_min":   session_record["bpm_min"],
            "bpm_avg":   session_record["bpm_avg"],
            "hrv_rmssd": session_record["hrv_rmssd"],
            "lf_power":  session_record["lf_power"],
            "rr_count":  session_record["rr_count"],
        },
    })


@results_bp.route("/api/sessions")
def list_sessions():
    files = sorted(state.DATA_DIR.glob("session_*.json"), reverse=True)
    results = []
    for f in files[:50]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            results.append({
                "file":      f.name,
                "timestamp": data.get("timestamp"),
                "username":  data.get("username"),
                "bpm_avg":   data.get("bpm_avg"),
                "hrv_rmssd": data.get("hrv_rmssd"),
                "lf_power":  data.get("lf_power"),
            })
        except Exception:
            pass
    return jsonify({"sessions": results})
