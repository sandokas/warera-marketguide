"""Retired workflow must not run or overwrite archived assets."""
import sys
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd

import warera_quant.cli as cli
from warera_quant.report import write_outputs


def test_current_complete_utc_midnight_is_deterministic_and_normalizes_offset():
    assert cli._current_complete_utc_midnight(datetime(2026,9,6,14,tzinfo=timezone.utc)) == datetime(2026,9,6,tzinfo=timezone.utc)


def test_report_preserves_archived_inflation_without_publishing_it(tmp_path):
    archive = tmp_path/'market_inflation.csv'
    archive.write_text('archived evidence')
    _, html = write_outputs(pd.DataFrame([{'item_name':'Bread'}]), tmp_path, inflation_results=[])
    assert archive.read_text() == 'archived evidence'
    assert 'Historical Inflation' not in html.read_text(encoding='utf-8')
    assert 'market_inflation.csv' not in html.read_text(encoding='utf-8')
    assert (tmp_path/'we23_series.csv').exists()


def test_cli_no_longer_has_dedicated_inflation_builder():
    assert not hasattr(cli, '_build_configured_inflation_results')
    assert not hasattr(cli, 'build_inflation_index_results')


def test_csv_mode_never_calculates_we23_from_substitute_prices(monkeypatch, tmp_path):
    source = tmp_path/'market.csv'
    pd.DataFrame([{'item_name':'Bread', 'bid':9, 'ask':10}]).to_csv(source,index=False)
    monkeypatch.setattr(cli, 'load_dotenv', lambda:None)
    monkeypatch.setattr(cli, 'build_we23_market_index', lambda *a,**k: (_ for _ in ()).throw(AssertionError('substitute index')))
    monkeypatch.setattr(cli, 'render_we23_chart', lambda _, path, **k:path)
    monkeypatch.setattr(cli, 'export_report_assets', lambda *a,**k:[])
    monkeypatch.setattr(sys,'argv',['guide','--csv',str(source),'--output',str(tmp_path/'report'),'--quiet'])
    cli.main()
    assert not (tmp_path/'report'/'market_inflation.csv').exists()
