"""
Setup / configuration routes  (start-scene).

GET  /api/config      – read current config
POST /api/configure   – set session parameters before training
"""

from flask import Blueprint, jsonify, request

from state import config_lock, session_config

setup_bp = Blueprint("setup", __name__)


@setup_bp.route("/api/configure", methods=["POST"])
def configure_session():
    """Set session parameters before starting training."""
    body = request.get_json(silent=True) or {}
    with config_lock:
        if "breath_cycle" in body:
            val = float(body["breath_cycle"])
            if 4.0 <= val <= 20.0:
                session_config["breath_cycle"] = val
        if "session_duration" in body:
            val = int(body["session_duration"])
            if 30 <= val <= 3600:
                session_config["session_duration"] = val
        if "adaptive" in body:
            session_config["adaptive"] = bool(body["adaptive"])
        if "username" in body:
            session_config["username"] = str(body["username"]).strip()[:64]
        config_copy = dict(session_config)
    return jsonify({"ok": True, "config": config_copy})


@setup_bp.route("/api/config")
def get_config():
    with config_lock:
        return jsonify(dict(session_config))
