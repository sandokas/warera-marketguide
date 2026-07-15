import pytest

from warera_quant.metrics import (
    FORECAST_MODEL_VERSION,
    ForecastEvaluationRow,
    FlipAssumptions,
    calculate_book_sweep,
    calculate_flip_opportunity,
    calculate_fair_value_guidance,
    calculate_direction_signal,
    calculate_liquidity_score,
    calculate_notional_book_sweep,
    calculate_metrics,
    classify_future_bid_outcome,
    classify_tendency,
    forecast_evidence_label,
    summarize_forecast_evaluations,
    summarize_order_book,
    total_upstream_production_points,
)
from warera_quant.warera_api import OrderLevel


def test_fair_value_guidance_uses_executable_prices_fees_and_position_specific_actions():
    guidance = calculate_fair_value_guidance(
        fair_price=10,
        rich_exit_price=11,
        executable_ask_vwap=9.6,
        executable_bid_vwap=11.1,
        entry_fully_filled=True,
        exit_fully_filled=True,
        assumptions=FlipAssumptions(
            quantity=5,
            fee_pct_per_side=1,
            minimum_net_margin_pct=2,
        ),
    )

    assert guidance.max_entry_price == pytest.approx(10 * 0.99 / (1.01 * 1.02))
    assert guidance.rich_exit_price == pytest.approx(10.2)
    assert guidance.net_to_fair_pct == pytest.approx((10 * 0.99 - 9.6 * 1.01) / (9.6 * 1.01) * 100)
    assert guidance.ask_gap_pct == pytest.approx(-4)
    assert guidance.bid_gap_pct == pytest.approx(11)
    assert guidance.entry_action == "BUY"
    assert guidance.holder_action == "SELL"


def test_fair_value_guidance_fails_closed_for_unfilled_quotes():
    guidance = calculate_fair_value_guidance(
        fair_price=10,
        rich_exit_price=11,
        executable_ask_vwap=9,
        executable_bid_vwap=12,
        entry_fully_filled=False,
        exit_fully_filled=True,
        assumptions=FlipAssumptions(max_quote_age_minutes=30),
    )

    assert guidance.executable_ask_vwap is None
    assert guidance.entry_action == "WAIT"
    assert guidance.holder_action == "SELL"


@pytest.mark.parametrize(
    ("state", "expected_max", "expected_action"),
    [
        ("Stable", 10 / 1.01, "BUY"),
        ("Falling", 9.5, "BUY"),
        ("Volatile", 9.5, "BUY"),
        ("Falling, Volatile", 9.0, "WAIT"),
    ],
)
def test_fair_value_guidance_risk_adjusts_max_buy_by_market_state(
    state, expected_max, expected_action
):
    guidance = calculate_fair_value_guidance(
        fair_price=10,
        rich_exit_price=11,
        price_p10=9,
        price_p25=9.5,
        market_state=state,
        executable_ask_vwap=9.25,
        executable_bid_vwap=9.1,
        entry_fully_filled=True,
        exit_fully_filled=True,
        assumptions=FlipAssumptions(minimum_net_margin_pct=1),
    )

    assert guidance.max_entry_price == pytest.approx(expected_max)
    assert guidance.entry_action == expected_action


def test_book_sweep_calculates_multilevel_buy_vwap_without_mutating_input():
    levels = [OrderLevel(11, 5), OrderLevel(10, 5)]

    result = calculate_book_sweep(levels, side="buy", quantity=8)

    assert levels == [OrderLevel(11, 5), OrderLevel(10, 5)]
    assert result.filled_quantity == 8
    assert result.fully_filled is True
    assert result.gross_value == 83
    assert result.average_price == pytest.approx(10.375)
    assert result.best_price == 10
    assert result.worst_price == 11
    assert result.slippage_abs == pytest.approx(0.375)
    assert result.slippage_pct == pytest.approx(3.75)


def test_book_sweep_calculates_sell_and_partial_fill():
    result = calculate_book_sweep(
        [{"price": 9, "quantity": 2}, {"price": 10, "quantity": 3}],
        side="sell",
        quantity=8,
    )

    assert result.filled_quantity == 5
    assert result.unfilled_quantity == 3
    assert result.fully_filled is False
    assert result.gross_value == 48
    assert result.average_price == pytest.approx(9.6)
    assert result.slippage_pct == pytest.approx(4)


