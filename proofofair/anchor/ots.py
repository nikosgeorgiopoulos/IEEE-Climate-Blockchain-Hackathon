"""Anchor Merkle roots in Bitcoin via OpenTimestamps (no wallet, no fees).

`ots stamp` submits the root to public calendar servers right away; the
proof only becomes a complete Bitcoin attestation after a few hours, when
`ots upgrade` succeeds. Until then the batch stays `pending`.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

from ..config import get_settings
from .base import AnchorResult, utcnow_iso

log = logging.getLogger(__name__)


def _ots_cmd() -> str:
    local = Path(sys.executable).parent / "ots"
    return shutil.which("ots") or str(local)


def bitcoin_attestation(ots_path: Path, data: bytes) -> int | None:
    """Block height of a Bitcoin attestation in `ots_path` for exactly `data`, else None.

    Structural check (no Bitcoin node needed): the proof must commit to
    sha256(data) and contain a BitcoinBlockHeaderAttestation.
    """
    from opentimestamps.core.notary import BitcoinBlockHeaderAttestation
    from opentimestamps.core.serialize import StreamDeserializationContext
    from opentimestamps.core.timestamp import DetachedTimestampFile

    try:
        with open(ots_path, "rb") as f:
            dtf = DetachedTimestampFile.deserialize(StreamDeserializationContext(f))
    except Exception as e:
        log.info("cannot parse %s: %s", ots_path, e)
        return None
    if dtf.file_digest != hashlib.sha256(data).digest():
        return None
    heights = [a.height for _, a in dtf.timestamp.all_attestations()
               if isinstance(a, BitcoinBlockHeaderAttestation)]
    return min(heights) if heights else None


class OtsBackend:
    name = "ots"
    explorer_label = "OpenTimestamps"

    def __init__(self, anchors_dir: str | Path | None = None, ots_cmd: str | None = None):
        self.dir = Path(anchors_dir or get_settings().anchors_dir)
        self.cmd = ots_cmd or _ots_cmd()

    def paths(self, batch_id: int) -> tuple[Path, Path]:
        b = self.dir / f"batch_{batch_id}.bin"
        return b, Path(str(b) + ".ots")

    def _run(self, *args: str, timeout: int = 90) -> subprocess.CompletedProcess:
        return subprocess.run([self.cmd, *args], capture_output=True, text=True, timeout=timeout)

    def _url(self, batch_id: int) -> str:
        return f"/api/batches/{batch_id}/ots"

    def anchor(self, root: bytes, batch_id: int) -> AnchorResult:
        self.dir.mkdir(parents=True, exist_ok=True)
        bin_path, ots_path = self.paths(batch_id)
        if bin_path.exists() and bin_path.read_bytes() != root:
            return AnchorResult(status="failed", detail=f"{bin_path} holds a different root")
        bin_path.write_bytes(root)
        if not ots_path.exists():
            proc = self._run("stamp", str(bin_path))
            if proc.returncode != 0 or not ots_path.exists():
                return AnchorResult(status="failed",
                                    detail=(proc.stderr or proc.stdout).strip()[-300:])
        return AnchorResult(status="pending", tx=f"sha256:{hashlib.sha256(root).hexdigest()}",
                            url=self._url(batch_id),
                            detail="submitted to OpenTimestamps; Bitcoin confirmation takes hours")

    def refresh(self, batch) -> AnchorResult:
        bin_path, ots_path = self.paths(batch["id"])
        if not ots_path.exists():
            return self.anchor(bytes.fromhex(batch["merkle_root_hex"]), batch["id"])
        proc = self._run("upgrade", str(ots_path))
        height = bitcoin_attestation(ots_path, bin_path.read_bytes())
        if height is not None:
            return AnchorResult(status="confirmed", url=self._url(batch["id"]), block=height,
                                anchored_at=utcnow_iso())
        log.info("batch %s still pending in OTS: %s", batch["id"],
                 (proc.stderr or proc.stdout).strip()[-200:])
        return AnchorResult(status="pending")

    def lookup(self, batch) -> bytes | None:
        bin_path, ots_path = self.paths(batch["id"])
        if not (bin_path.exists() and ots_path.exists()):
            return None
        data = bin_path.read_bytes()
        try:
            proc = self._run("verify", "-f", str(bin_path), str(ots_path), timeout=60)
            if proc.returncode == 0 and re.search(r"Success!", proc.stdout + proc.stderr):
                return data
        except (OSError, subprocess.TimeoutExpired) as e:
            log.info("ots verify unavailable: %s", e)
        # `ots verify` needs a local Bitcoin node; fall back to checking the proof's structure.
        return data if bitcoin_attestation(ots_path, data) is not None else None
