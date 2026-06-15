"""
CDI Personalized Resonance Breathing Frequency (PRBF) Module.

This module implements the CDI-specific PRBF protocol:
- 10 cycle-length stages from 5.5s to 10.0s (0.5s increment per breath)
- Each stage: 60s of paced breathing, followed by 30s washout
- Records LF Power and RMSSD for each cycle length
- Selects the cycle length with highest LF Power as the personalized
  resonance breathing frequency

Route: /cdi-prbf (page), /api/cdi-prbf/* (API)
"""

import json
import time
from flask import Blueprint, render_template, jsonify, request

import state
from state import SessionState
from routes.utils import get_current_session
from hrv import compute_lf_power, compute_rmssd

cdi_prbf_bp = Blueprint("cdi_prbf", __name__)


# CDI PRBF Protocol Constants
# Cycle lengths in seconds per full breath (inhale + exhale).
# Range: 5.5 s to 10.0 s in 0.5 s increments (10 stages).
CDI_CYCLE_LENGTHS = [5.5, 6.0, 6.5, 7.0, 7.5, 8.0, 8.5, 9.0, 9.5, 10.0]
# Derived: corresponding breathing frequencies in breaths per minute.
CDI_FREQUENCIES = [round(60.0 / cl, 2) for cl in CDI_CYCLE_LENGTHS]

# Each paced-breathing stage lasts 60 s; 30 s washout between stages.
STAGE_DURATION = 60      # seconds of paced breathing per stage
WASHOUT_DURATION = 30    # seconds of rest between stages

# Kept for backward compatibility (no longer used for stage advancement).
CYCLES_PER_FREQUENCY = 3


def _get_cdi_config(sess: SessionState) -> dict:
    """Get CDI PRBF specific config from session."""
    return sess.session_config.get("cdi_prbf", {})


def _compute_stage_metrics(sess: SessionState) -> dict:
    """Compute average LF Power and RMSSD for current stage."""
    config = _get_cdi_config(sess)
    stage_data = config.get("stage_data", [])
    
    if not stage_data:
        return {"lf_avg": None, "rmssd_avg": None, "bpm_avg": None, "count": 0}
    
    lf_values = [d["lf"] for d in stage_data if d.get("lf") is not None]
    rmssd_values = [d["rmssd"] for d in stage_data if d.get("rmssd") is not None]
    bpm_values = [d["bpm"] for d in stage_data if d.get("bpm") is not None]
    
    return {
        "lf_avg": sum(lf_values) / len(lf_values) if lf_values else None,
        "rmssd_avg": sum(rmssd_values) / len(rmssd_values) if rmssd_values else None,
        "bpm_avg": sum(bpm_values) / len(bpm_values) if bpm_values else None,
        "count": len(stage_data)
    }


# ── Page Route ───────────────────────────────────────────────────────────────

@cdi_prbf_bp.route("/cdi-prbf")
def cdi_prbf_page():
    """Serve the CDI PRBF test page."""
    return render_template("cdi_prbf.html")


# ── API Routes ───────────────────────────────────────────────────────────────

