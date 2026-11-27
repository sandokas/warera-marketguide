from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from warera_quant.charts import render_featured_chart, render_highlight_price_action_chart
from warera_quant.market_data import (
    DISPLAY_HISTORY_DAYS,
    build_forecast_features,
    estimate_latest_execution,
    evaluate_item_forecast,
    load_chart_data,
    load_chart_trades,
    load_highlight_trade_history,
    load_price_action_history,
    load_market_rows,
    parse_report_window,
)
from warera_quant.market_store import MarketStore
from warera_quant.report_context import resolve_context
from warera_quant.metrics import build_price_action_candles, prepare_price_action_item
from warera_quant.warera_api import OrderLevel, TopOrders


NOW = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)


def test_highlight_history_is_fixed_to_trailing_90_days_and_excludes_future(tmp_path):
    with _store(tmp_path) as store:
        store.upsert_transactions("bread", [
            _transaction("old", (NOW - timedelta(days=90, seconds=1)).isoformat(), money=10, quantity=1),
            _transaction("boundary", (NOW - timedelta(days=90)).isoformat(), money=11, quantity=1),
            _transaction("recent", (NOW - timedelta(days=1)).isoformat(), money=12, quantity=1),
            _transaction("future", (NOW + timedelta(seconds=1)).isoformat(), money=13, quantity=1),
        ], fetched_at=NOW)
        result = load_highlight_trade_history(store, item_codes=["Bread"], now=NOW)
    assert [trade["price"] for trade in result["bread"]] == [11.0, 12.0]


def test_price_action_history_reports_authentic_sparse_coverage(tmp_path):
    with _store(tmp_path) as store:
        first = NOW - timedelta(days=70)
        last = NOW - timedelta(days=2)
        store.upsert_transactions("bread", [
            _transaction("first", first.isoformat(), money=10, quantity=1),
            _transaction("same-time", first.isoformat(), money=11, quantity=1),
            _transaction("last", last.isoformat(), money=12, quantity=1),
        ], fetched_at=NOW)
        result = load_price_action_history(store, item_code="Bread", context=resolve_context(NOW), window_days=90)

    assert result.window_days == DISPLAY_HISTORY_DAYS == 90
    assert result.window_start == NOW - timedelta(days=90)
    assert result.window_end == NOW
    assert len(result.trades) == 3
    assert result.coverage.first_observation_at == first
    assert result.coverage.last_observation_at == last
    assert result.coverage.observation_count == 3


def test_price_action_history_empty_coverage_is_explicit(tmp_path):
    with _store(tmp_path) as store:
        result = load_price_action_history(store, item_code="bread", now=NOW)

    assert result.trades == ()
    assert result.coverage.first_observation_at is None
    assert result.coverage.last_observation_at is None
    assert result.coverage.observation_count == 0


@pytest.mark.parametrize("interval,start_hour", [("1h", 21), ("2h", 20), ("4h", 20), ("1D", 0)])
def test_price_action_history_loads_whole_first_candle(tmp_path, interval, start_hour):
    # Local time must not change the chart's UTC candle boundaries.
    now = datetime(2026, 9, 22, 22, 18, tzinfo=timezone(timedelta(hours=1)))
    cutoff = now.astimezone(timezone.utc) - timedelta(days=30)
    start = cutoff.replace(hour=start_hour, minute=0)
    with _store(tmp_path) as store:
        store.upsert_transactions("bread", [
            _transaction("before", (start - timedelta(seconds=1)).isoformat(), money=999, quantity=1),
            _transaction("open", start.isoformat(), money=14, quantity=2),
            _transaction("high", (start + timedelta(minutes=1)).isoformat(), money=60, quantity=3),
            _transaction("low", (cutoff + timedelta(seconds=1)).isoformat(), money=8, quantity=4),
            _transaction("close", (cutoff + timedelta(seconds=2)).isoformat(), money=55, quantity=5),
            _transaction("current", now.isoformat(), money=10, quantity=1),
            _transaction("future", (now + timedelta(seconds=1)).isoformat(), money=999, quantity=1),
        ], fetched_at=now)
        history = load_price_action_history(
            store, item_code="Bread", context=resolve_context(now), window_days=30, interval=interval,
        )

    assert history.window_start == start
    assert history.window_end == now
    assert history.coverage.observation_count == 5
    candles = build_price_action_candles(history.trades, interval=interval)
    assert candles.iloc[0].to_dict() == {
        "Open": 7, "High": 20, "Low": 2, "Close": 11, "Volume": 14,
    }
    assert candles.iloc[-1]["Close"] == 10


