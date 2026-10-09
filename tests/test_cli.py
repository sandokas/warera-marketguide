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


@pytest.mark.parametrize("flags", [
    ("--player-summary", "--mu-summary"),
    ("--player-summary", "--country-summary"),
    ("--mu-summary", "--country-summary"),
])
def test_summary_options_are_mutually_exclusive(flags, capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args([flags[0], "first", flags[1], "second"])
    assert exc.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_player_summary_prints_one_user_without_writing_report(monkeypatch, tmp_path, capsys):
    from test_participant_market_data import NOW, fact, ingest

    path = tmp_path / "market.db"
    with MarketStore(path) as store:
        ingest(store, [fact("buy", -2, buyer="player-id", seller="seller", money="12", quantity="3")])
        store.cache_entity_name("user", "player-id", "Example Player", NOW.isoformat(), "found")

    monkeypatch.setattr(cli_module, "write_outputs",
                        lambda *args, **kwargs: pytest.fail("player summary wrote the normal report"))
    monkeypatch.setattr(sys, "argv", [
        "warera-marketguide", "--player-summary", "example player",
        "--market-db", str(path), "--as-of", NOW.isoformat(), "--quiet",
    ])

    main()

    output = capsys.readouterr().out
    assert "Example Player - 7-day market summary" in output
    assert "Player ID: player-id" in output
    assert "Total turnover: 12.000 BTC (source-money)" in output
    assert "never-in-price-list" in output


def test_player_summary_reports_ambiguous_cached_name(monkeypatch, tmp_path):
    from test_participant_market_data import NOW

    path = tmp_path / "market.db"
    with MarketStore(path) as store:
        store.cache_entity_name("user", "one", "Shared Name", NOW.isoformat(), "found")
        store.cache_entity_name("user", "two", "Shared Name", NOW.isoformat(), "found")

    monkeypatch.setattr(sys, "argv", [
        "warera-marketguide", "--player-summary", "shared name",
        "--market-db", str(path), "--as-of", NOW.isoformat(), "--quiet",
    ])

    with pytest.raises(SystemExit, match="ambiguous") as exc:
        main()
    assert "one" in str(exc.value) and "two" in str(exc.value)


def test_player_summary_resolves_uncached_interface_name_live(monkeypatch, tmp_path, capsys):
    from test_participant_market_data import NOW, fact, ingest
    from warera_quant.market_models import DisplayIdentity

    path = tmp_path / "market.db"
    with MarketStore(path) as store:
        facts = [fact("buy", -2, buyer="live-id", seller="target-seller")]
        facts.extend(fact(f"higher-{index}", -2, buyer=f"higher-{index}",
                          seller=f"seller-{index}", money=str(100 + index))
                     for index in range(11))
        ingest(store, facts)

    class Api:
        def __init__(self, _client):
            pass

        def search_users(self, name):
            assert name == "Interface Name"
            return [DisplayIdentity("user", "live-id", "Interface Name")]

    monkeypatch.setattr(cli_module, "WarEraApiClient", lambda: object())
    monkeypatch.setattr(cli_module, "WarEraMarketApi", Api)
    monkeypatch.setattr(sys, "argv", [
        "warera-marketguide", "--player-summary", "Interface Name",
        "--market-db", str(path), "--as-of", NOW.isoformat(), "--quiet",
    ])

    main()

    output = capsys.readouterr().out
    assert "Interface Name - 7-day market summary" in output
    assert "Player ID: live-id" in output
    assert "Total turnover: 10.000 BTC" in output


def test_mu_summary_prints_one_mu_without_writing_report(monkeypatch, tmp_path, capsys):
    from test_participant_market_data import NOW, fact, ingest

    path = tmp_path / "market.db"
    with MarketStore(path) as store:
        ingest(store, [fact("buy", -2, buyer="player", seller="seller", money="12", quantity="3", buyerMuId="mu-id")])
        store.cache_entity_name("mu", "mu-id", "Example MU", NOW.isoformat(), "found")

    monkeypatch.setattr(cli_module, "write_outputs",
                        lambda *args, **kwargs: pytest.fail("mu summary wrote the normal report"))
    monkeypatch.setattr(sys, "argv", [
        "warera-marketguide", "--mu-summary", "example mu",
        "--market-db", str(path), "--as-of", NOW.isoformat(), "--quiet",
    ])

    main()

    output = capsys.readouterr().out
    assert "Example MU - 7-day market summary" in output
    assert "MU ID: mu-id" in output
    assert "Total turnover: 12.000 BTC" in output


def test_mu_summary_reports_ambiguous_cached_name(monkeypatch, tmp_path):
    from test_participant_market_data import NOW

    path = tmp_path / "market.db"
    with MarketStore(path) as store:
        store.cache_entity_name("mu", "one", "Shared MU Name", NOW.isoformat(), "found")
        store.cache_entity_name("mu", "two", "Shared MU Name", NOW.isoformat(), "found")

    monkeypatch.setattr(sys, "argv", [
        "warera-marketguide", "--mu-summary", "shared mu name",
        "--market-db", str(path), "--as-of", NOW.isoformat(), "--quiet",
    ])

    with pytest.raises(SystemExit, match="ambiguous") as exc:
        main()
    assert "one" in str(exc.value) and "two" in str(exc.value)


def test_mu_summary_resolves_uncached_interface_name_live(monkeypatch, tmp_path, capsys):
    from test_participant_market_data import NOW, fact, ingest
    from warera_quant.market_models import DisplayIdentity

    path = tmp_path / "market.db"
    with MarketStore(path) as store:
        facts = [fact("buy", -2, buyer="player", seller="target-seller", buyerMuId="live-mu-id")]
        facts.extend(fact(f"higher-{index}", -2, buyer=f"higher-{index}",
                          seller=f"seller-{index}", money=str(100 + index), buyerMuId=f"mu-{index}")
                     for index in range(11))
        ingest(store, facts)

    class Api:
        def __init__(self, _client):
            pass

        def search_mu(self, name):
            assert name == "Interface MU Name"
            return [DisplayIdentity("mu", "live-mu-id", "Interface MU Name")]

    monkeypatch.setattr(cli_module, "WarEraApiClient", lambda: object())
    monkeypatch.setattr(cli_module, "WarEraMarketApi", Api)
    monkeypatch.setattr(sys, "argv", [
        "warera-marketguide", "--mu-summary", "Interface MU Name",
        "--market-db", str(path), "--as-of", NOW.isoformat(), "--quiet",
    ])

    main()

    output = capsys.readouterr().out
    assert "Interface MU Name - 7-day market summary" in output
    assert "MU ID: live-mu-id" in output


def test_country_summary_prints_one_country_without_writing_report(monkeypatch, tmp_path, capsys):
    from test_participant_market_data import NOW, fact, ingest

    path = tmp_path / "market.db"
    with MarketStore(path) as store:
        ingest(store, [fact("buy", -2, buyer="player", seller="seller", money="12", quantity="3", buyerCountryId="country-id")])
        store.cache_entity_name("country", "country-id", "Example Country", NOW.isoformat(), "found")

    monkeypatch.setattr(cli_module, "write_outputs",
                        lambda *args, **kwargs: pytest.fail("country summary wrote the normal report"))
    monkeypatch.setattr(sys, "argv", [
        "warera-marketguide", "--country-summary", "example country",
        "--market-db", str(path), "--as-of", NOW.isoformat(), "--quiet",
    ])

    main()

    output = capsys.readouterr().out
    assert "Example Country - 7-day market summary" in output
    assert "Country ID: country-id" in output
    assert "Total turnover: 12.000 BTC" in output


def test_country_summary_reports_ambiguous_cached_name(monkeypatch, tmp_path):
    from test_participant_market_data import NOW

    path = tmp_path / "market.db"
    with MarketStore(path) as store:
        store.cache_entity_name("country", "one", "Shared Country Name", NOW.isoformat(), "found")
        store.cache_entity_name("country", "two", "Shared Country Name", NOW.isoformat(), "found")

    monkeypatch.setattr(sys, "argv", [
        "warera-marketguide", "--country-summary", "shared country name",
        "--market-db", str(path), "--as-of", NOW.isoformat(), "--quiet",
    ])

    with pytest.raises(SystemExit, match="ambiguous") as exc:
        main()
    assert "one" in str(exc.value) and "two" in str(exc.value)


def test_country_summary_resolves_uncached_interface_name_live(monkeypatch, tmp_path, capsys):
    from test_participant_market_data import NOW, fact, ingest
    from warera_quant.market_models import DisplayIdentity

    path = tmp_path / "market.db"
    with MarketStore(path) as store:
        facts = [fact("buy", -2, buyer="player", seller="target-seller", buyerCountryId="live-country-id")]
        facts.extend(fact(f"higher-{index}", -2, buyer=f"higher-{index}",
                          seller=f"seller-{index}", money=str(100 + index), buyerCountryId=f"country-{index}")
                     for index in range(11))
        ingest(store, facts)

    class Api:
        def __init__(self, _client):
            pass

        def search_country(self, name):
            assert name == "Interface Country Name"
            return [DisplayIdentity("country", "live-country-id", "Interface Country Name")]

    monkeypatch.setattr(cli_module, "WarEraApiClient", lambda: object())
    monkeypatch.setattr(cli_module, "WarEraMarketApi", Api)
    monkeypatch.setattr(sys, "argv", [
        "warera-marketguide", "--country-summary", "Interface Country Name",
        "--market-db", str(path), "--as-of", NOW.isoformat(), "--quiet",
    ])

    main()

    output = capsys.readouterr().out
    assert "Interface Country Name - 7-day market summary" in output
    assert "Country ID: live-country-id" in output


def test_player_summary_distinguishes_known_inactive_player(monkeypatch, tmp_path):
    from test_participant_market_data import NOW

    path = tmp_path / "market.db"
    with MarketStore(path) as store:
        store.cache_entity_name("user", "inactive", "Inactive Player", NOW.isoformat(), "found")

    monkeypatch.setattr(sys, "argv", [
        "warera-marketguide", "--player-summary", "Inactive Player",
        "--market-db", str(path), "--as-of", NOW.isoformat(), "--quiet",
    ])

    with pytest.raises(SystemExit, match="No observed seven-day activity"):
        main()


def test_table_pngs_is_opt_in():
    assert build_parser().parse_args([]).table_pngs is False
    assert build_parser().parse_args(["--table-pngs"]).table_pngs is True


def test_all_price_action_charts_is_opt_in():
    assert build_parser().parse_args([]).all_price_action_charts is False
    assert build_parser().parse_args(["--all-price-action-charts"]).all_price_action_charts is True


def test_from_db_preserves_structured_order_book_for_report(monkeypatch, tmp_path):
    class DummyStore:
        def resolve_report_context(self, as_of=None):
            from warera_quant.report_context import resolve_context
            return resolve_context(datetime(2026, 6, 30, tzinfo=timezone.utc), as_of)

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

    output, = output.glob("report-*")
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
    assert calls[0][2].parent == output
    assert calls[0][2].name.startswith("report-")


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
    def unexpected_report_work(*args, **kwargs):
        pytest.fail("Sync-only command started report preparation")
    monkeypatch.setattr(cli_module, "load_market_rows", unexpected_report_work)
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
        '--as-of','2026-09-23T01:00:00+01:00','--quiet','--output-layout','direct'])
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