@cdi_prbf_bp.route("/api/cdi-prbf/configure", methods=["POST"])
def configure():
    """Configure CDI PRBF test parameters."""
    sess = get_current_session()
    data = request.get_json(silent=True) or {}
    
    blossom_threshold = data.get("blossom_threshold", 500)

    cdi_config = {
        "mode": data.get("mode", "find_prbf"),  # "find_prbf", "control_group", or "baseline"
        "frequencies": CDI_FREQUENCIES,
        "cycle_lengths": CDI_CYCLE_LENGTHS,
        "cycles_per_frequency": data.get("cycles_per_frequency", CYCLES_PER_FREQUENCY),
        "stage_duration": STAGE_DURATION,
        "washout_duration": WASHOUT_DURATION,
        "inhale_ratio": data.get("inhale_ratio", 5),
        "exhale_ratio": data.get("exhale_ratio", 5),
        "current_stage": 0,
        "current_cycle": 0,
        "stage_results": [],
        "stage_data": [],  # Current stage cycle data
        "test_active": False,
        "test_complete": False,
        "username": data.get("username", ""),
        "duration": data.get("duration", 360),  # control_group/baseline, default 6 min
        "frequency": data.get("frequency", 6.0),  # control_group only, default 6 bpm (0.1 Hz)
    }

    with sess.lock:
        sess.session_config["cdi_prbf"] = cdi_config
        sess.session_config["inhale_ratio"] = cdi_config["inhale_ratio"]
        sess.session_config["exhale_ratio"] = cdi_config["exhale_ratio"]
        sess.blossom_threshold = float(blossom_threshold)
    
    return jsonify({
        "ok": True,
        "config": cdi_config,
        "frequencies": CDI_FREQUENCIES,
        "cycle_lengths": CDI_CYCLE_LENGTHS,
        "stage_duration": STAGE_DURATION,
        "washout_duration": WASHOUT_DURATION,
    })


@cdi_prbf_bp.route("/api/cdi-prbf/start", methods=["POST"])
def start_test():
    """Start CDI PRBF test (find_prbf, control_group, or baseline)."""
    sess = get_current_session()

    with sess.lock:
        cdi_config = sess.session_config.get("cdi_prbf", {})
        if not cdi_config:
            return jsonify({"error": "Not configured"}), 400

        mode = cdi_config.get("mode", "find_prbf")
        cdi_config["test_active"] = True
        cdi_config["current_stage"] = 0
        cdi_config["current_cycle"] = 0
        cdi_config["stage_results"] = []
        cdi_config["stage_data"] = []
        cdi_config["test_complete"] = False

        sess.session_active = True
        sess.session_start = time.time()
        sess.bpm_window.clear()
        sess.hrv_window.clear()
        sess.rr_intervals.clear()
        sess.rr_timestamps.clear()
        sess.bpm_all.clear()
        sess.latest_rmssd = None
        sess.latest_lf_power = None
        sess.latest_bpm = 0.0
        sess._rr_wall_ref = None
        sess._rr_cumulative_ts = 0.0
        sess.current_cycle_rr.clear()
        sess.cycle_rr_list.clear()
        sess.blossom_count = 0
        sess.blossom_streak = 0
        sess.last_blossom_time = -999

        if mode == "baseline" or mode == "deep_breathing":
            duration = cdi_config.get("duration", 360)
            return jsonify({
                "ok": True,
                "mode": mode,
                "duration": duration,
            })
        elif mode == "control_group":
            # Fixed 0.1 Hz (6 bpm) resonance breathing
            freq = cdi_config.get("frequency", 6.0)
            cycle = 60.0 / freq
            duration = cdi_config.get("duration", 360)
            sess.session_config["breath_cycle"] = cycle
            sess.breath_cycle = cycle
            return jsonify({
                "ok": True,
                "mode": "control_group",
                "frequency": freq,
                "cycle": cycle,
                "duration": duration,
            })
        else:
            # find_prbf: multi-frequency sweep with timed stages + washout
            first_cl = CDI_CYCLE_LENGTHS[0]
            first_freq = CDI_FREQUENCIES[0]
            sess.session_config["breath_cycle"] = first_cl
            sess.breath_cycle = first_cl
            return jsonify({
                "ok": True,
                "mode": "find_prbf",
                "current_stage": 0,
                "cycle_length": first_cl,
                "frequency": first_freq,
                "total_stages": len(CDI_CYCLE_LENGTHS),
                "stage_duration": STAGE_DURATION,
                "washout_duration": WASHOUT_DURATION,
            })


