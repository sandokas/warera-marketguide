import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import warera_quant.cli as cli_module
from warera_quant.cli import build_parser, main
from warera_quant.market_store import MarketStore


def test_order_book_sync_defaults_to_api_maximum():
    args = build_parser().parse_args([])
    assert args.order_limit == 100


def test_from_db_preserves_structured_order_book_for_report(monkeypatch, tmp_path):
    class DummyStore:
        def __init__(self, _path):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    captured = {}
    book = {"best_bid": 9, "best_ask": 10, "bids": [], "asks": []}
    monkeypatch.setattr(cli_module, "MarketStore", DummyStore)
    monkeypatch.setattr(
        cli_module,
        "load_market_rows",
        lambda _store, **_kwargs: [{
            "item_name": "Bread",
            "order_book": book,
            "order_book_executions": [],
        }],
    )

    def capture_outputs(df, output_dir, **_kwargs):
        captured["book"] = df.iloc[0]["order_book"]
        return output_dir / "market_trends.csv", output_dir / "market_report.html"

    monkeypatch.setattr(cli_module, "write_outputs", capture_outputs)
    monkeypatch.setattr(sys, "argv", [
        "warera-quant", "--from-db", "--market-db", str(tmp_path / "market.sqlite3"),
        "--output", str(tmp_path / "output"), "--quiet",
    ])

    main()

    assert captured["book"] == book


@pytest.mark.parametrize(
    "option,value",
    [
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
