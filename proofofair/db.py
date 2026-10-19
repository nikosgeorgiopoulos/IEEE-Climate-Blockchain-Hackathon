"""SQLite schema and helpers (plain sqlite3, no ORM).

Every call opens its own short-lived connection, which keeps things simple
across the API thread pool and the background scheduler.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from nacl.signing import SigningKey

from . import crypto
from .canonical import canonicalize, normalize
from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
  id TEXT PRIMARY KEY,                 -- e.g. "sc-18302", "sim-03", "esp32-01"
  source TEXT NOT NULL,                -- sensor_community | simulator | esp32
  name TEXT, lat REAL, lon REAL,
  pubkey_hex TEXT NOT NULL,            -- Ed25519 public key of the SIGNER
  signer TEXT NOT NULL                 -- "gateway" or "device"
);
CREATE TABLE IF NOT EXISTS readings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL REFERENCES devices(id),
  ts TEXT NOT NULL,                    -- ISO-8601 UTC, "2026-10-19T11:01:48Z"
  pm10 REAL, pm25 REAL, temperature REAL, humidity REAL,
  canonical TEXT NOT NULL,             -- exact bytes that were signed (utf-8 JSON)
  signature_hex TEXT NOT NULL,
  leaf_hash_hex TEXT,                  -- filled when batched
  batch_id INTEGER REFERENCES batches(id),
  UNIQUE(device_id, ts)
);
CREATE TABLE IF NOT EXISTS batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  period_start TEXT, period_end TEXT,
  n_leaves INTEGER NOT NULL,
  merkle_root_hex TEXT NOT NULL,
  anchor_backend TEXT, anchor_status TEXT,   -- pending | submitted | confirmed | failed
  anchor_tx TEXT, anchor_url TEXT, anchor_block INTEGER, anchored_at TEXT
);
CREATE TABLE IF NOT EXISTS proofs (
  reading_id INTEGER PRIMARY KEY REFERENCES readings(id),
  leaf_index INTEGER NOT NULL,
  proof_json TEXT NOT NULL               -- [{"pos":"L"|"R","hash":"..."}]
);
-- Demo only (Tamper Lab): remembers original values so Restore can undo an edit.
CREATE TABLE IF NOT EXISTS demo_tamper_log (
  reading_id INTEGER PRIMARY KEY REFERENCES readings(id),
  pm25 REAL, canonical TEXT NOT NULL, signature_hex TEXT NOT NULL,
  tampered_at TEXT NOT NULL, resigned INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_readings_device_ts ON readings(device_id, ts);
CREATE INDEX IF NOT EXISTS idx_readings_batch ON readings(batch_id);
"""

GATEWAY_KEY = "gateway"