@pytest.mark.parametrize("interval", ["1h", "2h", "4h", "1D"])
def test_price_action_history_preserves_exact_candle_boundary(tmp_path, interval):
    now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    with _store(tmp_path) as store:
        history = load_price_action_history(store, item_code="bread", context=resolve_context(now), interval=interval)
    assert history.window_start == now - timedelta(days=30)
    assert history.trades == ()


@pytest.mark.parametrize("interval", ["1h", "2h", "4h", "1D"])
def test_price_action_chart_marks_only_current_boundary_candle_partial(tmp_path, monkeypatch, interval):
    from matplotlib.figure import Figure

    now = datetime(2026, 9, 22, 21, 18, tzinfo=timezone.utc)
    with _store(tmp_path) as store:
        store.upsert_transactions("bread", [
            _transaction(str(day), (now - timedelta(days=day)).isoformat(), money=10, quantity=1)
            for day in range(31)
        ], fetched_at=now)
        history = load_price_action_history(store, item_code="bread", context=resolve_context(now), interval=interval)
    item = prepare_price_action_item({
        "item_code": "bread", "item_name": "Bread",
        "last_trade_price": 10, "stable_fair_price_7d": 10,
    }, history, interval=interval)
    assert item is not None
    captured = []

    def check_volume_bars(figure, *args, **kwargs):
        hatches = [bar.get_hatch() for bar in figure.axes[1].patches]
        assert hatches[0] is None
        assert hatches[-1] == "//"
        assert hatches.count("//") == 1
        captured.append(True)

    monkeypatch.setattr(Figure, "savefig", check_volume_bars)
    render_highlight_price_action_chart(item, tmp_path / "candles.png")
    assert captured == [True]


def _store(tmp_path: Path) -> MarketStore:
    store = MarketStore(tmp_path / "market.sqlite3")
    store.initialize()
    return store


def test_estimate_latest_execution_includes_snapshot_age_and_sweep(tmp_path):
    with _store(tmp_path) as store:
        store.insert_order_book_observations(
            {"bread": TopOrders(
                buy_orders=[OrderLevel(3, 10)],
                sell_orders=[OrderLevel(4, 2), OrderLevel(5, 4)],
            )},
            datetime(2026, 6, 30, 11, 59, tzinfo=timezone.utc),
        )

        estimate = estimate_latest_execution(
            store, item_code="bread", side="buy", quantity=5, now=NOW
        )

    assert estimate is not None
    assert estimate["levels_available"] is True
    assert estimate["snapshot_age_seconds"] == 0  # Future reference clamps to the DB ceiling.
    assert estimate["execution"].gross_value == 23
    assert estimate["execution"].fully_filled is True


def test_forecast_features_exclude_future_transactions():
    observations = [{
        "observation_id": 1,
        "observed_at": "2026-06-30T12:00:00Z",
        "observed_at_epoch": int(NOW.timestamp()),
        "best_bid": 10,
        "best_ask": 12,
        "bid_depth": 2,
        "ask_depth": 1,
        "bids": [],
        "asks": [],
        "levels_available": False,
    }]
    transactions = [
        {"id": "before", "created_at_epoch": int(NOW.timestamp()) - 60, "unit_price": 10, "quantity": 1},
        {"id": "future", "created_at_epoch": int(NOW.timestamp()) + 60, "unit_price": 1000, "quantity": 1},
    ]

    feature = build_forecast_features(observations, transactions)[0]

    assert feature["trailing_count"] == 1
    assert feature["trailing_close"] == 10
    assert feature["stable_fair_price"] == 10


