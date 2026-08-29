import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

import warera_quant.cli as cli
from warera_quant.config import AppConfig, InflationConfig


def test_current_complete_utc_midnight_is_deterministic_and_normalizes_offset():
    now = datetime.fromisoformat("2026-08-28T23:47:12+02:00")

    assert cli._current_complete_utc_midnight(now) == datetime(
        2026, 8, 28, tzinfo=timezone.utc
    )


def test_configured_inflation_uses_stable_base_and_complete_day(monkeypatch):
    captured = []
    store = SimpleNamespace(item_codes=lambda: ())
    config = InflationConfig(
        base_period_start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        version="v-test",
        price_window_days=7,
        min_base_trade_count=3,
        min_base_traded_quantity=0.5,
    )
    monkeypatch.setattr(
        cli, "_current_complete_utc_midnight",
        lambda: datetime(2026, 8, 28, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        cli, "build_inflation_index_results",
        lambda passed_store, **kwargs: captured.append((passed_store, kwargs)) or ("result",),
    )

    result = cli._build_configured_inflation_results(store, config, quiet=True)

    assert result == ("result",)
    assert len(captured) == 1
    assert captured[0][0] is store
    assert captured[0][1] == {
        "base_period_start": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "base_period_end": datetime(2026, 8, 8, tzinfo=timezone.utc),
        "first_as_of": datetime(2026, 7, 29, tzinfo=timezone.utc),
        "last_as_of": datetime(2026, 8, 28, tzinfo=timezone.utc),
        "version": "v-test",
        "price_window_days": 7,
        "min_base_trade_count": 3,
        "min_base_traded_quantity": 0.5,
    }


def test_csv_mode_never_calculates_inflation(monkeypatch, tmp_path):
    source = tmp_path / "market.csv"
    pd.DataFrame([{"item_name": "Bread", "bid": 1, "ask": 2}]).to_csv(source, index=False)
    calls = []
    monkeypatch.setattr(cli, "load_dotenv", lambda: False)
    monkeypatch.setattr(cli, "build_inflation_index_results", lambda *_a, **_k: calls.append(1))
    monkeypatch.setattr(
        cli, "write_outputs",
        lambda _df, out, **kwargs: (Path(out) / "market_trends.csv", Path(out) / "market_report.html"),
    )
    monkeypatch.setattr(sys, "argv", [
        "warera-quant", "--csv", str(source), "--output", str(tmp_path / "out"), "--quiet",
    ])

    cli.main()

    assert calls == []


def test_disabled_inflation_does_not_touch_store():
    class Store:
        def item_codes(self):
            raise AssertionError("disabled inflation must not read the database")

    result = cli._build_configured_inflation_results(
        Store(), AppConfig(inflation=InflationConfig(enabled=False)).inflation, quiet=True,
    )

    assert result is None


def test_from_db_calculates_inflation_once_and_forwards_same_results(monkeypatch, tmp_path):
    class Store:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def item_codes(self):
            return ("bread",)

        def market_sync_metadata(self):
            return SimpleNamespace(synced_at=None, status=None)

    results = ()
    calculations = []
    writes = []
    monkeypatch.setattr(cli, "load_dotenv", lambda: False)
    monkeypatch.setattr(cli, "MarketStore", Store)
    monkeypatch.setattr(cli, "load_market_rows", lambda *_a, **_k: [{
        "item_code": "bread", "item_name": "Bread", "bid": 1.0, "ask": 2.0,
    }])
    monkeypatch.setattr(cli, "load_highlight_trade_history", lambda *_a, **_k: {})
    monkeypatch.setattr(
        cli,
        "build_inflation_index_results",
        lambda store, **kwargs: calculations.append((store, kwargs)) or results,
    )
    monkeypatch.setattr(
        cli,
        "write_outputs",
        lambda _df, out, **kwargs: writes.append(kwargs) or (
            Path(out) / "market_trends.csv", Path(out) / "market_report.html"
        ),
    )
    monkeypatch.setattr(sys, "argv", [
        "warera-quant", "--from-db", "--market-db", str(tmp_path / "market.sqlite3"),
        "--output", str(tmp_path / "out"), "--quiet",
    ])

    cli.main()

    assert len(calculations) == 1
    assert len(writes) == 1
    assert writes[0]["inflation_results"] is results


def test_sync_only_never_calculates_inflation(monkeypatch, tmp_path):
    class Store:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def market_sync_metadata(self):
            return SimpleNamespace(synced_at=None, status=None)

    sync_result = SimpleNamespace(
        prices_observed=0,
        order_books_observed=0,
        pages_fetched=0,
        transactions_inserted=0,
        transactions_skipped=0,
    )
    calculations = []
    monkeypatch.setattr(cli, "load_dotenv", lambda: False)
    monkeypatch.setattr(cli, "MarketStore", Store)
    monkeypatch.setattr(cli, "WarEraApiClient", lambda **_kwargs: object())
    monkeypatch.setattr(cli, "WarEraMarketApi", lambda _client: object())
    monkeypatch.setattr(cli, "sync_market_data", lambda *_a, **_k: sync_result)
    monkeypatch.setattr(cli, "load_market_rows", lambda *_a, **_k: [])
    monkeypatch.setattr(
        cli, "build_inflation_index_results",
        lambda *_a, **_k: calculations.append(1),
    )
    monkeypatch.setattr(sys, "argv", [
        "warera-quant", "--sync", "--market-db", str(tmp_path / "market.sqlite3"), "--quiet",
    ])

    cli.main()

    assert calculations == []
