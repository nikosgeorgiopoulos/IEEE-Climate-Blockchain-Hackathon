"""FastAPI app: REST API + the static dashboard in web/."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, batcher, db, scheduler
from .anchor import get_anchor_backend
from .anchor.base import utcnow_iso
from .config import get_settings
from .ingest import simulator
from .ingest.device_endpoint import IngestError, ingest_device_reading
from .models import DeviceReading, TamperRequest, VerifyReport
from .verify import local_status, verify_reading

log = logging.getLogger("proofofair")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = get_settings()
    db.init_db()
    db.gateway_key()
    with db.session() as conn:
        simulator.register_devices(conn)
    get_anchor_backend()  # fail fast on a bad ANCHOR_BACKEND
    if s.scheduler_enabled:
        scheduler.start()
    log.info("ProofOfAir %s up: backend=%s demo_mode=%s db=%s", __version__,
             s.anchor_backend, s.demo_mode, s.db_path)
    yield
    scheduler.shutdown()


app = FastAPI(title="ProofOfAir", version=__version__, lifespan=lifespan)


def _404(what: str):
    return HTTPException(status_code=404, detail=f"{what} not found")


def _shift(ts: str, hours: float) -> str:
    return (datetime.strptime(ts, TS_FMT) - timedelta(hours=hours)).strftime(TS_FMT)


def _batch_out(b, backend_labels: dict[str, str]) -> dict:
    d = dict(b)
    d["explorer_label"] = backend_labels.get(b["anchor_backend"] or "", "explorer")
    return d


def _labels() -> dict[str, str]:
    out = {}
    for name in ("mock", "polygon", "ots"):
        try:
            out[name] = get_anchor_backend(name).explorer_label
        except Exception:  # e.g. polygon not configured
            out[name] = {"polygon": "Polygonscan", "ots": "OpenTimestamps"}.get(name, name)
    return out


READING_COLS = "r.id, r.device_id, r.ts, r.pm10, r.pm25, r.temperature, r.humidity, " \
               "r.canonical, r.signature_hex, r.leaf_hash_hex, r.batch_id"
JOIN_SQL = f"""SELECT {READING_COLS}, p.proof_json, b.merkle_root_hex, b.anchor_status,
                      b.anchor_block, d.pubkey_hex, t.pm25 AS original_pm25,
                      t.reading_id IS NOT NULL AS tampered
               FROM readings r
               JOIN devices d ON d.id = r.device_id
               LEFT JOIN proofs p ON p.reading_id = r.id
               LEFT JOIN batches b ON b.id = r.batch_id
               LEFT JOIN demo_tamper_log t ON t.reading_id = r.id"""


def _reading_out(row) -> dict:
    status = local_status(row, row["pubkey_hex"], row["proof_json"], row["merkle_root_hex"],
                          row["anchor_status"])
    return {
        "id": row["id"], "device_id": row["device_id"], "ts": row["ts"],
        "pm10": row["pm10"], "pm25": row["pm25"], "temperature": row["temperature"],
        "humidity": row["humidity"], "batch_id": row["batch_id"],
        "anchor_status": row["anchor_status"], "anchor_block": row["anchor_block"],
        "status": status, "tampered": bool(row["tampered"]),
        "original_pm25": row["original_pm25"] if row["tampered"] else None,
    }


# ------------------------------------------------------------------ read API

@app.get("/api/health")
def health():
    s = get_settings()
    with db.session() as conn:
        c = db.counts(conn)
        last = conn.execute(
            """SELECT id, anchored_at, anchor_url, anchor_tx, anchor_block, anchor_backend
               FROM batches WHERE anchor_status = 'confirmed' ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        by_source = dict(conn.execute(
            "SELECT source, COUNT(*) FROM devices GROUP BY source").fetchall())
    eta = scheduler.next_batch_eta()
    return {
        "status": "ok",
        "version": __version__,
        "backend": s.anchor_backend,
        "explorer_label": _labels().get(s.anchor_backend),
        "demo_mode": s.demo_mode,
        "scheduler_running": eta is not None,
        "counts": c,
        "devices_by_source": by_source,
        "batch_interval_sec": s.batch_interval_sec,
        "next_batch_eta": eta.strftime(TS_FMT) if eta else None,
        "next_batch_in_sec": scheduler.next_batch_in_sec(),
        "last_anchor": dict(last) if last else None,
        "server_time": utcnow_iso(),
    }


