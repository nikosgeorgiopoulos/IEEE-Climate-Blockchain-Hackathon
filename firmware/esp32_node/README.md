# ESP32 node (optional)

A ~€15 node that signs each reading **on the device** with Ed25519, so the data is
protected from the moment of measurement, not just from the moment the gateway sees it.
The server only accepts a reading if the device signature checks out
(`POST /api/ingest/device`), and the reading then goes through the same batch → Merkle →
anchor pipeline as everything else.

## Hardware

| Part | Wiring |
|---|---|
| ESP32 DevKit (any ESP32 with WiFi) | USB power |
| SDS011 PM sensor (optional) | TX → GPIO16, RX → GPIO17, 5V, GND |
| BME280 (optional, set `USE_BME280 1`) | SDA → GPIO21, SCL → GPIO22, 3V3, GND |
| No sensor? 10 kΩ potentiometer | wiper → GPIO34, ends → 3V3 / GND (stands in for PM2.5) |

## Software

1. Arduino IDE 2 with the ESP32 board package.
2. Libraries: **Crypto** by Rhys Weatherley (provides `Ed25519.h`), and
   **Adafruit BME280** only if you use that sensor.
3. Generate and register the device key on the server:
   ```bash
   python scripts/gen_keys.py --device esp32-01 --name "My balcony" --lat 37.979 --lon 23.783
   ```
   It prints `DEVICE_ID` and `DEVICE_PRIVATE_KEY` as a C array.
4. `cp secrets.example.h secrets.h` and paste the values in, plus WiFi and `SERVER_URL`.
   `secrets.h` is gitignored — never commit it.
5. Run the server so the ESP32 can reach it on your LAN:
   `.venv/bin/uvicorn proofofair.api:app --host 0.0.0.0 --port 8000`
6. Flash, open the serial monitor (115200 baud). Each minute you should see
   `{"device_id":"esp32-01",…} -> HTTP 201`.

## Canonical format test vector

The bytes the device signs must match `proofofair/canonical.py` exactly: JSON with sorted
keys, no spaces, values as strings with two decimals, `null` when missing, `"v":1`.
The same vector is checked in `tests/test_canonical.py`.

Input: `device_id=esp32-01, ts=2026-10-19T11:05:00Z, pm10=21.4, pm25=12.35, temperature=23.5, humidity=61`

Canonical bytes:
```
{"device_id":"esp32-01","humidity":"61.00","pm10":"21.40","pm25":"12.35","temperature":"23.50","ts":"2026-10-19T11:05:00Z","v":1}
```

With the **public RFC 8032 test key** (never use it for a real device)
seed `9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60`,
public key `d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a`,
the Ed25519 signature is:
```
13e8263b390e72e6b6ce43e964a3a3fd811f494432a1aa96dc1f46e657f6558fe90d34df6f97e4076ef04032668d1b1e3e17fd01f57bed3d8395e863b94e3f0a
```
Flash the sketch with that seed and a fixed timestamp/values to confirm your build produces
exactly this signature.

## Notes

- Time comes from NTP; the node waits until the clock is synced before sending.
- The firmware sends the same two-decimal strings it signed (`"pm25":"12.35"`), so the
  server rebuilds identical bytes. Floats are never re-rounded on the server side.
- This part is a stretch goal for the hackathon; it has not been flashed as part of the
  automated tests.
