from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from warera_quant.charts import render_featured_chart
from warera_quant.market_data import load_chart_data, load_chart_trades, load_market_rows, parse_report_window
from warera_quant.market_store import MarketStore


NOW = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)


def _store(tmp_path: Path) -> MarketStore:
    store = MarketStore(tmp_path / "market.sqlite3")
    store.initialize()
    return store


def test_parse_report_window_accepts_supported_windows():
    assert parse_report_window("1D").days == 1
    assert parse_report_window("7d").days == 7
    assert parse_report_window("30D").days == 30
    assert parse_report_window("90D").days == 90
    assert parse_report_window("1Y").days == 365


def test_parse_report_window_rejects_unknown_windows():
    with pytest.raises(ValueError, match="Unsupported report window"):
        parse_report_window("2W")


def test_load_market_rows_computes_window_statistics(tmp_path):
    with _store(tmp_path) as store:
        store.upsert_transactions(
            "iron_ore",
            [
                _transaction("tx-old", "2026-06-22T12:00:00Z", money=100, quantity=10),
                _transaction("tx-open", "2026-06-29T12:00:00Z", money=20, quantity=10),
                _transaction("tx-mid", "2026-06-30T00:00:00Z", money=50, quantity=10),
                _transaction("tx-close", "2026-06-30T11:00:00Z", money=120, quantity=20),
            ],
            fetched_at=NOW,
        )
        store.insert_price_observations(
            {"iron_ore": 6.5},
            datetime(2026, 6, 30, 11, 30, tzinfo=timezone.utc),
        )
        store.insert_order_book_observations(
            {
                "iron_ore": {
                    "buyOrders": [{"price": 6.0, "quantity": 10}],
                    "sellOrders": [{"price": 7.0, "quantity": 20}],
                }
            },
            datetime(2026, 6, 30, 11, 45, tzinfo=timezone.utc),
        )

        rows = load_market_rows(store, windows=["1D"], now=NOW)

    assert len(rows) == 1
    row = rows[0]
    stats = row["windows"]["1D"]
    assert stats["trade_count"] == 3
    assert stats["min"] == 2.0
    assert stats["max"] == 6.0
    assert stats["average"] == pytest.approx((2 + 5 + 6) / 3)
    assert stats["vwap"] == pytest.approx(190 / 40)
    assert stats["median"] == 5.0
    assert stats["price_p10"] == pytest.approx(2.6)
    assert stats["price_p90"] == pytest.approx(5.8)
    assert stats["stable_fair_price"] == pytest.approx(((190 / 40) * 0.5) + (5 * 0.3) + (((2 + 5 + 6) / 3) * 0.2))
    assert stats["stable_range_pct"] == pytest.approx((5.8 - 2.6) / 5 * 100)
    assert stats["open"] == 2.0
    assert stats["close"] == 6.0
    assert stats["change_abs"] == 4.0
    assert stats["percent_change"] == pytest.approx(200.0)
    assert stats["volume"] == 40.0
    assert stats["traded_value"] == 190.0
    assert stats["latest_price"] == 6.5
    assert stats["latest_bid"] == 6.0
    assert stats["latest_ask"] == 7.0
    assert stats["latest_bid_depth"] == 10.0
    assert stats["latest_ask_depth"] == 20.0
    assert stats["latest_depth_imbalance_pct"] == pytest.approx(-33.333333)
    assert stats["latest_spread"] == 1.0
    assert stats["latest_spread_pct"] == pytest.approx(15.384615)
    assert stats["average_spread"] == 1.0
    assert stats["liquidity"] == pytest.approx(3 * 40 / (1 + 15.384615 / 100))
    assert stats["rolling_average"] == pytest.approx((2 + 5 + 6) / 3)
    assert stats["distance_from_rolling_average"] == pytest.approx(6 - ((2 + 5 + 6) / 3))
    assert stats["tendency"] == "Rising"
    assert stats["tendency_labels"] == "Rising, Volatile"
    assert row["min_1d"] == 2.0
    assert row["vwap_1d"] == pytest.approx(190 / 40)
    assert row["stable_fair_price_1d"] == pytest.approx(stats["stable_fair_price"])
    assert row["stable_range_pct_1d"] == pytest.approx(stats["stable_range_pct"])
    assert row["tendency_labels_1d"] == "Rising, Volatile"
    assert row["latest_price"] == 6.5
    assert row["latest_bid"] == 6.0
    assert row["latest_ask"] == 7.0
    assert row["latest_spread"] == 1.0


def test_load_market_rows_applies_each_window_boundary(tmp_path):
    with _store(tmp_path) as store:
        store.upsert_transactions(
            "bread",
            [
                _transaction("tx-8d", "2026-06-22T11:59:59Z", money=100, quantity=10),
                _transaction("tx-7d", "2026-06-23T12:00:00Z", money=70, quantity=10),
                _transaction("tx-1d", "2026-06-29T12:00:00Z", money=30, quantity=10),
            ],
            fetched_at=NOW,
        )
        store.insert_price_observations({"bread": 3.0}, NOW)

        row = load_market_rows(store, windows=["1D", "7D", "30D", "90D", "1Y"], now=NOW)[0]

    assert row["trade_count_1d"] == 1
    assert row["trade_count_7d"] == 2
    assert row["trade_count_30d"] == 3
    assert row["trade_count_90d"] == 3
    assert row["trade_count_1y"] == 3
    assert row["open_7d"] == 7.0
    assert row["close_7d"] == 3.0


