"""
HeartBeat Study – Windows BLE pulse sender
Scans for ESP32 over BLE, reads BPM, and POSTs to the Flask server.

Usage:
    python pulse_sender_ble.py --server http://<WSL-IP>:5000

Dependencies:
    pip install bleak requests
"""
import argparse
import asyncio
import math
import sys
import time
import requests
from bleak import BleakScanner, BleakClient

# ── CONFIG ──────────────────────────────────────────────────────────────────
DEVICE_NAME = "HeartBeat-ESP32"
CHARACTERISTIC_UUID = "beb5483e-36e1-4688-b7f5-ea07361b26a8"

# ── BPM / HRV Calculation ───────────────────────────────────────────────────
last_beat_time = 0.0
ibi_buffer = []          # Inter-Beat Intervals in seconds (recent beats)
IBI_BUFFER_SIZE = 10     # Keep last 10 IBIs (~10 beats) for RMSSD calculation

# ── CLI args ────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Read Arduino BEAT events via BLE and calc BPM/HRV")
parser.add_argument("--server", default="http://127.0.0.1:5000", help="Server base URL")
parser.add_argument("--interval", default=1.0, type=float,       help="Min seconds between POSTs")
args = parser.parse_args()

POST_URL = args.server.rstrip("/") + "/api/pulse"
last_post_time = 0.0

def compute_rmssd(ibis):
    """Root Mean Square of Successive Differences (ms). Needs ≥2 IBIs."""
    if len(ibis) < 2:
        return None
    diffs = [(ibis[i] - ibis[i-1]) * 1000 for i in range(1, len(ibis))]  # convert to ms
    return math.sqrt(sum(d * d for d in diffs) / len(diffs))

def handle_notification(sender, data):
    global last_post_time, last_beat_time, ibi_buffer
    try:
        text = data.decode("utf-8").strip()

        if text == "BEAT":
            now = time.time()
            if last_beat_time > 0:
                ibi = now - last_beat_time
                # Filter unreasonable beats (30–240 BPM range: 0.25s–2.0s)
                if 0.25 < ibi < 2.0:
                    ibi_buffer.append(ibi)
                    if len(ibi_buffer) > IBI_BUFFER_SIZE:
                        ibi_buffer.pop(0)

                    avg_ibi = sum(ibi_buffer) / len(ibi_buffer)
                    bpm = int(60.0 / avg_ibi)
                    rmssd = compute_rmssd(ibi_buffer)

                    if rmssd is not None:
                        print(f"[BLE] BEAT! IBI={ibi:.3f}s  BPM={bpm}  RMSSD={rmssd:.1f}ms")
                    else:
                        print(f"[BLE] BEAT! IBI={ibi:.3f}s  BPM={bpm}  RMSSD=--")

                    # Anti-spam for POST requests
                    if (now - last_post_time) >= args.interval:
                        try:
                            payload = {"bpm": bpm}
                            if rmssd is not None:
                                payload["hrv_rmssd"] = round(rmssd, 2)
                            requests.post(POST_URL, json=payload, timeout=1)
                            last_post_time = now
                        except Exception:
                            pass  # Silently ignore network errors to keep loop tight

            last_beat_time = now

    except Exception as e:
        print(f"Error: {e}")

async def main():
    print(f"Scanning for device '{DEVICE_NAME}'...")
    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: d.name == DEVICE_NAME or (ad.local_name == DEVICE_NAME)
    )

    if not device:
        print(f"Device '{DEVICE_NAME}' not found.")
        return

    print(f"Found {device.name}, connecting...")
    
    async with BleakClient(device) as client:
        print(f"Connected: {client.is_connected}")
        
        await client.start_notify(CHARACTERISTIC_UUID, handle_notification)
        print("Listening for BEAT events... Press Ctrl-C to stop.")
        
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            print("Stopping...")
        except KeyboardInterrupt:
            print("Stopping...")
        finally:
            await client.stop_notify(CHARACTERISTIC_UUID)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAborted by user.")
        sys.exit(0)
