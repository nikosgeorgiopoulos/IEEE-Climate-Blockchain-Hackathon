"""Polygon and OpenTimestamps backends, exercised with fakes only (no network, no wallet)."""

import hashlib
import subprocess
from types import SimpleNamespace

import pytest
from opentimestamps.core.notary import BitcoinBlockHeaderAttestation, PendingAttestation
from opentimestamps.core.op import OpSHA256
from opentimestamps.core.serialize import StreamSerializationContext
from opentimestamps.core.timestamp import DetachedTimestampFile, Timestamp

from proofofair import anchor, batcher, db
from proofofair.anchor.ots import OtsBackend, bitcoin_attestation
from proofofair.anchor.polygon import PolygonBackend, fee_params
from proofofair.ingest import simulator
from proofofair.verify import verify_reading

ROOT = hashlib.sha256(b"root").digest()


# ------------------------------------------------------------------ fake web3

class FakeCall:
    def __init__(self, fn):
        self.fn = fn

    def call(self):
        return self.fn()

    def build_transaction(self, params):
        return {"to": "0xContract", "data": "anchor", **params}


class FakeChain:
    """Just enough of web3 + AnchorRegistry for the backend."""

    def __init__(self):
        self.roots = {}
        self.sent = []
        self.block = 1234567
        self.receipt_mode = "ok"  # ok | timeout | revert
        self.pending_tx = None
        self.eth = self
        self.max_priority_fee = 1

    # contract.functions.*
    @property
    def functions(self):
        chain = self

        class F:
            def roots(self, batch_id):
                return FakeCall(lambda: chain.roots.get(batch_id, b"\x00" * 32))

            def anchor(self, batch_id, root):
                chain.pending_tx = (batch_id, root)
                return FakeCall(lambda: None)

        return F()

    # w3.eth.*
    def get_transaction_count(self, addr, which="latest"):
        return len(self.sent)

    def get_block(self, which):
        return {"baseFeePerGas": 10, "timestamp": 1_792_400_000}

    def send_raw_transaction(self, raw):
        self.sent.append(raw)
        return bytes.fromhex("ab" * 32)

    def _receipt(self):
        if self.receipt_mode == "revert":
            return {"status": 0, "blockNumber": self.block}
        batch_id, root = self.pending_tx
        self.roots[batch_id] = root
        return {"status": 1, "blockNumber": self.block}

    def wait_for_transaction_receipt(self, tx, timeout):
        if self.receipt_mode == "timeout":
            raise TimeoutError("TimeExhausted")
        return self._receipt()

    def get_transaction_receipt(self, tx):
        return self._receipt()


class FakeAccount:
    address = "0x000000000000000000000000000000000000dEaD"

    def sign_transaction(self, tx):
        return SimpleNamespace(raw_transaction=b"signed:" + repr(sorted(tx)).encode())


@pytest.fixture
def chain():
    return FakeChain()


@pytest.fixture
def polygon(chain):
    return PolygonBackend(w3=chain, contract=chain, account=FakeAccount())


def test_fee_params_enforce_amoy_minimum_tip(chain):
    fp = fee_params(chain)
    assert fp["maxPriorityFeePerGas"] == 30 * 10**9
    assert fp["maxFeePerGas"] == 20 + 30 * 10**9


def test_polygon_anchor_and_lookup(polygon, chain):
    res = polygon.anchor(ROOT, 7)
    assert res.status == "confirmed" and res.block == 1234567
    assert res.tx == "0x" + "ab" * 32
    assert res.url == "https://amoy.polygonscan.com/tx/0x" + "ab" * 32
    assert len(chain.sent) == 1
    assert polygon.lookup({"id": 7}) == ROOT
    assert polygon.lookup({"id": 8}) is None


def test_polygon_anchor_is_idempotent(polygon, chain):
    polygon.anchor(ROOT, 7)
    again = polygon.anchor(ROOT, 7)
    assert again.status == "confirmed" and len(chain.sent) == 1
    clash = polygon.anchor(hashlib.sha256(b"other").digest(), 7)
    assert clash.status == "failed" and "make deploy" in clash.detail


def test_polygon_slow_receipt_then_refresh(polygon, chain):
    chain.receipt_mode = "timeout"
    res = polygon.anchor(ROOT, 3)
    assert res.status == "submitted" and res.tx
    chain.receipt_mode = "ok"
    done = polygon.refresh({"id": 3, "anchor_status": "submitted", "anchor_tx": res.tx,
                            "merkle_root_hex": ROOT.hex()})
    assert done.status == "confirmed" and done.block == chain.block


def test_polygon_revert_is_failed(polygon, chain):
    chain.receipt_mode = "revert"
    assert polygon.anchor(ROOT, 4).status == "failed"


def test_polygon_without_config_raises_cleanly():
    with pytest.raises(RuntimeError, match="ANCHOR_CONTRACT_ADDRESS"):
        PolygonBackend().anchor(ROOT, 1)


def test_polygon_end_to_end_verify(monkeypatch, polygon):
    """Batch + verify through the real batcher with the fake chain plugged in."""
    monkeypatch.setitem(anchor._cache, "polygon", polygon)
    db.init_db()
    with db.session() as conn:
        simulator.register_devices(conn)
        simulator.tick(conn)
    bid = batcher.close_batch(backend_name="polygon")
    with db.session() as conn:
        b = db.get_batch(conn, bid)
        rid = conn.execute("SELECT MIN(id) FROM readings").fetchone()[0]
    assert b["anchor_status"] == "confirmed"
    assert b["anchor_url"].startswith("https://amoy.polygonscan.com/tx/0x")
    rep = verify_reading(rid)
    assert rep.overall_ok and rep.anchor.explorer_label == "Polygonscan"
    assert rep.anchor.onchain_root == b["merkle_root_hex"]


