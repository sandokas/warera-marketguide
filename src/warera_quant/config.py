from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path("marketguide.toml")


class ConfigError(ValueError):
    """Raised when the application configuration is invalid."""


@dataclass(frozen=True)
class HousekeepingConfig:
    enabled: bool = True
    retention_days: int = 45
    vacuum_interval_days: int = 30


@dataclass(frozen=True)
class InflationConfig:
    """Stable inflation-index vintage configuration.

    ``base_period_start`` is always midnight at the start of the configured
    calendar date in UTC, regardless of whether TOML supplied a date or string.
    """

    enabled: bool = True
    base_period_start: datetime = datetime(2026, 8, 1, tzinfo=timezone.utc)
    version: str = "2026-08-01-v1"
    price_window_days: int = 7
    min_base_trade_count: int = 1
    min_base_traded_quantity: float = 1e-12
    events: tuple[InflationEventConfig, ...] = ()


@dataclass(frozen=True)
class InflationEventConfig:
    """A sourced structural change that may affect an inflation index."""

    event_id: str
    label: str
    status: str
    environment: str
    announced_at: datetime | None = None
    effective_at: datetime | None = None
    affected_index_keys: tuple[str, ...] = ()
    source_url: str = ""
    source_published_at: datetime | None = None
    details: str | None = None


@dataclass(frozen=True)
class AppConfig:
    housekeeping: HousekeepingConfig = HousekeepingConfig()
    inflation: InflationConfig = InflationConfig()


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        return AppConfig()

    try:
        with config_path.open("rb") as config_file:
            raw = tomllib.load(config_file)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {config_path}: {exc}") from exc

    housekeeping = raw.get("housekeeping", {})
    if not isinstance(housekeeping, dict):
        raise ConfigError("[housekeeping] must be a TOML table.")

    allowed_keys = {"enabled", "retention_days", "vacuum_interval_days"}
    unknown_keys = set(housekeeping) - allowed_keys
    if unknown_keys:
        names = ", ".join(sorted(unknown_keys))
        raise ConfigError(f"Unknown housekeeping setting(s): {names}.")

    enabled = housekeeping.get("enabled", True)
    retention_days = housekeeping.get("retention_days", 45)
    vacuum_interval_days = housekeeping.get("vacuum_interval_days", 30)
    if not isinstance(enabled, bool):
        raise ConfigError("housekeeping.enabled must be true or false.")
    _require_int(retention_days, "housekeeping.retention_days", minimum=1)
    _require_int(vacuum_interval_days, "housekeeping.vacuum_interval_days", minimum=0)

    inflation = raw.get("inflation", {})
    if not isinstance(inflation, dict):
        raise ConfigError("[inflation] must be a TOML table.")

    inflation_allowed_keys = {
        "enabled",
        "base_period_start",
        "version",
        "price_window_days",
        "min_base_trade_count",
        "min_base_traded_quantity",
        "events",
    }
    unknown_inflation_keys = set(inflation) - inflation_allowed_keys
    if unknown_inflation_keys:
        names = ", ".join(sorted(unknown_inflation_keys))
        raise ConfigError(f"Unknown inflation setting(s): {names}.")

    inflation_enabled = inflation.get("enabled", True)
    if not isinstance(inflation_enabled, bool):
        raise ConfigError("inflation.enabled must be true or false.")
    base_period_start = _parse_utc_date(
        inflation.get("base_period_start", "2026-08-01"),
        "inflation.base_period_start",
    )
    version = inflation.get("version", "2026-08-01-v1")
    if not isinstance(version, str) or not version.strip():
        raise ConfigError("inflation.version must be a nonblank string.")
    price_window_days = inflation.get("price_window_days", 7)
    min_base_trade_count = inflation.get("min_base_trade_count", 1)
    min_base_traded_quantity = inflation.get("min_base_traded_quantity", 1e-12)
    events = _parse_inflation_events(inflation.get("events", []))
    _require_int(price_window_days, "inflation.price_window_days", minimum=1)
    _require_int(min_base_trade_count, "inflation.min_base_trade_count", minimum=1)
    if (
        isinstance(min_base_traded_quantity, bool)
        or not isinstance(min_base_traded_quantity, (int, float))
        or not math.isfinite(min_base_traded_quantity)
        or min_base_traded_quantity <= 0
    ):
        raise ConfigError("inflation.min_base_traded_quantity must be a finite positive number.")

    return AppConfig(
        housekeeping=HousekeepingConfig(
            enabled=enabled,
            retention_days=retention_days,
            vacuum_interval_days=vacuum_interval_days,
        ),
        inflation=InflationConfig(
            enabled=inflation_enabled,
            base_period_start=base_period_start,
            version=version.strip(),
            price_window_days=price_window_days,
            min_base_trade_count=min_base_trade_count,
            min_base_traded_quantity=float(min_base_traded_quantity),
            events=events,
        ),
    )


