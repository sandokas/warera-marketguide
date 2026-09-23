"""Pure normalized market facts. Dictionary keys encode presence; None is explicit null."""
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ScalarField:
    path: str
    value_type: str
    value: str | None


@dataclass(frozen=True)
class TransactionFacts:
    values: dict[str, Any]
    participants: dict[str, dict[str, str | None]] = field(default_factory=dict)
    equipment: dict[str, Any] = field(default_factory=dict)
    stats: dict[str, str | None] = field(default_factory=dict)
    extras: tuple[ScalarField, ...] = ()
    presence: dict[str, bool] = field(default_factory=dict)
    diagnostics: tuple[str, ...] = ()
    normalization_version: int = 1


@dataclass(frozen=True)
class OrderEntry:
    side: str
    position: int
    values: dict[str, Any]
    extras: tuple[ScalarField, ...] = ()
    presence: tuple[str, ...] = ()


@dataclass(frozen=True)
class StreamProgress:
    stream: str
    normalization_version: int = 1
    scan_mode: str = "incremental"
    scan_anchor: str | None = None
    oldest_at: str | None = None
    oldest_id: str | None = None
    newest_at: str | None = None
    newest_id: str | None = None
    attempted_at: str | None = None
    status: str = "running"
    attempts: int = 0
    pages: int = 0
    inserted: int = 0
    enriched: int = 0
    unchanged: int = 0
    rejected: int = 0
    last_error: str | None = None


@dataclass(frozen=True)
class EnrichmentCoverage:
    stream: str
    start_at: str
    end_at: str
    source: str
    completion_reason: str
    observed_at: str
    normalization_version: int = 1


@dataclass(frozen=True)
class OrderLevel:
    price: float
    quantity: float


class RejectedTransactionPage(ValueError):
    """A page was rolled back; count only rows known to have been rejected."""

    def __init__(self, rejected: int):
        super().__init__("Rejected transaction page; no rows committed")
        self.rejected = rejected
