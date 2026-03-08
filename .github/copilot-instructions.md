# HeartBeat Study 1 - Copilot Instructions

This repository contains a multi-component system for adaptive breathing guidance based on heart rate data. It involves an Arduino/ESP32 sensor, a Flask server, and Python-based relays for Bluetooth/BLE connectivity.

## High-Level Architecture

The system aggregates heart rate data from multiple sources into a single Flask server, which drives a browser-based breathing animation.

1.  **Central Server (Flask)**:
    *   Endpoint: `POST /api/pulse` (Receives JSON: `{"bpm": 72}`)
    *   Endpoint: `GET /api/stream` (SSE stream for frontend)
    *   Logic: Maintains a rolling window of BPM readings to smooth data from multiple sources.

2.  **Data Sources**:
    *   **ESP32 (WiFi)**: Posts directly to the Flask server.
    *   **ESP32 (Bluetooth Classic)**: Sends serial data to a Windows Python script (`windows/pulse_sender.py`), which forwards it to the server via HTTP.
    *   **BLE Device (e.g., ECG Belt)**: Connected via `ble-relay-server-python/main.py`. This script acts as a bridge, decoding custom Protobuf-over-BLE protocols, relaying data to Unity (TCP 65432), and forwarding heart rates to the Flask server (HTTP).

## Project Components & Commands

### 1. Flask Server (`server/`)
The core application running on WSL/Linux.

*   **Install**: `pip install -r requirements.txt`
*   **Run**: `python app.py`
*   **Port**: 5000 (Host: 0.0.0.0)
*   **Key Files**:
    *   `app.py`: Entry point, blueprint registration.
    *   `routes/training.py`: Handles pulse ingestion and SSE streaming.
    *   `hrv.py`: HRV calculation logic.

### 2. Windows Relay (`windows/`)
Bridges Bluetooth Classic (COM port) to HTTP.

*   **Install**: `pip install -r requirements.txt`
*   **Run**: `python pulse_sender.py --port COMx --server http://<WSL-IP>:5000`

### 3. BLE Relay (`ble-relay-server-python/`)
Bridges BLE devices to Unity (TCP) and Flask (HTTP).

*   **Install**: `pip install -r requirements.txt`
*   **Run**: `python main.py`
*   **Key Dependencies**: `bleak` (async Bluetooth), pure Python Protobuf decoding (manual implementation in `far_ferry.py`).

### 4. Arduino (`arduino/`)
Firmware for ESP32.

*   **File**: `pulse_sensor_esp32.ino`
*   **Config**: Update `WIFI_SSID`, `WIFI_PASS`, and `SERVER_URL` at the top of the file.

## Key Conventions

*   **Networking**: The server often runs in WSL while relays run in Windows. Always verify IP connectivity (`hostname -I` in WSL).
*   **Data Simulation**:
    *   Use `curl` to simulate pulse events: `curl -X POST http://localhost:5000/api/pulse -H "Content-Type: application/json" -d '{"bpm": 80}'`
*   **BLE Handling**:
    *   The BLE relay artificially delays batched data (`REPLAY_DELAY_SECONDS = 5.0`) to simulate a smooth real-time stream when forwarding to the web server.
    *   It uses a custom "FarFerry" protocol; changes to decoding logic should be made in `far_ferry.py`, matching the logic in the WeChat mini-program (`utils/FarFerry.js`).
*   **Frontend**:
    *   Simple HTML/JS in `server/templates/index.html`.
    *   Connects to `/api/stream` EventSource for real-time updates.

## Testing

*   **Manual**: Use the `curl` commands in `README.md` to trigger breathing state changes (e.g., bradycardia < 60, stress > 120).
*   **No Automated Tests**: There is currently no automated test suite. Validation is done by observing the browser animation in response to data injection.
