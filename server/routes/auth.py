"""
Authentication routes for admin access.

Provides login/logout functionality with session-based auth.
Admin credentials are configured via environment variables.
"""

import os
from functools import wraps
from flask import Blueprint, request, jsonify, session, render_template, redirect, url_for, current_app

auth_bp = Blueprint("auth", __name__)

# Admin credentials from environment variables
ADMIN_USERNAME = os.environ.get("HEARTBEAT_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("HEARTBEAT_ADMIN_PASS", "admin")


def admin_required(f):
    """Decorator to require admin login for a route."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("is_admin"):
            if request.is_json or request.headers.get("Accept", "").startswith("application/json"):
                return jsonify({"ok": False, "error": "Unauthorized"}), 401
            return redirect(url_for("auth.login_page"))
        return f(*args, **kwargs)
    return decorated_function


@auth_bp.route("/admin/login", methods=["GET"])
def login_page():
    """Render login page."""
    if session.get("is_admin"):
        return redirect(url_for("auth.admin_dashboard"))
    return render_template("login.html")


@auth_bp.route("/admin/login", methods=["POST"])
def login():
    """Handle login form submission."""
    data = request.get_json(silent=True) or request.form
    username = data.get("username", "").strip()
    password = data.get("password", "")
    
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        session["is_admin"] = True
        session["username"] = username
        if request.is_json:
            return jsonify({"ok": True, "redirect": url_for("auth.admin_dashboard")})
        return redirect(url_for("auth.admin_dashboard"))
    
    if request.is_json:
        return jsonify({"ok": False, "error": "Invalid credentials"}), 401
    return render_template("login.html", error="Invalid username or password"), 401


@auth_bp.route("/admin/logout", methods=["POST", "GET"])
def logout():
    """Logout admin user."""
    session.clear()
    if request.is_json:
        return jsonify({"ok": True})
    return redirect(url_for("auth.login_page"))


@auth_bp.route("/admin")
@admin_required
def admin_dashboard():
    """Render admin dashboard."""
    return render_template("admin.html")


@auth_bp.route("/admin/check")
def check_auth():
    """Check if user is authenticated (for frontend)."""
    return jsonify({"ok": True, "authenticated": session.get("is_admin", False)})
