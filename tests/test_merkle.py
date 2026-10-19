import hashlib

import pytest

from proofofair.merkle import (
    ProofStep,
    build,
    leaf_hash,
    node_hash,
    root_from_leaf_hash,
    verify_proof,
)


def leaves(n):
    return [f"reading-{i}".encode() for i in range(n)]


def rfc6962_root(data):
    """Reference MTH from RFC 6962 §2.1 (split at largest power of two < n)."""
    n = len(data)
    if n == 1:
        return leaf_hash(data[0])
    k = 1
    while k * 2 < n:
        k *= 2
    return node_hash(rfc6962_root(data[:k]), rfc6962_root(data[k:]))


@pytest.mark.parametrize("n", [1, 2, 3, 7, 1000])
def test_every_proof_verifies(n):
    data = leaves(n)
    tree = build(data)
    for i, d in enumerate(data):
        assert verify_proof(d, tree.proof(i), tree.root)


@pytest.mark.parametrize("n", [1, 2, 3, 7, 1000])
def test_wrong_leaf_fails(n):
    data = leaves(n)
    tree = build(data)
    i = n // 2
    assert not verify_proof(b"forged", tree.proof(i), tree.root)


@pytest.mark.parametrize("n", list(range(1, 65)) + [1000])
def test_matches_rfc6962_reference(n):
    assert build(leaves(n)).root == rfc6962_root(leaves(n))


def test_single_leaf_root_is_leaf_hash():
    tree = build([b"x"])
    assert tree.root == leaf_hash(b"x")
    assert tree.proof(0) == []


def test_three_leaves_promote_not_duplicate():
    a, b, c = leaves(3)
    tree = build([a, b, c])
    assert tree.root == node_hash(node_hash(leaf_hash(a), leaf_hash(b)), leaf_hash(c))
    # leaf 2 is promoted at level 0, so its proof has a single step
    assert len(tree.proof(2)) == 1
    ab = node_hash(leaf_hash(a), leaf_hash(b))
    dup_root = node_hash(ab, node_hash(leaf_hash(c), leaf_hash(c)))
    assert tree.root != dup_root


def test_domain_separation():
    assert leaf_hash(b"x") != hashlib.sha256(b"x").digest()
    left, right = leaf_hash(b"l"), leaf_hash(b"r")
    # an inner node can't be passed off as a leaf
    assert leaf_hash(left + right) != node_hash(left, right)


def test_tampered_proof_and_root_fail():
    data = leaves(7)
    tree = build(data)
    proof = tree.proof(3)
    bad = [ProofStep(s.pos, s.hash) for s in proof]
    bad[0] = ProofStep(bad[0].pos, b"\x00" * 32)
    assert not verify_proof(data[3], bad, tree.root)
    flipped = [ProofStep("R" if s.pos == "L" else "L", s.hash) for s in proof]
    assert not verify_proof(data[3], flipped, tree.root)
    assert not verify_proof(data[3], proof, b"\x11" * 32)
    assert not verify_proof(data[3], [{"pos": "X", "hash": "00"}], tree.root)


def test_proof_dict_roundtrip():
    data = leaves(5)
    tree = build(data)
    as_json = [s.to_dict() for s in tree.proof(4)]
    assert root_from_leaf_hash(leaf_hash(data[4]), as_json) == tree.root


def test_empty_tree_rejected():
    with pytest.raises(ValueError):
        build([])
