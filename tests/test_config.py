from __future__ import annotations

import pytest

from warera_quant.config import ConfigError, load_config


def test_load_config_uses_defaults_when_file_is_missing(tmp_path):
    config = load_config(tmp_path / "missing.toml")

    assert config.housekeeping.enabled is True
    assert config.housekeeping.retention_days == 45
    assert config.housekeeping.vacuum_interval_days == 30


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