def test_from_db_refreshes_identities_before_first_output(monkeypatch, tmp_path):
    from test_participant_market_data import fact, ingest, NOW
    from test_display_identity import Client
    from warera_quant.market_models import DisplayIdentity
    path = tmp_path / 'market.db'
    with MarketStore(path) as store:
        ingest(store, [fact('inside', -1)])

    class Api:
        def __init__(self, client):
            self.client = client

        def get_identity(self, kind, entity_id):
            return DisplayIdentity(kind, entity_id, 'Fetched ' + entity_id,
                'https://media.warera.io/avatar.png',
                citizenship_id='citizenship' if kind != 'country' else None)

    monkeypatch.setattr(cli_module, 'WarEraApiClient', Client)
    monkeypatch.setattr(cli_module, 'WarEraMarketApi', Api)
    captured = []

    def write(df, output_dir, **kwargs):
        rows = kwargs['participant_report']['rankings']['user']['volume']
        assert rows
        for row in rows:
            assert row['identity']['display_name'].startswith('Fetched ')
            assert row['identity']['image_src'].startswith('data:image/')
            assert row['identity']['citizenship_image_src'].startswith('data:image/')
        captured.append(True)
        return output_dir/'x.csv', output_dir/'x.html'

    monkeypatch.setattr(cli_module, 'write_outputs', write)
    monkeypatch.setattr(sys, 'argv', ['warera-marketguide', '--from-db', '--refresh-identities',
        '--market-db', str(path), '--output', str(tmp_path/'out'), '--as-of', NOW.isoformat(), '--quiet'])
    main()
    assert captured == [True]


