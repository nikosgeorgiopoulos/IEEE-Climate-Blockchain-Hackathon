#!/usr/bin/env python3
"""Create the gateway key and one key per simulated device (or one ESP32 key).

    python scripts/gen_keys.py                       # gateway + SIM_DEVICES keys
    python scripts/gen_keys.py --device esp32-01     # + prints a secrets.h for the firmware
"""

import argparse

import _bootstrap  # noqa: F401

from proofofair import crypto, db
from proofofair.config import get_settings
from proofofair.ingest import simulator
from proofofair.ingest.device_endpoint import c_array, register_device


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--device", help="register a device-signed node (e.g. esp32-01)")
    ap.add_argument("--name", help="human-readable name for --device")
    ap.add_argument("--lat", type=float, default=37.9790, help="latitude for --device")
    ap.add_argument("--lon", type=float, default=23.7830, help="longitude for --device")
    args = ap.parse_args()

    s = get_settings()
    db.init_db()
    gw = db.gateway_key()
    print(f"gateway key      {crypto.key_path('gateway')}  pub {crypto.pubkey_hex(gw)}")
    with db.session() as conn:
        for dev_id in simulator.register_devices(conn):
            pub = db.get_device(conn, dev_id)["pubkey_hex"]
            print(f"{dev_id:<16} {crypto.key_path(dev_id)}  pub {pub}")
        if args.device:
            seed = register_device(conn, args.device, args.name or f"{args.device} (ESP32)",
                                   args.lat, args.lon)
            pub = db.get_device(conn, args.device)["pubkey_hex"]
            print(f"\n{args.device} registered (signer=device), pub {pub}")
            print("\nPaste into firmware/esp32_node/secrets.h (gitignored, NEVER commit it):\n")
            print(f'#define DEVICE_ID "{args.device}"')
            print(c_array(seed))
            print(f"// public key (registered on the server): {pub}")
    print(f"\nKeys live in {s.keys_dir}/ (mode 600, gitignored).")


if __name__ == "__main__":
    main()
