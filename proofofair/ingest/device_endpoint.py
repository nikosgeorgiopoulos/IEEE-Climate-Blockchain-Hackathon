"""Helpers for readings that are signed on the device itself (ESP32 node).

The device signs the canonical JSON (see canonical.py) with its own Ed25519
key. The server re-builds the same bytes from the posted values and only
stores the reading if the signature checks out, so the gateway never needs
to be trusted for these readings.
"""

from __future__ import annotations

from .. import crypto, db
from ..canonical import canonicalize, normalize
from ..models import DeviceReading


class IngestError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def register_device(conn, device_id: str, name: str | None = None, lat: float | None = None,
                    lon: float | None = None, source: str = "esp32") -> str:
    """Create (or load) the device key, register the device, return the 32-byte seed hex."""
    sk = crypto.load_or_create_key(device_id)
    db.upsert_device(conn, device_id, source, crypto.pubkey_hex(sk), "device",
                     name=name or device_id, lat=lat, lon=lon)
    return sk.encode().hex()


def c_array(seed_hex: str, name: str = "DEVICE_PRIVATE_KEY") -> str:
    """Format a key seed as a C array for firmware/esp32_node/secrets.h."""
    b = bytes.fromhex(seed_hex)
    rows = [", ".join(f"0x{x:02x}" for x in b[i:i + 8]) for i in range(0, len(b), 8)]
    body = ",\n  ".join(rows)
    return f"const uint8_t {name}[32] = {{\n  {body}\n}};"


def ingest_device_reading(conn, payload: DeviceReading) -> int:
    """Verify the device signature and insert. Returns the new reading id."""
    device = db.get_device(conn, payload.device_id)
    if device is None:
        raise IngestError(404, f"unknown device {payload.device_id!r}; run gen_keys.py --device")
    if device["signer"] != "device":
        raise IngestError(403, "this device is gateway-signed and cannot post signed readings")
    vals = [normalize(v) for v in (payload.pm10, payload.pm25, payload.temperature,
                                   payload.humidity)]
    msg = canonicalize(payload.device_id, payload.ts, *vals)
    if not crypto.verify(device["pubkey_hex"], msg, payload.signature_hex.lower()):
        raise IngestError(400, "invalid signature for canonical record " + msg.decode())
    rid = db.insert_signed_reading(conn, payload.device_id, payload.ts, *vals,
                                   signature_hex=payload.signature_hex.lower())
    if rid is None:
        raise IngestError(409, "duplicate reading (device_id, ts)")
    return rid
