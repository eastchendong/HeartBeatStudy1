"""
HeartBeat Study – Flask server (entry point)

Registers scene-based blueprints and serves the frontend.
All shared state lives in state.py; HRV maths in hrv.py;
relay subprocess helpers in relay_helpers.py.

Blueprints
  routes/setup.py      – start-scene  (configure, get config)
  routes/training.py   – training-scene (pulse, stream, start, pause, play, status)
  routes/results.py    – results-scene  (stop, reset, save_session, sessions)
  routes/relay.py      – BLE relay management (search, connect, disconnect, status)
  routes/auth.py       – Admin authentication (login, logout)
  routes/admin_api.py  – Admin API for session management
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, render_template
from flask_cors import CORS

# Load .env file from project root
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

from routes.setup import setup_bp
from routes.training import training_bp
from routes.results import results_bp
from routes.relay import relay_bp
from routes.buteyko import buteyko_bp
from routes.auth import auth_bp
from routes.admin_api import admin_api_bp
from routes.cdi_prbf import cdi_prbf_bp

app = Flask(__name__)

# Configure secret key for sessions (required for admin auth)
app.secret_key = os.environ.get("HEARTBEAT_SECRET_KEY", os.urandom(32))

CORS(app)

# Register blueprints
app.register_blueprint(setup_bp)
app.register_blueprint(training_bp)
app.register_blueprint(results_bp)
app.register_blueprint(relay_bp)
app.register_blueprint(buteyko_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(admin_api_bp)
app.register_blueprint(cdi_prbf_bp)


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True, debug=False)