def db_path() -> Path:
    return Path(get_settings().db_path)


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(path or db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def session(path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """Connection that commits on success, rolls back on error, always closes."""
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(path: str | Path | None = None) -> None:
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- devices

def upsert_device(
    conn: sqlite3.Connection,
    id: str,
    source: str,
    pubkey_hex: str,
    signer: str,
    name: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
) -> None:
    conn.execute(
        """INSERT INTO devices (id, source, name, lat, lon, pubkey_hex, signer)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET source=excluded.source, name=excluded.name,
             lat=excluded.lat, lon=excluded.lon, pubkey_hex=excluded.pubkey_hex,
             signer=excluded.signer""",
        (id, source, name, lat, lon, pubkey_hex, signer),
    )


def get_device(conn: sqlite3.Connection, device_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()


def gateway_key() -> SigningKey:
    return crypto.load_or_create_key(GATEWAY_KEY)


def signing_key_for(device: sqlite3.Row | dict) -> SigningKey | None:
    """The private key this server holds for a device's signer, if any."""
    if device["signer"] == "gateway":
        return gateway_key()
    path = crypto.key_path(device["id"])
    return crypto.load_or_create_key(device["id"]) if path.exists() else None


# ---------------------------------------------------------------- readings

def insert_signed_reading(
    conn: sqlite3.Connection,
    device_id: str,
    ts: str,
    pm10,
    pm25,
    temperature,
    humidity,
    signature_hex: str,
) -> int | None:
    """Insert a reading whose signature was produced elsewhere. None if duplicate."""
    vals = [normalize(v) for v in (pm10, pm25, temperature, humidity)]
    canonical = canonicalize(device_id, ts, *vals)
    cur = conn.execute(
        """INSERT OR IGNORE INTO readings
           (device_id, ts, pm10, pm25, temperature, humidity, canonical, signature_hex)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (device_id, ts, *vals, canonical.decode(), signature_hex),
    )
    return cur.lastrowid if cur.rowcount else None


def insert_reading(
    conn: sqlite3.Connection,
    sk: SigningKey,
    device_id: str,
    ts: str,
    pm10=None,
    pm25=None,
    temperature=None,
    humidity=None,
) -> int | None:
    """Canonicalize, sign with `sk` and insert. Returns the new id or None if duplicate."""
    vals = [normalize(v) for v in (pm10, pm25, temperature, humidity)]
    sig = crypto.sign(sk, canonicalize(device_id, ts, *vals))
    return insert_signed_reading(conn, device_id, ts, *vals, signature_hex=sig)


def get_reading(conn: sqlite3.Connection, reading_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM readings WHERE id = ?", (reading_id,)).fetchone()


def get_batch(conn: sqlite3.Connection, batch_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()


def get_proof(conn: sqlite3.Connection, reading_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM proofs WHERE reading_id = ?", (reading_id,)).fetchone()


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "devices": one("SELECT COUNT(*) FROM devices"),
        "readings": one("SELECT COUNT(*) FROM readings"),
        "unbatched": one("SELECT COUNT(*) FROM readings WHERE batch_id IS NULL"),
        "batches": one("SELECT COUNT(*) FROM batches"),
        "anchored": one("SELECT COUNT(*) FROM batches WHERE anchor_status = 'confirmed'"),
    }


# ---------------------------------------------------------------- demo (Tamper Lab)

def tamper_reading(
    conn: sqlite3.Connection,
    reading_id: int,
    new_pm25: float | None = None,
    factor: float = 0.3,
    resign: bool = False,
    now: str = "",
) -> sqlite3.Row:
    """Edit pm25 directly in the database, like a malicious admin would.

    Default: "hide the spike" (pm25 * 0.3) WITHOUT re-signing. With `resign`
    the attacker also has the signing key and produces a fresh, valid
    signature; only the on-chain anchor can catch that.
    """
    row = get_reading(conn, reading_id)
    if row is None:
        raise KeyError(reading_id)
    conn.execute(
        """INSERT OR IGNORE INTO demo_tamper_log
           (reading_id, pm25, canonical, signature_hex, tampered_at, resigned)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (reading_id, row["pm25"], row["canonical"], row["signature_hex"], now, int(resign)),
    )
    original = conn.execute(
        "SELECT pm25 FROM demo_tamper_log WHERE reading_id = ?", (reading_id,)
    ).fetchone()["pm25"]
    if new_pm25 is None:
        new_pm25 = (original or 0.0) * factor
    new_pm25 = normalize(new_pm25)
    conn.execute("UPDATE readings SET pm25 = ? WHERE id = ?", (new_pm25, reading_id))
    if resign:
        device = get_device(conn, row["device_id"])
        sk = signing_key_for(device)
        if sk is None:
            raise PermissionError("no private key on this server for that signer")
        canonical = canonicalize(
            row["device_id"], row["ts"], row["pm10"], new_pm25, row["temperature"], row["humidity"]
        )
        conn.execute(
            "UPDATE readings SET canonical = ?, signature_hex = ? WHERE id = ?",
            (canonical.decode(), crypto.sign(sk, canonical), reading_id),
        )
        conn.execute("UPDATE demo_tamper_log SET resigned = 1 WHERE reading_id = ?", (reading_id,))
    return get_reading(conn, reading_id)


def restore_reading(conn: sqlite3.Connection, reading_id: int) -> sqlite3.Row:
    log = conn.execute(
        "SELECT * FROM demo_tamper_log WHERE reading_id = ?", (reading_id,)
    ).fetchone()
    if log is None:
        raise LookupError(f"reading {reading_id} has not been tampered with")
    conn.execute(
        "UPDATE readings SET pm25 = ?, canonical = ?, signature_hex = ? WHERE id = ?",
        (log["pm25"], log["canonical"], log["signature_hex"], reading_id),
    )
    conn.execute("DELETE FROM demo_tamper_log WHERE reading_id = ?", (reading_id,))
    return get_reading(conn, reading_id)


def tampered_ids(conn: sqlite3.Connection) -> dict[int, dict]:
    rows = conn.execute("SELECT * FROM demo_tamper_log").fetchall()
    return {r["reading_id"]: dict(r) for r in rows}
