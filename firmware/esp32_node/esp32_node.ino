/*
 * ProofOfAir ESP32 node — signs every reading ON THE DEVICE with Ed25519.
 *
 * Sensors (pick what you have):
 *   - SDS011 particulate sensor on UART2 (RX=16, TX=17)       -> pm25, pm10
 *   - BME280 on I2C (SDA=21, SCL=22), set USE_BME280 1        -> temperature, humidity
 *   - nothing attached: a potentiometer on GPIO34 stands in for PM2.5
 *
 * Libraries: "Crypto" by Rhys Weatherley (Ed25519.h), Adafruit BME280 (optional).
 * The canonical JSON below MUST byte-match proofofair/canonical.py (schema v1).
 */
#include <WiFi.h>
#include <HTTPClient.h>
#include <time.h>
#include <Ed25519.h>
#include "secrets.h"

#define USE_SDS011 1
#define USE_BME280 0
#define POT_PIN 34
#define SEND_EVERY_MS 60000UL

#if USE_BME280
#include <Adafruit_BME280.h>
Adafruit_BME280 bme;
#endif

uint8_t publicKey[32];

struct Reading {
  bool hasPm = false, hasEnv = false;
  float pm25 = 0, pm10 = 0, temperature = 0, humidity = 0;
};

// ---------------------------------------------------------------- SDS011
#if USE_SDS011
// Frame: AA C0 PM25_L PM25_H PM10_L PM10_H ID1 ID2 CHECKSUM AB
bool readSds011(float &pm25, float &pm10, uint32_t timeoutMs = 3000) {
  uint8_t buf[10];
  uint32_t start = millis();
  while (millis() - start < timeoutMs) {
    if (Serial2.available() && Serial2.read() == 0xAA) {
      if (Serial2.readBytes(buf + 1, 9) != 9) continue;
      if (buf[1] != 0xC0 || buf[9] != 0xAB) continue;
      uint8_t sum = 0;
      for (int i = 2; i < 8; i++) sum += buf[i];
      if (sum != buf[8]) continue;
      pm25 = (buf[3] * 256 + buf[2]) / 10.0f;
      pm10 = (buf[5] * 256 + buf[4]) / 10.0f;
      return true;
    }
    delay(5);
  }
  return false;
}
#endif

Reading readSensors() {
  Reading r;
#if USE_SDS011
  r.hasPm = readSds011(r.pm25, r.pm10);
#endif
  if (!r.hasPm) {  // stand-in so the pipeline can be demoed without a sensor
    r.pm25 = analogRead(POT_PIN) / 4095.0f * 80.0f;
    r.pm10 = r.pm25 * 1.6f;
    r.hasPm = true;
  }
#if USE_BME280
  r.temperature = bme.readTemperature();
  r.humidity = bme.readHumidity();
  r.hasEnv = !isnan(r.temperature) && !isnan(r.humidity);
#endif
  return r;
}

// ---------------------------------------------------------------- canonical form
// Two decimals, as a JSON string; "null" when missing. Mirrors canonical.fmt_value().
String fmt2(bool present, float v) {
  if (!present || isnan(v) || isinf(v)) return "null";
  char b[24];
  snprintf(b, sizeof b, "%.2f", v);
  String s(b);
  if (s == "-0.00") s = "0.00";
  return "\"" + s + "\"";
}

// Keys sorted, no whitespace: device_id, humidity, pm10, pm25, temperature, ts, v
String canonicalJson(const char *ts, const String &hum, const String &pm10,
                     const String &pm25, const String &temp) {
  return String("{\"device_id\":\"") + DEVICE_ID + "\",\"humidity\":" + hum +
         ",\"pm10\":" + pm10 + ",\"pm25\":" + pm25 + ",\"temperature\":" + temp +
         ",\"ts\":\"" + ts + "\",\"v\":1}";
}

String toHex(const uint8_t *b, size_t n) {
  static const char *h = "0123456789abcdef";
  String s;
  s.reserve(n * 2);
  for (size_t i = 0; i < n; i++) { s += h[b[i] >> 4]; s += h[b[i] & 15]; }
  return s;
}

bool utcNow(char *out, size_t len) {
  time_t now = time(nullptr);
  if (now < 1700000000) return false;  // NTP not synced yet
  struct tm t;
  gmtime_r(&now, &t);
  strftime(out, len, "%Y-%m-%dT%H:%M:%SZ", &t);
  return true;
}

// ---------------------------------------------------------------- main
void setup() {
  Serial.begin(115200);
#if USE_SDS011
  Serial2.begin(9600, SERIAL_8N1, 16, 17);
#endif
#if USE_BME280
  if (!bme.begin(0x76)) Serial.println("BME280 not found");
#endif
  Ed25519::derivePublicKey(publicKey, DEVICE_PRIVATE_KEY);
  Serial.printf("ProofOfAir node %s, public key %s\n", DEVICE_ID, toHex(publicKey, 32).c_str());

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.printf("\nWiFi OK, IP %s\n", WiFi.localIP().toString().c_str());
  configTime(0, 0, "pool.ntp.org", "time.google.com");
}

void loop() {
  static uint32_t last = 0;
  if (last && millis() - last < SEND_EVERY_MS) { delay(100); return; }
  last = millis();

  char ts[24];
  if (!utcNow(ts, sizeof ts)) { Serial.println("waiting for NTP…"); last = 0; delay(1000); return; }

  Reading r = readSensors();
  String pm25 = fmt2(r.hasPm, r.pm25), pm10 = fmt2(r.hasPm, r.pm10);
  String temp = fmt2(r.hasEnv, r.temperature), hum = fmt2(r.hasEnv, r.humidity);
  String canon = canonicalJson(ts, hum, pm10, pm25, temp);

  uint8_t sig[64];
  Ed25519::sign(sig, DEVICE_PRIVATE_KEY, publicKey, canon.c_str(), canon.length());

  // Send the same 2-decimal strings that were signed, so the server rebuilds identical bytes.
  String body = String("{\"device_id\":\"") + DEVICE_ID + "\",\"ts\":\"" + ts +
                "\",\"pm10\":" + pm10 + ",\"pm25\":" + pm25 + ",\"temperature\":" + temp +
                ",\"humidity\":" + hum + ",\"signature_hex\":\"" + toHex(sig, 64) + "\"}";

  HTTPClient http;
  http.begin(SERVER_URL);
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(body);
  Serial.printf("%s -> HTTP %d %s\n", canon.c_str(), code, http.getString().c_str());
  http.end();
}
