from __future__ import annotations

import tomllib
from dataclasses import dataclass
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
class AppConfig:
    housekeeping: HousekeepingConfig = HousekeepingConfig()


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

    return AppConfig(
        housekeeping=HousekeepingConfig(
            enabled=enabled,
            retention_days=retention_days,
            vacuum_interval_days=vacuum_interval_days,
        )
    )


def _require_int(value: Any, name: str, *, minimum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{name} must be an integer greater than or equal to {minimum}.")