@app.get("/api/devices")
def devices(hours: float = Query(24, gt=0, le=24 * 31)):
    with db.session() as conn:
        devs = conn.execute("SELECT * FROM devices ORDER BY id").fetchall()
        latest = {r["device_id"]: r for r in conn.execute(
            f"""{JOIN_SQL} JOIN (SELECT device_id, MAX(ts) AS m FROM readings GROUP BY device_id)
                x ON x.device_id = r.device_id AND x.m = r.ts""").fetchall()}
        since = min((_shift(r["ts"], hours) for r in latest.values()), default=None)
        window: dict[str, list] = {}
        if since:
            for r in conn.execute(f"{JOIN_SQL} WHERE r.ts >= ?", (since,)).fetchall():
                if r["ts"] >= _shift(latest[r["device_id"]]["ts"], hours):
                    window.setdefault(r["device_id"], []).append(r)
    out = []
    for d in devs:
        rows = window.get(d["id"], [])
        statuses = [local_status(r, r["pubkey_hex"], r["proof_json"], r["merkle_root_hex"],
                                 r["anchor_status"]) for r in rows]
        n_ver, n_bad = statuses.count("verified"), statuses.count("tampered")
        lt = latest.get(d["id"])
        status = "none" if lt is None else ("tampered" if n_bad else
                                            "verified" if n_ver else "pending")
        out.append({
            "id": d["id"], "source": d["source"], "name": d["name"], "lat": d["lat"],
            "lon": d["lon"], "signer": d["signer"], "pubkey_hex": d["pubkey_hex"],
            "latest": _reading_out(lt) if lt else None,
            "n_readings": len(rows), "n_verified": n_ver, "n_tampered": n_bad,
            "n_pending": statuses.count("pending"),
            "verified_ratio": round(n_ver / len(rows), 4) if rows else None,
            "status": status,
        })
    return out


@app.get("/api/devices/{device_id}/readings")
def device_readings(device_id: str, hours: float = Query(24, gt=0, le=24 * 31),
                    anchored_only: bool = False):
    with db.session() as conn:
        dev = db.get_device(conn, device_id)
        if dev is None:
            raise _404("device")
        end = conn.execute("SELECT MAX(ts) FROM readings WHERE device_id = ?",
                           (device_id,)).fetchone()[0]
        rows = []
        if end:
            sql = f"{JOIN_SQL} WHERE r.device_id = ? AND r.ts >= ?"
            if anchored_only:
                sql += " AND b.anchor_status = 'confirmed'"
            rows = conn.execute(sql + " ORDER BY r.ts", (device_id, _shift(end, hours))).fetchall()
    return {"device": dict(dev), "hours": hours,
            "readings": [_reading_out(r) for r in rows]}


@app.get("/api/readings/{reading_id}")
def reading(reading_id: int):
    with db.session() as conn:
        r = conn.execute(f"{JOIN_SQL} WHERE r.id = ?", (reading_id,)).fetchone()
        if r is None:
            raise _404("reading")
        dev = db.get_device(conn, r["device_id"])
        proof = db.get_proof(conn, reading_id)
    out = _reading_out(r)
    out.update(canonical=r["canonical"], signature_hex=r["signature_hex"],
               leaf_hash_hex=r["leaf_hash_hex"], merkle_root_hex=r["merkle_root_hex"],
               leaf_index=proof["leaf_index"] if proof else None,
               device=dict(dev))
    return out


@app.get("/api/readings/{reading_id}/verify", response_model=VerifyReport)
def verify(reading_id: int):
    try:
        return verify_reading(reading_id)
    except KeyError:
        raise _404("reading") from None


