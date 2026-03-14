"""
Setup / configuration routes  (start-scene).

GET  /api/config      – read current config
POST /api/configure   – set session parameters before training
"""

from flask import Blueprint, jsonify, request
from routes.utils import get_current_session

setup_bp = Blueprint("setup", __name__)


@setup_bp.route("/api/configure", methods=["POST"])
def configure_session():
    sess = get_current_session()
    body = request.get_json(silent=True) or {}
    with sess.lock:
        if "breath_cycle" in body:
            val = float(body["breath_cycle"])
            if 4.0 <= val <= 20.0:
                sess.session_config["breath_cycle"] = val
        if "session_duration" in body:
            val = int(body["session_duration"])
            if 30 <= val <= 3600:
                sess.session_config["session_duration"] = val
        if "adaptive" in body:
            sess.session_config["adaptive"] = bool(body["adaptive"])
        if "username" in body:
            sess.session_config["username"] = str(body["username"]).strip()[:64]
        if "inhale_ratio" in body:
            val = int(body["inhale_ratio"])
            if 1 <= val <= 9:
                sess.session_config["inhale_ratio"] = val
        if "exhale_ratio" in body:
            val = int(body["exhale_ratio"])
            if 1 <= val <= 9:
                sess.session_config["exhale_ratio"] = val
        config_copy = dict(sess.session_config)
    return jsonify({"ok": True, "config": config_copy, "session_id": sess.session_id})


@setup_bp.route("/api/config")
def get_config():
    sess = get_current_session()
    with sess.lock:
        return jsonify(dict(sess.session_config))
