import json
from datetime import UTC, datetime, timedelta

from proofofair import batcher, db
from proofofair.anchor import AnchorResult, get_anchor_backend
from proofofair.ingest import simulator
from proofofair.merkle import leaf_data, leaf_hash, verify_proof_hash

T0 = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)


def seed(ticks=3, start=T0):
    db.init_db()
    with db.session() as conn:
        simulator.register_devices(conn)
        for k in range(ticks):
            simulator.tick(conn, start + timedelta(minutes=5 * k))


def test_no_readings_no_batch():
    db.init_db()
    assert batcher.close_batch() is None
    with db.session() as conn:
        assert db.counts(conn)["batches"] == 0


def test_close_batch_builds_tree_proofs_and_anchors():
    seed(ticks=3)
    bid = batcher.close_batch()
    assert bid == 1
    with db.session() as conn:
        b = db.get_batch(conn, bid)
        assert b["n_leaves"] == 36
        assert b["anchor_status"] == "confirmed"
        assert b["anchor_backend"] == "mock"
        assert b["anchor_tx"].startswith("0x")
        assert b["anchor_block"] > 0
        rows = conn.execute("SELECT * FROM readings ORDER BY id").fetchall()
        for i, r in enumerate(rows):
            assert r["batch_id"] == bid
            lh = leaf_hash(leaf_data(r["canonical"].encode(), r["signature_hex"]))
            assert r["leaf_hash_hex"] == lh.hex()
            p = db.get_proof(conn, r["id"])
            assert p["leaf_index"] == i
            assert verify_proof_hash(lh, json.loads(p["proof_json"]),
                                     bytes.fromhex(b["merkle_root_hex"]))
    assert get_anchor_backend("mock").lookup(b) == bytes.fromhex(b["merkle_root_hex"])


def test_second_batch_only_takes_new_readings():
    seed(ticks=1)
    assert batcher.close_batch() == 1
    assert batcher.close_batch() is None
    seed(ticks=1, start=T0 + timedelta(hours=1))
    assert batcher.close_batch() == 2
    with db.session() as conn:
        assert db.get_batch(conn, 2)["n_leaves"] == 12
        assert db.counts(conn)["unbatched"] == 0


def test_until_limits_the_window():
    seed(ticks=4)  # 08:00 .. 08:15
    bid = batcher.close_batch(until="2026-10-19T08:10:00Z")
    with db.session() as conn:
        b = db.get_batch(conn, bid)
        assert b["n_leaves"] == 24
        assert b["period_end"] < "2026-10-19T08:10:00Z"
        assert db.counts(conn)["unbatched"] == 24


def test_anchor_failure_is_recorded_then_retried(monkeypatch):
    seed(ticks=1)
    backend = get_anchor_backend("mock")
    real = backend.anchor

    def boom(root, batch_id):
        raise ConnectionError("RPC down")

    monkeypatch.setattr(backend, "anchor", boom)
    bid = batcher.close_batch()
    with db.session() as conn:
        assert db.get_batch(conn, bid)["anchor_status"] == "failed"
    monkeypatch.setattr(backend, "anchor", real)
    assert batcher.retry_failed() == 1
    with db.session() as conn:
        assert db.get_batch(conn, bid)["anchor_status"] == "confirmed"


def test_refresh_pending_updates_status(monkeypatch):
    seed(ticks=1)
    backend = get_anchor_backend("mock")
    monkeypatch.setattr(backend, "anchor", lambda root, bid: AnchorResult(status="pending"))
    bid = batcher.close_batch()
    monkeypatch.setattr(backend, "refresh",
                        lambda b: AnchorResult(status="confirmed", block=7, tx="0xabc"))
    assert batcher.refresh_pending() == 1
    with db.session() as conn:
        b = db.get_batch(conn, bid)
        assert (b["anchor_status"], b["anchor_block"], b["anchor_tx"]) == ("confirmed", 7, "0xabc")


def test_simulator_values_in_spec_ranges():
    for dev_id, site in simulator.devices():
        for h in range(0, 24):
            v = simulator.values_at(dev_id, site, T0.replace(hour=h))
            assert 5 <= v["pm25"] <= 60
            assert 10 <= v["pm10"] <= 90


def test_simulator_rush_hour_and_industry_baseline():
    devs = dict((s.slug, (d, s)) for d, s in simulator.devices())

    def day_mean(slug, hours):
        d, s = devs[slug]
        days = [T0 - timedelta(days=k) for k in range(7)]
        vals = [simulator.values_at(d, s, day.replace(hour=h, minute=0)) ["pm25"]
                for day in days for h in hours]
        return sum(vals) / len(vals)

    # Athens is UTC+3 in October: 05:00 UTC = 08:00 local rush hour, 00:00 UTC = 03:00 local
    assert day_mean("patision", [5]) > day_mean("patision", [0]) * 1.3
    assert day_mean("elefsina", range(24)) > day_mean("pallini", range(24)) * 2