@pytest.mark.parametrize('value', ['2026-09-23', 'not-a-date'])
def test_as_of_requires_timezone(value):
    with pytest.raises(SystemExit):
        build_parser().parse_args(['--as-of', value])


@pytest.mark.parametrize("mode,expected", [
    ("--sync", ["sync"]),
    ("--live", ["sync", "workflow", "prepare", "enrich", "generate"]),
    ("--from-db", ["workflow", "prepare", "generate"]),
])
def test_db_commands_share_workflow_in_order(monkeypatch, tmp_path, mode, expected):
    events = []
    monkeypatch.setattr(cli_module, "WarEraApiClient", lambda **k: object())
    monkeypatch.setattr(cli_module, "WarEraMarketApi", lambda client: object())
    monkeypatch.setattr(cli_module, "sync_market_data",
                        lambda *a, **k: events.append("sync") or SimpleNamespace())
    monkeypatch.setattr(cli_module, "_refresh_participant_display",
                        lambda *a, **k: events.append("enrich"))
    monkeypatch.setattr(cli_module, "generate_report",
                        lambda *a, **k: events.append("generate"))
    prepare = cli_module.prepare_db_report
    workflow = cli_module.run_db_report_workflow

    def wrapped_prepare(*a, **k):
        events.append("prepare")
        return prepare(*a, **k)

    def wrapped_workflow(*a, **k):
        events.append("workflow")
        return workflow(*a, **k)

    monkeypatch.setattr(cli_module, "prepare_db_report", wrapped_prepare)
    monkeypatch.setattr(cli_module, "run_db_report_workflow", wrapped_workflow)
    monkeypatch.setattr(sys, "argv", ["warera-marketguide", mode, "--quiet",
        "--market-db", str(tmp_path / "market.db"), "--output", str(tmp_path / "out")])
    main()
    assert events == expected
    assert not (tmp_path / "out").exists()


