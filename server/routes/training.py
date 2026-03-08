"""
Training-scene routes.

Handles the live training loop: receiving pulse data, SSE streaming,
session start / pause / play, and the breathing-cycle adaptive logic.
"""

import json
import queue
import time

from flask import Blueprint, Response, jsonify, request

import state
from state import SessionState
from routes.utils import get_current_session
from hrv import compute_lf_power, compute_rmssd

training_bp = Blueprint("training", __name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_session_duration(sess: SessionState) -> float:
    return float(sess.session_config["session_duration"])


def _get_base_cycle(sess: SessionState) -> float:
    return float(sess.session_config["breath_cycle"])


def _is_adaptive(sess: SessionState) -> bool:
    return bool(sess.session_config["adaptive"])


def _compute_cycle(sess: SessionState, rmssd: float | None) -> float:
    base = _get_base_cycle(sess)
    if not _is_adaptive(sess):
        return base
    if rmssd is None or len(sess.hrv_window) < 3:
        return base
    baseline = sorted(sess.hrv_window)[len(sess.hrv_window) // 2]
    if baseline > 0 and rmssd < baseline * state.HRV_DROP_RATIO:
        return base + 1.0
    return base


def _elapsed_seconds(sess: SessionState) -> float:
    if sess.paused:
        return sess.elapsed_before_pause
    return sess.elapsed_before_pause + (time.time() - sess.session_start)


def _make_payload(sess: SessionState, bpm: float, rmssd: float | None, cycle: float) -> dict:
    half = round(cycle / 2, 1)
    session_dur = _get_session_duration(sess)
    remaining = max(0.0, session_dur - _elapsed_seconds(sess)) if sess.session_active else session_dur
    return {
        "bpm":               round(bpm, 1),
        "hrv_rmssd":         round(rmssd, 1) if rmssd is not None else None,
        "lf_power":          round(sess.latest_lf_power, 4) if sess.latest_lf_power is not None else None,
        "total":             cycle,
        "inhale":            half,
        "exhale":            half,
        "session_remaining": round(remaining),
        "session_duration":  round(session_dur),
        "paused":            sess.paused,
        "active":            sess.session_active,
        "session_id":        sess.session_id,
    }


def _broadcast(sess: SessionState, payload: dict):
    data = json.dumps(payload)
    dead = []
    with sess.lock:
        for q in sess.subscribers:
            try:
                q.put_nowait(data)
            except queue.Full:
                dead.append(q)
        for q in dead:
            sess.subscribers.remove(q)


# ── Routes ────────────────────────────────────────────────────────────────────

@training_bp.route("/api/pulse", methods=["POST"])
def receive_pulse():
    sess = get_current_session()
    try:
        body = request.get_json(silent=True)
        if body and "bpm" in body:
            bpm = float(body["bpm"])
        else:
            bpm = float(request.data.decode().strip())
        rr_intervals = body.get("rr_intervals", []) if body else []
    except (ValueError, TypeError):
        return jsonify({"error": "invalid payload"}), 400

    with sess.lock:
        sess.bpm_window.append(bpm)
        avg_bpm = sum(sess.bpm_window) / len(sess.bpm_window)
        sess.latest_bpm = avg_bpm
        sess.bpm_all.append(bpm)

        now = time.time()
        for rr in rr_intervals:
            rr_val = float(rr)
            if 200 < rr_val < 2000:
                sess.rr_intervals.append(rr_val)
                sess.rr_timestamps.append(now)

        recent_rr = sess.rr_intervals[-120:] if len(sess.rr_intervals) > 120 else list(sess.rr_intervals)
        computed_rmssd = compute_rmssd(recent_rr)
        computed_lf    = compute_lf_power(recent_rr)

        if computed_rmssd is not None:
            sess.latest_rmssd = computed_rmssd
            sess.hrv_window.append(computed_rmssd)
        elif body and "hrv_rmssd" in body:
            rmssd_val = float(body["hrv_rmssd"])
            sess.hrv_window.append(rmssd_val)
            sess.latest_rmssd = rmssd_val

        if computed_lf is not None:
            sess.latest_lf_power = computed_lf

        sess.breath_cycle = _compute_cycle(sess, sess.latest_rmssd)
        is_paused  = sess.paused
        is_active  = sess.session_active
        snap_bpm   = avg_bpm
        snap_rmssd = sess.latest_rmssd
        snap_cycle = sess.breath_cycle

    payload = _make_payload(sess, snap_bpm, snap_rmssd, snap_cycle)
    if not is_paused and is_active:
        _broadcast(sess, payload)
    return jsonify(payload), 200


@training_bp.route("/api/status")
def status():
    sess = get_current_session()
    with sess.lock:
        avg_bpm = sum(sess.bpm_window) / len(sess.bpm_window) if sess.bpm_window else 0
        cycle   = sess.breath_cycle
        rmssd   = sess.latest_rmssd
    return jsonify(_make_payload(sess, avg_bpm, rmssd, cycle))


@training_bp.route("/api/start", methods=["POST"])
def start_session():
    sess = get_current_session()
    with sess.lock:
        sess.session_start        = time.time()
        sess.paused               = False
        sess.session_active       = True
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
        sess.breath_cycle    = _get_base_cycle(sess)
        avg_bpm = 0
        cycle   = sess.breath_cycle
        rmssd   = None

    payload = _make_payload(sess, avg_bpm, rmssd, cycle)
    _broadcast(sess, payload)
    return jsonify({"ok": True, "config": dict(sess.session_config), "session_id": sess.session_id})


@training_bp.route("/api/pause", methods=["POST"])
def pause_session():
    sess = get_current_session()
    with sess.lock:
        if not sess.paused:
            sess.elapsed_before_pause += time.time() - sess.session_start
            sess.paused    = True
            sess.pause_time = time.time()
        avg_bpm = sum(sess.bpm_window) / len(sess.bpm_window) if sess.bpm_window else 0
        cycle, rmssd = sess.breath_cycle, sess.latest_rmssd
    payload = _make_payload(sess, avg_bpm, rmssd, cycle)
    _broadcast(sess, payload)
    return jsonify(payload)


@training_bp.route("/api/play", methods=["POST"])
def play_session():
    sess = get_current_session()
    with sess.lock:
        if sess.paused:
            sess.session_start = time.time()
            sess.paused = False
        avg_bpm = sum(sess.bpm_window) / len(sess.bpm_window) if sess.bpm_window else 0
        cycle, rmssd = sess.breath_cycle, sess.latest_rmssd
    payload = _make_payload(sess, avg_bpm, rmssd, cycle)
    _broadcast(sess, payload)
    return jsonify(payload)


@training_bp.route("/api/stream")
def stream():
    """Server-Sent Events endpoint – one persistent connection per browser tab."""
    sess = get_current_session()

    def event_generator():
        q: queue.Queue = queue.Queue(maxsize=20)
        with sess.lock:
            sess.subscribers.append(q)
        try:
            with sess.lock:
                avg_bpm = sum(sess.bpm_window) / len(sess.bpm_window) if sess.bpm_window else 0
                cycle   = sess.breath_cycle
                rmssd   = sess.latest_rmssd
            yield f"data: {json.dumps(_make_payload(sess, avg_bpm, rmssd, cycle))}\n\n"

            while True:
                try:
                    data = q.get(timeout=20)
                    yield f"data: {data}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            with sess.lock:
                try:
                    sess.subscribers.remove(q)
                except ValueError:
                    pass

    return Response(
        event_generator(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
