"""Fresh CLI report folders preserve earlier complete publications and user files."""
from datetime import datetime, timedelta, timezone
import json
import sys

import pytest

from warera_quant import cli
from warera_quant.market_store import MarketStore
from warera_quant.report import create_report_output_directory
from test_report_context import C, trade, ingest


def test_generation_folder_uses_utc_and_reserves_collisions(tmp_path):
    generated = datetime(2026, 10, 6, 18, 0, 0, 123456,
                         tzinfo=timezone(timedelta(hours=2)))
    first = create_report_output_directory(tmp_path, generated)
    marker = first / "keep.txt"
    marker.write_text("earlier report")
    second = create_report_output_directory(tmp_path, generated)
    assert first.name == "report-20261006T160000123456Z"
    assert second.name == first.name + "-2"
    assert marker.read_text() == "earlier report"
    assert create_report_output_directory(tmp_path, generated, layout="direct") == tmp_path


def test_default_cli_keeps_repeated_stale_reports_isolated(tmp_path, monkeypatch):
    database = tmp_path / "market.db"
    root = tmp_path / "reports"
    root.mkdir()
    old = {"old-report.html": "previous publication", "old-item.png": "previous image",
           "notes.txt": "user notes"}
    for name, content in old.items():
        (root / name).write_text(content)
    with MarketStore(database) as store:
        ingest(store, trade("newest", C))
        # An identical frozen generation time deliberately exercises collision handling.
        context = store.resolve_report_context(generated_at=C + timedelta(days=90))
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    monkeypatch.setattr(cli, "WarEraApiClient", lambda *a, **k: pytest.fail("offline network"))
    monkeypatch.setattr(cli, "sync_market_data", lambda *a, **k: pytest.fail("offline sync"))
    monkeypatch.setattr(sys, "argv", ["guide", "--from-db", "--quiet", "--market-db",
        str(database), "--output", str(root)])
    prepared = []
    workflow = cli.run_db_report_workflow

    def observe(*a, **k):
        result = workflow(*a, **k)
        prepared.append(result)
        return result

    monkeypatch.setattr(cli, "run_db_report_workflow", observe)
    cli.main(report_context=context)
    first = prepared[0].output_dir
    first_files = {p.relative_to(first): p.read_bytes() for p in first.rglob("*") if p.is_file()}
    cli.main(report_context=context)
    second = prepared[1].output_dir
    assert first != second and first.parent == second.parent == root
    assert first.name.startswith("report-20261222T")  # generation time, not stale September C
    assert second.name == first.name + "-2"
    assert {p.relative_to(first): p.read_bytes() for p in first.rglob("*") if p.is_file()} == first_files
    assert all((root / name).read_text() == content for name, content in old.items())
    for destination in (first, second):
        assert json.loads((destination / "report_context.json").read_text()) == context.to_dict()
        inventory = json.loads((destination / "asset_inventory.json").read_text())
        assert inventory and all((destination / a["path"]).is_file() for a in inventory)
        assert not any(a["path"] in old for a in inventory)
        assert '<table ' not in (destination / "market_report.html").read_text(encoding="utf-8")
