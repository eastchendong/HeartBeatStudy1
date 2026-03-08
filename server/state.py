"""
Shared application state for the HeartBeat Study server.

Multi-user support:
State is now encapsulated in `SessionState` objects, stored in a global `sessions` dict.
"""

import queue
import time
import subprocess
from collections import deque
from pathlib import Path
from threading import Lock
from typing import Dict, Optional, List, Deque

# ── Data directory for JSON persistence ────────────────────────────────────
DATA_DIR = Path(__file__).resolve().parent / "data" / "sessions"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── HRV adaptive constants ────────────────────────────────────────────────
HRV_WINDOW     = 8
HRV_DROP_RATIO = 0.80

class SessionState:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.lock = Lock()

        # ── Session configuration ──────────────────────────────────────────────
        self.session_config: dict = {
            "breath_cycle": 10.0,       # total cycle seconds (e.g. 10 → 5+5)
            "session_duration": 120,    # seconds (default 2 min)
            "adaptive": True,           # auto-adjust cycle with HRV
            "username": "",             # optional
        }

        # ── Core mutable state ─────────────────────────────────────────────────
        self.bpm_window: Deque[float]   = deque(maxlen=10)
        self.hrv_window: Deque[float]   = deque(maxlen=HRV_WINDOW)
        self.latest_bpm:  float         = 0.0
        self.latest_rmssd: Optional[float] = None
        self.latest_lf_power: Optional[float] = None
        self.breath_cycle: float        = 10.0
        self.subscribers: List[queue.Queue] = []

        # ── RR interval collection for the entire session ──────────────────────
        self.rr_intervals: List[float] = []      # all RR intervals in ms
        self.rr_timestamps: List[float] = []     # server receive time for each RR
        self.bpm_all: List[float] = []           # all BPM readings for max/min

        # ── Session timer state ────────────────────────────────────────────────
        self.session_start: float  = time.time()
        self.session_active: bool  = False   # True after user clicks "Start Training"
        self.paused:        bool   = False
        self.pause_time:    float  = 0.0
        self.elapsed_before_pause: float = 0.0
        
        # ── Relay subprocess state (per session? or global?) ───────────────────
        self.relay_process: Optional[subprocess.Popen] = None
        self.relay_device_keyword: Optional[str] = None


# ── Global Session Store ───────────────────────────────────────────────────
sessions_lock = Lock()
sessions: Dict[str, SessionState] = {}

def get_session(session_id: str) -> SessionState:
    """Get existing session or create a new one."""
    with sessions_lock:
        if session_id not in sessions:
            sessions[session_id] = SessionState(session_id)
        return sessions[session_id]

# Default session for backward compatibility or default access
DEFAULT_SESSION_ID = "default"
