"""
Admin API routes for managing session data.

Provides endpoints for:
- Searching sessions by username
- Sorting by date, training type
- Downloading individual files
- Bulk download as ZIP
"""

import json
import zipfile
import io
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from flask import Blueprint, jsonify, request, send_file, abort

import state
from routes.auth import admin_required

admin_api_bp = Blueprint("admin_api", __name__)


def load_session_data(filepath: Path) -> Optional[Dict[str, Any]]:
    """Load and parse a session JSON file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception:
        return None


def detect_training_type(data: Dict[str, Any]) -> str:
    """Detect training type from session data."""
    # Check for Buteyko-specific fields
    if "bolt_seconds" in data or "round_results" in data or "buteyko_config" in data:
        return "buteyko"
    # Check for PRBF/adaptive experiment
    if "prbf_test" in data or data.get("config", {}).get("prbf_mode"):
        return "prbf"
    # Check frequency-based classification
    config = data.get("config", {})
    breath_cycle = config.get("breath_cycle", 10)
    if isinstance(breath_cycle, (int, float)):
        if breath_cycle < 5:
            return "fast_resonance"
        elif breath_cycle > 8:
            return "slow_resonance"
    return "resonance"


def get_session_info(filepath: Path) -> Optional[Dict[str, Any]]:
    """Extract session info from a JSON file for listing."""
    data = load_session_data(filepath)
    if not data:
        return None
    
    stat = filepath.stat()
    training_type = detect_training_type(data)
    
    return {
        "filename": filepath.name,
        "id": data.get("id", ""),
        "session_id": data.get("session_id", ""),
        "username": data.get("username", "anonymous"),
        "timestamp": data.get("timestamp", ""),
        "training_type": training_type,
        "training_type_label": get_training_type_label(training_type),
        "size_bytes": stat.st_size,
        "mtime_utc": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "bpm_avg": data.get("bpm_avg"),
        "bpm_min": data.get("bpm_min"),
        "bpm_max": data.get("bpm_max"),
        "hrv_rmssd": data.get("hrv_rmssd"),
        "lf_power": data.get("lf_power"),
        "rr_count": data.get("rr_count", 0),
    }


def get_training_type_label(training_type: str) -> str:
    """Get human-readable label for training type."""
    labels = {
        "buteyko": "布捷伊科屏气",
        "prbf": "个体共振频率 (PRBF)",
        "fast_resonance": "快速共振呼吸",
        "slow_resonance": "慢速共振呼吸",
        "resonance": "共振呼吸",
    }
    return labels.get(training_type, training_type)


@admin_api_bp.route("/api/admin/sessions")
@admin_required
def list_sessions():
    """
    List all sessions with optional filtering and sorting.
    
    Query params:
        - username: Filter by username (partial match)
        - training_type: Filter by training type
        - sort_by: Sort field (date, username, type, bpm_avg, hrv_rmssd)
        - sort_order: asc or desc
        - limit: Max results (default 100)
    """
    username_filter = request.args.get("username", "").strip().lower()
    training_type_filter = request.args.get("training_type", "").strip()
    sort_by = request.args.get("sort_by", "date")
    sort_order = request.args.get("sort_order", "desc")
    limit = request.args.get("limit", 100, type=int)
    
    # Load all sessions
    files = list(state.DATA_DIR.glob("session_*.json"))
    sessions = []
    for f in files:
        info = get_session_info(f)
        if info:
            sessions.append(info)
    
    # Apply filters
    if username_filter:
        sessions = [s for s in sessions if username_filter in s["username"].lower()]
    
    if training_type_filter:
        sessions = [s for s in sessions if s["training_type"] == training_type_filter]
    
    # Sort
    sort_key_map = {
        "date": "timestamp",
        "username": "username",
        "type": "training_type",
        "bpm_avg": "bpm_avg",
        "hrv_rmssd": "hrv_rmssd",
    }
    sort_key = sort_key_map.get(sort_by, "timestamp")
    reverse = sort_order == "desc"
    
    # Handle None values in sort
    def sort_func(s):
        val = s.get(sort_key)
        if val is None:
            return (1, "") if reverse else (0, "")
        return (0, val)
    
    sessions.sort(key=sort_func, reverse=reverse)
    
    # Limit results
    total = len(sessions)
    sessions = sessions[:limit]
    
    return jsonify({
        "ok": True,
        "sessions": sessions,
        "total": total,
        "filters": {
            "username": username_filter,
            "training_type": training_type_filter,
        },
        "sort": {
            "by": sort_by,
            "order": sort_order,
        },
        "training_types": [
            {"value": "buteyko", "label": "布捷伊科屏气"},
            {"value": "prbf", "label": "个体共振频率 (PRBF)"},
            {"value": "fast_resonance", "label": "快速共振呼吸"},
            {"value": "slow_resonance", "label": "慢速共振呼吸"},
            {"value": "resonance", "label": "共振呼吸"},
        ],
    })


@admin_api_bp.route("/api/admin/sessions/download/<path:filename>")
@admin_required
def download_session(filename: str):
    """Download a single session JSON file."""
    # Validate filename
    if not filename.endswith(".json") or "/" in filename or "\\" in filename or ".." in filename:
        abort(400, description="Invalid filename")
    
    filepath = state.DATA_DIR / filename
    if not filepath.exists() or not filepath.is_file():
        abort(404, description="File not found")
    
    return send_file(
        filepath,
        mimetype="application/json",
        as_attachment=True,
        download_name=filename
    )


@admin_api_bp.route("/api/admin/sessions/download-zip", methods=["POST"])
@admin_required
def download_zip():
    """
    Download multiple sessions as a ZIP file.
    
    Request body:
        - filenames: List of filenames to include
        - Or use filters (same as list_sessions) to auto-select
    """
    data = request.get_json(silent=True) or {}
    filenames = data.get("filenames", [])
    
    # If no filenames provided, use filters
    if not filenames:
        username_filter = data.get("username", "").strip().lower()
        training_type_filter = data.get("training_type", "").strip()
        
        files = list(state.DATA_DIR.glob("session_*.json"))
        for f in files:
            info = get_session_info(f)
            if not info:
                continue
            if username_filter and username_filter not in info["username"].lower():
                continue
            if training_type_filter and info["training_type"] != training_type_filter:
                continue
            filenames.append(info["filename"])
    
    if not filenames:
        return jsonify({"ok": False, "error": "No files selected"}), 400
    
    # Create ZIP in memory
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename in filenames:
            # Validate filename
            if not filename.endswith(".json") or "/" in filename or "\\" in filename or ".." in filename:
                continue
            filepath = state.DATA_DIR / filename
            if filepath.exists() and filepath.is_file():
                zf.write(filepath, filename)
    
    memory_file.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    return send_file(
        memory_file,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"heartbeat_sessions_{timestamp}.zip"
    )


@admin_api_bp.route("/api/admin/sessions/stats")
@admin_required
def get_stats():
    """Get overall statistics about stored sessions."""
    files = list(state.DATA_DIR.glob("session_*.json"))
    
    total_files = len(files)
    total_size = sum(f.stat().st_size for f in files)
    usernames = set()
    training_types = {}
    
    for f in files:
        info = get_session_info(f)
        if info:
            usernames.add(info["username"])
            t = info["training_type"]
            training_types[t] = training_types.get(t, 0) + 1
    
    return jsonify({
        "ok": True,
        "stats": {
            "total_sessions": total_files,
            "total_size_bytes": total_size,
            "unique_users": len(usernames),
            "training_types": training_types,
        },
    })


@admin_api_bp.route("/api/admin/sessions/view/<path:filename>")
@admin_required
def view_session(filename: str):
    """View a session JSON file (for preview)."""
    # Validate filename
    if not filename.endswith(".json") or "/" in filename or "\\" in filename or ".." in filename:
        abort(400, description="Invalid filename")
    
    filepath = state.DATA_DIR / filename
    if not filepath.exists() or not filepath.is_file():
        abort(404, description="File not found")
    
    data = load_session_data(filepath)
    if not data:
        abort(500, description="Failed to load session data")
    
    # Add metadata
    stat = filepath.stat()
    data["_admin_meta"] = {
        "filename": filename,
        "size_bytes": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "training_type": detect_training_type(data),
    }
    
    return jsonify(data)
