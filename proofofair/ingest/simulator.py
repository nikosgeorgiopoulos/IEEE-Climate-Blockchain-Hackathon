"""Synthetic air-quality sensors around Athens, so the map is never empty.

Each simulated device has its own Ed25519 key (signer="device"), i.e. it
behaves like real signing hardware. Values are deterministic for a given
(device, timestamp), which makes seeding reproducible.
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .. import crypto, db
from ..config import get_settings

log = logging.getLogger(__name__)
ATHENS_TZ = ZoneInfo("Europe/Athens")


@dataclass(frozen=True)
class SimSite:
    slug: str
    name: str
    lat: float
    lon: float
    base_pm25: float  # µg/m³ background level
    traffic: float  # how strongly rush hour shows up (0..1)
    spike_hour: float | None = None  # daily emission event (local time), e.g. industry


SITES: list[SimSite] = [
    SimSite("syntagma", "Syntagma Square", 37.9755, 23.7348, 14, 0.9),
    SimSite("piraeus", "Piraeus Port", 37.9420, 23.6465, 24, 0.6, spike_hour=11.0),
    SimSite("patision", "Patision Street", 37.9990, 23.7320, 16, 1.0),
    SimSite("marousi", "Marousi", 38.0560, 23.8080, 10, 0.6),
    SimSite("kifisias", "Kifisias Avenue", 38.0300, 23.7950, 15, 1.0),
    SimSite("ntua", "NTUA Zografou Campus", 37.9790, 23.7830, 9, 0.4),
    SimSite("elefsina", "Elefsina Industrial Zone", 38.0410, 23.5420, 28, 0.4, spike_hour=14.0),
    SimSite("peristeri", "Peristeri", 38.0130, 23.6910, 13, 0.7),
    SimSite("glyfada", "Glyfada", 37.8650, 23.7530, 8, 0.5),
    SimSite("kallithea", "Kallithea", 37.9550, 23.7020, 13, 0.8),
    SimSite("halandri", "Halandri", 38.0210, 23.7990, 10, 0.6),
    SimSite("pallini", "Pallini", 38.0010, 23.8800, 7, 0.3),
]


def device_id(i: int) -> str:
    return f"sim-{i + 1:02d}"


def devices(n: int | None = None) -> list[tuple[str, SimSite]]:
    n = get_settings().sim_devices if n is None else n
    out = []
    for i in range(max(0, n)):
        site = SITES[i % len(SITES)]
        if i >= len(SITES):  # more devices than sites: jitter around the same spots
            r = random.Random(i)
            site = SimSite(
                f"{site.slug}-{i}",
                f"{site.name} #{i // len(SITES) + 1}",
                site.lat + r.uniform(-0.01, 0.01),
                site.lon + r.uniform(-0.01, 0.01),
                site.base_pm25,
                site.traffic,
                site.spike_hour,
            )
        out.append((device_id(i), site))
    return out


def register_devices(conn, n: int | None = None) -> list[str]:
    ids = []
    for dev_id, site in devices(n):
        sk = crypto.load_or_create_key(dev_id)
        db.upsert_device(
            conn, dev_id, "simulator", crypto.pubkey_hex(sk), "device",
            name=f"{site.name} (simulated)", lat=site.lat, lon=site.lon,
        )
        ids.append(dev_id)
    return ids


def _bump(hour: float, center: float, width: float) -> float:
    d = min(abs(hour - center), 24 - abs(hour - center))
    return math.exp(-0.5 * (d / width) ** 2)


def values_at(dev_id: str, site: SimSite, when: datetime) -> dict[str, float]:
    """PM/temperature/humidity for one device at one moment (deterministic)."""
    local = when.astimezone(ATHENS_TZ)
    hour = local.hour + local.minute / 60
    seed = hashlib.sha256(f"{dev_id}|{when.isoformat()}".encode()).digest()
    rng = random.Random(seed)
    day_rng = random.Random(f"{dev_id}|{local.date()}")  # day-to-day variation
    day_factor = day_rng.uniform(0.75, 1.3)

    rush = 1.4 * _bump(hour, 8.0, 1.3) + 1.2 * _bump(hour, 19.0, 1.6)
    night_low = -0.25 * _bump(hour, 4.0, 2.5)
    pm25 = site.base_pm25 * day_factor * (1 + site.traffic * rush + night_low)
    if site.spike_hour is not None:
        pm25 += 22 * day_factor * _bump(hour, site.spike_hour, 0.7)
    pm25 *= rng.uniform(0.88, 1.12)
    pm25 = min(60.0, max(5.0, pm25))

    pm10 = min(90.0, max(10.0, pm25 * rng.uniform(1.45, 1.8)))
    temperature = 17 + 5 * math.sin((hour - 9) / 24 * 2 * math.pi) + rng.uniform(-0.6, 0.6)
    humidity = min(95.0, max(25.0, 62 - 14 * math.sin((hour - 9) / 24 * 2 * math.pi)
                                + rng.uniform(-3, 3)))
    return {
        "pm10": round(pm10, 2),
        "pm25": round(pm25, 2),
        "temperature": round(temperature, 2),
        "humidity": round(humidity, 2),
    }


def iso(when: datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def tick(conn, when: datetime | None = None) -> int:
    """One reading per simulated device at `when` (default: now). Returns rows inserted."""
    when = (when or datetime.now(UTC)).replace(microsecond=0)
    inserted = 0
    for dev_id, site in devices():
        sk = crypto.load_or_create_key(dev_id)
        if db.get_device(conn, dev_id) is None:
            register_devices(conn)
        if db.insert_reading(conn, sk, dev_id, iso(when), **values_at(dev_id, site, when)):
            inserted += 1
    return inserted


def backfill(conn, start: datetime, end: datetime, step: timedelta) -> int:
    """Readings on a regular grid between start and end (aligned, so re-runs are no-ops)."""
    step_s = int(step.total_seconds())
    t = datetime.fromtimestamp((int(start.timestamp()) // step_s + 1) * step_s, UTC)
    inserted = 0
    while t <= end:
        inserted += tick(conn, t)
        t += step
    return inserted
