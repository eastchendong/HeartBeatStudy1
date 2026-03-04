/*
 * HeartBeat Study – ESP32 Pulse Sensor Sketch
 * Transmits BPM simultaneously over:
 *   • WiFi  → HTTP POST to Flask server (direct, no PC needed)
 *   • Bluetooth Classic Serial → Windows PC → Flask server (fallback / parallel)
 *
 * Board: ESP32 Dev Module (select in Arduino IDE: Tools → Board → ESP32 Arduino)
 * Required libraries (install via Library Manager):
 *   – "ESP32" board support package (by Espressif)
 *   – BluetoothSerial  (bundled with ESP32 board package)
 *   – HTTPClient       (bundled with ESP32 board package)
 *
 * Wiring (Pulse Sensor Amped or generic analog pulse sensor):
 *   Sensor S (signal) → GPIO 34   (ADC1_CH6 – input only, no DAC conflict)
 *   Sensor + (VCC)    → 3.3 V
 *   Sensor – (GND)    → GND
 *   Built-in LED      → GPIO 2    (blinks on each detected beat)
 *
 * ── Configuration ─────────────────────────────────────────────────────────
 * Edit the four constants below, then upload.
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// ── USER CONFIG ────────────────────────────────────────────────────────────
const char* WIFI_SSID   = "ChenDong";
const char* WIFI_PASS   = "123456cd";
// Flask server URL – use WSL IP (run `hostname -I` in WSL to find it)
const char* SERVER_URL  = "http://172.30.182.116:5000/api/pulse";
const char* BT_NAME     = "HeartBeat-ESP32";   // Bluetooth device name
// ── END CONFIG ─────────────────────────────────────────────────────────────

// ── Pin assignments ────────────────────────────────────────────────────────
const int PULSE_PIN = 4;    // ADC input (3.3 V max – do NOT use 5 V sensor VCC) - Changed for ESP32-S3
const int LED_PIN   = 2;    // Built-in LED on most ESP32 dev boards

// ── Peak detection ─────────────────────────────────────────────────────────
// ESP32 ADC is 12-bit (0–4095). Midpoint ≈ 2048.
const int THRESHOLD = 2200; // Raised slightly to avoid noise floor

// ── Beat Event Logic ───────────────────────────────────────────────────────
unsigned long lastBeatTime  = 0;
bool          aboveThreshold = false;

// BLE Globals
BLEServer* pServer = NULL;
BLECharacteristic* pCharacteristic = NULL;
bool deviceConnected = false;
bool oldDeviceConnected = false;

// See the following for generating UUIDs:
// https://www.uuidgenerator.net/
#define SERVICE_UUID        "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
#define CHARACTERISTIC_UUID "beb5483e-36e1-4688-b7f5-ea07361b26a8"

class MyServerCallbacks: public BLEServerCallbacks {
    void onConnect(BLEServer* pServer) {
      deviceConnected = true;
    };

    void onDisconnect(BLEServer* pServer) {
      deviceConnected = false;
    }
};

// ── WiFi helpers ───────────────────────────────────────────────────────────
void connectWiFi() {
  Serial.printf("[WiFi] Connecting to %s ", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n[WiFi] Connected – IP: %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println("\n[WiFi] Failed – will retry later. BT still active.");
  }
}

bool postBpmWiFi(int bpm) {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();                          // attempt reconnect
    if (WiFi.status() != WL_CONNECTED) return false;
  }
  HTTPClient http;
  http.begin(SERVER_URL);
  http.addHeader("Content-Type", "application/json");
  String body = "{\"bpm\":" + String(bpm) + "}";
  int code = http.POST(body);
  http.end();
  return (code == 200);
}

// ── Detect Beat ────────────────────────────────────────────────────────
// Only triggers once per rising edge, with debouncing
void detectBeat() {
  int signal = analogRead(PULSE_PIN);
  unsigned long now = millis();

  // Basic threshold crossing + time gate (min 250ms = max 240 BPM)
  if (signal > THRESHOLD && !aboveThreshold) {
    if ((now - lastBeatTime) > 250) { 
        aboveThreshold = true;
        digitalWrite(LED_PIN, HIGH);
        
        lastBeatTime = now;
        
        // Send "BEAT" notification immediately via BLE
        if (deviceConnected) {
            std::string msg = "BEAT";
            pCharacteristic->setValue(msg);
            pCharacteristic->notify();
            Serial.println("[BLE] Sent: BEAT");
        }
    }
  }

  if (signal < THRESHOLD && aboveThreshold) {
    aboveThreshold = false;
    digitalWrite(LED_PIN, LOW);
  }
}

// ── Setup ──────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);

  // Start BLE
  Serial.println("[BLE] Starting BLE...");
  BLEDevice::init(BT_NAME);
  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new MyServerCallbacks());

  BLEService *pService = pServer->createService(SERVICE_UUID);

  pCharacteristic = pService->createCharacteristic(
                      CHARACTERISTIC_UUID,
                      BLECharacteristic::PROPERTY_READ   |
                      BLECharacteristic::PROPERTY_WRITE  |
                      BLECharacteristic::PROPERTY_NOTIFY |
                      BLECharacteristic::PROPERTY_INDICATE
                    );

  pCharacteristic->addDescriptor(new BLE2902());

  pService->start();

  BLEAdvertising *pAdvertising = BLEDevice::getAdvertising();
  pAdvertising->addServiceUUID(SERVICE_UUID);
  pAdvertising->setScanResponse(false);
  pAdvertising->setMinPreferred(0x0);  // set value to 0x00 to not advertise this parameter
  BLEDevice::startAdvertising();
  Serial.printf("[BLE] Device ready – scan for \"%s\"\n", BT_NAME);

  // Start WiFi (non-blocking – BT works even if WiFi fails)
  connectWiFi();
}

// ── Main loop ──────────────────────────────────────────────────────────────
void loop() {
  detectBeat();

  // BLE Management
  if (!deviceConnected && oldDeviceConnected) {
      delay(500); 
      pServer->startAdvertising(); 
      Serial.println("[BLE] Restarting advertising...");
      oldDeviceConnected = deviceConnected;
  }
  
  if (deviceConnected && !oldDeviceConnected) {
      oldDeviceConnected = deviceConnected;
  }
  // Short delay for stability
  delay(10); 
}