def test_walk_forward_uses_first_target_and_rejects_late_next_target(tmp_path):
    t0 = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
    with _store(tmp_path) as store:
        store.upsert_transactions(
            "bread",
            [
                _transaction("open", "2026-05-31T10:00:00Z", money=10, quantity=1),
                _transaction("close", "2026-06-01T10:00:00Z", money=11, quantity=1),
            ],
            fetched_at=t0,
        )
        _insert_book(store, t0, bid=10, ask=11, quantity=5)
        _insert_book(store, t0 + timedelta(hours=25), bid=12, ask=13, quantity=5)
        _insert_book(store, t0 + timedelta(hours=56), bid=14, ask=15, quantity=5)

        result = evaluate_item_forecast(store, item_code="bread", min_samples=1)

    assert result.candidate_samples == 1
    assert result.evaluable_samples == 1
    assert result.evaluated_rows[0].target_timestamp_epoch == int((t0 + timedelta(hours=25)).timestamp())
    assert result.evaluated_rows[0].outcome == "Up"
    assert result.execution_evaluable_samples == 1
    assert result.evaluated_rows[0].gross_flip_return_pct == pytest.approx((12 - 11) / 11 * 100)


def test_walk_forward_quantity_excludes_partial_entry_and_exit_depth(tmp_path):
    t0 = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
    with _store(tmp_path) as store:
        store.upsert_transactions(
            "bread",
            [
                _transaction("open", "2026-05-31T10:00:00Z", money=10, quantity=1),
                _transaction("close", "2026-06-01T10:00:00Z", money=11, quantity=1),
            ],
            fetched_at=t0,
        )
        _insert_book(store, t0, bid=10, ask=11, quantity=1)
        _insert_book(store, t0 + timedelta(hours=24), bid=12, ask=13, quantity=1)
        one = evaluate_item_forecast(store, item_code="bread", quantity=1)
        two = evaluate_item_forecast(store, item_code="bread", quantity=2)

    assert one.execution_evaluable_samples == 1
    assert two.execution_evaluable_samples == 0
    assert two.evaluated_rows[0].gross_flip_return_pct is None


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
                "iron_ore": TopOrders(
                    buy_orders=[OrderLevel(6.0, 10)],
                    sell_orders=[OrderLevel(7.0, 20)],
                )
            },
            datetime(2026, 6, 30, 11, 45, tzinfo=timezone.utc),
        )

        rows = load_market_rows(store, windows=["1D"], context=resolve_context(NOW))

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
    assert stats["price_p25"] == pytest.approx(3.5)
    assert stats["price_p90"] == pytest.approx(5.8)
    assert stats["stable_fair_price"] == pytest.approx(((190 / 40) * 0.5) + (5 * 0.3) + (((2 + 5 + 6) / 3) * 0.2))
    assert stats["stable_range_pct"] == pytest.approx((5.8 - 2.6) / 5 * 100)
    assert stats["open"] == 2.0
    assert stats["close"] == 6.0
    assert stats["change_abs"] == 4.0
    assert stats["percent_change"] == pytest.approx(200.0)
    assert stats["volume"] == 40.0
    assert stats["traded_value"] == 190.0
    assert stats["last_trade_price"] == 6.0
    assert stats["quote_price"] == 6.5
    assert stats["mid_price"] == 6.5
    assert stats["current_price"] == 6.0
    assert stats["latest_price"] == 6.0
    assert stats["latest_bid"] == 6.0
    assert stats["latest_ask"] == 7.0
    assert stats["latest_bid_depth"] == 10.0
    assert stats["latest_ask_depth"] == 20.0
    assert stats["latest_depth_imbalance_pct"] == pytest.approx(-33.333333)
    assert stats["latest_spread"] == 1.0
    assert stats["latest_spread_pct"] == pytest.approx(15.384615)
    assert stats["average_spread"] == 1.0
    assert stats["liquidity"] == pytest.approx(30 / (1 + 15.384615 / 100))
    assert stats["rolling_average"] == pytest.approx((2 + 5 + 6) / 3)
    assert stats["distance_from_rolling_average"] == pytest.approx(6 - ((2 + 5 + 6) / 3))
    assert stats["tendency"] == "Rising"
    assert stats["tendency_labels"] == "Rising, Volatile"
    assert row["min_1d"] == 2.0
    assert row["vwap_1d"] == pytest.approx(190 / 40)
    assert row["stable_fair_price_1d"] == pytest.approx(stats["stable_fair_price"])
    assert row["stable_range_pct_1d"] == pytest.approx(stats["stable_range_pct"])
    assert row["tendency_labels_1d"] == "Rising, Volatile"
    assert row["latest_price"] == 6.0
    assert row["latest_bid"] == 6.0
    assert row["latest_ask"] == 7.0
    assert row["latest_spread"] == 1.0
    assert row["order_book"]["bid_value"] == 60.0
    assert row["order_book"]["ask_value"] == 140.0
    assert row["order_book"]["pressure_pct"] == pytest.approx(-40.0)
    assert row["flip_quantity"] == 1.0
    assert row["guide_fair_price"] == pytest.approx(stats["vwap"])
    assert row["guide_fair_price"] != pytest.approx(stats["stable_fair_price"])
    assert row["flip_quote_age_minutes"] == 15.0
    assert row["flip_entry_fully_filled"] is True
    assert row["flip_verdict"] == "Unavailable"
    assert row["flip_reason_codes"] == "missing_execution_interval"


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
    assert row["trend_path_30d"] == [
        {"timestamp": int(datetime(2026, 6, 22, 11, 59, 59, tzinfo=timezone.utc).timestamp()), "price": 10.0},
        {"timestamp": int(datetime(2026, 6, 23, 12, tzinfo=timezone.utc).timestamp()), "price": 7.0},
        {"timestamp": int(datetime(2026, 6, 29, 12, tzinfo=timezone.utc).timestamp()), "price": 3.0},
    ]
    assert row["trend_path_30d_start_epoch"] == int((NOW - timedelta(days=30)).timestamp())
    assert row["trend_path_30d_end_epoch"] == int(NOW.timestamp())
    assert row["trend_path_90d"] == row["trend_path_30d"]
    assert row["trend_path_90d_start_epoch"] == int((NOW - timedelta(days=90)).timestamp())
    assert row["trend_path_90d_end_epoch"] == int(NOW.timestamp())
    assert row["trend_path_90d_first_observation_epoch"] == int(
        datetime(2026, 6, 22, 11, 59, 59, tzinfo=timezone.utc).timestamp()
    )
    assert row["trend_path_90d_last_observation_epoch"] == int(
        datetime(2026, 6, 29, 12, tzinfo=timezone.utc).timestamp()
    )
    assert row["trend_path_90d_observation_count"] == 3