def test_live_and_offline_use_equivalent_prepared_and_chart_inputs(monkeypatch, tmp_path):
    from test_participant_market_data import NOW, fact, ingest
    from warera_quant import market_data

    path = tmp_path / "market.db"
    with MarketStore(path) as store:
        ingest(store, [fact("old", -20), fact("buy", -2),
            fact("sell", -1, buyer="V", seller="U"),
            fact("equipment", -1, equipment={"_id": "helmet", "code": "helmet"})])
    # Exercise real read models, while leaving actual publication/browser work isolated.
    monkeypatch.setattr(cli_module, "build_we24_market_index", market_data.build_we24_market_index)
    monkeypatch.setattr(cli_module, "load_action_cost_results", market_data.load_action_cost_results)
    monkeypatch.setattr(cli_module, "WarEraApiClient", lambda **k: object())
    monkeypatch.setattr(cli_module, "WarEraMarketApi", lambda client: object())
    syncs = []
    monkeypatch.setattr(cli_module, "sync_market_data",
        lambda *a, **k: syncs.append(k) or SimpleNamespace())
    enrichments = []
    monkeypatch.setattr(cli_module, "_refresh_participant_display",
        lambda *a, **k: enrichments.append(True))
    prepared = []
    prepare = cli_module.prepare_db_report

    def capture_prepare(*a, **k):
        result = prepare(*a, **k)
        prepared.append(result)
        return result

    monkeypatch.setattr(cli_module, "prepare_db_report", capture_prepare)
    generated = []

    def capture_write(df, output, **kwargs):
        generated.append((df.copy(), kwargs))
        return output / "x.csv", output / "x.html"

    monkeypatch.setattr(cli_module, "write_outputs", capture_write)
    histories = []
    load_history = cli_module.load_price_action_history

    def capture_history(*a, **k):
        result = load_history(*a, **k)
        histories.append((k, result))
        return result

    monkeypatch.setattr(cli_module, "load_price_action_history", capture_history)
    captured_histories = []
    for mode in ("--live", "--from-db"):
        histories.clear()
        monkeypatch.setattr(sys, "argv", ["warera-marketguide", mode, "--quiet",
            "--market-db", str(path), "--output", str(tmp_path / mode[2:]),
            "--as-of", NOW.isoformat(), "--all-price-action-charts"])
        main()
        captured_histories.append(list(histories))
    assert len(syncs) == 1 and enrichments == [True]
    left, right = prepared
    pd.testing.assert_frame_equal(left.market_frame, right.market_frame)
    assert left.as_of == right.as_of == NOW - timedelta(days=1)
    for field in ("data_sync_metadata", "we24", "action_cost_results",
                  "participant_report", "equipment_details"):
        a, b = getattr(left, field), getattr(right, field)
        if field == "participant_report":
            a, b = dict(a), dict(b)
            a.pop("context", None)
            b.pop("context", None)
        assert a == b
    pd.testing.assert_frame_equal(generated[0][0], generated[1][0])
    for _, kwargs in generated:
        kwargs["we24_chart_path"] = kwargs["we24_chart_path"].name
        kwargs.pop("context", None)
        if kwargs.get("participant_report"):
            kwargs["participant_report"] = dict(kwargs["participant_report"])
            kwargs["participant_report"].pop("context", None)
    assert generated[0][1] == generated[1][1]
    for collection in captured_histories:
        for kwargs, _history in collection:
            context = kwargs.pop("context")
            kwargs["context"] = {k: v for k, v in context.to_dict().items() if k != "generated_at"}
    assert captured_histories[0] == captured_histories[1]
    assert captured_histories[0]  # Verify chart preparation was actually exercised.


