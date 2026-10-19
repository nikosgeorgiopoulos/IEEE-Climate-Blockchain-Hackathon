"""End-to-end verification of one reading.

Everything is recomputed from the *editable* columns (pm10, pm25, ...), never
from the stored `canonical` string, so editing a value in the database breaks
verification. Each check verifies exactly one link of the chain:

  1. DB columns     -> canonical bytes          (recompute)
  2. canonical      -> signer's public key      (Ed25519)
  3. canonical+sig  -> committed leaf hash      (data unchanged since batching?)
  4. leaf hash      -> batch Merkle root        (inclusion proof)
  5. Merkle root    -> public ledger            (on-chain anchor)
"""

from __future__ import annotations

import json
import sqlite3

from . import crypto, db
from .anchor import get_anchor_backend
from .canonical import canonicalize_row
from .merkle import leaf_data, leaf_hash, verify_proof_hash
from .models import AnchorInfo, Check, ProofStepOut, VerifyReport

NAMES = {
    1: "Recompute canonical record",
    2: "Ed25519 signature",
    3: "Leaf hash matches batch",
    4: "Merkle inclusion proof",
    5: "On-chain anchor",
}


def short(h: str | None, n: int = 10) -> str:
    return f"{h[:n]}…{h[-6:]}" if h and len(h) > n + 6 else (h or "—")


def _pending_detail() -> str:
    from .scheduler import next_batch_eta_text

    return f"pending (next batch {next_batch_eta_text()})"


def verify_reading(reading_id: int, conn: sqlite3.Connection | None = None) -> VerifyReport:
    own = conn is None
    conn = conn or db.connect()
    try:
        return _verify(conn, reading_id)
    finally:
        if own:
            conn.close()


