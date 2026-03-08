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
"""

from flask import Flask, render_template
from flask_cors import CORS

from routes.setup import setup_bp
from routes.training import training_bp
from routes.results import results_bp
from routes.relay import relay_bp
from routes.buteyko import buteyko_bp

app = Flask(__name__)
CORS(app)

# Register blueprints
app.register_blueprint(setup_bp)
app.register_blueprint(training_bp)
app.register_blueprint(results_bp)
app.register_blueprint(relay_bp)
app.register_blueprint(buteyko_bp)


@app.route("/")
def index():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True, debug=False)