def test_window_change_requires_two_distinct_transaction_timestamps(tmp_path):
    with _store(tmp_path) as store:
        store.upsert_transactions(
            "bread",
            [
                _transaction("tx-a", "2026-06-30T10:00:00Z", money=10, quantity=1),
                _transaction("tx-b", "2026-06-30T10:00:00Z", money=12, quantity=1),
            ],
            fetched_at=NOW,
        )

        row = load_market_rows(store, windows=["1D"], now=NOW)[0]

    assert row["trade_count_1d"] == 2
    assert row["distinct_trade_timestamp_count_1d"] == 1
    assert row["percent_change_1d"] is None


def test_load_market_rows_keeps_legacy_metric_fields_for_current_report(tmp_path):
    with _store(tmp_path) as store:
        store.upsert_transactions(
            "bread",
            [_transaction("tx-1", "2026-06-30T11:00:00Z", money=12, quantity=4)],
            fetched_at=NOW,
        )
        store.insert_price_observations({"bread": 3.0}, NOW)
        store.upsert_item_production_points({"bread": 10}, NOW)

        row = load_market_rows(store, lookback_days=1, now=NOW)[0]

    assert row["trades_7d"] == 1
    assert row["high_7d"] == 3.0
    assert row["low_7d"] == 3.0
    assert row["open_7d"] == 3.0
    assert row["close_7d"] == 3.0
    assert row["production_points"] == 10.0


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