def test_book_sweep_empty_and_invalid_inputs():
    empty = calculate_book_sweep([], side="buy", quantity=1)
    assert empty.filled_quantity == 0
    assert empty.average_price is None
    assert empty.best_price is None
    assert empty.worst_price is None

    for quantity in (0, -1, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            calculate_book_sweep([], side="buy", quantity=quantity)
    with pytest.raises(ValueError):
        calculate_book_sweep([], side="hold", quantity=1)
    with pytest.raises(ValueError):
        calculate_book_sweep([{"price": 1, "quantity": 0}], side="buy", quantity=1)


def test_order_book_summary_uses_monetary_depth_and_marks_largest_quantity_wall():
    summary = summarize_order_book(
        bids=[{"price": 9, "quantity": 100}, {"price": 10, "quantity": 2}],
        asks=[{"price": 11, "quantity": 3}, {"price": 12, "quantity": 1_000_000}],
    )

    assert summary.best_bid == 10
    assert summary.best_ask == 11
    assert summary.bid_value == 920
    assert summary.ask_value == 12_000_033
    assert summary.pressure_pct == pytest.approx((920 - 12_000_033) / (920 + 12_000_033) * 100)
    assert summary.spread_pct == pytest.approx(1 / 10.5 * 100)
    assert summary.bids[1].is_wall is True
    assert summary.asks[1].is_wall is True
    assert summary.asks[1].cumulative_quantity == 1_000_003


def test_notional_sweep_reports_slippage_and_insufficient_visible_depth():
    buy = calculate_notional_book_sweep(
        [{"price": 10, "quantity": 5}, {"price": 11, "quantity": 10}],
        side="buy",
        value=100,
    )
    sell = calculate_notional_book_sweep(
        [{"price": 10, "quantity": 5}, {"price": 9, "quantity": 10}],
        side="sell",
        value=100,
    )
    too_large = calculate_notional_book_sweep(
        [{"price": 10, "quantity": 1}], side="buy", value=100,
    )

    assert buy.fully_filled is True
    assert buy.average_price == pytest.approx(100 / (5 + 50 / 11))
    assert buy.slippage_pct == pytest.approx((buy.average_price - 10) / 10 * 100)
    assert sell.fully_filled is True
    assert sell.average_price == pytest.approx(100 / (5 + 50 / 9))
    assert sell.slippage_pct == pytest.approx((10 - sell.average_price) / 10 * 100)
    assert too_large.fully_filled is False


def _flip_input(**changes):
    values = {
        "item_code": "bread",
        "item_name": "Bread",
        "snapshot_at": "2026-07-14T10:00:00Z",
        "quote_age_minutes": 2,
        "best_bid": 9,
        "best_ask": 10,
        "asks": [{"price": 10, "quantity": 5}, {"price": 11, "quantity": 5}],
        "forecast_signal": "Up",
        "forecast_evidence": "Supported",
        "forecast_samples": 40,
        "gross_flip_return_p10_pct": -5,
        "gross_flip_return_median_pct": 10,
        "gross_flip_return_p90_pct": 20,
    }
    values.update(changes)
    return values


def test_flip_opportunity_uses_sweep_and_per_side_fees_once():
    source = _flip_input()
    result = calculate_flip_opportunity(
        source,
        FlipAssumptions(quantity=8, fee_pct_per_side=2, minimum_net_margin_pct=1),
    )

    assert source["asks"][0] == {"price": 10, "quantity": 5}
    assert result.verdict == "Potential flip"
    assert result.entry_average_price == pytest.approx(10.375)
    assert result.total_entry_cost == pytest.approx(83 * 1.02)
    assert result.break_even_exit_vwap == pytest.approx((83 * 1.02) / (8 * .98))
    assert result.forecast_exit_vwap_median == pytest.approx(83 * 1.10 / 8)
    assert result.net_profit_median == pytest.approx(83 * 1.10 * .98 - 83 * 1.02)
    assert result.net_margin_p10_pct < 0 < result.net_margin_median_pct < result.net_margin_p90_pct


@pytest.mark.parametrize(
    ("changes", "expected_verdict", "expected_reason"),
    [
        ({"asks": None}, "Unavailable", "missing_order_book"),
        ({"quote_age_minutes": 31}, "No trade", "stale_quote"),
        ({"asks": [{"price": 10, "quantity": 1}]}, "No trade", "insufficient_ask_depth"),
        ({"best_bid": 10}, "No trade", "invalid_book"),
        ({"gross_flip_return_median_pct": 0}, "No trade", "margin_non_positive"),
        ({"forecast_evidence": "Limited"}, "Watch", "forecast_not_above_baseline"),
        ({"gross_flip_return_median_pct": 1.5}, "Watch", "margin_below_threshold"),
    ],
)
def test_flip_verdict_rules_fail_closed(changes, expected_verdict, expected_reason):
    result = calculate_flip_opportunity(
        _flip_input(**changes),
        FlipAssumptions(quantity=5, minimum_net_margin_pct=2, max_quote_age_minutes=30),
    )
    assert result.verdict == expected_verdict
    assert result.reason_codes == (expected_reason,)


def test_partial_and_stale_precedence_and_passive_limit_candidate():
    result = calculate_flip_opportunity(
        _flip_input(
            quote_age_minutes=31,
            asks=[{"price": 10, "quantity": 1}],
            best_bid=9.9995,
            best_ask=10,
            min_tick=.001,
        ),
        FlipAssumptions(quantity=5),
    )
    assert result.reason_codes == ("stale_quote",)
    assert result.entry_average_price is None
    assert result.passive_limit_price == pytest.approx(9.9995)


@pytest.mark.parametrize(
    ("momentum", "expected", "reason"),
    [
        (6, "Up", "strong_positive_momentum"),
        (1, "Up", "positive_momentum"),
        (0.999, "No clear move", None),
        (-0.999, "No clear move", None),
        (-1, "Down", "negative_momentum"),
        (-6, "Down", "strong_negative_momentum"),
    ],
)
def test_direction_v1_momentum_thresholds(momentum, expected, reason):
    result = calculate_direction_signal(momentum_pct=momentum)
    assert result.signal == expected
    assert (reason in result.reason_codes) if reason else not result.reason_codes


def test_direction_v1_combines_fair_gap_without_tendency_input():
    assert calculate_direction_signal(momentum_pct=1, fair_gap_pct=2).signal == "No clear move"
    assert calculate_direction_signal(momentum_pct=-1, fair_gap_pct=-2).signal == "No clear move"
    assert calculate_direction_signal(fair_gap_pct=-2).signal == "Up"
    assert calculate_direction_signal(fair_gap_pct=2).signal == "Down"


def test_future_bid_outcome_uses_strict_tick_boundaries():
    assert classify_future_bid_outcome(current_bid=1, future_bid=1.001)[0] == "Flat"
    assert classify_future_bid_outcome(current_bid=1, future_bid=0.999)[0] == "Flat"
    assert classify_future_bid_outcome(current_bid=1, future_bid=1.002)[0] == "Up"
    assert classify_future_bid_outcome(current_bid=1, future_bid=0.998)[0] == "Down"


def test_forecast_summary_excludes_no_signal_and_flat_and_requires_ten_for_intervals():
    rows = [
        ForecastEvaluationRow(
            FORECAST_MODEL_VERSION, f"f{i}", i, f"t{i}", i + 1,
            "Up", "Up", True, float(i), float(i - 2),
        )
        for i in range(10)
    ]
    rows.extend([
        ForecastEvaluationRow(FORECAST_MODEL_VERSION, "flat", 20, "flat-t", 21, "Up", "Flat", None, 0),
        ForecastEvaluationRow(FORECAST_MODEL_VERSION, "none", 22, "none-t", 23, "No clear move", "Down", None, -1),
    ])
    result = summarize_forecast_evaluations(
        item_code="bread", horizon_hours=24, rows=rows, current_signal="Up", min_samples=1
    )
    assert result.candidate_samples == 12
    assert result.evaluable_samples == 10
    assert result.correct_predictions == 10
    assert result.accuracy_pct == 100
    assert result.baseline_accuracy_pct == 100
    assert result.evidence_label == "Weak"
    assert result.p10_future_bid_return_pct == pytest.approx(0)
    assert result.gross_flip_return_median_pct == pytest.approx(2.5)


@pytest.mark.parametrize(
    ("samples", "accuracy", "baseline", "expected"),
    [(2, 80, 50, "Insufficient"), (3, 50, 50, "Weak"), (3, 54, 50, "Limited"), (3, 55, 50, "Supported")],
)
def test_forecast_evidence_boundaries(samples, accuracy, baseline, expected):
    assert forecast_evidence_label(
        evaluable_samples=samples, min_samples=3, accuracy_pct=accuracy, baseline_accuracy_pct=baseline
    ) == expected


def test_liquidity_score_uses_depth_and_spread_penalty():
    assert calculate_liquidity_score(
        bid_depth=40,
        ask_depth=60,
        spread_pct=2,
    ) == pytest.approx(100 / 1.02)


def test_liquidity_score_uses_spread_floor_and_low_fallback_for_missing_depth():
    assert calculate_liquidity_score(
        bid_depth=40,
        ask_depth=60,
        spread_pct=None,
    ) == pytest.approx(100 / 1.005)
    assert calculate_liquidity_score(
        bid_depth=None,
        ask_depth=None,
        spread_pct=2,
    ) == 0.0


def test_calculate_metrics_basic():
    m = calculate_metrics({
        "item_name": "Cooked Fish",
        "bid": 7.05,
        "ask": 7.35,
        "trades_7d": 120,
        "high_7d": 7.45,
        "low_7d": 6.90,
        "open_7d": 7.085,
        "close_7d": 7.207,
    })
    assert m.status == "OK"
    assert round(m.mid_price, 3) == 7.200
    assert round(m.spread_pct, 2) == 4.15
    assert round(m.crossing_loss_pct, 2) == 4.08
    assert m.trading_attractiveness is not None
    assert m.total_production_points == 80


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ("Grain", 1), ("Limestone", 1), ("Lead", 1), ("Petroleum", 1),
        ("Mysterious Plant", 1), ("Iron", 1), ("Wood", 1),
        ("Livestock", 20), ("Fish", 40),
        ("Steel", 20), ("Concrete", 20), ("Oil", 2), ("Bread", 20),
        ("Steak", 40), ("Cooked Fish", 80), ("Paper", 2),
        ("Light Ammo", 2), ("Ammo", 8),
        ("Heavy Ammo", 32), ("Pill", 400),
    ],
)
def test_total_upstream_production_points_covers_factory_items(item, expected):
    assert total_upstream_production_points(item_name=item) == expected