@cdi_prbf_bp.route("/api/cdi-prbf/on-cycle-complete", methods=["POST"])
def on_cycle_complete():
    """
    Called when a breathing cycle completes.
    Records current metrics and advances stage/cycle if needed.
    """
    sess = get_current_session()
    
    with sess.lock:
        cdi_config = sess.session_config.get("cdi_prbf", {})
        if not cdi_config.get("test_active"):
            return jsonify({"error": "Test not active"}), 400

        mode = cdi_config.get("mode", "find_prbf")
        current_stage = cdi_config["current_stage"]
        current_cycle = cdi_config["current_cycle"]

        # Save RR intervals for this cycle
        cycle_rr = list(sess.current_cycle_rr)
        if cycle_rr:
            sess.cycle_rr_list.append(cycle_rr)
        sess.current_cycle_rr.clear()

        # Record current cycle data
        cycle_data = {
            "cycle": current_cycle + 1,
            "bpm": sess.latest_bpm,
            "rmssd": sess.latest_rmssd,
            "lf": sess.latest_lf_power,
            "rr_intervals": cycle_rr,
        }
        cdi_config["stage_data"].append(cycle_data)

        # Only find_prbf mode has frequency staging; control_group/baseline just record cycles
        if mode != "find_prbf":
            cdi_config["current_cycle"] = current_cycle + 1
            return jsonify({
                "ok": True,
                "stage_complete": False,
                "test_complete": False,
                "mode": mode,
                "cycle": current_cycle + 2,
            })

        # find_prbf: time-based stage advancement.
        # on-cycle-complete only records per-cycle data; stage advancement is
        # triggered separately by on-stage-time-up when the 60 s timer expires.
        cdi_config["current_cycle"] = current_cycle + 1

        return jsonify({
            "ok": True,
            "stage_complete": False,
            "test_complete": False,
            "current_stage": current_stage + 1,  # 1-indexed
            "current_cycle": current_cycle + 2,  # 1-indexed
            "cycle_data": cycle_data,
        })


@cdi_prbf_bp.route("/api/cdi-prbf/advance-stage", methods=["POST"])
def advance_stage():
    """
    Called by frontend when the 60 s stage timer expires.
    Computes stage metrics, advances to the next stage, and returns
    the next cycle length (or signals test completion).
    """
    sess = get_current_session()

    with sess.lock:
        cdi_config = sess.session_config.get("cdi_prbf", {})
        if not cdi_config.get("test_active"):
            return jsonify({"error": "Test not active"}), 400

        mode = cdi_config.get("mode", "find_prbf")
        if mode != "find_prbf":
            return jsonify({"error": "Not in find_prbf mode"}), 400

        current_stage = cdi_config["current_stage"]
        total_stages = len(CDI_CYCLE_LENGTHS)

        # Compute metrics for the stage that just finished
        metrics = _compute_stage_metrics(sess)
        stage_result = {
            "stage": current_stage + 1,
            "cycle_length": CDI_CYCLE_LENGTHS[current_stage],
            "frequency": CDI_FREQUENCIES[current_stage],
            "lf_avg": metrics["lf_avg"],
            "rmssd_avg": metrics["rmssd_avg"],
            "bpm_avg": metrics["bpm_avg"],
            "cycles_completed": metrics["count"],
        }
        cdi_config["stage_results"].append(stage_result)

        # Advance to next stage (or finish)
        if current_stage + 1 >= total_stages:
            # Test complete — all stages done
            cdi_config["test_active"] = False
            cdi_config["test_complete"] = True

            # Find best stage (highest LF Power)
            best = None
            for sr in cdi_config["stage_results"]:
                if sr.get("lf_avg") is not None:
                    if best is None or sr["lf_avg"] > best["lf_avg"]:
                        best = sr
            cdi_config["best_result"] = best

            sess.session_active = False

            return jsonify({
                "ok": True,
                "stage_complete": True,
                "test_complete": True,
                "stage": current_stage + 1,
                "total_stages": total_stages,
                "stage_result": stage_result,
                "best_result": best,
            })
        else:
            # Advance to next stage
            cdi_config["current_stage"] = current_stage + 1
            cdi_config["current_cycle"] = 0
            cdi_config["stage_data"] = []

            next_cl = CDI_CYCLE_LENGTHS[current_stage + 1]
            next_freq = CDI_FREQUENCIES[current_stage + 1]
            sess.session_config["breath_cycle"] = next_cl
            sess.breath_cycle = next_cl

            return jsonify({
                "ok": True,
                "stage_complete": True,
                "test_complete": False,
                "new_stage": current_stage + 2,  # 1-indexed
                "new_cycle_length": next_cl,
                "new_frequency": next_freq,
                "stage_result": stage_result,
                "washout_duration": WASHOUT_DURATION,
            })