def test_load_market_rows_prefers_trade_history_for_current_price(tmp_path):
    with _store(tmp_path) as store:
        store.upsert_transactions(
            "paper",
            [_transaction("tx-1", "2026-06-30T11:00:00Z", money=18.6, quantity=100)],
            fetched_at=NOW,
        )
        store.insert_price_observations({"paper": 0.2}, NOW)
        store.insert_order_book_observations(
            {
                "paper": TopOrders(
                    buy_orders=[OrderLevel(0.19, 10)],
                    sell_orders=[OrderLevel(0.21, 10)],
                )
            },
            datetime(2026, 6, 30, 11, 30, tzinfo=timezone.utc),
        )

        row = load_market_rows(store, windows=["1D"], now=NOW)[0]

    assert row["last_trade_price"] == pytest.approx(0.186)
    assert row["quote_price"] == pytest.approx(0.2)
    assert row["mid_price"] == pytest.approx(0.2)
    assert row["current_price"] == pytest.approx(0.186)
    assert row["latest_price"] == pytest.approx(0.186)
    assert row["quote_gap_pct"] == pytest.approx(7.526881720430108)


def test_load_market_rows_keeps_trade_price_missing_when_no_trade_exists(tmp_path):
    with _store(tmp_path) as store:
        store.insert_price_observations({"gold": 97.5}, NOW)
        store.insert_order_book_observations(
            {
                "gold": TopOrders(
                    buy_orders=[OrderLevel(97.0, 5)],
                    sell_orders=[OrderLevel(98.0, 8)],
                )
            },
            datetime(2026, 6, 30, 11, 30, tzinfo=timezone.utc),
        )

        row = load_market_rows(store, windows=["1D"], now=NOW)[0]

    assert row["last_trade_price"] is None
    assert row["quote_price"] == pytest.approx(97.5)
    assert row["mid_price"] == pytest.approx(97.5)
    assert row["current_price"] is None
    assert row["latest_price"] is None
    assert row["quote_gap_pct"] is None
    assert row["stable_fair_price_1d"] is None
    assert row["guide_fair_price"] is None
    assert row["guide_entry_action"] == "WAIT"
    assert row["guide_holder_action"] == "HOLD"


def test_load_market_rows_uses_low_liquidity_fallback_when_depth_is_missing(tmp_path):
    with _store(tmp_path) as store:
        store.upsert_transactions(
            "gold",
            [_transaction("tx-1", "2026-06-30T11:00:00Z", money=100, quantity=10)],
            fetched_at=NOW,
        )

        row = load_market_rows(store, windows=["1D"], now=NOW)[0]

    assert row["volume_1d"] == 10
    assert row["trade_count_1d"] == 1
    assert row["liquidity_1d"] == 0


def test_load_market_rows_tracks_quote_divergence_and_depth_imbalance(tmp_path):
    with _store(tmp_path) as store:
        store.upsert_transactions(
            "silver",
            [_transaction("tx-1", "2026-06-30T11:00:00Z", money=105.0, quantity=5)],
            fetched_at=NOW,
        )
        store.insert_price_observations({"silver": 110.0}, NOW)
        store.insert_order_book_observations(
            {
                "silver": TopOrders(
                    buy_orders=[OrderLevel(108.0, 20)],
                    sell_orders=[OrderLevel(112.0, 10)],
                )
            },
            datetime(2026, 6, 30, 11, 30, tzinfo=timezone.utc),
        )

        row = load_market_rows(store, windows=["1D"], now=NOW)[0]

    assert row["last_trade_price"] == pytest.approx(21.0)
    assert row["quote_price"] == pytest.approx(110.0)
    assert row["mid_price"] == pytest.approx(110.0)
    assert row["current_price"] == pytest.approx(21.0)
    assert row["quote_gap_pct"] == pytest.approx(423.8095238095238)
    assert row["latest_bid"] == pytest.approx(108.0)
    assert row["latest_ask"] == pytest.approx(112.0)
    assert row["latest_depth_imbalance_pct"] == pytest.approx(33.33333333333333)
    assert row["depth_imbalance_pct"] == pytest.approx(33.33333333333333)


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
                "bread": TopOrders(
                    buy_orders=[OrderLevel(2.8, 4)],
                    sell_orders=[OrderLevel(3.2, 4)],
                )
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
                "bread": TopOrders(
                    buy_orders=[OrderLevel(2.8, 4)],
                    sell_orders=[OrderLevel(3.2, 4)],
                )
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


def _insert_book(
    store: MarketStore, observed_at: datetime, *, bid: float, ask: float, quantity: float
) -> None:
    store.insert_order_book_observations(
        {"bread": TopOrders(
            buy_orders=[OrderLevel(bid, quantity)],
            sell_orders=[OrderLevel(ask, quantity)],
        )},
        observed_at,
    )


