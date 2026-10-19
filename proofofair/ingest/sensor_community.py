"""Real air-quality data from Sensor.Community (ex-luftdaten) around Athens.

Public citizen sensors do not sign their data, so ProofOfAir signs each
reading with its *gateway* key at the moment of ingestion. Integrity is
protected from that moment on, not before.

Three sources, all parsed defensively (missing fields are skipped, network
errors are logged and swallowed):

* live:    /airrohr/v1/filter/area=LAT,LON,KM   (last ~5 min, sparse)
* 1-hour:  /static/v2/data.1h.json              (global, large: streamed)
* archive: archive.sensor.community/DAY/DAY_TYPE_sensor_ID.csv  (backfill)
"""

from __future__ import annotations

import csv
import io
import json
import logging
import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from .. import USER_AGENT, crypto, db
from ..config import get_settings

log = logging.getLogger(__name__)

LIVE_URL = "https://data.sensor.community/airrohr/v1/filter/area={lat},{lon},{radius}"
HOURLY_URL = "https://data.sensor.community/static/v2/data.1h.json"
ARCHIVE_URL = "https://archive.sensor.community/{day}/{day}_{type}_sensor_{sid}.csv"

VALUE_MAP = {"P1": "pm10", "P2": "pm25", "temperature": "temperature", "humidity": "humidity"}
BOUNDS = {"pm10": (0, 1000), "pm25": (0, 1000), "temperature": (-40, 60), "humidity": (0, 100)}


@dataclass
class ParsedReading:
    location_id: str
    ts: str
    lat: float | None = None
    lon: float | None = None
    values: dict[str, float] = field(default_factory=dict)
    sensors: dict[str, str] = field(default_factory=dict)  # sensor_id -> type (e.g. SDS011)

    @property
    def device_id(self) -> str:
        return f"sc-{self.location_id}"


# ------------------------------------------------------------------ parsing helpers

