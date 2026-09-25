import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from types import SimpleNamespace

import warera_quant.cli as cli_module
from warera_quant.cli import build_parser, main
from warera_quant.market_store import MarketStore


@pytest.fixture(autouse=True)
def prevent_tests_from_loading_local_dotenv(monkeypatch):
    monkeypatch.setattr(cli_module, "load_dotenv", lambda: False)
    monkeypatch.setattr(cli_module, "export_report_assets", lambda *a, **k: [])
    monkeypatch.setattr(cli_module, "render_we24_chart", lambda index, path, **k: path)
    monkeypatch.setattr(cli_module, "build_we24_market_index", lambda *a, **k: {})
    monkeypatch.setattr(cli_module, "load_action_cost_results", lambda *a, **k: ())


def test_order_book_sync_defaults_to_api_maximum():
    args = build_parser().parse_args([])
    assert args.order_limit == 100


def test_table_pngs_is_opt_in():
    assert build_parser().parse_args([]).table_pngs is False
    assert build_parser().parse_args(["--table-pngs"]).table_pngs is True


def test_all_price_action_charts_is_opt_in():
    assert build_parser().parse_args([]).all_price_action_charts is False
    assert build_parser().parse_args(["--all-price-action-charts"]).all_price_action_charts is True


def test_from_db_preserves_structured_order_book_for_report(monkeypatch, tmp_path):
    class DummyStore:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def market_sync_metadata(self):
            return SimpleNamespace(
                synced_at="2026-06-30T10:00:00Z",
                status="complete",
            )

        def item_codes(self, *, transaction_type="trading"):
            return []

        def transactions_for_period(self, _item_codes, _start_epoch, _end_epoch):
            return []

    monkeypatch.setattr(cli_module, "load_participant_report", lambda *a, **k: None)
    monkeypatch.setattr(cli_module, "iter_equipment_sale_details", lambda *a, **k: iter(()))
    captured = {}
    book = {"best_bid": 9, "best_ask": 10, "bids": [], "asks": []}
    monkeypatch.setattr(cli_module, "MarketStore", DummyStore)
    monkeypatch.setattr(
        cli_module,
        "load_market_rows",
        lambda _store, **_kwargs: [{
            "item_name": "Bread",
            "order_book": book,
        }],
    )

    def capture_outputs(df, output_dir, **_kwargs):
        captured["book"] = df.iloc[0]["order_book"]
        captured["data_synced_at"] = _kwargs["data_synced_at"]
        captured["kwargs"] = _kwargs
        return output_dir / "market_trends.csv", output_dir / "market_report.html"

    monkeypatch.setattr(cli_module, "write_outputs", capture_outputs)
    monkeypatch.setattr(sys, "argv", [
        "warera-quant", "--from-db", "--market-db", str(tmp_path / "market.sqlite3"),
        "--output", str(tmp_path / "output"), "--quiet",
    ])

    main()

    assert captured["book"] == book
    assert captured["data_synced_at"] == "2026-06-30T10:00:00Z"
    assert "metric_window" not in captured["kwargs"]


@pytest.mark.parametrize(
    "option,value",
    [
        ("--min-tick", "0"),
        ("--min-tick", "nan"),
        ("--trade-quantity", "0"),
        ("--trade-fee-pct", "100"),
        ("--min-net-margin-pct", "-1"),
        ("--max-quote-age-minutes", "nan"),
    ],
)
def test_trade_options_validate_bounds(monkeypatch, option, value):
    monkeypatch.setattr(sys, "argv", ["warera-quant", option, value])
    with pytest.raises(SystemExit):
        main()