def test_guidance_read_model_preserves_size_slippage_and_rejects_future_quotes(tmp_path):
    from warera_quant.metrics import FlipAssumptions
    with _store(tmp_path) as store:
        store.insert_order_book_observations({'bread': TopOrders(
            buy_orders=[OrderLevel(3, 10)],
            sell_orders=[OrderLevel(4, 2), OrderLevel(5, 4)],
        )}, NOW)
        row = load_market_rows(store, now=NOW, flip_assumptions=FlipAssumptions(quantity=5))[0]
        assert row['guide_executable_ask_vwap'] == pytest.approx(4.6)
        assert row['guide_entry_slippage_pct'] == pytest.approx(15)
        assert row['guide_entry_filled_quantity'] == 5
        assert row['short_term_quote_fresh'] is True
        future = load_market_rows(store, now=NOW-timedelta(minutes=1), flip_assumptions=FlipAssumptions(quantity=5))[0]
        assert future['flip_quote_age_minutes'] == -1
        assert future['short_term_quote_fresh'] is False
        assert future['short_term_reference_return_pct'] is None


def test_mixed_database_keeps_item_cards_tables_and_metrics_commodity_only(tmp_path):
    import pandas as pd
    from warera_quant.warera_api import normalize_transaction
    from warera_quant.report import generate_html_report
    from warera_quant.market_data import build_we24_market_index
    with _store(tmp_path) as store:
        store.upsert_transactions("bread", [_transaction("bread-sale", "2026-06-30T11:00:00Z", money=12, quantity=4)])
        store.upsert_transactions("delistedCode", [_transaction("historic", "2026-06-30T11:00:00Z", money=2, quantity=1)])
        store.insert_price_observations({"priceOnly": 1}, NOW)
        store.insert_order_book_observations({"orderOnly": TopOrders([OrderLevel(1, 3)], [])}, NOW)
        before = load_market_rows(store, now=NOW)
        index_before = build_we24_market_index(store, as_of=NOW)
        for code in ("bread", "rareWeapon"):
            store.ingest_transactions([normalize_transaction({"_id": "equipment-" + code,
                "transactionType": "itemMarket", "itemCode": code, "createdAt": "2026-06-30T11:01:00Z",
                "money": 999999, "quantity": 1,
                "item": {"code": code, "skills": {"attack": 999}, "state": 10}})])
        after = load_market_rows(store, now=NOW)
        assert after == before  # Includes VWAP, guidance, forecasts and the entire shared item list.
        assert build_we24_market_index(store, as_of=NOW) == index_before
        assert {r["item_code"] for r in after} == {"bread", "delistedCode", "priceOnly", "orderOnly"}
        assert next(r for r in after if r["item_code"] == "delistedCode")["item_name"] == "delistedCode"
        assert len(load_chart_trades(store, item_code="bread", window="7D", now=NOW)) == 1
        html = generate_html_report(pd.DataFrame(after))
        assert "rareWeapon" not in html and "Rare Weapon" not in html
        assert "Bread" in html
        assert store.item_codes(transaction_type="itemMarket") == ["bread", "rareWeapon"]
        assert len(store.transactions_for_window("rareWeapon", 0, transaction_type="itemMarket")) == 1
        assert store.transaction_details("equipment-rareWeapon")["stats"][0]["value_decimal"] == "999"

def test_market_rows_hide_wooden_case_from_removal_date(tmp_path):
    removal = datetime(2026, 11, 18, tzinfo=timezone.utc)
    with _store(tmp_path) as store:
        for code in ("woodenCase", "case1"):
            store.upsert_transactions(code, [
                _transaction(code, "2026-11-17T11:00:00Z", money=2, quantity=1),
            ])
        store.insert_price_observations({"case1": 2}, removal)
        assert {row["item_code"] for row in load_market_rows(
            store, now=removal - timedelta(seconds=1),
        )} == {"woodenCase", "case1"}
        assert [row["item_code"] for row in load_market_rows(store, now=removal)] == ["case1"]
        assert set(store.item_codes(transaction_type="trading")) == {"woodenCase", "case1"}