def _verify(conn: sqlite3.Connection, reading_id: int) -> VerifyReport:
    r = db.get_reading(conn, reading_id)
    if r is None:
        raise KeyError(reading_id)
    device = db.get_device(conn, r["device_id"])
    checks: list[Check] = []

    def add(step: int, status: str, detail: str) -> None:
        checks.append(Check(step=step, name=NAMES[step], ok=status == "pass", status=status,
                            detail=detail))

    # 1 — canonical bytes from the current, editable DB columns
    try:
        canonical = canonicalize_row(r)
        add(1, "pass", canonical.decode())
    except (ValueError, TypeError) as e:
        canonical = b""
        add(1, "fail", f"cannot canonicalize row: {e}")

    # 2 — signature over those bytes
    pub = device["pubkey_hex"] if device else None
    signer = device["signer"] if device else None
    if device is None:
        add(2, "fail", f"unknown device {r['device_id']!r}")
    elif crypto.verify(pub, canonical, r["signature_hex"]):
        who = "gateway key (signed at ingestion)" if signer == "gateway" else "device's own key"
        add(2, "pass", f"valid Ed25519 signature by the {who} {short(pub)}")
    else:
        add(2, "fail", "signature does NOT match the data: the record was edited after signing")

    try:
        lh = leaf_hash(leaf_data(canonical, r["signature_hex"])).hex()
    except ValueError:
        lh = None

    report = dict(
        reading_id=r["id"], device_id=r["device_id"], ts=r["ts"], canonical=canonical.decode(),
        signature_hex=r["signature_hex"], pubkey_hex=pub, signer=signer, leaf_hash=lh,
        stored_leaf_hash=r["leaf_hash_hex"], batch_id=r["batch_id"],
    )

    batch = db.get_batch(conn, r["batch_id"]) if r["batch_id"] is not None else None
    proof_row = db.get_proof(conn, r["id"])
    if batch is None or proof_row is None or not r["leaf_hash_hex"]:
        detail = _pending_detail()
        for step in (3, 4, 5):
            add(step, "pending", detail)
        return _finish(report, checks)

    # 3 — does the (recomputed) leaf equal the leaf committed in the batch?
    if lh == r["leaf_hash_hex"]:
        add(3, "pass", f"leaf {short(lh)} is the one committed in batch #{batch['id']}")
    else:
        add(3, "fail", f"data changed after batching: recomputed leaf {short(lh)} ≠ committed "
                       f"leaf {short(r['leaf_hash_hex'])}")

    # 4 — inclusion proof from the committed leaf up to the batch root
    proof = json.loads(proof_row["proof_json"])
    report.update(leaf_index=proof_row["leaf_index"], merkle_root=batch["merkle_root_hex"],
                  proof=[ProofStepOut(**p) for p in proof])
    ok4 = verify_proof_hash(bytes.fromhex(r["leaf_hash_hex"]), proof,
                            bytes.fromhex(batch["merkle_root_hex"]))
    if ok4:
        add(4, "pass", f"{len(proof)} hashes lead from leaf #{proof_row['leaf_index']} "
                       f"to root {short(batch['merkle_root_hex'])} ({batch['n_leaves']} leaves)")
    else:
        add(4, "fail", "the Merkle proof does not reproduce the batch root")

    # 5 — is that root really on the public ledger?
    anchor = AnchorInfo(backend=batch["anchor_backend"], status=batch["anchor_status"],
                        tx=batch["anchor_tx"], url=batch["anchor_url"],
                        block=batch["anchor_block"], anchored_at=batch["anchored_at"])
    try:
        backend = get_anchor_backend(batch["anchor_backend"])
        anchor.explorer_label = backend.explorer_label
        onchain = backend.lookup(batch)
    except Exception as e:
        backend, onchain = None, None
        lookup_error = str(e)
    else:
        lookup_error = None
    anchor.onchain_root = onchain.hex() if onchain else None
    report["anchor"] = anchor
    where = f" in block {batch['anchor_block']}" if batch["anchor_block"] else ""
    if onchain is not None and onchain.hex() == batch["merkle_root_hex"]:
        add(5, "pass", f"root found on {batch['anchor_backend']}{where}, tx {short(anchor.tx)}")
    elif onchain is not None:
        add(5, "fail", f"ledger holds a DIFFERENT root ({short(onchain.hex())}) for this batch")
    elif lookup_error:
        add(5, "fail", f"could not read the ledger: {lookup_error}")
    elif batch["anchor_status"] in ("pending", "submitted"):
        msg = ("waiting for Bitcoin confirmation via OpenTimestamps (usually a few hours)"
               if batch["anchor_backend"] == "ots" else "anchor transaction not confirmed yet")
        add(5, "pending", msg)
    elif batch["anchor_status"] == "failed":
        add(5, "fail", "anchoring failed; it will be retried automatically")
    else:
        add(5, "fail", "root NOT found on the ledger for this batch")
    return _finish(report, checks)


def _finish(report: dict, checks: list[Check]) -> VerifyReport:
    failed = [c.step for c in checks if c.status == "fail"]
    pending = any(c.status == "pending" for c in checks)
    status = "tampered" if failed else ("pending" if pending else "verified")
    return VerifyReport(**report, checks=checks, overall_ok=all(c.ok for c in checks),
                        status=status, first_failed_step=failed[0] if failed else None)


def local_status(row, pubkey_hex: str, proof_json: str | None, root_hex: str | None,
                 anchor_status: str | None) -> str:
    """Cheap offline version of checks 1–4 for map badges: verified|tampered|pending."""
    try:
        canonical = canonicalize_row(row)
    except (ValueError, TypeError):
        return "tampered"
    if not crypto.verify(pubkey_hex, canonical, row["signature_hex"]):
        return "tampered"
    if not row["leaf_hash_hex"] or proof_json is None or root_hex is None:
        return "pending"
    lh = leaf_hash(leaf_data(canonical, row["signature_hex"]))
    if lh.hex() != row["leaf_hash_hex"]:
        return "tampered"
    if not verify_proof_hash(lh, json.loads(proof_json), bytes.fromhex(root_hex)):
        return "tampered"
    return "verified" if anchor_status == "confirmed" else "pending"