def test_csv_mode_writes_unavailable_flip_fields_without_assumption_badges(tmp_path, monkeypatch):
    csv_path = tmp_path / "market.csv"
    output = tmp_path / "output"
    pd.DataFrame([{
        "item_name": "Bread", "bid": 9, "ask": 10, "trades_7d": 5,
        "high_7d": 11, "low_7d": 8,
    }]).to_csv(csv_path, index=False)
    monkeypatch.setattr(sys, "argv", [
        "warera-quant", "--csv", str(csv_path), "--output", str(output),
        "--trade-quantity", "7", "--trade-fee-pct", "2.5",
        "--min-net-margin-pct", "3", "--max-quote-age-minutes", "15", "--quiet",
    ])

    main()

    exported = pd.read_csv(output / "market_trends.csv")
    assert exported.loc[0, "flip_verdict"] == "Unavailable"
    assert exported.loc[0, "flip_quantity"] == 7
    report = (output / "market_report.html").read_text(encoding="utf-8")
    assert 'aria-label="Analysis assumptions"' not in report
    assert "Quantity <strong>7</strong>" not in report
    assert "Fees <strong>2.50% / side</strong>" not in report
    assert "Min margin <strong>3.00%</strong>" not in report
    assert "Freshness <strong>≤ 15m</strong>" not in report


def test_table_png_flow_exports_header_cards_and_tables_without_a_new_flag(tmp_path, monkeypatch):
    csv_path = tmp_path / "market.csv"
    output = tmp_path / "output"
    pd.DataFrame([{
        "item_name": "Bread", "item_code": "bread", "bid": 9, "ask": 10,
        "trades_7d": 5, "high_7d": 11, "low_7d": 8,
    }]).to_csv(csv_path, index=False)
    calls = []

    monkeypatch.setattr(cli_module, "export_report_assets", lambda report, destination, **kwargs: calls.append(("inventory", report, destination)) or [])
    monkeypatch.setattr(sys, "argv", [
        "warera-quant", "--csv", str(csv_path), "--output", str(output),
        "--table-pngs", "--quiet",
    ])

    main()

    assert [call[0] for call in calls] == ["inventory"]
    assert calls[0][2] == output


def test_housekeeping_is_an_independent_command(tmp_path, monkeypatch):
    database_path = tmp_path / "market.sqlite3"
    config_path = tmp_path / "marketguide.toml"
    config_path.write_text(
        "[housekeeping]\nretention_days = 45\nvacuum_interval_days = 0\n",
        encoding="utf-8",
    )
    now = datetime.now(timezone.utc)
    with MarketStore(database_path) as store:
        store.upsert_transactions(
            "bread",
            [{
                "id": "expired",
                "createdAt": (now - timedelta(days=46)).isoformat(),
                "transactionType": "trading",
                "money": 10,
                "quantity": 2,
            }],
            fetched_at=now,
        )

    monkeypatch.setattr(sys, "argv", [
        "warera-quant",
        "--housekeeping",
        "--market-db", str(database_path),
        "--config", str(config_path),
        "--quiet",
    ])

    main()

    with MarketStore(database_path) as store:
        assert store.transactions_for_window("bread", 0) == []


def test_display_settings_do_not_change_download_window():
    args = build_parser().parse_args(["--item-chart-days", "14", "--we24-days", "60", "--research-days", "90", "--lookback-days", "180"])
    assert (args.item_chart_days, args.we24_days, args.research_days, args.lookback_days) == (14, 60, 90, 180)
    defaults = build_parser().parse_args([])
    assert (defaults.item_chart_days, defaults.chart_interval, defaults.we24_days) == (30, "4h", 30)


@pytest.mark.parametrize("flags", [
    ["--resync-market"], ["--sync", "--history-scope", "7d"],
    ["--sync", "--resync-market", "--history-scope", "all", "--history-pages", "1"],
    ["--sync", "--resync-market", "--history-scope", "7d", "--history-pages", "1"],
    ["--sync", "--resync-market", "--history-scope", "7d", "--exclude-item-code", "bread"],
    ["--from-db", "--resync-market", "--history-scope", "7d"],
])
def test_recent_enrichment_validates_before_api_construction(monkeypatch, flags):
    def forbidden(*args, **kwargs):
        pytest.fail("API constructed for invalid CLI scope")
    monkeypatch.setattr(cli_module, "WarEraApiClient", forbidden)
    monkeypatch.setattr(sys, "argv", ["warera-marketguide", *flags])
    with pytest.raises(SystemExit):
        main()


