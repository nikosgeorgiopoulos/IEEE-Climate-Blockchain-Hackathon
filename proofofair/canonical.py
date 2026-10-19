"""Canonical serialization of a reading: the exact bytes that get signed.

Rules (schema v1): JSON, sorted keys, no whitespace, UTF-8. Measurements are
strings with exactly two decimals ("4.47"); missing values are null. The same
bytes are produced by the ESP32 firmware, so keep both in sync.
"""

from __future__ import annotations

import json

SCHEMA_VERSION = 1
FIELDS = ("pm10", "pm25", "temperature", "humidity")


def fmt_value(x: float | str | None) -> str | None:
    """Format a measurement with exactly two decimals, or None."""
    if x is None:
        return None
    v = float(x)
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError(f"not a finite number: {x!r}")
    s = f"{v:.2f}"
    return "0.00" if s == "-0.00" else s


def normalize(x: float | str | None) -> float | None:
    """Round a value to what the canonical form keeps, so DB and signature agree."""
    s = fmt_value(x)
    return None if s is None else float(s)


def canonicalize(
    device_id: str,
    ts: str,
    pm10: float | str | None,
    pm25: float | str | None,
    temperature: float | str | None,
    humidity: float | str | None,
) -> bytes:
    obj = {
        "device_id": device_id,
        "ts": ts,
        "pm10": fmt_value(pm10),
        "pm25": fmt_value(pm25),
        "temperature": fmt_value(temperature),
        "humidity": fmt_value(humidity),
        "v": SCHEMA_VERSION,
    }
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def canonicalize_row(row) -> bytes:
    """Canonical bytes from a readings row (mapping with the editable columns)."""
    return canonicalize(
        row["device_id"], row["ts"], row["pm10"], row["pm25"], row["temperature"], row["humidity"]
    )
