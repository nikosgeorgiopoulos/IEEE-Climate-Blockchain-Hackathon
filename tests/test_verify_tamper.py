"""The heart of the project: editing the database must be detectable."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from proofofair import batcher, db
from proofofair.config import get_settings
from proofofair.ingest import simulator
from proofofair.verify import verify_reading

T0 = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)


@pytest.fixture
def anchored():
    db.init_db()
    with db.session() as conn:
        simulator.register_devices(conn)
        for k in range(3):
            simulator.tick(conn, T0 + timedelta(minutes=5 * k))
    batcher.close_batch()
    with db.session() as conn:
        rid = conn.execute("SELECT id FROM readings ORDER BY id LIMIT 1 OFFSET 5").fetchone()[0]
    return rid


def statuses(rep):
    return [c.status for c in rep.checks]


def raw_sql(sql, params=()):
    """Edit the DB the way an attacker would: plain sqlite3, no app code."""
    conn = sqlite3.connect(get_settings().db_path)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def test_honest_reading_passes_all_five(anchored):
    rep = verify_reading(anchored)
    assert statuses(rep) == ["pass"] * 5
    assert rep.overall_ok and rep.status == "verified"
    assert rep.first_failed_step is None
    assert rep.anchor.tx and rep.anchor.block
    assert rep.anchor.onchain_root == rep.merkle_root
    assert [c.step for c in rep.checks] == [1, 2, 3, 4, 5]


def test_editing_pm25_in_sqlite_turns_check_3_red(anchored):
    before = verify_reading(anchored)
    raw_sql("UPDATE readings SET pm25 = pm25 * 0.3 WHERE id = ?", (anchored,))
    rep = verify_reading(anchored)
    # checks 1-2 are recomputed from the edited columns ...
    assert rep.checks[0].status == "pass"
    assert rep.canonical != before.canonical
    new_pm25 = json.loads(rep.canonical)["pm25"]
    assert float(new_pm25) == pytest.approx(float(json.loads(before.canonical)["pm25"]) * 0.3,
                                            abs=0.01)
    assert rep.checks[1].status == "fail"  # signature no longer covers the data
    # ... and check 3 catches that the data changed after it was batched/anchored
    assert rep.checks[2].status == "fail"
    assert "data changed after batching" in rep.checks[2].detail
    # the anchored batch itself is untouched
    assert rep.checks[3].status == "pass" and rep.checks[4].status == "pass"
    assert not rep.overall_ok and rep.status == "tampered"


def test_insider_who_resigns_is_still_caught_at_step_3(anchored):
    with db.session() as conn:
        db.tamper_reading(conn, anchored, resign=True, now="t")
    rep = verify_reading(anchored)
    assert statuses(rep) == ["pass", "pass", "fail", "pass", "pass"]
    assert rep.first_failed_step == 3


def test_tamper_and_restore_via_helpers(anchored):
    with db.session() as conn:
        orig = db.get_reading(conn, anchored)["pm25"]
        row = db.tamper_reading(conn, anchored, now="t")
        assert row["pm25"] == pytest.approx(round(orig * 0.3, 2))
    assert verify_reading(anchored).first_failed_step == 2
    with db.session() as conn:
        assert db.restore_reading(conn, anchored)["pm25"] == orig
    assert verify_reading(anchored).overall_ok


def test_double_tamper_restores_true_original(anchored):
    with db.session() as conn:
        orig = db.get_reading(conn, anchored)["pm25"]
        db.tamper_reading(conn, anchored, now="t")
        db.tamper_reading(conn, anchored, new_pm25=1.0, now="t")
        assert db.restore_reading(conn, anchored)["pm25"] == orig


def test_editing_timestamp_or_signature_detected(anchored):
    raw_sql("UPDATE readings SET ts = '2026-10-19T09:00:00Z' WHERE id = ?", (anchored,))
    assert not verify_reading(anchored).overall_ok
    raw_sql("UPDATE readings SET ts = '2026-10-19T08:00:00Z' WHERE id = ?", (anchored,))
    assert verify_reading(anchored).overall_ok
    raw_sql("UPDATE readings SET signature_hex = ? WHERE id = ?", ("00" * 64, anchored))
    rep = verify_reading(anchored)
    assert rep.checks[1].status == "fail" and rep.checks[2].status == "fail"


def test_rewriting_the_batch_root_is_caught_by_the_chain(anchored):
    """Attacker recomputes leaf+proof+root in the DB: only the anchor (step 5) disagrees."""
    from proofofair.merkle import build, leaf_data

    with db.session() as conn:
        db.tamper_reading(conn, anchored, resign=True, now="t")
        bid = db.get_reading(conn, anchored)["batch_id"]
        rows = conn.execute("SELECT * FROM readings WHERE batch_id=? ORDER BY id",
                            (bid,)).fetchall()
        tree = build([leaf_data(r["canonical"].encode(), r["signature_hex"]) for r in rows])
        for i, r in enumerate(rows):
            conn.execute("UPDATE readings SET leaf_hash_hex=? WHERE id=?",
                         (tree.leaf_hashes[i].hex(), r["id"]))
            conn.execute("UPDATE proofs SET proof_json=? WHERE reading_id=?",
                         (json.dumps([s.to_dict() for s in tree.proof(i)]), r["id"]))
        conn.execute("UPDATE batches SET merkle_root_hex=? WHERE id=?", (tree.root.hex(), bid))
    rep = verify_reading(anchored)
    assert statuses(rep) == ["pass", "pass", "pass", "pass", "fail"]
    assert "DIFFERENT root" in rep.checks[4].detail


def test_corrupted_proof_fails_step_4(anchored):
    with db.session() as conn:
        p = json.loads(db.get_proof(conn, anchored)["proof_json"])
        p[0]["hash"] = "00" * 32
        conn.execute("UPDATE proofs SET proof_json=? WHERE reading_id=?",
                     (json.dumps(p), anchored))
    assert verify_reading(anchored).first_failed_step == 4


def test_unbatched_reading_is_pending():
    db.init_db()
    with db.session() as conn:
        simulator.register_devices(conn)
        simulator.tick(conn, T0)
        rid = conn.execute("SELECT MIN(id) FROM readings").fetchone()[0]
    rep = verify_reading(rid)
    assert statuses(rep) == ["pass", "pass", "pending", "pending", "pending"]
    assert rep.status == "pending" and not rep.overall_ok
    assert "next batch" in rep.checks[2].detail


def test_unbatched_edit_still_breaks_signature():
    db.init_db()
    with db.session() as conn:
        simulator.register_devices(conn)
        simulator.tick(conn, T0)
        rid = conn.execute("SELECT MIN(id) FROM readings").fetchone()[0]
    raw_sql("UPDATE readings SET pm25 = 1 WHERE id = ?", (rid,))
    assert verify_reading(rid).first_failed_step == 2


def test_missing_reading_raises():
    db.init_db()
    with pytest.raises(KeyError):
        verify_reading(999)