def production_inflation_chart_events(
    events: tuple[InflationEventConfig, ...],
    *,
    index_key: str | None = None,
) -> tuple[dict[str, datetime | str], ...]:
    """Convert eligible production events to the mapping accepted by charts.

    Non-live, development, undated, and unsourced records intentionally cannot
    annotate production history.
    """
    return tuple(
        {"at": event.effective_at, "label": event.label}
        for event in events
        if event.status == "live"
        and event.environment == "production"
        and event.effective_at is not None
        and event.source_url.strip()
        and (index_key is None or index_key in event.affected_index_keys)
    )


def _parse_inflation_events(value: Any) -> tuple[InflationEventConfig, ...]:
    if not isinstance(value, list):
        raise ConfigError("inflation.events must be an array of TOML tables.")
    events: list[InflationEventConfig] = []
    seen_ids: set[str] = set()
    allowed = {
        "event_id", "label", "status", "environment", "announced_at",
        "effective_at", "affected_index_keys", "source_url",
        "source_published_at", "details",
    }
    for position, raw_event in enumerate(value, start=1):
        prefix = f"inflation.events[{position}]"
        if not isinstance(raw_event, dict):
            raise ConfigError(f"{prefix} must be a TOML table.")
        unknown = set(raw_event) - allowed
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ConfigError(f"Unknown {prefix} setting(s): {names}.")
        event_id = _require_nonblank(raw_event.get("event_id"), f"{prefix}.event_id")
        if event_id in seen_ids:
            raise ConfigError(f"Duplicate inflation event_id: {event_id}.")
        seen_ids.add(event_id)
        label = _require_nonblank(raw_event.get("label"), f"{prefix}.label")
        status = _require_choice(
            raw_event.get("status"), f"{prefix}.status",
            {"planned", "announced", "live", "retired"},
        )
        environment = _require_choice(
            raw_event.get("environment"), f"{prefix}.environment", {"dev", "production"},
        )
        affected = raw_event.get("affected_index_keys")
        if not isinstance(affected, list) or not affected:
            raise ConfigError(f"{prefix}.affected_index_keys must be a non-empty array of strings.")
        affected_keys = tuple(
            _require_nonblank(item, f"{prefix}.affected_index_keys") for item in affected
        )
        if len(set(affected_keys)) != len(affected_keys):
            raise ConfigError(f"{prefix}.affected_index_keys must not contain duplicates.")
        source_url = raw_event.get("source_url", "")
        if not isinstance(source_url, str):
            raise ConfigError(f"{prefix}.source_url must be a string.")
        details = raw_event.get("details")
        if details is not None:
            details = _require_nonblank(details, f"{prefix}.details")
        event = InflationEventConfig(
            event_id=event_id,
            label=label,
            status=status,
            environment=environment,
            announced_at=_parse_optional_utc_datetime(raw_event.get("announced_at"), f"{prefix}.announced_at"),
            effective_at=_parse_optional_utc_datetime(raw_event.get("effective_at"), f"{prefix}.effective_at"),
            affected_index_keys=affected_keys,
            source_url=source_url.strip(),
            source_published_at=_parse_optional_utc_datetime(
                raw_event.get("source_published_at"), f"{prefix}.source_published_at"
            ),
            details=details,
        )
        if event.environment == "dev" and event.status in {"live", "retired"}:
            raise ConfigError(f"{prefix}: dev events cannot have status {event.status!r}.")
        if event.status == "live" and event.effective_at is None:
            raise ConfigError(f"{prefix}.effective_at is required when status is live.")
        events.append(event)
    return tuple(events)


def _require_nonblank(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a nonblank string.")
    return value.strip()


def _require_choice(value: Any, name: str, choices: set[str]) -> str:
    parsed = _require_nonblank(value, name)
    if parsed not in choices:
        expected = ", ".join(sorted(choices))
        raise ConfigError(f"{name} must be one of: {expected}.")
    return parsed


def _parse_optional_utc_datetime(value: Any, name: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, datetime)):
        raise ConfigError(f"{name} must be an ISO 8601 UTC timestamp.")
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ConfigError(f"{name} must be an ISO 8601 UTC timestamp.") from exc
    else:
        parsed = value
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ConfigError(f"{name} must be a timezone-aware UTC timestamp.")
    return parsed.astimezone(timezone.utc)


def _require_int(value: Any, name: str, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{name} must be an integer greater than or equal to {minimum}.")


def _parse_utc_date(value: Any, name: str) -> datetime:
    if isinstance(value, datetime) or isinstance(value, bool):
        raise ConfigError(f"{name} must be a YYYY-MM-DD date.")
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ConfigError(f"{name} must be a YYYY-MM-DD date.") from exc
        if value != parsed.isoformat():
            raise ConfigError(f"{name} must be a YYYY-MM-DD date.")
    elif isinstance(value, date):
        parsed = value
    else:
        raise ConfigError(f"{name} must be a YYYY-MM-DD date.")
    return datetime.combine(parsed, time.min, tzinfo=timezone.utc)
