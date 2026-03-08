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
from hrv import compute_lf_power, compute_rmssd

training_bp = Blueprint("training", __name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_session_duration() -> float:
    with state.config_lock:
        return float(state.session_config["session_duration"])


def _get_base_cycle() -> float:
    with state.config_lock:
        return float(state.session_config["breath_cycle"])


def _is_adaptive() -> bool:
    with state.config_lock:
        return bool(state.session_config["adaptive"])


def _compute_cycle(rmssd: float | None) -> float:
    """Return adjusted cycle when HRV is poor (adaptive mode)."""
    base = _get_base_cycle()
    if not _is_adaptive():
        return base
    if rmssd is None or len(state.hrv_window) < 3:
        return base
    baseline = sorted(state.hrv_window)[len(state.hrv_window) // 2]
    if baseline > 0 and rmssd < baseline * state.HRV_DROP_RATIO:
        return base + 1.0
    return base


def _elapsed_seconds() -> float:
    """Return total elapsed session seconds, accounting for pauses."""
    if state.paused:
        return state.elapsed_before_pause
    return state.elapsed_before_pause + (time.time() - state.session_start)


def _make_payload(bpm: float, rmssd: float | None, cycle: float) -> dict:
    half = round(cycle / 2, 1)
    session_dur = _get_session_duration()
    remaining = max(0.0, session_dur - _elapsed_seconds()) if state.session_active else session_dur
    return {
        "bpm":        round(bpm, 1),
        "hrv_rmssd":  round(rmssd, 1) if rmssd is not None else None,
        "lf_power":   round(state.latest_lf_power, 4) if state.latest_lf_power is not None else None,
        "total":      cycle,
        "inhale":     half,
        "exhale":     half,
        "session_remaining": round(remaining),
        "session_duration":  round(session_dur),
        "paused":     state.paused,
        "active":     state.session_active,
    }


def _broadcast(payload: dict):
    data = json.dumps(payload)
    dead = []
    with state.lock:
        for q in state.subscribers:
            try:
                q.put_nowait(data)
            except queue.Full:
                dead.append(q)
        for q in dead:
            state.subscribers.remove(q)


# ── Routes ────────────────────────────────────────────────────────────────────

@training_bp.route("/api/pulse", methods=["POST"])
def receive_pulse():
    try:
        body = request.get_json(silent=True)
        if body and "bpm" in body:
            bpm = float(body["bpm"])
        else:
            bpm = float(request.data.decode().strip())
        rr_intervals = body.get("rr_intervals", []) if body else []
    except (ValueError, TypeError):
        return jsonify({"error": "invalid payload"}), 400

    with state.lock:
        state.bpm_window.append(bpm)
        avg_bpm = sum(state.bpm_window) / len(state.bpm_window)
        state.latest_bpm = avg_bpm
        state.bpm_all.append(bpm)

        # Collect RR intervals
        now = time.time()
        for rr in rr_intervals:
            rr_val = float(rr)
            if 200 < rr_val < 2000:  # sanity: 30–300 BPM range
                state.rr_intervals.append(rr_val)
                state.rr_timestamps.append(now)

        # Compute HRV from collected RR intervals (server-side)
        recent_rr = state.rr_intervals[-120:] if len(state.rr_intervals) > 120 else list(state.rr_intervals)
        computed_rmssd = compute_rmssd(recent_rr)
        computed_lf = compute_lf_power(recent_rr)

        if computed_rmssd is not None:
            state.latest_rmssd = computed_rmssd
            state.hrv_window.append(computed_rmssd)
        elif body and "hrv_rmssd" in body:
            rmssd_val = float(body["hrv_rmssd"])
            state.hrv_window.append(rmssd_val)
            state.latest_rmssd = rmssd_val

        if computed_lf is not None:
            state.latest_lf_power = computed_lf

        state.breath_cycle = _compute_cycle(state.latest_rmssd)
        is_paused = state.paused
        is_active = state.session_active

    payload = _make_payload(avg_bpm, state.latest_rmssd, state.breath_cycle)
    if not is_paused and is_active:
        _broadcast(payload)
    return jsonify(payload), 200


@training_bp.route("/api/status")
def status():
    with state.lock:
        avg_bpm = sum(state.bpm_window) / len(state.bpm_window) if state.bpm_window else 0
        cycle = state.breath_cycle
        rmssd = state.latest_rmssd
    return jsonify(_make_payload(avg_bpm, rmssd, cycle))


@training_bp.route("/api/start", methods=["POST"])
def start_session():
    """Begin the breathing training session. Resets all counters."""
    with state.lock:
        state.session_start = time.time()
        state.paused = False
        state.session_active = True
        state.pause_time = 0.0
        state.elapsed_before_pause = 0.0
        state.bpm_window.clear()
        state.hrv_window.clear()
        state.rr_intervals.clear()
        state.rr_timestamps.clear()
        state.bpm_all.clear()
        state.latest_rmssd = None
        state.latest_lf_power = None
        state.latest_bpm = 0.0
        state.breath_cycle = _get_base_cycle()
    with state.lock:
        avg_bpm = sum(state.bpm_window) / len(state.bpm_window) if state.bpm_window else 0
        cycle, rmssd = state.breath_cycle, state.latest_rmssd
    payload = _make_payload(avg_bpm, rmssd, cycle)
    _broadcast(payload)
    return jsonify({"ok": True, "config": dict(state.session_config)})


@training_bp.route("/api/pause", methods=["POST"])
def pause_session():
    with state.lock:
        if not state.paused:
            state.elapsed_before_pause += time.time() - state.session_start
            state.paused = True
            state.pause_time = time.time()
        avg_bpm = sum(state.bpm_window) / len(state.bpm_window) if state.bpm_window else 0
        cycle, rmssd = state.breath_cycle, state.latest_rmssd
    payload = _make_payload(avg_bpm, rmssd, cycle)
    _broadcast(payload)
    return jsonify(payload)


@training_bp.route("/api/play", methods=["POST"])
def play_session():
    with state.lock:
        if state.paused:
            state.session_start = time.time()
            state.paused = False
        avg_bpm = sum(state.bpm_window) / len(state.bpm_window) if state.bpm_window else 0
        cycle, rmssd = state.breath_cycle, state.latest_rmssd
    payload = _make_payload(avg_bpm, rmssd, cycle)
    _broadcast(payload)
    return jsonify(payload)


@training_bp.route("/api/stream")
def stream():
    """Server-Sent Events endpoint – one persistent connection per browser tab."""
    def event_generator():
        q: queue.Queue = queue.Queue(maxsize=20)
        with state.lock:
            state.subscribers.append(q)
        try:
            with state.lock:
                avg_bpm = sum(state.bpm_window) / len(state.bpm_window) if state.bpm_window else 0
                cycle = state.breath_cycle
                rmssd = state.latest_rmssd
            payload = _make_payload(avg_bpm, rmssd, cycle)
            yield f"data: {json.dumps(payload)}\n\n"

            while True:
                try:
                    data = q.get(timeout=20)
                    yield f"data: {data}\n\n"
                except queue.Empty:
                    yield ": heartbeat\n\n"
        finally:
            with state.lock:
                try:
                    state.subscribers.remove(q)
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
