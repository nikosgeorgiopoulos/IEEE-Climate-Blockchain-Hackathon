"""The interface every anchoring backend implements."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable


def utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class AnchorResult:
    status: str  # pending | submitted | confirmed | failed
    tx: str | None = None
    url: str | None = None
    block: int | None = None
    anchored_at: str | None = None
    detail: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AnchorBackend(Protocol):
    name: str
    #: human label for links in the UI, e.g. "Polygonscan"
    explorer_label: str

    def anchor(self, root: bytes, batch_id: int) -> AnchorResult:
        """Publish `root` for `batch_id`."""
        ...

    def refresh(self, batch) -> AnchorResult:
        """Re-check a pending/submitted anchor (OTS upgrade, receipt polling)."""
        ...

    def lookup(self, batch) -> bytes | None:
        """Return the root that the public ledger holds for this batch, or None."""
        ...
