from __future__ import annotations

from datetime import datetime, timezone

import pytest

from warera_quant.config import ConfigError, load_config, production_inflation_chart_events


def _load(tmp_path, event_body: str):
    path = tmp_path / "marketguide.toml"
    path.write_text(f"[inflation]\n{event_body}\n", encoding="utf-8")
    return load_config(path)


def test_parses_repeated_event_tables_as_immutable_utc_config(tmp_path):
    config = _load(
        tmp_path,
        """
[[inflation.events]]
event_id = "oil-live"
label = "Oil movement launched"
status = "live"
environment = "production"
announced_at = 2026-08-01T12:30:00Z
effective_at = "2026-08-10T00:00:00+00:00"
affected_index_keys = ["infrastructure", "broad_market"]
source_url = "https://example.test/release"
source_published_at = 2026-08-01T00:00:00Z
details = "Production release."

[[inflation.events]]
event_id = "future"
label = "Future change"
status = "planned"
environment = "dev"
affected_index_keys = ["infrastructure"]
source_url = ""
""",
    )

    assert len(config.inflation.events) == 2
    event = config.inflation.events[0]
    assert event.effective_at == datetime(2026, 8, 10, tzinfo=timezone.utc)
    assert event.affected_index_keys == ("infrastructure", "broad_market")
    with pytest.raises(AttributeError):
        event.label = "changed"


def test_only_sourced_live_production_events_become_chart_records(tmp_path):
    bodies = []
    for event_id, status, environment, effective, url in (
        ("eligible", "live", "production", 'effective_at = 2026-08-10T00:00:00Z', "https://example.test/release"),
        ("planned", "planned", "production", "", "https://example.test/plan"),
        ("dev", "planned", "dev", "", "https://example.test/dev"),
        ("unsourced", "live", "production", 'effective_at = 2026-08-11T00:00:00Z', "   "),
    ):
        bodies.append(
            f'''[[inflation.events]]\nevent_id = "{event_id}"\nlabel = "{event_id} label"\nstatus = "{status}"\nenvironment = "{environment}"\n{effective}\naffected_index_keys = ["infrastructure"]\nsource_url = "{url}"'''
        )
    config = _load(tmp_path, "\n".join(bodies))

    assert production_inflation_chart_events(config.inflation.events) == (
        {"at": datetime(2026, 8, 10, tzinfo=timezone.utc), "label": "eligible label"},
    )
    assert production_inflation_chart_events(
        config.inflation.events, index_key="broad_market"
    ) == ()
    assert production_inflation_chart_events(
        config.inflation.events, index_key="infrastructure"
    ) == (
        {"at": datetime(2026, 8, 10, tzinfo=timezone.utc), "label": "eligible label"},
    )


@pytest.mark.parametrize(
    "body",
    (
        "events = {}",
        '[[inflation.events]]\nevent_id = "x"\nlabel = "X"\nstatus = "unknown"\nenvironment = "dev"\naffected_index_keys = ["a"]',
        '[[inflation.events]]\nevent_id = "x"\nlabel = "X"\nstatus = "planned"\nenvironment = "staging"\naffected_index_keys = ["a"]',
        '[[inflation.events]]\nevent_id = "x"\nlabel = "X"\nstatus = "planned"\nenvironment = "dev"\naffected_index_keys = []',
        '[[inflation.events]]\nevent_id = "x"\nlabel = "X"\nstatus = "planned"\nenvironment = "dev"\naffected_index_keys = ["a", "a"]',
        '[[inflation.events]]\nevent_id = "x"\nlabel = "X"\nstatus = "live"\nenvironment = "production"\naffected_index_keys = ["a"]',
        '[[inflation.events]]\nevent_id = "x"\nlabel = "X"\nstatus = "live"\nenvironment = "dev"\neffective_at = 2026-08-10T00:00:00Z\naffected_index_keys = ["a"]',
        '[[inflation.events]]\nevent_id = "x"\nlabel = "X"\nstatus = "planned"\nenvironment = "dev"\nannounced_at = 2026-08-10T01:00:00+01:00\naffected_index_keys = ["a"]',
        '[[inflation.events]]\nevent_id = "x"\nlabel = "X"\nstatus = "planned"\nenvironment = "dev"\naffected_index_keys = ["a"]\nextra = true',
    ),
)
def test_rejects_invalid_event_configuration(tmp_path, body):
    with pytest.raises(ConfigError):
        _load(tmp_path, body)


def test_rejects_duplicate_event_ids(tmp_path):
    event = '''[[inflation.events]]
event_id = "same"
label = "An event"
status = "planned"
environment = "dev"
affected_index_keys = ["infrastructure"]'''

    with pytest.raises(ConfigError, match="Duplicate inflation event_id"):
        _load(tmp_path, f"{event}\n{event}")