@app.get("/api/batches")
def batches(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    labels = _labels()
    with db.session() as conn:
        rows = conn.execute("SELECT * FROM batches ORDER BY id DESC LIMIT ? OFFSET ?",
                            (limit, offset)).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0]
    return {"total": total, "batches": [_batch_out(b, labels) for b in rows]}


@app.get("/api/batches/{batch_id}")
def batch(batch_id: int):
    with db.session() as conn:
        b = db.get_batch(conn, batch_id)
        if b is None:
            raise _404("batch")
        stats = conn.execute(
            """SELECT COUNT(*) AS n, COUNT(DISTINCT device_id) AS devices
               FROM readings WHERE batch_id = ?""", (batch_id,)).fetchone()
        sample = [r[0] for r in conn.execute(
            "SELECT id FROM readings WHERE batch_id = ? ORDER BY id LIMIT 20", (batch_id,))]
    out = _batch_out(b, _labels())
    out.update(leaf_count=stats["n"], device_count=stats["devices"], sample_reading_ids=sample)
    return out


@app.get("/api/batches/{batch_id}/ots")
def batch_ots(batch_id: int):
    """Download the OpenTimestamps proof; verify it at https://opentimestamps.org."""
    from .anchor.ots import OtsBackend

    _, ots_path = OtsBackend().paths(batch_id)
    if not ots_path.exists():
        raise _404("OpenTimestamps proof")
    return FileResponse(ots_path, media_type="application/octet-stream",
                        filename=ots_path.name)


@app.get("/api/mockchain/{batch_id}")
def mockchain(batch_id: int):
    """'Block explorer' for the offline mock backend."""
    rec = get_anchor_backend("mock").record(batch_id)
    if rec is None:
        raise _404("mock anchor")
    return {"chain": "proofofair-mock (offline dev chain, NOT a real blockchain)",
            "batch_id": batch_id, **rec}


# ------------------------------------------------------------------ device ingest

@app.post("/api/ingest/device", status_code=201)
def ingest_device(payload: DeviceReading):
    try:
        with db.session() as conn:
            rid = ingest_device_reading(conn, payload)
    except IngestError as e:
        return JSONResponse(status_code=e.status, content={"detail": str(e)})
    return {"id": rid, "status": "stored", "verify_url": f"/api/readings/{rid}/verify"}


# ------------------------------------------------------------------ demo (Tamper Lab)

def _require_demo():
    if not get_settings().demo_mode:
        raise HTTPException(status_code=403, detail="DEMO_MODE is off")


@app.post("/api/demo/tamper/{reading_id}")
def demo_tamper(reading_id: int, req: TamperRequest | None = Body(default=None)):
    _require_demo()
    req = req or TamperRequest()
    try:
        with db.session() as conn:
            row = db.tamper_reading(conn, reading_id, new_pm25=req.pm25, resign=req.resign,
                                    now=utcnow_iso())
            log_row = db.tampered_ids(conn)[reading_id]
    except KeyError:
        raise _404("reading") from None
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    log.warning("DEMO: reading %s tampered: pm25 %s -> %s (resign=%s)", reading_id,
                log_row["pm25"], row["pm25"], req.resign)
    return {"reading_id": reading_id, "original_pm25": log_row["pm25"], "pm25": row["pm25"],
            "resigned": bool(log_row["resigned"])}


@app.post("/api/demo/restore/{reading_id}")
def demo_restore(reading_id: int):
    _require_demo()
    try:
        with db.session() as conn:
            row = db.restore_reading(conn, reading_id)
    except LookupError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    return {"reading_id": reading_id, "pm25": row["pm25"], "restored": True}


@app.post("/api/demo/batch-now")
def demo_batch_now():
    _require_demo()
    with db.session() as conn:
        simulator.tick(conn)  # make sure there is something fresh to batch
    bid = batcher.close_batch()
    if bid is None:
        return {"batch_id": None, "detail": "nothing to batch"}
    return batch(bid)


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