def test_rpc_down_marks_failed_then_retries(monkeypatch, polygon, chain):
    monkeypatch.setitem(anchor._cache, "polygon", polygon)
    db.init_db()
    with db.session() as conn:
        simulator.register_devices(conn)
        simulator.tick(conn)

    def down(*a, **k):
        raise ConnectionError("rpc down")

    monkeypatch.setattr(chain, "send_raw_transaction", down)
    bid = batcher.close_batch(backend_name="polygon")
    with db.session() as conn:
        assert db.get_batch(conn, bid)["anchor_status"] == "failed"
    monkeypatch.undo()
    monkeypatch.setitem(anchor._cache, "polygon", polygon)
    assert batcher.retry_failed() == 1


# ------------------------------------------------------------------ OpenTimestamps

def write_ots(path, data, height=None):
    ts = Timestamp(hashlib.sha256(data).digest())
    ts.attestations.add(BitcoinBlockHeaderAttestation(height) if height
                        else PendingAttestation("https://alice.btc.calendar.opentimestamps.org"))
    with open(path, "wb") as f:
        DetachedTimestampFile(OpSHA256(), ts).serialize(StreamSerializationContext(f))


class FakeOts:
    """Stands in for the `ots` CLI: stamp -> pending proof, upgrade -> confirmed when told."""

    def __init__(self):
        self.calls = []
        self.confirm_height = None

    def __call__(self, argv, capture_output, text, timeout):
        self.calls.append(argv[1:])
        cmd, target = argv[1], argv[-1]
        if cmd == "stamp":
            with open(target, "rb") as f:
                write_ots(target + ".ots", f.read())
            return subprocess.CompletedProcess(argv, 0, "", "")
        if cmd == "upgrade":
            if self.confirm_height:
                with open(target[:-4], "rb") as f:
                    write_ots(target, f.read(), self.confirm_height)
                return subprocess.CompletedProcess(argv, 0, "", "Success! Timestamp complete")
            return subprocess.CompletedProcess(argv, 1, "", "Failed! Timestamp not complete")
        if cmd == "verify":
            return subprocess.CompletedProcess(argv, 1, "", "Could not connect to Bitcoin node")
        raise AssertionError(argv)


@pytest.fixture
def fake_ots(monkeypatch):
    fake = FakeOts()
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


def test_ots_stamp_pending_then_confirmed(fake_ots, tmp_path):
    ots = OtsBackend(anchors_dir=tmp_path / "anchors", ots_cmd="ots")
    res = ots.anchor(ROOT, 5)
    assert res.status == "pending" and res.url == "/api/batches/5/ots"
    bin_path, ots_path = ots.paths(5)
    assert bin_path.read_bytes() == ROOT and ots_path.exists()
    batch = {"id": 5, "merkle_root_hex": ROOT.hex(), "anchor_status": "pending"}
    assert ots.lookup(batch) is None  # not in Bitcoin yet
    assert ots.refresh(batch).status == "pending"

    fake_ots.confirm_height = 868_000
    res = ots.refresh(batch)
    assert res.status == "confirmed" and res.block == 868_000
    assert ots.lookup(batch) == ROOT


def test_ots_lookup_rejects_swapped_bin(fake_ots, tmp_path):
    ots = OtsBackend(anchors_dir=tmp_path / "anchors", ots_cmd="ots")
    ots.anchor(ROOT, 6)
    fake_ots.confirm_height = 1
    ots.refresh({"id": 6, "merkle_root_hex": ROOT.hex()})
    bin_path, ots_path = ots.paths(6)
    bin_path.write_bytes(b"\x00" * 32)  # attacker rewrites the anchored root file
    assert ots.lookup({"id": 6}) is None
    assert bitcoin_attestation(ots_path, ROOT) == 1


def test_ots_stamp_failure_is_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run",
                        lambda argv, **k: subprocess.CompletedProcess(argv, 1, "", "no calendars"))
    res = OtsBackend(anchors_dir=tmp_path, ots_cmd="ots").anchor(ROOT, 1)
    assert res.status == "failed" and "no calendars" in res.detail


def test_ots_verify_reports_pending_step_5(fake_ots, monkeypatch):
    monkeypatch.setenv("ANCHOR_BACKEND", "ots")
    from proofofair import config

    config.reload_settings()
    db.init_db()
    with db.session() as conn:
        simulator.register_devices(conn)
        simulator.tick(conn)
    bid = batcher.close_batch()
    with db.session() as conn:
        assert db.get_batch(conn, bid)["anchor_status"] == "pending"
        rid = conn.execute("SELECT MIN(id) FROM readings").fetchone()[0]
    rep = verify_reading(rid)
    assert [c.status for c in rep.checks] == ["pass", "pass", "pass", "pass", "pending"]
    assert "Bitcoin" in rep.checks[4].detail
    fake_ots.confirm_height = 870_001
    assert batcher.refresh_pending() == 1
    assert verify_reading(rid).overall_ok


def test_unknown_backend_rejected():
    with pytest.raises(ValueError):
        anchor.get_anchor_backend("ethereum-mainnet")