def _to_float(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _clean(kind: str, x) -> float | None:
    v = _to_float(x)
    if v is None:
        return None
    lo, hi = BOUNDS[kind]
    return v if lo <= v <= hi else None


def _minute_ts(raw: str) -> str | None:
    """'2026-10-19 11:01:48' (UTC) -> '2026-10-19T11:01:00Z' (merge key + stable dedup)."""
    try:
        t = datetime.fromisoformat(str(raw).strip().replace("Z", "").replace(" ", "T"))
    except ValueError:
        return None
    return t.replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def parse_records(records: Iterable[dict]) -> list[ParsedReading]:
    """Merge API records from the same location and minute into one reading each."""
    merged: dict[tuple[str, str], ParsedReading] = {}
    for rec in records:
        try:
            loc = rec.get("location") or {}
            loc_id = loc.get("id")
            ts = _minute_ts(rec.get("timestamp", ""))
            if loc_id is None or ts is None:
                continue
            key = (str(loc_id), ts)
            pr = merged.get(key) or ParsedReading(
                location_id=str(loc_id), ts=ts,
                lat=_to_float(loc.get("latitude")), lon=_to_float(loc.get("longitude")),
            )
            sensor = rec.get("sensor") or {}
            stype = ((sensor.get("sensor_type") or {}).get("name") or "").strip()
            if sensor.get("id") is not None and stype:
                pr.sensors[str(sensor["id"])] = stype
            for dv in rec.get("sensordatavalues") or []:
                kind = VALUE_MAP.get(dv.get("value_type"))
                if kind:
                    v = _clean(kind, dv.get("value"))
                    if v is not None:
                        pr.values[kind] = v
            merged[key] = pr
        except (AttributeError, TypeError) as e:
            log.debug("skipping malformed record: %s", e)
    return [pr for pr in merged.values() if pr.values]


def iter_json_array(chunks: Iterable[str]) -> Iterator[dict]:
    """Yield the elements of a huge top-level JSON array without loading it all."""
    dec = json.JSONDecoder()
    buf, started = "", False
    for chunk in chunks:
        buf += chunk
        pos = 0
        while True:
            while pos < len(buf) and buf[pos] in " \t\r\n,":
                pos += 1
            if pos >= len(buf):
                break
            if not started:
                if buf[pos] != "[":
                    raise ValueError("expected a JSON array")
                started, pos = True, pos + 1
                continue
            if buf[pos] == "]":
                return
            try:
                obj, end = dec.raw_decode(buf, pos)
            except json.JSONDecodeError:
                break  # incomplete element: wait for more data
            yield obj
            pos = end
        buf = buf[pos:]


def in_area(pr_or_rec, lat: float, lon: float, radius_km: float) -> bool:
    if isinstance(pr_or_rec, ParsedReading):
        plat, plon = pr_or_rec.lat, pr_or_rec.lon
    else:
        loc = pr_or_rec.get("location") or {}
        plat, plon = _to_float(loc.get("latitude")), _to_float(loc.get("longitude"))
    if plat is None or plon is None:
        return False
    return haversine_km(lat, lon, plat, plon) <= radius_km


def parse_archive_csv(text: str, location_id: str | None = None,
                      bucket_min: int = 5) -> list[ParsedReading]:
    """Parse an archive CSV (semicolon separated), one reading per `bucket_min` minutes."""
    out: dict[str, ParsedReading] = {}
    for row in csv.DictReader(io.StringIO(text), delimiter=";"):
        loc = location_id or (row.get("location") or "").strip()
        raw_ts = (row.get("timestamp") or "").strip()
        if not loc or not raw_ts:
            continue
        try:
            t = datetime.fromisoformat(raw_ts.replace("Z", ""))
        except ValueError:
            continue
        t = t.replace(minute=t.minute - t.minute % bucket_min, second=0, microsecond=0)
        ts = t.strftime("%Y-%m-%dT%H:%M:%SZ")
        if ts in out:
            continue
        values = {}
        for col, kind in VALUE_MAP.items():
            v = _clean(kind, row.get(col))
            if v is not None:
                values[kind] = v
        if not values:
            continue
        sid, stype = (row.get("sensor_id") or "").strip(), (row.get("sensor_type") or "").strip()
        out[ts] = ParsedReading(location_id=str(loc), ts=ts, lat=_to_float(row.get("lat")),
                                lon=_to_float(row.get("lon")), values=values,
                                sensors={sid: stype} if sid and stype else {})
    return list(out.values())


def merge_readings(readings: Iterable[ParsedReading]) -> list[ParsedReading]:
    """Combine e.g. SDS011 (PM) and BME280 (temp/hum) rows of one location and time."""
    merged: dict[tuple[str, str], ParsedReading] = {}
    for pr in readings:
        key = (pr.location_id, pr.ts)
        if key in merged:
            m = merged[key]
            m.values.update(pr.values)
            m.sensors.update(pr.sensors)
            m.lat, m.lon = m.lat or pr.lat, m.lon or pr.lon
        else:
            merged[key] = ParsedReading(pr.location_id, pr.ts, pr.lat, pr.lon,
                                        dict(pr.values), dict(pr.sensors))
    return list(merged.values())


# ------------------------------------------------------------------ registry of sensors

def _registry_path() -> Path:
    return get_settings().data_dir / "sc_sensors.json"


def load_registry() -> dict:
    p = _registry_path()
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _update_registry(readings: Iterable[ParsedReading]) -> None:
    reg = load_registry()
    changed = False
    for pr in readings:
        entry = reg.setdefault(pr.location_id, {"lat": pr.lat, "lon": pr.lon, "sensors": {}})
        for sid, stype in pr.sensors.items():
            if entry["sensors"].get(sid) != stype:
                entry["sensors"][sid] = stype
                changed = True
        changed = changed or entry.get("lat") is None
    if changed or not _registry_path().exists():
        _registry_path().parent.mkdir(parents=True, exist_ok=True)
        _registry_path().write_text(json.dumps(reg, indent=2, sort_keys=True))


# ------------------------------------------------------------------ storage

def store(readings: list[ParsedReading]) -> int:
    """Register devices and insert gateway-signed readings. Returns # new readings."""
    if not readings:
        return 0
    _update_registry(readings)
    sk = db.gateway_key()
    pub = crypto.pubkey_hex(sk)
    reg = load_registry()
    inserted = 0
    with db.session() as conn:
        for pr in readings:
            if pr.lat is None or pr.lon is None:
                continue
            types = sorted(set((reg.get(pr.location_id) or {}).get("sensors", {}).values())
                           | set(pr.sensors.values()))
            name = f"Sensor.Community #{pr.location_id}" + (f" ({', '.join(types)})"
                                                            if types else "")
            db.upsert_device(conn, pr.device_id, "sensor_community", pub, "gateway",
                             name=name, lat=pr.lat, lon=pr.lon)
            v = pr.values
            if db.insert_reading(conn, sk, pr.device_id, pr.ts, v.get("pm10"), v.get("pm25"),
                                 v.get("temperature"), v.get("humidity")):
                inserted += 1
    return inserted


# ------------------------------------------------------------------ network

def make_client() -> httpx.Client:
    return httpx.Client(
        timeout=20.0,
        headers={"User-Agent": USER_AGENT},
        transport=httpx.HTTPTransport(retries=2),
        follow_redirects=True,
    )


def fetch_live(client: httpx.Client | None = None) -> list[ParsedReading]:
    s = get_settings()
    url = LIVE_URL.format(lat=s.athens_lat, lon=s.athens_lon, radius=int(s.athens_radius_km))
    own = client is None
    client = client or make_client()
    try:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()
        return parse_records(data if isinstance(data, list) else [])
    finally:
        if own:
            client.close()


def fetch_hourly(client: httpx.Client | None = None) -> list[ParsedReading]:
    """Stream the global 1-hour average file and keep only sensors near Athens."""
    s = get_settings()
    own = client is None
    client = client or make_client()
    keep = []
    try:
        with client.stream("GET", HOURLY_URL) as resp:
            resp.raise_for_status()
            for rec in iter_json_array(resp.iter_text()):
                if in_area(rec, s.athens_lat, s.athens_lon, s.athens_radius_km):
                    keep.append(rec)
    finally:
        if own:
            client.close()
    return parse_records(keep)


def ingest_live(client: httpx.Client | None = None) -> int:
    try:
        return store(fetch_live(client))
    except (httpx.HTTPError, ValueError) as e:
        log.warning("Sensor.Community live fetch failed: %s", e)
        return 0


def ingest_hourly_snapshot(client: httpx.Client | None = None) -> int:
    try:
        return store(fetch_hourly(client))
    except (httpx.HTTPError, ValueError) as e:
        log.warning("Sensor.Community 1h snapshot failed: %s", e)
        return 0


def backfill_archive(days: int = 3, client: httpx.Client | None = None,
                     today: date | None = None) -> int:
    """Fetch archive CSVs for every known sensor for the last `days` days (404 = skip)."""
    reg = load_registry()
    if not reg:
        return 0
    today = today or datetime.now(UTC).date()
    own = client is None
    client = client or make_client()
    total = 0
    try:
        for loc_id, entry in reg.items():
            parsed: list[ParsedReading] = []
            for d in range(days, 0, -1):
                day = (today - timedelta(days=d)).isoformat()
                for sid, stype in entry.get("sensors", {}).items():
                    url = ARCHIVE_URL.format(day=day, type=stype.lower(), sid=sid)
                    try:
                        resp = client.get(url)
                        if resp.status_code == 404:
                            continue
                        resp.raise_for_status()
                    except httpx.HTTPError as e:
                        log.info("archive fetch failed for %s: %s", url, e)
                        continue
                    for pr in parse_archive_csv(resp.text, loc_id):
                        pr.lat = pr.lat or entry.get("lat")
                        pr.lon = pr.lon or entry.get("lon")
                        parsed.append(pr)
            total += store(merge_readings(parsed))
    finally:
        if own:
            client.close()
    return total
