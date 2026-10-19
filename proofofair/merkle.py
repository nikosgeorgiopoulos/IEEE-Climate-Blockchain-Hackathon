"""RFC 6962-style Merkle tree with domain separation.

leaf_hash(d)    = sha256(0x00 || d)
node_hash(l, r) = sha256(0x01 || l || r)

At a level with an odd number of nodes the last node is promoted unchanged
(never duplicated), which gives the same root as RFC 6962 and avoids the
CVE-2012-2459 duplicate-leaf ambiguity.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


def leaf_hash(data: bytes) -> bytes:
    return hashlib.sha256(b"\x00" + data).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


@dataclass(frozen=True)
class ProofStep:
    pos: str  # position of the SIBLING: "L" or "R"
    hash: bytes

    def to_dict(self) -> dict[str, str]:
        return {"pos": self.pos, "hash": self.hash.hex()}

    @classmethod
    def from_any(cls, step: Any) -> ProofStep:
        if isinstance(step, ProofStep):
            return step
        h = step["hash"]
        return cls(pos=step["pos"], hash=bytes.fromhex(h) if isinstance(h, str) else h)


class MerkleTree:
    def __init__(self, leaves: list[bytes]):
        if not leaves:
            raise ValueError("cannot build a Merkle tree with no leaves")
        level = [leaf_hash(d) for d in leaves]
        self.levels: list[list[bytes]] = [level]
        while len(level) > 1:
            nxt = [node_hash(level[i], level[i + 1]) for i in range(0, len(level) - 1, 2)]
            if len(level) % 2 == 1:
                nxt.append(level[-1])  # promote
            self.levels.append(nxt)
            level = nxt

    @property
    def root(self) -> bytes:
        return self.levels[-1][0]

    @property
    def leaf_hashes(self) -> list[bytes]:
        return self.levels[0]

    def __len__(self) -> int:
        return len(self.levels[0])

    def proof(self, index: int) -> list[ProofStep]:
        if not 0 <= index < len(self):
            raise IndexError(index)
        steps: list[ProofStep] = []
        i = index
        for level in self.levels[:-1]:
            sib = i ^ 1
            if sib < len(level):  # a promoted node has no sibling at this level
                steps.append(ProofStep("L" if sib < i else "R", level[sib]))
            i //= 2
        return steps


def build(leaves: list[bytes]) -> MerkleTree:
    return MerkleTree(leaves)


def root_from_leaf_hash(lh: bytes, proof) -> bytes:
    h = lh
    for raw in proof:
        step = ProofStep.from_any(raw)
        if step.pos == "L":
            h = node_hash(step.hash, h)
        elif step.pos == "R":
            h = node_hash(h, step.hash)
        else:
            raise ValueError(f"bad proof position: {step.pos!r}")
    return h


def verify_proof_hash(lh: bytes, proof, root: bytes) -> bool:
    try:
        return root_from_leaf_hash(lh, proof) == root
    except (ValueError, KeyError, TypeError):
        return False


def verify_proof(leaf: bytes, proof, root: bytes) -> bool:
    """Verify that leaf *data* is committed under `root` via `proof`."""
    return verify_proof_hash(leaf_hash(leaf), proof, root)


def leaf_data(canonical: bytes, signature_hex: str) -> bytes:
    """What goes into a leaf: canonical bytes followed by the raw signature."""
    return canonical + bytes.fromhex(signature_hex)
