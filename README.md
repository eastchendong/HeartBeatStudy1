# HeartBeat Study – Breathing Guidance System

A three-part system that reads Arduino heart-pulse sensor data and displays an **adaptive breathing guidance animation** in the browser.

## Architecture

```
Pulse Sensor → ESP32
                 ├── WiFi ──────────────── HTTP POST /api/pulse ──▶ Flask server (WSL)
                 │                                                         │
                 └── Bluetooth Classic ──▶ Windows Python script           │
                                              └── HTTP POST /api/pulse ───▶┘
                                                                           │
                                                                     SSE /api/stream
                                                                           │
                                                                    Browser (breathing guide)
```

**Both channels post to the same `/api/pulse` endpoint.** The server deduplicates via its rolling BPM window — extra readings just improve the average.

---

## Project structure

```
HeartBeatStudy1/
├── server/
│   ├── app.py               Flask server (runs in WSL)
│   ├── requirements.txt
│   └── templates/
│       └── index.html       Breathing-guidance frontend
├── windows/
│   ├── pulse_sender.py      Windows Python script (reads COM port → POSTs to server)
│   └── requirements.txt
└── arduino/
    └── pulse_sensor.ino     Arduino sketch
```

---

## 1 · ESP32 setup

### Arduino IDE configuration
1. Add ESP32 board support: **File → Preferences → Additional Boards Manager URLs**  
   add `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
2. **Tools → Board → Boards Manager** → search "esp32" → install **esp32 by Espressif Systems**
3. Select board: **Tools → Board → ESP32 Arduino → ESP32 Dev Module**

### Edit config at the top of `arduino/pulse_sensor_esp32.ino`
```cpp
const char* WIFI_SSID  = "YOUR_WIFI_SSID";
const char* WIFI_PASS  = "YOUR_WIFI_PASSWORD";
const char* SERVER_URL = "http://172.30.182.116:5000/api/pulse";  // WSL IP
const char* BT_NAME    = "HeartBeat-ESP32";   // Bluetooth device name
```

### Wiring (Pulse Sensor Amped)
| Sensor pin | ESP32 pin |
|------------|-----------|
| S (signal) | GPIO 34   |
| + (VCC)    | 3.3 V     |
| − (GND)    | GND       |

> ⚠️ Use **3.3 V** for VCC — GPIO 34 is a 3.3 V input-only pin.

### What the ESP32 transmits (every second)
- **WiFi:** HTTP POST `{"bpm": 72}` directly to Flask — no PC needed
- **Bluetooth Classic:** prints `BPM:72` over a virtual serial port — Windows Python reads it

---

## 2 · Flask server (WSL / Linux)

```bash
cd server
pip install -r requirements.txt
python app.py
```

The server listens on **all interfaces at port 5000**.  
Find your WSL IP with:

```bash
hostname -I   # e.g. 172.28.x.x
```

Open `http://localhost:5000` (or the WSL IP from Windows) to see the breathing guide.

---

## 3 · Windows Python script (Bluetooth path)

> This is the **Bluetooth fallback/parallel path**. The ESP32 already POSTs over WiFi directly — you only need this if WiFi is unavailable or you want both channels active.

### Pair the ESP32 first
1. ESP32 must be powered and running the sketch
2. Windows **Settings → Bluetooth → Add device** → find `HeartBeat-ESP32` → pair (no PIN)
3. Find the COM port: **Settings → Bluetooth → More Bluetooth settings → COM Ports tab**  
   Note the **Outgoing** port for `HeartBeat-ESP32` (e.g. `COM5`)

```powershell
cd windows
pip install -r requirements.txt
python pulse_sender.py --port COM5 --server http://172.30.182.116:5000
```

---

## Adaptive breathing logic

| Heart rate (BPM) | Breathing cycle | Rationale |
|-----------------|-----------------|-----------|
| ≤ 60 | 5 s | Already calm / bradycardic |
| 60 – 90 | 5 – 6 s | Normal resting range |
| 90 – 120 | 6 – 8 s | Elevated – slow down |
| ≥ 120 | 8 – 10 s | High stress – extended exhale |

Within each cycle the split is **40 % inhale · 10 % hold · 50 % exhale**.  
The longer exhale activates the parasympathetic nervous system (vagal brake).

Cycles update smoothly at each cycle boundary (no abrupt restarts mid-breath).

---

## Testing without hardware

You can simulate sensor data with `curl`:

```bash
# From WSL or any machine on the network
curl -X POST http://localhost:5000/api/pulse \
     -H "Content-Type: application/json" \
     -d '{"bpm": 95}'
```

Or send a burst to watch the animation adapt:

```bash
for bpm in 65 68 72 80 90 105 112 118 108 95 82; do
  curl -s -X POST http://localhost:5000/api/pulse \
       -H "Content-Type: application/json" \
       -d "{\"bpm\": $bpm}" > /dev/null
  sleep 2
done
```
