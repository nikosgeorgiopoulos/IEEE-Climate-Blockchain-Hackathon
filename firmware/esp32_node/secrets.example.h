// Copy to secrets.h (gitignored) and fill in. NEVER commit secrets.h.
// `python scripts/gen_keys.py --device esp32-01` prints DEVICE_ID and DEVICE_PRIVATE_KEY.
#pragma once
#include <stdint.h>

#define WIFI_SSID     "your-wifi"
#define WIFI_PASSWORD "your-password"
// Your laptop's LAN address, where `make run` listens (use --host 0.0.0.0, see README).
#define SERVER_URL    "http://192.168.1.50:8000/api/ingest/device"

#define DEVICE_ID "esp32-01"
const uint8_t DEVICE_PRIVATE_KEY[32] = {
  0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
  0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
  0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
  0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00
};