@cdi_prbf_bp.route("/api/cdi-prbf/status")
def get_status():
    """Get current test status and progress."""
    sess = get_current_session()
    
    with sess.lock:
        cdi_config = sess.session_config.get("cdi_prbf", {})
        if not cdi_config:
            return jsonify({"error": "Not configured"}), 400
        
        current_stage = cdi_config.get("current_stage", 0)
        current_cycle = cdi_config.get("current_cycle", 0)
        
        # Compute current stage metrics
        metrics = _compute_stage_metrics(sess)
        
        return jsonify({
            "test_active": cdi_config.get("test_active", False),
            "test_complete": cdi_config.get("test_complete", False),
            "current_stage": current_stage + 1,  # 1-indexed
            "current_cycle": current_cycle + 1,  # 1-indexed
            "total_stages": len(CDI_CYCLE_LENGTHS),
            "cycles_per_frequency": cdi_config.get("cycles_per_frequency", CYCLES_PER_FREQUENCY),
            "current_cycle_length": CDI_CYCLE_LENGTHS[current_stage] if current_stage < len(CDI_CYCLE_LENGTHS) else None,
            "current_frequency": CDI_FREQUENCIES[current_stage] if current_stage < len(CDI_FREQUENCIES) else None,
            "current_metrics": metrics,
            "stage_results": cdi_config.get("stage_results", []),
            "best_result": cdi_config.get("best_result"),
            "blossom_count": sess.blossom_count,
            "blossom_threshold": sess.blossom_threshold,
            "stage_duration": STAGE_DURATION,
            "washout_duration": WASHOUT_DURATION,
        })


@cdi_prbf_bp.route("/api/cdi-prbf/stop", methods=["POST"])
def stop_test():
    """Stop the test early."""
    sess = get_current_session()
    
    with sess.lock:
        cdi_config = sess.session_config.get("cdi_prbf", {})
        if cdi_config:
            cdi_config["test_active"] = False
        sess.session_active = False
    
    return jsonify({"ok": True})


@cdi_prbf_bp.route("/api/cdi-prbf/reset", methods=["POST"])
def reset_test():
    """Reset test state."""
    sess = get_current_session()
    
    with sess.lock:
        if "cdi_prbf" in sess.session_config:
            del sess.session_config["cdi_prbf"]
        sess.session_active = False
        sess.bpm_window.clear()
        sess.hrv_window.clear()
        sess.rr_intervals.clear()
        sess.rr_timestamps.clear()
        sess.bpm_all.clear()
        sess.latest_rmssd = None
        sess.latest_lf_power = None
        sess.latest_bpm = 0.0
        # Clear cycle RR data
        if hasattr(sess, 'current_cycle_rr'):
            sess.current_cycle_rr.clear()
        if hasattr(sess, 'cycle_rr_list'):
            sess.cycle_rr_list.clear()
    
    return jsonify({"ok": True})


