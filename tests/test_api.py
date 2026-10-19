from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from proofofair import batcher, config, db
from proofofair.canonical import canonicalize
from proofofair.crypto import sign
from proofofair.ingest import simulator
from proofofair.ingest.device_endpoint import register_device

T0 = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)


@pytest.fixture
def client():
    from proofofair.api import app

    with TestClient(app) as c:  # runs the lifespan (init_db, devices) — scheduler is off
        yield c


@pytest.fixture
def seeded(client):
    with db.session() as conn:
        for k in range(4):
            simulator.tick(conn, T0 + timedelta(minutes=5 * k))
    batcher.close_batch()
    with db.session() as conn:
        simulator.tick(conn, T0 + timedelta(minutes=30))  # one unbatched round
        rid = conn.execute("SELECT MIN(id) FROM readings WHERE device_id='sim-07'").fetchone()[0]
    return rid


def test_health(client, seeded):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["backend"] == "mock"
    assert h["counts"]["readings"] == 60 and h["counts"]["unbatched"] == 12
    assert h["counts"]["anchored"] == 1
    assert h["last_anchor"]["id"] == 1
    assert h["devices_by_source"]["simulator"] == 12


def test_devices_have_latest_and_ratio(client, seeded):
    devs = client.get("/api/devices").json()
    assert len(devs) >= 12
    d = next(x for x in devs if x["id"] == "sim-07")
    assert d["source"] == "simulator" and "Elefsina" in d["name"]
    assert d["latest"]["ts"] == "2026-10-19T08:30:00Z"
    assert d["n_readings"] == 5 and d["n_verified"] == 4 and d["n_pending"] == 1
    assert d["verified_ratio"] == 0.8
    assert d["status"] == "verified"


def test_device_readings_series(client, seeded):
    r = client.get("/api/devices/sim-01/readings?hours=24").json()
    assert [x["ts"] for x in r["readings"]][:2] == ["2026-10-19T08:00:00Z",
                                                    "2026-10-19T08:05:00Z"]
    assert r["readings"][-1]["status"] == "pending"
    anchored = client.get("/api/devices/sim-01/readings?anchored_only=true").json()["readings"]
    assert len(anchored) == 4 and all(x["anchor_status"] == "confirmed" for x in anchored)
    assert client.get("/api/devices/nope/readings").status_code == 404


def test_reading_and_verify(client, seeded):
    r = client.get(f"/api/readings/{seeded}").json()
    assert r["canonical"].startswith('{"device_id":"sim-07"')
    assert len(r["signature_hex"]) == 128 and r["leaf_index"] is not None
    v = client.get(f"/api/readings/{seeded}/verify").json()
    assert v["overall_ok"] is True and v["status"] == "verified"
    assert [c["status"] for c in v["checks"]] == ["pass"] * 5
    assert v["anchor"]["url"] == "/api/mockchain/1"
    assert client.get(v["anchor"]["url"]).json()["root"] == v["merkle_root"]
    assert client.get("/api/readings/999999/verify").status_code == 404


def test_batches(client, seeded):
    b = client.get("/api/batches").json()
    assert b["total"] == 1
    assert b["batches"][0]["anchor_status"] == "confirmed"
    assert b["batches"][0]["explorer_label"] == "mock chain"
    d = client.get("/api/batches/1").json()
    assert d["leaf_count"] == d["n_leaves"] == 48 and d["device_count"] == 12
    assert client.get("/api/batches/42").status_code == 404


