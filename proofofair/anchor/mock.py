"""Offline stand-in for a blockchain: an append-only JSON file.

Good enough to develop and demo without a wallet. It is NOT tamper-proof
(anyone can edit the file), which is exactly why the real backends exist.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

from ..config import get_settings
from .base import AnchorResult, utcnow_iso

GENESIS_BLOCK = 1_000_000


class MockBackend:
    name = "mock"
    explorer_label = "mock chain"

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or get_settings().mock_chain_path)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {"chain": "proofofair-mock", "height": GENESIS_BLOCK, "anchors": {}}

    def _save(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(self.path)

    def anchor(self, root: bytes, batch_id: int) -> AnchorResult:
        with self._lock:
            state = self._load()
            key = str(batch_id)
            existing = state["anchors"].get(key)
            if existing and existing["root"] != root.hex():
                return AnchorResult(status="failed", detail=f"batch {batch_id} already anchored")
            if not existing:
                state["height"] += 1
                tx = "0x" + hashlib.sha256(
                    root + batch_id.to_bytes(8, "big") + str(state["height"]).encode()
                ).hexdigest()
                existing = {
                    "root": root.hex(),
                    "tx": tx,
                    "block": state["height"],
                    "timestamp": utcnow_iso(),
                }
                state["anchors"][key] = existing
                self._save(state)
        return AnchorResult(
            status="confirmed",
            tx=existing["tx"],
            url=f"/api/mockchain/{batch_id}",
            block=existing["block"],
            anchored_at=existing["timestamp"],
        )

    def refresh(self, batch) -> AnchorResult:
        return self.anchor(bytes.fromhex(batch["merkle_root_hex"]), batch["id"])

    def lookup(self, batch) -> bytes | None:
        rec = self._load()["anchors"].get(str(batch["id"]))
        return bytes.fromhex(rec["root"]) if rec else None

    def record(self, batch_id: int) -> dict | None:
        return self._load()["anchors"].get(str(batch_id))
