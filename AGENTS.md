# AGENTS.md – HeartBeat Study

> Agent-focused documentation for the HeartBeat Study breathing guidance system.

---

## Project Overview

A Flask-based breathing guidance system that reads heart-rate data from multiple sources (ESP32 pulse sensor, BLE heart belt) and displays an adaptive breathing animation in the browser. The system supports multi-user sessions, HRV-based adaptive breathing cycles, and real-time SSE streaming.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLIENT SIDE                                    │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────────────────────┐│
│  │  ESP32       │  │  BLE Heart   │  │  Browser (Web Bluetooth)            ││
│  │  Pulse Sensor│  │  Belt        │  │  - Web Bluetooth API                ││
│  │  - WiFi POST │  │  - BLE GATT  │  │  - SSE /api/stream                  ││
│  │  - Bluetooth │  │    Notify    │  │  - Breathing animation UI           ││
│  └──────┬───────┘  └──────┬───────┘  └─────────────┬───────────────────────┘│
└─────────┼─────────────────┼────────────────────────┼────────────────────────┘
          │                 │                        │
          │ HTTP POST       │ HTTP POST (via relay)  │ HTTP POST (direct)
          ▼                 ▼                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           FLASK SERVER (Port 5000)                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │  /api/pulse │  │ /api/stream │  │ /api/config │  │ /api/buteyko/*      │ │
│  │  (POST BPM) │  │ (SSE)       │  │ (setup)     │  │ (breath holding)    │ │
│  └──────┬──────┘  └──────┬──────┘  └─────────────┘  └─────────────────────┘ │
│         │                │                                                   │
│         ▼                ▼                                                   │
│  ┌────────────────────────────────────┐    ┌────────────────────────────┐   │
│  │  SessionState (per session_id)     │    │  HRV Analysis (hrv.py)     │   │
│  │  - bpm_window (rolling)            │    │  - RMSSD calculation       │   │
│  │  - hrv_window (8 samples)          │    │  - LF power estimation     │   │
│  │  - rr_intervals (full session)     │    │  - Adaptive cycle adjust   │   │
│  │  - subscribers (SSE queues)        │    └────────────────────────────┘   │
│  └────────────────────────────────────┘                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **Input Sources**: ESP32 (WiFi/Bluetooth), BLE heart belt (browser Web Bluetooth or Python relay)
2. **Unified Endpoint**: All sources POST to `/api/pulse` with `{"bpm": N, "rr_intervals": [...]}`
3. **Processing**: Server deduplicates via rolling BPM window, calculates HRV metrics
4. **Output**: SSE stream pushes updates to all connected browsers

---

## Directory Structure

```
HeartBeatStudy1/
├── server/                     # Flask application (main server)
│   ├── app.py                 # Entry point, blueprint registration
│   ├── state.py               # SessionState class, global session store
│   ├── hrv.py                 # HRV calculations (RMSSD, LF power, adaptive cycles)
│   ├── relay_helpers.py       # BLE relay subprocess management
│   ├── requirements.txt       # Python deps (flask, flask-cors, numpy, bleak)
│   ├── routes/                # Blueprint modules (scene-based)
│   │   ├── setup.py           # Scene: start (config, get config)
│   │   ├── training.py        # Scene: training (pulse, stream, start, pause, play, status)
│   │   ├── results.py         # Scene: results (stop, reset, save_session, sessions)
│   │   ├── relay.py           # BLE relay management (search, connect, disconnect)
│   │   ├── buteyko.py         # Buteyko breathing (breath hold protocol)
│   │   ├── auth.py            # Admin authentication (login/logout)
│   │   ├── admin_api.py       # Admin API for session data management
│   │   └── utils.py           # Shared route utilities
│   ├── templates/
│   │   ├── index.html         # Single-page breathing guidance UI
│   │   ├── login.html         # Admin login page
│   │   └── admin.html         # Admin dashboard for session management
│   └── data/sessions/         # JSON session persistence (gitignored)
│
├── arduino/
│   └── pulse_sensor_esp32.ino # ESP32 sketch (WiFi + Bluetooth)
│
├── windows/
│   ├── pulse_sender.py        # Windows COM port → HTTP relay (legacy)
│   └── pulse_sender_BLE.py    # Windows BLE relay (unused?)
│
├── ble-relay-server-python/   # Standalone Python BLE relay (fallback)
│   ├── main.py                # Entry point
│   ├── bluetooth.py           # BLE GATT client
│   ├── far_ferry.py           # Protocol decoder (心电带)
│   └── crc.py                 # CRC validation
│
├── deploy.sh                  # Full deployment (nginx, SSL, systemd)
├── deploy_update.sh           # Lightweight update (rsync + restart)
└── README.md                  # Human documentation
```

---

## Coding Conventions

### Python (Server)

- **Style**: PEP 8, 4-space indentation
- **Type hints**: Use `typing` module for function signatures
- **Docstrings**: Google-style docstrings for modules and functions
- **Blueprints**: Each scene has its own blueprint in `routes/`
- **State access**: Always use `state.get_session(session_id)` to retrieve session
- **Thread safety**: Use `session.lock` when mutating shared state

```python
# Example: Adding a new route
def get_session_or_404(session_id: str) -> SessionState:
    """Get session or return 404."""
    sess = sessions.get(session_id)
    if not sess:
        abort(404, description="Session not found")
    return sess

@training_bp.route("/api/pulse", methods=["POST"])
def pulse():
    data = request.get_json(force=True, silent=True) or {}
    bpm = data.get("bpm")
    rr_intervals = data.get("rr_intervals", [])
    session_id = data.get("session_id", DEFAULT_SESSION_ID)
    
    sess = state.get_session(session_id)
    with sess.lock:
        sess.bpm_window.append(bpm)
        sess.rr_intervals.extend(rr_intervals)
        sess.latest_bpm = bpm
    
    _broadcast_to_subscribers(sess)
    return jsonify({"ok": True, "bpm": bpm})
```

### JavaScript (Frontend)

- **ES6+**: Use modern JavaScript (arrow functions, async/await, destructuring)
- **EventSource**: Use native `EventSource` for SSE, with reconnection logic
- **Web Bluetooth**: Feature-detect before using, handle permissions properly

### Arduino

- **Naming**: snake_case for variables, PascalCase for classes
- **Config**: Hardcoded constants at top of file (WIFI_SSID, SERVER_URL)
- **Serial**: Use `Serial.println()` for debug output

---

## Development Workflow

### Local Development (WSL/Linux)

```bash
cd server
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
# Server runs at http://localhost:5000
```

### Testing Without Hardware

```bash
# Simulate pulse data
curl -X POST http://localhost:5000/api/pulse \
     -H "Content-Type: application/json" \
     -d '{"bpm": 75, "session_id": "test"}'

# Simulate with RR intervals
curl -X POST http://localhost:5000/api/pulse \
     -H "Content-Type: application/json" \
     -d '{"bpm": 72, "rr_intervals": [833, 820, 838], "session_id": "test"}'
```

### Testing Session Flow

```bash
# 1. Configure session
curl -X POST http://localhost:5000/api/config \
     -H "Content-Type: application/json" \
     -d '{"session_id": "test", "breath_cycle": 10, "session_duration": 120}'

# 2. Start training
curl -X POST http://localhost:5000/api/start \
     -H "Content-Type: application/json" \
     -d '{"session_id": "test"}'

# 3. Send pulse data (while SSE client is connected)
curl -X POST http://localhost:5000/api/pulse \
     -d '{"bpm": 70, "session_id": "test"}'

# 4. Stop and save
curl -X POST http://localhost:5000/api/stop \
     -d '{"session_id": "test"}'
```

---

## Key Concepts

### Session State Model

Sessions are identified by `session_id` (default: `"default"`). Each session has:

| Field | Type | Description |
|-------|------|-------------|
| `session_config` | dict | User-configurable params (breath_cycle, duration, etc.) |
| `bpm_window` | deque[maxlen=10] | Rolling window for current BPM average |
| `hrv_window` | deque[maxlen=8] | Rolling window for HRV calculations |
| `rr_intervals` | list | All RR intervals for the session (ms) |
| `subscribers` | list[Queue] | SSE subscriber queues for this session |
| `session_active` | bool | Whether training is currently running |
| `paused` | bool | Whether training is paused |

### Adaptive Breathing Logic

Located in `hrv.py`. The breathing cycle adapts based on HRV metrics:

```python
# hrv.py
HRV_WINDOW = 8          # Number of RMSSD samples
HRV_DROP_RATIO = 0.80   # Trigger threshold (cycle extends if RMSSD drops below 80% of mean)

# Cycle ranges by heart rate:
# BPM ≤ 60    → 5s cycle
# 60 < BPM ≤ 90 → 5-6s cycle
# 90 < BPM ≤ 120 → 6-8s cycle
# BPM > 120   → 8-10s cycle

# Split: 40% inhale / 10% hold / 50% exhale
```

### HRV Calculations

- **RMSSD**: Root mean square of successive RR differences
- **LF Power**: Low frequency (0.04-0.15 Hz) power from RR interval spectrum
- Both are calculated on the rolling `hrv_window` (last 8 samples)

---

## Deployment

### Full Deploy (new server)

```bash
# Set required env vars for SSL certificate
export CLOUDFLARE_EMAIL=your@email.com
export CLOUDFLARE_API_KEY=your_cf_api_key

./deploy.sh
```

This sets up:
- Python venv with dependencies
- systemd service (`heartbeat`)
- nginx reverse proxy with HTTPS
- Let's Encrypt certificate (via Cloudflare DNS-01)

### Update Deploy (code changes only)

```bash
./deploy_update.sh
```

This rsyncs `server/` to remote and restarts the service.

### Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `REMOTE_HOST` | Deployment target | `47.100.80.20` |
| `REMOTE_USER` | SSH user | `root` |
| `REMOTE_PASS` | SSH password | `REMOTE_PASS_PLACEHOLDER` |
| `APP_DIR` | Remote app path | `/opt/heartbeat` |
| `SERVICE_NAME` | systemd service name | `heartbeat` |
| `HTTPS_HOSTNAME` | Domain for SSL | `live.tongjicdi.com` |
| `HEARTBEAT_SHARED_SESSION_DIR` | Optional mirror for JSON files | (unset) |

---

## Session Data Format

Saved sessions are JSON files in `server/data/sessions/`:

```json
{
  "session_id": "default",
  "start_time": 1700000000.0,
  "end_time": 1700000120.0,
  "duration_seconds": 120,
  "config": {
    "breath_cycle": 10.0,
    "session_duration": 120,
    "adaptive": true
  },
  "bpm_stats": {
    "min": 65,
    "max": 85,
    "avg": 74.5
  },
  "hrv_stats": {
    "rmssd_avg": 45.2,
    "lf_power_avg": 1250.0
  },
  "rr_intervals": [833, 820, 838, ...],
  "rr_timestamps": [1700000000.1, 1700000001.0, ...],
  "bpm_readings": [72, 73, 71, ...]
}
```

---

## Admin Interface

A web-based admin dashboard is available at `/admin` for managing session data. Access requires authentication.

### Access

1. Navigate to `/admin/login`
2. Login with admin credentials (configured via environment variables)
3. After successful login, access the dashboard at `/admin`

### Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `HEARTBEAT_ADMIN_USER` | Admin username | `admin` |
| `HEARTBEAT_ADMIN_PASS` | Admin password | `admin` |
| `HEARTBEAT_SECRET_KEY` | Flask session secret key | Random 32 bytes |

### Features

The admin dashboard provides:

- **Statistics Overview**: Total sessions, unique users, data storage size
- **Search**: Filter sessions by username (partial match)
- **Filter by Type**: Buteyko, PRBF, Resonance breathing types
- **Sort**: By date, username, training type, BPM avg, HRV RMSSD
- **Individual Actions**:
  - View JSON data (preview modal)
  - Download single JSON file
- **Bulk Actions**:
  - Select multiple sessions via checkboxes
  - Download selected as ZIP
  - Download all filtered results as ZIP

### Admin API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin/login` | GET/POST | Login page and authentication |
| `/admin/logout` | POST/GET | Logout and clear session |
| `/admin/check` | GET | Check authentication status |
| `/api/admin/sessions` | GET | List/filter/sort sessions |
| `/api/admin/sessions/stats` | GET | Get overall statistics |
| `/api/admin/sessions/view/<filename>` | GET | Preview session JSON |
| `/api/admin/sessions/download/<filename>` | GET | Download single JSON |
| `/api/admin/sessions/download-zip` | POST | Bulk download as ZIP |

---

## Common Tasks

### Adding a New API Endpoint

1. Identify the scene (setup/training/results/relay/buteyko)
2. Add route to appropriate file in `server/routes/`
3. Use `state.get_session(session_id)` to access session
4. Update this AGENTS.md if the endpoint changes state model

### CDI PRBF Test Page

A dedicated page at `/cdi-prbf` for CDI research group's Personalized Resonance Frequency Breathing test:

- **Protocol**: 10 frequency stages from 8.0 to 10.25 bpm (0.25 increment)
- **Cycles**: 3 breathing cycles per frequency
- **Metrics**: Records LF Power and RMSSD for each frequency
- **Result**: Selects frequency with highest LF Power as PRBF

**Files**:
- `server/routes/cdi_prbf.py` - API routes
- `server/templates/cdi_prbf.html` - Page template

**API Endpoints**:
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/cdi-prbf` | GET | Page |
| `/api/cdi-prbf/configure` | POST | Configure test |
| `/api/cdi-prbf/start` | POST | Start test |
| `/api/cdi-prbf/on-cycle-complete` | POST | Called when each cycle completes |
| `/api/cdi-prbf/status` | GET | Get current status |
| `/api/cdi-prbf/stop` | POST | Stop test early |
| `/api/cdi-prbf/reset` | POST | Reset test state |
| `/api/cdi-prbf/results` | GET | Get test results |
| `/api/cdi-prbf/save` | POST | Save results to file |

### Adding a New State Field

1. Add field to `SessionState.__init__()` in `state.py`
2. Initialize with appropriate default
3. Update session serialization if needed (in `results.py`)

### Debugging SSE Issues

- Check browser console for EventSource errors
- Verify `session_id` matches between POST and SSE connection
- Server logs: `journalctl -u heartbeat -f` (on remote)
- Local logs: Flask debug output

### BLE Relay Issues

- Python relay uses `bleak` library (BLE GATT client)
- Default service UUID: `0000ffe0-0000-1000-8000-00805f9b34fb`
- Check `relay.py` and `relay_helpers.py` for subprocess management

---

## Security Notes

- HTTPS is enforced in production (nginx redirect)
- Web Bluetooth requires HTTPS or localhost
- Session IDs are simple strings—no auth for regular users
- **Admin interface** uses Flask sessions for authentication; always use HTTPS in production
- Change default admin credentials via environment variables (`HEARTBEAT_ADMIN_USER`, `HEARTBEAT_ADMIN_PASS`)
- Set a persistent `HEARTBEAT_SECRET_KEY` for production (otherwise sessions are invalidated on restart)
- BLE relay subprocess has access to system Bluetooth

---

## Related Documentation

- `README.md` – User-facing setup and usage guide
- `server/hrv.py` – HRV algorithm implementations
- `server/state.py` – State model documentation in docstrings