def test_total_upstream_production_points_accepts_item_codes_and_rejects_unknown_items():
    assert total_upstream_production_points(item_code="cookedFish") == 80
    assert total_upstream_production_points(item_code="heavy_ammo") == 32
    assert total_upstream_production_points(item_code="coca") == 1
    assert total_upstream_production_points(item_code="cocain") == 400
    assert total_upstream_production_points(item_name="Cocaine") == 400
    assert total_upstream_production_points(item_name="Case 1") is None


def test_one_tick_spread_is_not_exploitable():
    m = calculate_metrics({
        "item_name": "Limestone",
        "bid": 0.080,
        "ask": 0.081,
        "trades_7d": 100,
        "high_7d": 0.082,
        "low_7d": 0.080,
        "min_tick": 0.001,
    })

    assert m.spread == 0
    assert m.spread_pct == 0
    assert m.trading_attractiveness is None
    assert m.status == "Insufficient score data"


def test_crossing_loss_is_available_without_trade_history():
    m = calculate_metrics({"item_name": "Oil", "bid": 0.086, "ask": 0.087})

    assert m.crossing_loss_pct == pytest.approx(1.149425)
    assert m.status == "Missing: trades_7d, high_7d, low_7d"


def test_preserves_explicit_fair_price_fields():
    m = calculate_metrics({
        "item_name": "Grain",
        "bid": 1.00,
        "ask": 1.02,
        "trades_7d": 20,
        "high_7d": 1.05,
        "low_7d": 0.98,
        "open_7d": 1.00,
        "close_7d": 1.02,
        "average_7d": 1.00,
        "vwap_7d": 0.99,
        "rolling_average_7d": 1.01,
    })

    assert m.average_7d == 1.00
    assert m.vwap_7d == 0.99
    assert m.rolling_average_7d == 1.01


def test_classify_tendency_labels_market_behavior():
    assert classify_tendency(
        open_price=10,
        close_price=13,
        min_price=10,
        max_price=13,
        average_price=11.5,
        rolling_average=11,
        trade_count=12,
        volume=40,
        spread_pct=1,
    ) == ["Rising", "Volatile"]

    assert classify_tendency(
        open_price=10,
        close_price=9,
        min_price=9,
        max_price=10,
        average_price=9.5,
        rolling_average=9.8,
        trade_count=12,
        volume=40,
        spread_pct=1,
    ) == ["Falling"]

    assert classify_tendency(
        open_price=10,
        close_price=10.1,
        min_price=10,
        max_price=10.2,
        average_price=10.1,
        rolling_average=10.1,
        trade_count=8,
        volume=50,
        spread_pct=1,
    ) == ["Range-bound", "Stable"]

    assert "Thin" in classify_tendency(
        open_price=10,
        close_price=10,
        min_price=10,
        max_price=10,
        average_price=10,
        trade_count=1,
        volume=1,
        spread_pct=1,
    )
