"""Close a batch: Merkle-tree all unbatched readings, store proofs, anchor the root."""

from __future__ import annotations

import json
import logging
import threading

from . import db
from .anchor import AnchorResult, get_anchor_backend
from .anchor.base import utcnow_iso
from .merkle import build, leaf_data

log = logging.getLogger(__name__)
_lock = threading.Lock()  # scheduler and /api/demo/batch-now must not race


def _store_anchor(conn, batch_id: int, res: AnchorResult) -> None:
    conn.execute(
        """UPDATE batches SET anchor_status = ?, anchor_tx = COALESCE(?, anchor_tx),
             anchor_url = COALESCE(?, anchor_url), anchor_block = COALESCE(?, anchor_block),
             anchored_at = COALESCE(?, anchored_at)
           WHERE id = ?""",
        (res.status, res.tx, res.url, res.block, res.anchored_at, batch_id),
    )


def _anchor(conn, batch_id: int, root: bytes, backend_name: str) -> AnchorResult:
    try:
        res = get_anchor_backend(backend_name).anchor(root, batch_id)
    except Exception as e:  # network/RPC errors must never kill the scheduler
        log.warning("anchoring batch %s via %s failed: %s", batch_id, backend_name, e)
        res = AnchorResult(status="failed", detail=str(e))
    _store_anchor(conn, batch_id, res)
    conn.commit()
    log.info("batch %s anchor status=%s tx=%s", batch_id, res.status, res.tx)
    return res


def close_batch(until: str | None = None, backend_name: str | None = None) -> int | None:
    """Batch every reading with batch_id IS NULL (and ts < `until`, if given).

    Returns the new batch id, or None when there was nothing to batch.
    """
    backend_name = backend_name or get_anchor_backend().name
    with _lock:
        conn = db.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            sql = "SELECT * FROM readings WHERE batch_id IS NULL"
            params: tuple = ()
            if until:
                sql += " AND ts < ?"
                params = (until,)
            rows = conn.execute(sql + " ORDER BY id", params).fetchall()
            if not rows:
                conn.rollback()
                return None

            tree = build([leaf_data(r["canonical"].encode(), r["signature_hex"]) for r in rows])
            cur = conn.execute(
                """INSERT INTO batches (created_at, period_start, period_end, n_leaves,
                     merkle_root_hex, anchor_backend, anchor_status)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
                (
                    utcnow_iso(),
                    min(r["ts"] for r in rows),
                    max(r["ts"] for r in rows),
                    len(rows),
                    tree.root.hex(),
                    backend_name,
                ),
            )
            batch_id = cur.lastrowid
            for i, r in enumerate(rows):
                conn.execute(
                    "UPDATE readings SET leaf_hash_hex = ?, batch_id = ? WHERE id = ?",
                    (tree.leaf_hashes[i].hex(), batch_id, r["id"]),
                )
                conn.execute(
                    "INSERT INTO proofs (reading_id, leaf_index, proof_json) VALUES (?, ?, ?)",
                    (r["id"], i, json.dumps([s.to_dict() for s in tree.proof(i)])),
                )
            conn.commit()
            log.info("closed batch %s with %s readings, root %s", batch_id, len(rows),
                     tree.root.hex())
            _anchor(conn, batch_id, tree.root, backend_name)
            return batch_id
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def retry_failed() -> int:
    """Re-anchor batches whose anchoring failed (e.g. RPC down). Returns # confirmed now."""
    fixed = 0
    with db.session() as conn:
        rows = conn.execute("SELECT * FROM batches WHERE anchor_status = 'failed'").fetchall()
        for b in rows:
            res = _anchor(conn, b["id"], bytes.fromhex(b["merkle_root_hex"]), b["anchor_backend"])
            fixed += res.status == "confirmed"
    return fixed


def refresh_pending() -> int:
    """Ask the backend about pending/submitted anchors (OTS upgrade, late receipts)."""
    updated = 0
    with db.session() as conn:
        rows = conn.execute(
            "SELECT * FROM batches WHERE anchor_status IN ('pending', 'submitted')"
        ).fetchall()
        for b in rows:
            try:
                res = get_anchor_backend(b["anchor_backend"]).refresh(b)
            except Exception as e:
                log.warning("refresh of batch %s failed: %s", b["id"], e)
                continue
            if res.status != b["anchor_status"]:
                updated += 1
            _store_anchor(conn, b["id"], res)
    return updated