def test_sync_failure_never_prepares_report(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_module, "WarEraApiClient", lambda **k: object())
    monkeypatch.setattr(cli_module, "WarEraMarketApi", lambda client: object())

    def fail_sync(*a, **k):
        raise RuntimeError("sync failed")

    monkeypatch.setattr(cli_module, "sync_market_data", fail_sync)
    monkeypatch.setattr(cli_module, "run_db_report_workflow",
        lambda *a, **k: pytest.fail("report started after sync exception"))
    monkeypatch.setattr(sys, "argv", ["warera-marketguide", "--live", "--quiet",
        "--market-db", str(tmp_path / "market.db")])
    with pytest.raises(RuntimeError, match="sync failed"):
        main()


def test_live_preserves_partial_sync_publication(monkeypatch, tmp_path):
    from test_participant_market_data import NOW, fact, ingest

    monkeypatch.setattr(cli_module, "WarEraApiClient", lambda **k: object())
    monkeypatch.setattr(cli_module, "WarEraMarketApi", lambda client: object())
    monkeypatch.setattr(cli_module, "_refresh_participant_display", lambda *a, **k: None)

    def partial_sync(api, store, **kwargs):
        ingest(store, [fact("committed", -1)])
        store.record_market_sync(NOW, status="partial")
        return SimpleNamespace(error_count=1)

    captured = []
    monkeypatch.setattr(cli_module, "sync_market_data", partial_sync)

    def write(df, output, **kwargs):
        assert kwargs["data_sync_status"] == "partial"
        assert kwargs["participant_report"]["entities"]
        captured.append(True)
        return output / "x.csv", output / "x.html"

    monkeypatch.setattr(cli_module, "write_outputs", write)
    monkeypatch.setattr(sys, "argv", ["warera-marketguide", "--live", "--quiet",
        "--market-db", str(tmp_path / "market.db"), "--output", str(tmp_path / "out"),
        "--as-of", NOW.isoformat()])
    main()
    assert captured == [True]


