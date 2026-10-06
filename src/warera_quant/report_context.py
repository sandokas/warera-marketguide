"""Frozen UTC timestamp authority for a database report (no database access)."""
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone


def utc(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ReportContext:
    data_as_of: datetime | None
    requested_as_of: datetime | None
    analysis_as_of: datetime | None
    window_end_exclusive: datetime | None
    generated_at: datetime
    boundary_mode: str
    accounting_mode: str = "window"

    def window_start(self, days=7):
        return self.analysis_as_of - timedelta(days=days) if self.analysis_as_of else None

    def to_dict(self):
        return {k: v.isoformat() if isinstance(v, datetime) else v for k, v in asdict(self).items()}

    @classmethod
    def from_dict(cls, values):
        values = dict(values)
        for key in ("data_as_of", "requested_as_of", "analysis_as_of", "window_end_exclusive", "generated_at"):
            if values.get(key) is not None:
                values[key] = utc(datetime.fromisoformat(values[key]))
        return cls(**values)


def resolve_context(data_as_of, requested_as_of=None, *, generated_at=None):
    c = utc(data_as_of) if data_as_of is not None else None
    t = utc(requested_as_of) if requested_as_of is not None else None
    generated = utc(generated_at or datetime.now(timezone.utc))
    if c is None:
        return ReportContext(None, t, None, None, generated, "unavailable")
    historical = t is not None and t <= c
    analysis = t if historical else c
    end = analysis if historical else c + timedelta(microseconds=1)
    return ReportContext(c, t, analysis, end, generated,
                         "historical-exclusive" if historical else "database-inclusive")