@pytest.mark.parametrize("start_relation", ["earlier", "equal", "later"])
def test_report_forecast_trade_history_reuse(tmp_path, monkeypatch, start_relation):
    from warera_quant import market_data

    cutoff = NOW + timedelta(microseconds=500000 if start_relation == "earlier" else 0)
    monkeypatch.setattr(market_data, "DISPLAY_HISTORY_DAYS", 37)
    report_start = cutoff - timedelta(days=37)
    first_book = NOW - timedelta(days=30)
    if start_relation == "earlier":
        first_book += timedelta(microseconds=750000)
    if start_relation == "later":
        first_book += timedelta(seconds=1)

    with _store(tmp_path) as store:
        store.upsert_transactions(
            "bread",
            [
                _transaction(str(index), (report_start + timedelta(microseconds=offset)).isoformat(),
                             money=10 + index, quantity=1)
                for index, offset in enumerate((-1, 0, 0, 1, 1_000_000))
            ] + [_transaction("cutoff", cutoff.isoformat(), money=20, quantity=1)],
            fetched_at=cutoff,
        )
        _insert_book(store, first_book, bid=10, ask=11, quantity=5)
        _insert_book(store, first_book + timedelta(days=1), bid=12, ask=13, quantity=5)
        read = store.transactions_for_window
        reads = []

        def record_read(item, start, *, end):
            reads.append((start, end))
            return read(item, start, end=end)

        monkeypatch.setattr(store, "transactions_for_window", record_read)
        context = store.resolve_report_context(cutoff)
        optimized = load_market_rows(store, context=context, forecast_min_samples=1)
        forecast_start = int(first_book.timestamp()) - 7 * 86400
        expected = [(report_start.timestamp(), context.window_end_exclusive)]
        if start_relation == "earlier":
            expected.append((forecast_start, report_start))
        assert reads == expected

        evaluate = market_data._evaluate_item_forecast

        def fetch_separately(*args, report_history, **kwargs):
            shared = evaluate(*args, report_history=report_history, **kwargs)
            separate = evaluate(*args, **kwargs)
            assert shared == separate
            return separate

        monkeypatch.setattr(market_data, "_evaluate_item_forecast", fetch_separately)
        assert load_market_rows(store, context=context, forecast_min_samples=1) == optimized

def test_recent_backtests_use_trades_and_ignore_old_orders(tmp_path):
    origin = NOW.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=2)
    cutoff = origin + timedelta(days=2, hours=12)
    sales = [
        ("warmup", origin - timedelta(days=6), 8),
        *[(f"before{i}", origin - timedelta(minutes=50 - i), 10) for i in range(3)],
        *[(f"after{i}", origin + timedelta(days=1, minutes=i), 13) for i in range(3)],
        ("cutoff", cutoff, 14),
    ]
    with _store(tmp_path) as store:
        store.upsert_transactions(
            "bread", [_transaction(key, at.isoformat(), money=price, quantity=1)
                      for key, at, price in sales], fetched_at=cutoff,
        )
        arguments = dict(now=cutoff, requested_backtest_start=origin, forecast_min_samples=1)
        no_orders = load_market_rows(store, **arguments)[0]
        assert no_orders["trade_forecast_scored_samples"] == 1
        assert no_orders["trade_forecast_accuracy_pct"] == 100
        assert no_orders["trade_forecast_sparse_samples"] == 1
        assert no_orders["trade_forecast_incomplete_samples"] == 1
        old = origin - timedelta(days=31)
        _insert_book(store, old, bid=9, ask=11, quantity=5)
        old_only = load_market_rows(store, **arguments)[0]
        assert datetime.fromisoformat(old_only["forecast_current_observed_at"]) == old
        assert old_only["forecast_candidate_samples"] == 0
        _insert_book(store, origin, bid=9, ask=11, quantity=5)
        _insert_book(store, origin + timedelta(days=1), bid=13, ask=15, quantity=5)
        recent = load_market_rows(store, **arguments)[0]
        assert recent["forecast_candidate_samples"] == 1
        trade_keys = [key for key in no_orders if key.startswith("trade_forecast_")]
        assert all(recent[key] == old_only[key] == no_orders[key] for key in trade_keys)