def test_tamper_lab_flow(client, seeded):
    orig = client.get(f"/api/readings/{seeded}").json()["pm25"]
    t = client.post(f"/api/demo/tamper/{seeded}").json()
    assert t["original_pm25"] == orig and t["pm25"] == pytest.approx(round(orig * 0.3, 2))
    v = client.get(f"/api/readings/{seeded}/verify").json()
    assert v["overall_ok"] is False and v["status"] == "tampered"
    assert v["checks"][2]["status"] == "fail"
    assert "data changed after batching" in v["checks"][2]["detail"]
    dev = next(x for x in client.get("/api/devices").json() if x["id"] == "sim-07")
    assert dev["status"] == "tampered" and dev["n_tampered"] == 1
    row = client.get(f"/api/readings/{seeded}").json()
    assert row["tampered"] and row["original_pm25"] == orig and row["status"] == "tampered"

    assert client.post(f"/api/demo/restore/{seeded}").json()["pm25"] == orig
    assert client.get(f"/api/readings/{seeded}/verify").json()["overall_ok"] is True
    assert client.post(f"/api/demo/restore/{seeded}").status_code == 409


def test_insider_tamper_fails_only_step_3(client, seeded):
    client.post(f"/api/demo/tamper/{seeded}", json={"resign": True, "pm25": 9.99})
    v = client.get(f"/api/readings/{seeded}/verify").json()
    assert [c["status"] for c in v["checks"]] == ["pass", "pass", "fail", "pass", "pass"]
    assert v["first_failed_step"] == 3


def test_tamper_unknown_reading(client, seeded):
    assert client.post("/api/demo/tamper/999999").status_code == 404


def test_batch_now(client, seeded):
    r = client.post("/api/demo/batch-now").json()
    assert r["id"] == 2 and r["anchor_status"] == "confirmed"
    assert r["n_leaves"] == 24  # the unbatched round + the fresh tick


def test_demo_endpoints_disabled(client, seeded, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "false")
    config.reload_settings()
    assert client.post(f"/api/demo/tamper/{seeded}").status_code == 403
    assert client.post(f"/api/demo/restore/{seeded}").status_code == 403
    assert client.post("/api/demo/batch-now").status_code == 403


def test_device_ingest(client):
    with db.session() as conn:
        seed_hex = register_device(conn, "esp32-01", "ESP32 test node", 37.979, 23.783)
    sk = SigningKey(bytes.fromhex(seed_hex))
    ts = "2026-10-19T11:05:00Z"
    msg = canonicalize("esp32-01", ts, 21.4, 12.35, 23.5, 61.0)
    body = {"device_id": "esp32-01", "ts": ts, "pm10": "21.40", "pm25": 12.35,
            "temperature": 23.5, "humidity": "61.00", "signature_hex": sign(sk, msg)}
    r = client.post("/api/ingest/device", json=body)
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    v = client.get(f"/api/readings/{rid}/verify").json()
    assert v["checks"][1]["status"] == "pass" and "device's own key" in v["checks"][1]["detail"]
    assert client.post("/api/ingest/device", json=body).status_code == 409

    forged = dict(body, pm25=1.0, ts="2026-10-19T11:10:00Z")
    assert client.post("/api/ingest/device", json=forged).status_code == 400
    unknown = dict(body, device_id="esp32-99")
    assert client.post("/api/ingest/device", json=unknown).status_code == 404
    gw = dict(body, device_id="sc-1")
    with db.session() as conn:
        db.upsert_device(conn, "sc-1", "sensor_community", "00" * 32, "gateway")
    assert client.post("/api/ingest/device", json=gw).status_code == 403
    bad = dict(body, ts="yesterday")
    assert client.post("/api/ingest/device", json=bad).status_code == 422


def test_static_frontend_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "ProofOfAir" in r.text


def test_ots_download_and_mockchain_404(client, seeded):
    assert client.get("/api/batches/1/ots").status_code == 404
    assert client.get("/api/mockchain/999").status_code == 404
    from proofofair.anchor.ots import OtsBackend

    _, ots_path = OtsBackend().paths(1)
    ots_path.parent.mkdir(parents=True, exist_ok=True)
    ots_path.write_bytes(b"\x00OpenTimestamps")
    r = client.get("/api/batches/1/ots")
    assert r.status_code == 200 and r.content == b"\x00OpenTimestamps"