def test_load_market_rows_keeps_legacy_metric_fields_for_current_report(tmp_path):
    with _store(tmp_path) as store:
        store.upsert_transactions(
            "bread",
            [_transaction("tx-1", "2026-06-30T11:00:00Z", money=12, quantity=4)],
            fetched_at=NOW,
        )
        store.insert_price_observations({"bread": 3.0}, NOW)

        row = load_market_rows(store, lookback_days=1, now=NOW)[0]

    assert row["trades_7d"] == 1
    assert row["high_7d"] == 3.0
    assert row["low_7d"] == 3.0
    assert row["open_7d"] == 3.0
    assert row["close_7d"] == 3.0


def test_load_market_rows_uses_percentile_range_to_reduce_outlier_noise(tmp_path):
    with _store(tmp_path) as store:
        store.upsert_transactions(
            "copper",
            [
                _transaction("tx-1", "2026-06-30T08:00:00Z", money=10, quantity=10),
                _transaction("tx-2", "2026-06-30T09:00:00Z", money=100, quantity=10),
                _transaction("tx-3", "2026-06-30T10:00:00Z", money=101, quantity=10),
                _transaction("tx-4", "2026-06-30T11:00:00Z", money=102, quantity=10),
                _transaction("tx-5", "2026-06-30T11:30:00Z", money=1000, quantity=10),
            ],
            fetched_at=NOW,
        )
        store.insert_price_observations({"copper": 10.2}, NOW)

        row = load_market_rows(store, windows=["1D"], now=NOW)[0]

    stats = row["windows"]["1D"]
    raw_range = 100.0 - 1.0
    stable_range = stats["price_p90"] - stats["price_p10"]
    assert stable_range < raw_range
    assert stats["stable_fair_price"] < 30


def test_load_chart_trades_returns_normalized_priced_trades_inside_window(tmp_path):
    with _store(tmp_path) as store:
        store.upsert_transactions(
            "bread",
            [
                _transaction("tx-old", "2026-06-28T12:00:00Z", money=10, quantity=10),
                _transaction("tx-new", "2026-06-30T11:00:00Z", money=12, quantity=4),
            ],
            fetched_at=NOW,
        )

        rows = load_chart_trades(store, item_code="bread", window="1D", now=NOW)

    assert rows == [
        {
            "item_code": "bread",
            "created_at": "2026-06-30T11:00:00Z",
            "created_at_epoch": 1782817200,
            "price": 3.0,
            "quantity": 4.0,
            "value": 12.0,
            "transaction_type": "trading",
        }
    ]


def test_load_chart_data_returns_trades_and_spread_observations(tmp_path):
    with _store(tmp_path) as store:
        store.upsert_transactions(
            "bread",
            [_transaction("tx-new", "2026-06-30T11:00:00Z", money=12, quantity=4)],
            fetched_at=NOW,
        )
        store.insert_order_book_observations(
            {
                "bread": {
                    "buyOrders": [{"price": 2.8, "quantity": 4}],
                    "sellOrders": [{"price": 3.2, "quantity": 4}],
                }
            },
            datetime(2026, 6, 30, 11, 30, tzinfo=timezone.utc),
        )

        chart_data = load_chart_data(store, item_code="bread", window="1D", now=NOW)

    assert chart_data["item_code"] == "bread"
    assert chart_data["window"] == "1D"
    assert chart_data["trades"] == [
        {
            "item_code": "bread",
            "created_at": "2026-06-30T11:00:00Z",
            "created_at_epoch": 1782817200,
            "price": 3.0,
            "quantity": 4.0,
            "value": 12.0,
            "transaction_type": "trading",
        }
    ]
    assert chart_data["spread_observations"] == [
        {
            "item_code": "bread",
            "observed_at": "2026-06-30T11:30:00Z",
            "observed_at_epoch": 1782819000,
            "bid": 2.8,
            "ask": 3.2,
            "spread": pytest.approx(0.4),
            "spread_pct": pytest.approx(13.333333),
        }
    ]


def test_db_backed_chart_data_can_render(tmp_path):
    with _store(tmp_path) as store:
        store.upsert_transactions(
            "bread",
            [
                _transaction("tx-1", "2026-06-30T11:00:00Z", money=12, quantity=4),
                _transaction("tx-2", "2026-06-30T11:15:00Z", money=14, quantity=4),
            ],
            fetched_at=NOW,
        )
        store.insert_order_book_observations(
            {
                "bread": {
                    "buyOrders": [{"price": 2.8, "quantity": 4}],
                    "sellOrders": [{"price": 3.2, "quantity": 4}],
                }
            },
            datetime(2026, 6, 30, 11, 30, tzinfo=timezone.utc),
        )

        chart_data = load_chart_data(store, item_code="bread", window="1D", now=NOW)

    output = render_featured_chart(
        chart_data,
        tmp_path / "featured-trade.png",
        item_name="Featured Trade: Bread",
        show_moving_average=False,
    )

    assert output == tmp_path / "featured-trade.png"
    assert output.exists()


def _transaction(transaction_id: str, created_at: str, *, money: float, quantity: float) -> dict[str, object]:
    return {
        "id": transaction_id,
        "createdAt": created_at,
        "transactionType": "trading",
        "money": money,
        "quantity": quantity,
    }