@cdi_prbf_bp.route("/api/cdi-prbf/results")
def get_results():
    """Get complete test results."""
    sess = get_current_session()
    
    with sess.lock:
        cdi_config = sess.session_config.get("cdi_prbf", {})
        if not cdi_config:
            return jsonify({"error": "No test data"}), 400
        
        return jsonify({
            "test_complete": cdi_config.get("test_complete", False),
            "stage_results": cdi_config.get("stage_results", []),
            "best_result": cdi_config.get("best_result"),
            "all_rr_intervals": list(sess.rr_intervals),
            "all_bpm": list(sess.bpm_all),
        })


@cdi_prbf_bp.route("/api/cdi-prbf/blossom-event", methods=["POST"])
def blossom_event():
    """
    Called by frontend when a blossom burst triggers.
    Tracks blossom count and timing server-side so it can be saved.
    """
    sess = get_current_session()

    with sess.lock:
        sess.blossom_count = min(sess.blossom_count + 1, 5)
        sess.blossom_streak = 0
        if sess.session_start:
            sess.last_blossom_time = time.time() - sess.session_start
        else:
            sess.last_blossom_time = 0

    return jsonify({
        "ok": True,
        "blossom_count": sess.blossom_count,
        "last_blossom_time": sess.last_blossom_time,
    })


@cdi_prbf_bp.route("/api/cdi-prbf/blossom-status")
def blossom_status():
    """Get current blossom state (count, streak, cooldown progress)."""
    sess = get_current_session()

    with sess.lock:
        cdi_config = sess.session_config.get("cdi_prbf", {})
        cooldown = cdi_config.get("duration", 360) / 5.0

        elapsed = 0.0
        if sess.session_start and sess.last_blossom_time > 0:
            elapsed = (time.time() - sess.session_start) - sess.last_blossom_time

        cooldown_remaining = max(0, cooldown - elapsed) if sess.blossom_count < 5 else 0

        return jsonify({
            "blossom_count": sess.blossom_count,
            "blossom_streak": sess.blossom_streak,
            "blossom_threshold": sess.blossom_threshold,
            "last_blossom_time": sess.last_blossom_time,
            "cooldown_remaining": round(cooldown_remaining, 1),
            "max_blossoms": 5,
        })


@cdi_prbf_bp.route("/api/cdi-prbf/blossom-streak", methods=["POST"])
def update_blossom_streak():
    """
    Called by frontend every ~1s with current LF power.
    Server tracks the streak counter for blossom trigger gating.
    Returns whether a blossom should trigger.
    """
    sess = get_current_session()
    data = request.get_json(silent=True) or {}
    lf_power = data.get("lf_power", 0)
    threshold = sess.blossom_threshold

    with sess.lock:
        cdi_config = sess.session_config.get("cdi_prbf", {})
        cooldown = cdi_config.get("duration", 360) / 5.0

        elapsed_since_last = 999.0
        if sess.session_start:
            now = time.time() - sess.session_start
            if sess.last_blossom_time > 0:
                elapsed_since_last = now - sess.last_blossom_time

        can_blossom = (
            sess.blossom_count < 5
            and elapsed_since_last >= cooldown
        )

        should_trigger = False
        if can_blossom and lf_power is not None and lf_power >= threshold:
            sess.blossom_streak += 1
            if sess.blossom_streak >= 3:
                should_trigger = True
        elif lf_power is not None and lf_power < threshold:
            sess.blossom_streak = 0

        # Progress toward next blossom
        cooldown_ratio = min(1.0, max(0.0, elapsed_since_last / cooldown)) if can_blossom else 1.0
        power_ratio = min(1.0, max(0.0, (lf_power or 0) / (threshold * 2)))
        progress = cooldown_ratio * (0.5 + 0.5 * power_ratio) if sess.blossom_count < 5 else 1.0

        return jsonify({
            "should_trigger": should_trigger,
            "blossom_count": sess.blossom_count,
            "blossom_streak": sess.blossom_streak,
            "progress": round(progress, 4),
            "cooldown_remaining": round(max(0, cooldown - elapsed_since_last), 1),
        })


