from __future__ import annotations

from datetime import datetime, timezone

import pytest

from warera_quant.config import ConfigError, load_config


def test_load_config_uses_defaults_when_file_is_missing(tmp_path):
    config = load_config(tmp_path / "missing.toml")

    assert config.housekeeping.enabled is True
    assert config.housekeeping.retention_days == 45
    assert config.housekeeping.vacuum_interval_days == 30
    assert config.inflation.enabled is True
    assert config.inflation.base_period_start == datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert config.inflation.version == "2026-08-01-v1"
    assert config.inflation.price_window_days == 7
    assert config.inflation.min_base_trade_count == 1
    assert config.inflation.min_base_traded_quantity == 1e-12
    assert config.inflation.events == ()


def test_repository_config_keeps_oil_movement_event_out_of_production_history():
    config = load_config("marketguide.toml")

    event = next(event for event in config.inflation.events if event.event_id == "oil-player-movement-dev")
    assert event.status == "planned"
    assert event.environment == "dev"
    assert event.effective_at is None
    assert event.source_url.startswith("https://warera.io/")


def test_load_config_reads_housekeeping_settings(tmp_path):
    path = tmp_path / "marketguide.toml"
    path.write_text(
        """
[housekeeping]
enabled = false
retention_days = 90
vacuum_interval_days = 0
""".strip(),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.housekeeping.enabled is False
    assert config.housekeeping.retention_days == 90
    assert config.housekeeping.vacuum_interval_days == 0


@pytest.mark.parametrize(
    "setting",
    (
        "retention_days = 0",
        'retention_days = "45"',
        "vacuum_interval_days = -1",
        'enabled = "yes"',
        "unexpected = 1",
    ),
)
def test_load_config_rejects_invalid_housekeeping_settings(tmp_path, setting):
    path = tmp_path / "marketguide.toml"
    path.write_text(f"[housekeeping]\n{setting}\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize("date_value", ('"2026-07-15"', "2026-07-15"))
def test_load_config_reads_inflation_settings_and_normalizes_utc_date(tmp_path, date_value):
    path = tmp_path / "marketguide.toml"
    path.write_text(
        f"""
[inflation]
enabled = false
base_period_start = {date_value}
version = "2026-07-15-v2"
price_window_days = 14
min_base_trade_count = 3
min_base_traded_quantity = 0.25
""".strip(),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.inflation.enabled is False
    assert config.inflation.base_period_start == datetime(2026, 7, 15, tzinfo=timezone.utc)
    assert config.inflation.version == "2026-07-15-v2"
    assert config.inflation.price_window_days == 14
    assert config.inflation.min_base_trade_count == 3
    assert config.inflation.min_base_traded_quantity == 0.25


@pytest.mark.parametrize(
    "setting",
    (
        'enabled = "yes"',
        'base_period_start = "15-07-2026"',
        "base_period_start = 2026-07-15T00:00:00Z",
        'version = "   "',
        "price_window_days = 0",
        "price_window_days = 1.5",
        "min_base_trade_count = 0",
        "min_base_trade_count = true",
        "min_base_traded_quantity = 0",
        "min_base_traded_quantity = -1",
        "min_base_traded_quantity = inf",
        "min_base_traded_quantity = nan",
        'min_base_traded_quantity = "1"',
        "unexpected = 1",
    ),
)
def test_load_config_rejects_invalid_inflation_settings(tmp_path, setting):
    path = tmp_path / "marketguide.toml"
    path.write_text(f"[inflation]\n{setting}\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(path)
