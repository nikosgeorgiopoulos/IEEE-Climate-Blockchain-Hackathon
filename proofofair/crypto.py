"""Ed25519 key management, signing and verification (PyNaCl)."""

from __future__ import annotations

import os
import re
from pathlib import Path

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from .config import get_settings

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def key_path(name: str, keys_dir: str | Path | None = None) -> Path:
    if not _NAME_RE.match(name):
        raise ValueError(f"invalid key name: {name!r}")
    return Path(keys_dir or get_settings().keys_dir) / f"{name}.key"


def load_or_create_key(name: str, keys_dir: str | Path | None = None) -> SigningKey:
    """Load `KEYS_DIR/<name>.key` (hex seed) or create it with mode 600."""
    path = key_path(name, keys_dir)
    if path.exists():
        return SigningKey(bytes.fromhex(path.read_text().strip()))
    path.parent.mkdir(parents=True, exist_ok=True)
    sk = SigningKey.generate()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(sk.encode().hex() + "\n")
    os.chmod(path, 0o600)
    return sk


def pubkey_hex(sk: SigningKey) -> str:
    return sk.verify_key.encode().hex()


def sign(sk: SigningKey, msg: bytes) -> str:
    """Detached Ed25519 signature, hex encoded (128 chars)."""
    return sk.sign(msg).signature.hex()


def verify(pubkey_hex: str, msg: bytes, sig_hex: str) -> bool:
    """True iff `sig_hex` is a valid signature of `msg`. Never raises."""
    try:
        VerifyKey(bytes.fromhex(pubkey_hex)).verify(msg, bytes.fromhex(sig_hex))
        return True
    except (BadSignatureError, ValueError, TypeError):
        return False
    except Exception:  # never let a malformed DB row crash verification
        return False