@pytest.mark.parametrize("flags,expected", [
    (["--sync", "--resync-market", "--history-scope", "all", "--resume-market"],
     {"resync_market": True, "history_scope": "all", "resume_market": True}),
    (["--live", "--transaction-backfill", "--lookback-days", "90", "--history-pages", "2",
      "--exclude-item-code", "bread"],
     {"transaction_backfill": True, "lookback_days": 90, "history_pages": 2,
      "exclude_item_codes": {"bread"}}),
])
def test_shared_sync_keeps_resume_and_backfill_options(monkeypatch, tmp_path, flags, expected):
    monkeypatch.setattr(cli_module, "WarEraApiClient", lambda **k: object())
    monkeypatch.setattr(cli_module, "WarEraMarketApi", lambda client: object())
    calls = []
    monkeypatch.setattr(cli_module, "sync_market_data",
        lambda *a, **k: calls.append(k) or SimpleNamespace())
    monkeypatch.setattr(cli_module, "run_db_report_workflow", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["warera-marketguide", *flags, "--quiet",
        "--market-db", str(tmp_path / "market.db")])
    main()
    assert len(calls) == 1
    assert {key: calls[0][key] for key in expected} == expected


def test_custom_endpoint_keeps_compatibility_pipeline(monkeypatch, tmp_path):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        def get_json(self, endpoint, *, params):
            calls.append((endpoint, params))
            return {"data": {"items": [{"item_name": "Bread", "bid": 9,
                "ask": 10, "trades_7d": 5, "high_7d": 11, "low_7d": 8}]}}

    monkeypatch.setattr(cli_module, "WarEraApiClient", Client)
    monkeypatch.setattr(cli_module, "sync_market_data", lambda *a, **k: pytest.fail("custom endpoint synced"))
    monkeypatch.setattr(cli_module, "run_db_report_workflow", lambda *a, **k: pytest.fail("custom endpoint used DB"))
    output = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["warera-marketguide", "--api-endpoint", "/custom",
        "--api-param", "page=2", "--api-records-path", "data.items", "--quiet",
        "--output", str(output)])
    main()
    assert calls == [("/custom", {"page": "2"})]
    output, = output.glob("report-*")
    assert pd.read_csv(output / "market_trends.csv").iloc[0]["flip_verdict"] == "Unavailable"


