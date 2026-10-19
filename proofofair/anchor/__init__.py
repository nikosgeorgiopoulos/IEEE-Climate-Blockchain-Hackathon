"""Anchor backends: mock (offline), polygon (Amoy testnet) and ots (Bitcoin)."""

from __future__ import annotations

import threading

from ..config import get_settings
from .base import AnchorBackend, AnchorResult

BACKENDS = ("mock", "polygon", "ots")
_cache: dict[str, AnchorBackend] = {}
_lock = threading.Lock()


def get_anchor_backend(name: str | None = None) -> AnchorBackend:
    name = (name or get_settings().anchor_backend).strip().lower()
    with _lock:
        if name not in _cache:
            if name == "mock":
                from .mock import MockBackend

                _cache[name] = MockBackend()
            elif name == "polygon":
                from .polygon import PolygonBackend

                _cache[name] = PolygonBackend()
            elif name == "ots":
                from .ots import OtsBackend

                _cache[name] = OtsBackend()
            else:
                raise ValueError(f"unknown ANCHOR_BACKEND {name!r}; use one of {BACKENDS}")
        return _cache[name]


def reset_backends() -> None:
    with _lock:
        _cache.clear()


__all__ = ["AnchorBackend", "AnchorResult", "get_anchor_backend", "reset_backends", "BACKENDS"]