@pytest.mark.parametrize("scope", ["7d", "all"])
def test_recent_enrichment_command_wires_scope_independently(tmp_path, monkeypatch, scope):
    captured = {}
    monkeypatch.setattr(cli_module, "WarEraApiClient", lambda **kw: object())
    monkeypatch.setattr(cli_module, "WarEraMarketApi", lambda client: object())
    def sync(api, store, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace()
    monkeypatch.setattr(cli_module, "sync_market_data", sync)
    monkeypatch.setattr(cli_module, "load_market_rows", lambda *a, **k: [])
    monkeypatch.setattr(sys, "argv", ["warera-marketguide", "--sync", "--resync-market",
        "--history-scope", scope, "--lookback-days", "30", "--market-db", str(tmp_path / "db"), "--quiet"])
    main()
    assert captured["resync_market"] and captured["history_scope"] == scope
    assert captured["lookback_days"] == 30


def test_from_db_never_constructs_api_with_equipment_participants(tmp_path, monkeypatch):
    from warera_quant.warera_api import normalize_transaction
    path = tmp_path / "db"
    with MarketStore(path) as store:
        store.ingest_transactions([normalize_transaction({"_id": "e", "itemCode": "weapon",
            "transactionType": "itemMarket", "createdAt": "2026-09-23T00:00:00Z", "buyerId": "needs-name"})])
        store.upsert_transactions("bread", [{"_id": "c", "createdAt": "2026-09-23T00:00:00Z", "money": 3, "quantity": 1}])
    def forbidden(*a, **k):
        pytest.fail("Offline report attempted network construction")
    monkeypatch.setattr(cli_module, "WarEraApiClient", forbidden)
    monkeypatch.setattr(cli_module, "write_outputs", lambda df, output_dir, **kw: (output_dir / "x.csv", output_dir / "x.html"))
    monkeypatch.setattr(sys, "argv", ["warera-marketguide", "--from-db", "--market-db", str(path),
                                    "--output", str(tmp_path / "out"), "--quiet"])
    main()


def test_as_of_offline_participant_and_equipment_exports(monkeypatch, tmp_path):
    from test_participant_market_data import fact, ingest, NOW
    import csv
    path = tmp_path / 'market.db'
    with MarketStore(path) as store:
        ingest(store, [fact('start', -7), fact('inside', -1), fact('excluded', 0),
            fact('equipment', -2, equipment={'_id':'helmet-id','code':'helmet','skills':{'attack':3}}),
            fact('equipment-excluded', 0, equipment={'_id':'future','code':'helmet','skills':{'attack':8}})])
    monkeypatch.setattr(cli_module, 'WarEraApiClient', lambda **k: pytest.fail('offline report attempted API'))
    out = tmp_path / 'output'
    monkeypatch.setattr(sys, 'argv', ['warera-quant','--from-db','--market-db',str(path),'--output',str(out),
        '--as-of','2026-09-23T01:00:00+01:00','--quiet'])
    main()
    first = (out / 'participant_rankings_7d.csv').read_bytes()
    main()
    assert (out / 'participant_rankings_7d.csv').read_bytes() == first
    with (out / 'participant_rankings_7d.csv').open() as f:
        rows = list(csv.DictReader(f))
    assert all(r['as_of'] == NOW.isoformat() for r in rows)
    assert all(r['source_turnover'] == '30' for r in rows)
    with (out / 'equipment_sales_7d.csv').open() as f:
        assert [r['id'] for r in csv.DictReader(f)] == ['equipment']
    html = (out / 'market_report.html').read_text(encoding='utf-8')
    assert 'data-item-code="helmet"' not in html
    assert 'participants-user-volume' in html


@pytest.mark.parametrize('value', ['2026-09-23', 'not-a-date'])
def test_as_of_requires_timezone(value):
    with pytest.raises(SystemExit):
        build_parser().parse_args(['--as-of', value])
