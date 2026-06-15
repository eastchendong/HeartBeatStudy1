"""
Training-scene routes.

Handles the live training loop: receiving pulse data, SSE streaming,
session start / pause / play, and the breathing-cycle adaptive logic.
"""

import json
import queue
import time
from typing import Optional

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


def _compute_cycle(sess: SessionState, rmssd: Optional[float]) -> float:
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


def _make_payload(sess: SessionState, bpm: float, rmssd: Optional[float],
                  cycle: float, rr_pulses: Optional[list] = None) -> dict:
    session_dur = _get_session_duration(sess)
    remaining = max(0.0, session_dur - _elapsed_seconds(sess)) if sess.session_active else session_dur

    # Calculate inhale/exhale based on configured ratio
    inhale_ratio = int(sess.session_config.get("inhale_ratio", 5))
    exhale_ratio = int(sess.session_config.get("exhale_ratio", 5))
    total_ratio = inhale_ratio + exhale_ratio
    inhale = round(cycle * inhale_ratio / total_ratio, 1)
    exhale = round(cycle * exhale_ratio / total_ratio, 1)

    payload = {
        "bpm":               round(bpm, 1),
        "hrv_rmssd":         round(rmssd, 1) if rmssd is not None else None,
        "lf_power":          round(sess.latest_lf_power, 4) if sess.latest_lf_power is not None else None,
        "rr_interval":       round(sess.latest_rr_interval, 1) if sess.latest_rr_interval is not None else None,
        "total":             cycle,
        "inhale":            inhale,
        "exhale":            exhale,
        "inhale_ratio":      inhale_ratio,
        "exhale_ratio":      exhale_ratio,
        "session_remaining": round(remaining),
        "session_duration":  round(session_dur),
        "paused":            sess.paused,
        "active":            sess.session_active,
        "session_id":        sess.session_id,
    }
    # Per-beat RR pulses with cumulative timestamps for frontend coherence
    # (matching Unity rrPulse TCP messages)
    if rr_pulses:
        payload["rr_pulses"] = rr_pulses
    return payload


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

        # Spread RR intervals across proper cumulative timestamps.
        # BLE notifications arrive every ~5 s carrying a batch of 5-9 RRs.
        # Without spreading, all RRs in a batch share the same wall-clock
        # timestamp — which destroys spectral resolution (Nyquist drops to
        # ~0.09 Hz, below the 0.1 Hz breathing guidance frequency).
        #
        # This mirrors the Python relay server's RRPulseDealer: each RR
        # advances a cumulative clock by rr_ms / 1000, anchored to wall-
        # clock on the first RR of the first batch after session start.
        now = time.time()
        if not hasattr(sess, '_rr_wall_ref') or sess._rr_wall_ref is None:
            sess._rr_wall_ref = now
            sess._rr_cumulative_ts = 0.0

        # Collect per-beat RR pulses for frontend coherence (matching Unity rrPulse)
        batch_rr_pulses = []
        for rr in rr_intervals:
            rr_val = float(rr)
            if 200 < rr_val < 2000:
                sess._rr_cumulative_ts += rr_val / 1000.0
                rr_ts = sess._rr_wall_ref + sess._rr_cumulative_ts
                batch_rr_pulses.append({"rr": round(rr_val, 1), "ts": round(rr_ts, 3)})
                sess.rr_intervals.append(rr_val)
                sess.rr_timestamps.append(rr_ts)
                sess.latest_rr_interval = rr_val
                # Also track for per-cycle RR (CDI PRBF)
                if hasattr(sess, 'current_cycle_rr'):
                    sess.current_cycle_rr.append(rr_val)

        is_paused  = sess.paused
        is_active  = sess.session_active

        if is_active:
            # Pass ALL session RRs — compute_lf_power() applies its own 120 s
            # time-based sliding window internally, matching Unity's
            # LFPowerAnalyzer which also keeps all RRs and trims by
            # cumulativeTime - windowDuration.
            recent_rr = list(sess.rr_intervals)
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
        snap_bpm   = avg_bpm
        snap_rmssd = sess.latest_rmssd
        snap_cycle = sess.breath_cycle

    payload = _make_payload(sess, snap_bpm, snap_rmssd, snap_cycle, batch_rr_pulses)
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
        sess._rr_wall_ref    = None
        sess._rr_cumulative_ts = 0.0
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
