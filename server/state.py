"""
Shared application state for the HeartBeat Study server.

All mutable global state lives here so that every blueprint / module
can import and mutate the same objects through a single lock.
"""

import queue
import time
from collections import deque
from pathlib import Path
from threading import Lock

# ── Data directory for JSON persistence ────────────────────────────────────
DATA_DIR = Path(__file__).resolve().parent / "data" / "sessions"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Session configuration (can be changed via /api/configure) ──────────────
config_lock = Lock()
session_config: dict = {
    "breath_cycle": 10.0,       # total cycle seconds (e.g. 10 → 5+5)
    "session_duration": 120,    # seconds (default 2 min)
    "adaptive": True,           # auto-adjust cycle with HRV
    "username": "",             # optional
}

# ── HRV adaptive constants ────────────────────────────────────────────────
HRV_WINDOW     = 8
HRV_DROP_RATIO = 0.80

# ── Core mutable state (guarded by `lock`) ─────────────────────────────────
lock = Lock()

bpm_window: deque[float]   = deque(maxlen=10)
hrv_window: deque[float]   = deque(maxlen=HRV_WINDOW)
latest_bpm:  float         = 0.0
latest_rmssd: float | None = None
latest_lf_power: float | None = None
breath_cycle: float        = 10.0
subscribers: list[queue.Queue] = []

# ── RR interval collection for the entire session ─────────────────────────
rr_intervals: list[float] = []      # all RR intervals in ms
rr_timestamps: list[float] = []     # server receive time for each RR
bpm_all: list[float] = []           # all BPM readings for max/min

# ── Session timer state ───────────────────────────────────────────────────
session_start: float  = time.time()
session_active: bool  = False   # True after user clicks "Start Training"
paused:        bool   = False
pause_time:    float  = 0.0
elapsed_before_pause: float = 0.0

# ── Relay subprocess state ────────────────────────────────────────────────
import subprocess
relay_lock = Lock()
relay_process: subprocess.Popen | None = None
relay_device_keyword: str | None = None