@pytest.mark.parametrize("mode", ["window", "full-fifo"])
def test_participant_accounting_cli_mode_reaches_read_model(monkeypatch, tmp_path, mode):
    from test_participant_market_data import NOW, fact, ingest
    path = tmp_path / "mode.db"
    with MarketStore(path) as store:
        ingest(store, [fact("old", -30), fact("recent", -1)])
    calls = []
    original = cli_module.load_entity_activity
    def observe(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result["accounting_mode"])
        return result
    monkeypatch.setattr(cli_module, "load_entity_activity", observe)
    monkeypatch.setattr(sys, "argv", ["warera-marketguide", "--player-summary", "U",
        "--market-db", str(path), "--quiet", "--participant-accounting", mode])
    main()
    assert calls == [mode]
    assert build_parser().parse_args([]).participant_accounting == "window"


@pytest.mark.parametrize("value,active", [("U", True), ("old-user", False), ("actor", False)])
def test_summary_uncached_ids_avoid_global_analysis_and_network(monkeypatch, tmp_path, capsys, value, active):
    from test_participant_market_data import NOW, fact, ingest
    path = tmp_path / "ids.db"
    with MarketStore(path) as store:
        ingest(store, [fact("recent", -1), fact("old", -30, buyer="old-user"),
                      fact("actor", -1, buyer="actor", buyerMuId="M")])
        store.cache_entity_name("user", "wrong", "U", NOW.isoformat(), "found")
    monkeypatch.setattr(cli_module, "load_participant_report", lambda *a, **k: pytest.fail("global analysis"))
    monkeypatch.setattr(cli_module, "WarEraApiClient", lambda: pytest.fail("unexpected network"))
    monkeypatch.setattr(cli_module, "write_outputs", lambda *a, **k: pytest.fail("artifact writes"))
    output = tmp_path / "output"
    monkeypatch.setattr(sys, "argv", ["warera-marketguide", "--player-summary", value,
        "--market-db", str(path), "--output", str(output), "--quiet"])
    if active:
        main()
        assert "Player ID: U" in capsys.readouterr().out
    else:
        with pytest.raises(SystemExit, match="No observed seven-day activity"):
            main()
    assert not output.exists()


def test_ambiguous_summary_exits_before_participant_analysis(monkeypatch, tmp_path):
    from test_participant_market_data import NOW
    path = tmp_path / "ambiguity.db"
    with MarketStore(path) as store:
        for id in ("one", "two"):
            store.cache_entity_name("user", id, "Shared", NOW.isoformat(), "found")
    monkeypatch.setattr(cli_module, "load_entity_activity", lambda *a, **k: pytest.fail("analysis before resolution"))
    monkeypatch.setattr(cli_module, "WarEraApiClient", lambda: pytest.fail("network for cached name"))
    monkeypatch.setattr(sys, "argv", ["warera-marketguide", "--player-summary", "SHARED", "--market-db", str(path), "--quiet"])
    with pytest.raises(SystemExit, match="ambiguous"):
        main()


@pytest.mark.parametrize("matches,message", [(0, "Player not found"), (1, "No observed seven-day activity"), (2, "ambiguous")])
def test_public_summary_lookup_resolves_before_activity(monkeypatch, tmp_path, matches, message):
    from warera_quant.market_models import DisplayIdentity
    path = tmp_path / "public.db"
    with MarketStore(path):
        pass
    class Api:
        def __init__(self, client):
            pass
        def search_users(self, value):
            assert value == "Public Name"
            return [DisplayIdentity("user", str(i), "Public Name") for i in range(matches)]
    monkeypatch.setattr(cli_module, "WarEraApiClient", lambda: object())
    monkeypatch.setattr(cli_module, "WarEraMarketApi", Api)
    monkeypatch.setattr(cli_module, "load_participant_report", lambda *a, **k: pytest.fail("global analysis"))
    if matches != 1:
        monkeypatch.setattr(cli_module, "load_entity_activity", lambda *a, **k: pytest.fail("analysis without unique identity"))
    monkeypatch.setattr(sys, "argv", ["warera-marketguide", "--player-summary", "Public Name", "--market-db", str(path), "--quiet"])
    with pytest.raises(SystemExit, match=message):
        main()