@cdi_prbf_bp.route("/api/cdi-prbf/on-baseline-complete", methods=["POST"])
def baseline_complete():
    """Mark baseline test as complete (called by frontend timer)."""
    sess = get_current_session()

    with sess.lock:
        cdi_config = sess.session_config.get("cdi_prbf", {})
        if not cdi_config.get("test_active"):
            return jsonify({"error": "Test not active"}), 400

        cdi_config["test_active"] = False
        cdi_config["test_complete"] = True
        sess.session_active = False

    return jsonify({"ok": True})


@cdi_prbf_bp.route("/api/cdi-prbf/save", methods=["POST"])
def save_results():
    """Save CDI PRBF test results - handles both baseline and control_group modes."""
    import uuid
    from datetime import datetime, timezone

    sess = get_current_session()
    data = request.get_json(silent=True) or {}
    username = data.get("username", "") or sess.session_config.get("username", "") or "anonymous"

    with sess.lock:
        cdi_config = sess.session_config.get("cdi_prbf", {})
        if not cdi_config:
            return jsonify({"error": "No test data"}), 400

        mode = cdi_config.get("mode", "find_prbf")
        sub_type = mode  # sub_type mirrors mode
        bpm_all = list(sess.bpm_all)
        rr_data = list(sess.rr_intervals)
        rr_ts_data = list(sess.rr_timestamps)

        # Compute HRV metrics
        final_rmssd = compute_rmssd(rr_data)
        final_lf = compute_lf_power(rr_data)

        # Initialize variables shared across mode branches
        stage_results = []
        best_result = None
        cycle_rr_list = []

        if mode == "baseline" or mode == "deep_breathing":
            # Baseline / Deep Breathing: flat format, no visual guidance
            session_record = {
                "id": str(uuid.uuid4()),
                "session_id": sess.session_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "username": username,
                "training_type": "cdi_prbf",
                "sub_type": mode,
                "tag": "同济设创数据",
                "config": {
                    "mode": mode,
                    "duration": cdi_config.get("duration", 360),
                },
                "rr_intervals_ms": rr_data,
                "rr_timestamps": rr_ts_data,
                "rr_count": len(rr_data),
                "bpm_readings": bpm_all,
                "bpm_max": round(max(bpm_all), 1) if bpm_all else None,
                "bpm_min": round(min(bpm_all), 1) if bpm_all else None,
                "bpm_avg": round(sum(bpm_all) / len(bpm_all), 1) if bpm_all else None,
                "hrv_rmssd": round(final_rmssd, 2) if final_rmssd is not None else None,
                "lf_power": round(final_lf, 4) if final_lf is not None else None,
                "blossom_count": sess.blossom_count,
                "blossom_threshold": sess.blossom_threshold,
            }
        elif mode == "control_group":
            # Control group: flat format, fixed 0.1 Hz resonance with visual guidance
            freq = cdi_config.get("frequency", 6.0)
            session_record = {
                "id": str(uuid.uuid4()),
                "session_id": sess.session_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "username": username,
                "training_type": "cdi_prbf",
                "sub_type": "control_group",
                "tag": "同济设创数据",
                "config": {
                    "mode": "control_group",
                    "frequency": freq,
                    "breath_cycle": 60.0 / freq,
                    "duration": cdi_config.get("duration", 360),
                },
                "rr_intervals_ms": rr_data,
                "rr_timestamps": rr_ts_data,
                "rr_count": len(rr_data),
                "bpm_readings": bpm_all,
                "bpm_max": round(max(bpm_all), 1) if bpm_all else None,
                "bpm_min": round(min(bpm_all), 1) if bpm_all else None,
                "bpm_avg": round(sum(bpm_all) / len(bpm_all), 1) if bpm_all else None,
                "hrv_rmssd": round(final_rmssd, 2) if final_rmssd is not None else None,
                "lf_power": round(final_lf, 4) if final_lf is not None else None,
                "blossom_count": sess.blossom_count,
                "blossom_threshold": sess.blossom_threshold,
                # Frontend time-series (1 Hz sampling during the session)
                "lf_power_series": data.get("lf_power_series", []),
                "coherence_series": data.get("coherence_series", []),
                "series_timestamps": data.get("series_timestamps", []),
            }
        else:
            # find_prbf: multi-frequency sweep with timed stages + washout
            stage_data = cdi_config.get("stage_data", [])
            stage_results = cdi_config.get("stage_results", [])
            best_result = cdi_config.get("best_result")
            cycle_rr_list = list(sess.cycle_rr_list)

            # Build per-cycle data
            cycles = []
            for i, rr_cycle in enumerate(cycle_rr_list):
                # Cycles are recorded sequentially; map to stage via
                # cumulative cycle counts stored alongside each stage's data.
                # For simplicity we tag each cycle with the stage inferred
                # from the cycle index and the per-stage cycle counts in
                # stage_results.
                cycles.append({
                    "cycle_number": i + 1,
                    "rr_intervals": rr_cycle,
                    "rr_count": len(rr_cycle),
                })

            # Attach stage info to cycles by walking through stage_results
            cycle_idx = 0
            for sr in stage_results:
                n = sr.get("cycles_completed", 0)
                for j in range(n):
                    if cycle_idx + j < len(cycles):
                        cycles[cycle_idx + j]["stage"] = sr["stage"]
                        cycles[cycle_idx + j]["cycle_length"] = sr["cycle_length"]
                        cycles[cycle_idx + j]["frequency"] = sr["frequency"]
                cycle_idx += n

            session_record = {
                "id": str(uuid.uuid4()),
                "session_id": sess.session_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "username": username,
                "training_type": "cdi_prbf",
                "sub_type": "find_prbf",
                "tag": "同济设创数据",
                "config": {
                    "cycle_lengths": CDI_CYCLE_LENGTHS,
                    "frequencies": CDI_FREQUENCIES,
                    "stage_duration": STAGE_DURATION,
                    "washout_duration": WASHOUT_DURATION,
                    "total_stages": len(CDI_CYCLE_LENGTHS),
                    "total_cycles": len(cycle_rr_list),
                },
                "cycles": cycles,
                "stage_results": stage_results,
                "best_result": best_result,
                "all_bpm": bpm_all,
                "bpm_avg": sum(bpm_all) / len(bpm_all) if bpm_all else None,
                "bpm_max": max(bpm_all) if bpm_all else None,
                "bpm_min": min(bpm_all) if bpm_all else None,
                "hrv_rmssd": round(final_rmssd, 2) if final_rmssd is not None else None,
                "lf_power": round(final_lf, 4) if final_lf is not None else None,
            }

        # Save to file
        filename = f"cdi_prbf_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{session_record['id'][:8]}.json"
        filepath = state.DATA_DIR / filename
        import json as json_mod
        with open(filepath, "w", encoding="utf-8") as f:
            json_mod.dump(session_record, f, ensure_ascii=False, indent=2)

        # Mirror to shared dir if configured
        mirrored_to = None
        if state.SHARED_DATA_DIR is not None:
            shared_file = state.SHARED_DATA_DIR / filename
            import shutil
            shutil.copy2(filepath, shared_file)
            mirrored_to = str(shared_file)

        # Clear cycle data to prevent duplicate saves
        sess.cycle_rr_list.clear()
        sess.current_cycle_rr.clear()

        # Reset test state
        cdi_config["test_active"] = False

        return jsonify({
            "ok": True,
            "file": str(filepath.name),
            "mirrored_to": mirrored_to,
            "summary": {
                "mode": mode,
                "total_cycles": len(cycle_rr_list),
                "total_stages": len(stage_results),
                "bpm_avg": session_record["bpm_avg"],
                "best_frequency": best_result.get("frequency") if best_result else None,
                "best_cycle_length": best_result.get("cycle_length") if best_result else None,
            },
        })
