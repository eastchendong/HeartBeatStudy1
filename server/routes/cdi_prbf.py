"""
CDI Personalized Resonance Frequency Breathing (PRBF) Module.

This module implements the CDI-specific PRBF protocol:
- 10 frequency stages from 8.0 to 10.25 bpm (0.25 increment)
- 3 breathing cycles per frequency
- Records LF Power and RMSSD for each frequency
- Selects the frequency with highest LF Power as PRBF

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
CDI_FREQUENCIES = [8.0, 8.25, 8.5, 8.75, 9.0, 9.25, 9.5, 9.75, 10.0, 10.25]
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
    
    cdi_config = {
        "frequencies": CDI_FREQUENCIES,
        "cycles_per_frequency": data.get("cycles_per_frequency", CYCLES_PER_FREQUENCY),
        "inhale_ratio": data.get("inhale_ratio", 5),
        "exhale_ratio": data.get("exhale_ratio", 5),
        "current_stage": 0,
        "current_cycle": 0,
        "stage_results": [],
        "stage_data": [],  # Current stage cycle data
        "test_active": False,
        "test_complete": False,
        "username": data.get("username", ""),
    }
    
    with sess.lock:
        sess.session_config["cdi_prbf"] = cdi_config
        sess.session_config["inhale_ratio"] = cdi_config["inhale_ratio"]
        sess.session_config["exhale_ratio"] = cdi_config["exhale_ratio"]
    
    return jsonify({
        "ok": True,
        "config": cdi_config,
        "frequencies": CDI_FREQUENCIES,
    })


@cdi_prbf_bp.route("/api/cdi-prbf/start", methods=["POST"])
def start_test():
    """Start CDI PRBF test."""
    sess = get_current_session()
    
    with sess.lock:
        cdi_config = sess.session_config.get("cdi_prbf", {})
        if not cdi_config:
            return jsonify({"error": "Not configured"}), 400
        
        cdi_config["test_active"] = True
        cdi_config["current_stage"] = 0
        cdi_config["current_cycle"] = 0
        cdi_config["stage_results"] = []
        cdi_config["stage_data"] = []
        cdi_config["test_complete"] = False
        
        # Set initial breathing cycle for first frequency
        first_freq = CDI_FREQUENCIES[0]
        cycle = 60.0 / first_freq
        sess.session_config["breath_cycle"] = cycle
        sess.breath_cycle = cycle
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
    
    return jsonify({
        "ok": True,
        "current_stage": 0,
        "frequency": CDI_FREQUENCIES[0],
        "cycle": 60.0 / CDI_FREQUENCIES[0],
        "total_stages": len(CDI_FREQUENCIES),
        "cycles_per_frequency": cdi_config.get("cycles_per_frequency", CYCLES_PER_FREQUENCY),
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
        
        current_stage = cdi_config["current_stage"]
        current_cycle = cdi_config["current_cycle"]
        
        # Record current cycle data
        cycle_data = {
            "cycle": current_cycle + 1,
            "bpm": sess.latest_bpm,
            "rmssd": sess.latest_rmssd,
            "lf": sess.latest_lf_power,
        }
        cdi_config["stage_data"].append(cycle_data)
        
        cycles_per_freq = cdi_config.get("cycles_per_frequency", CYCLES_PER_FREQUENCY)
        total_stages = len(CDI_FREQUENCIES)
        
        # Check if we've completed all cycles for this frequency
        if current_cycle + 1 >= cycles_per_freq:
            # Compute stage metrics
            metrics = _compute_stage_metrics(sess)
            stage_result = {
                "stage": current_stage + 1,
                "frequency": CDI_FREQUENCIES[current_stage],
                "cycle": 60.0 / CDI_FREQUENCIES[current_stage],
                "lf_avg": metrics["lf_avg"],
                "rmssd_avg": metrics["rmssd_avg"],
                "bpm_avg": metrics["bpm_avg"],
                "cycles_completed": metrics["count"],
            }
            cdi_config["stage_results"].append(stage_result)
            
            # Move to next stage
            if current_stage + 1 >= total_stages:
                # Test complete
                cdi_config["test_active"] = False
                cdi_config["test_complete"] = True
                
                # Find best frequency (highest LF Power)
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
                
                next_freq = CDI_FREQUENCIES[current_stage + 1]
                next_cycle = 60.0 / next_freq
                sess.session_config["breath_cycle"] = next_cycle
                sess.breath_cycle = next_cycle
                
                return jsonify({
                    "ok": True,
                    "stage_complete": True,
                    "test_complete": False,
                    "new_stage": current_stage + 2,  # 1-indexed
                    "new_frequency": next_freq,
                    "new_cycle": next_cycle,
                    "stage_result": stage_result,
                })
        else:
            # Continue to next cycle in same stage
            cdi_config["current_cycle"] = current_cycle + 1
            
            return jsonify({
                "ok": True,
                "stage_complete": False,
                "test_complete": False,
                "current_stage": current_stage + 1,  # 1-indexed
                "current_cycle": current_cycle + 2,  # 1-indexed
                "cycle_data": cycle_data,
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
            "total_stages": len(CDI_FREQUENCIES),
            "cycles_per_frequency": cdi_config.get("cycles_per_frequency", CYCLES_PER_FREQUENCY),
            "current_frequency": CDI_FREQUENCIES[current_stage] if current_stage < len(CDI_FREQUENCIES) else None,
            "current_cycle_duration": 60.0 / CDI_FREQUENCIES[current_stage] if current_stage < len(CDI_FREQUENCIES) else None,
            "current_metrics": metrics,
            "stage_results": cdi_config.get("stage_results", []),
            "best_result": cdi_config.get("best_result"),
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


@cdi_prbf_bp.route("/api/cdi-prbf/save", methods=["POST"])
def save_results():
    """Save test results to file."""
    sess = get_current_session()
    data = request.get_json(silent=True) or {}
    username = data.get("username", "")
    
    with sess.lock:
        cdi_config = sess.session_config.get("cdi_prbf", {})
        if not cdi_config:
            return jsonify({"error": "No test data"}), 400
        
        from routes.results import _save_session_to_file
        
        result = _save_session_to_file(
            sess,
            username=username,
            training_type="cdi_prbf",
            extra_data={
                "cdi_prbf_results": cdi_config.get("stage_results", []),
                "cdi_prbf_best": cdi_config.get("best_result"),
            }
        )
        
        return jsonify(result)
