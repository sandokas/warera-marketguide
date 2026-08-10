import pytest

from warera_quant.metrics import (
    FORECAST_MODEL_VERSION,
    ForecastEvaluationRow,
    FlipAssumptions,
    calculate_book_sweep,
    calculate_range_position_pct,
    calculate_flip_opportunity,
    calculate_fair_value_guidance,
    calculate_direction_signal,
    calculate_liquidity_score,
    calculate_metrics,
    classify_price_dislocation,
    classify_price_dislocations,
    classify_future_bid_outcome,
    classify_market_trend_pattern,
    classify_tendency,
    forecast_evidence_label,
    summarize_forecast_evaluations,
    summarize_order_book,
    build_price_action_candles,
    prepare_price_action_item,
    price_action_chart_filename,
    select_highlighted_items,
    select_price_action_interval,
    time_based_sma_7d,
    total_upstream_production_points,
)
from warera_quant.warera_api import OrderLevel


def _highlight_trades(*, price=10.0):
    return [
        {"created_at": f"2026-06-{day:02d}T{hour:02d}:00:00Z", "price": price + index / 10,
         "quantity": index + 1}
        for index, (day, hour) in enumerate(
            [(1, 0), (1, 4), (1, 8), (1, 12), (2, 0), (2, 4),
             (2, 8), (2, 12), (3, 0), (3, 4), (3, 8), (3, 12)]
        )
    ]


def test_highlight_interval_boundary_and_unmodified_ohlc_units_volume():
    trades = _highlight_trades()
    selected = select_price_action_interval(trades)
    assert selected is not None
    interval, candles = selected
    assert interval == "4h"
    assert len(candles) == 12
    assert candles.iloc[0].to_dict() == {
        "Open": 10.0, "High": 10.0, "Low": 10.0, "Close": 10.0, "Volume": 1.0,
    }
    assert select_price_action_interval(trades[:-1]) is None

    combined = build_price_action_candles([
        {"created_at": "2026-06-01T00:01:00Z", "price": 10, "quantity": 2},
        {"created_at": "2026-06-01T03:59:00Z", "price": 12, "quantity": 3},
    ], interval="4h")
    assert combined.iloc[0].to_dict() == {
        "Open": 10.0, "High": 12.0, "Low": 10.0, "Close": 12.0, "Volume": 5.0,
    }


def test_highlight_selection_ranks_gaps_before_optional_chart_capability():
    history = _highlight_trades()
    rows = [
        {"item_code": "zeta", "item_name": "Zeta", "last_trade_price": 8,
         "stable_fair_price_7d": 10, "price_p10_7d": 9, "price_p90_7d": 11},
        {"item_code": "alpha", "item_name": "Alpha", "last_trade_price": 8,
         "stable_fair_price_7d": 10, "price_p10_7d": 9, "price_p90_7d": 11},
        {"item_code": "premium", "item_name": "Premium", "last_trade_price": 13,
         "stable_fair_price_7d": 10, "price_p10_7d": 9, "price_p90_7d": 11},
        {"item_code": "unsupported", "item_name": "Unsupported", "last_trade_price": 5,
         "stable_fair_price_7d": 10, "price_p10_7d": 9, "price_p90_7d": 11},
    ]
    selected = select_highlighted_items(rows, {
        "zeta": history, "alpha": history, "premium": history, "unsupported": history[:2],
    })
    assert [(item.item_code, item.role) for item in selected] == [
        ("alpha", "largest_discount"), ("premium", "largest_premium"),
    ]
    assert selected[0].interval == "4h"
    assert selected[1].interval == "4h"

    same_side = select_highlighted_items(rows[:2], {"zeta": history, "alpha": history})
    assert [(item.item_code, item.role, item.rank_within_role) for item in same_side] == [
        ("alpha", "largest_discount", 1), ("zeta", "second_largest_discount", 2),
    ]
    assert select_highlighted_items([rows[0]], {"zeta": history})[0].filename == "largest-discount-price-action.png"
    assert select_highlighted_items([], {}) == []


@pytest.mark.parametrize(
    ("latest", "fair", "lower", "upper", "tick", "expected"),
    [
        (10.5, 10, 9, 11, 0.1, "within_normal_range"),
        (9.5, 10, 9, 11, 0.1, "within_normal_range"),
        (11, 10, 9, 11, 0.1, "within_normal_range"),
        (9, 10, 9, 11, 0.1, "within_normal_range"),
        (11.0 + 5e-14, 10, 9, 11, 0.1, "within_normal_range"),
        (9.0 - 5e-14, 10, 9, 11, 0.1, "within_normal_range"),
        (10.05, 10, 9, 10.01, 0.1, "within_normal_range"),
        (9.95, 10, 9.99, 11, 0.1, "within_normal_range"),
        (11.1, 11.0, 9, 11, 0.1, "meaningful_premium"),
        (8.9, 9.0, 9, 11, 0.1, "meaningful_discount"),
        (0.079, 0.0781503825, 0.077, 0.079, 0.001, "within_normal_range"),
    ],
)
def test_meaningful_dislocation_boundaries(latest, fair, lower, upper, tick, expected):
    item = classify_price_dislocation({
        "item_code": "boundary",
        "item_name": "Boundary",
        "last_trade_price": latest,
        "stable_fair_price_7d": fair,
        "price_p10_7d": lower,
        "price_p90_7d": upper,
    }, min_tick=tick)

    assert item.classification == expected


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("last_trade_price", None),
        ("last_trade_price", 0),
        ("stable_fair_price_7d", float("nan")),
        ("price_p10_7d", None),
        ("price_p90_7d", float("inf")),
        ("min_tick", 0),
        ("min_tick", -0.001),
    ],
)
def test_meaningful_dislocation_invalid_inputs_fail_closed(field, value):
    row = {
        "item_code": "invalid",
        "last_trade_price": 12,
        "stable_fair_price_7d": 10,
        "price_p10_7d": 9,
        "price_p90_7d": 11,
        "min_tick": 0.001,
    }
    row[field] = value

    item = classify_price_dislocation(row)

    assert item.classification == "insufficient_evidence"
    assert select_highlighted_items([item], {}, require_chart_history=False) == []


def test_flat_band_uses_tick_for_severity_and_rail_scale():
    item = classify_price_dislocation({
        "item_code": "flat", "last_trade_price": 10.002,
        "stable_fair_price_7d": 10, "price_p10_7d": 10, "price_p90_7d": 10,
    }, min_tick=0.001)

    assert item.classification == "meaningful_premium"
    assert item.band_width == pytest.approx(0.001)
    assert item.severity == pytest.approx(2)
    assert item.normal_band_start_position == item.normal_band_end_position == 50
    assert item.fair_normalized_position == 50
    assert item.latest_normalized_position == 100


def test_rail_coordinates_are_deterministic_and_clamped():
    item = classify_price_dislocation({
        "item_code": "rail", "last_trade_price": 14,
        "stable_fair_price_7d": 10, "price_p10_7d": 8, "price_p90_7d": 12,
    }, min_tick=0.001)

    assert item.normal_band_start_position == 20
    assert item.normal_band_end_position == 80
    assert item.fair_normalized_position == 50
    assert item.latest_normalized_position == 100


def test_severity_ranking_is_normalized_and_ties_use_item_code():
    rows = [
        {"item_code": "wide", "last_trade_price": 13.5, "stable_fair_price_7d": 10,
         "price_p10_7d": 5, "price_p90_7d": 13},
        {"item_code": "zeta", "last_trade_price": 11.2, "stable_fair_price_7d": 10,
         "price_p10_7d": 9, "price_p90_7d": 11},
        {"item_code": "alpha", "last_trade_price": 11.2, "stable_fair_price_7d": 10,
         "price_p10_7d": 9, "price_p90_7d": 11},
    ]
    items = classify_price_dislocations(rows, min_tick=0.001)

    selected = select_highlighted_items(items, {}, require_chart_history=False)

    assert [(item.item_code, item.role) for item in selected] == [
        ("alpha", "largest_premium"), ("zeta", "second_largest_premium")
    ]
    assert selected[0].severity == pytest.approx(0.1)
    assert next(item for item in items if item.item_code == "wide").raw_gap_pct > selected[0].raw_gap_pct


def test_7d_sma_is_elapsed_time_based_and_evidence_gated():
    candles = build_price_action_candles([
        {"created_at": f"2026-06-{day:02d}T00:00:00Z", "price": float(day), "quantity": 1}
        for day in (1, 2, 3, 4, 5, 6, 10)
    ], interval="1D")
    sma = time_based_sma_7d(candles)
    assert sma.iloc[:5].isna().all()
    assert sma.iloc[5] == pytest.approx(3.5)
    assert sma.iloc[6] != sma.iloc[6]  # only four closes remain inside the elapsed 7D window


def test_all_item_price_action_preparation_supports_neutral_items_and_stable_filename():
    item = prepare_price_action_item({
        "item_code": "Heavy Ammo", "item_name": "Heavy Ammo",
        "last_trade_price": 10, "stable_fair_price_7d": 10,
    }, _highlight_trades())
    assert item is not None
    assert item.role == "price_action"
    assert item.gap_pct == 0
    assert item.interval == "4h"
    assert price_action_chart_filename("Heavy Ammo") == "heavy-ammo-price-action.png"


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ((None, 1, None), "Insufficient history"),
        ((0.5, -0.5, 0), "Flat"),
        ((1, 2, 0), "Persistent rise"),
        ((-1, -2, 0), "Persistent fall"),
        ((1, -2, 0), "Rebound"),
        ((-1, 2, 0), "Pullback"),
        ((1, -2, 3), "Mixed"),
    ],
)
def test_market_trend_pattern_classes(changes, expected):
    result = classify_market_trend_pattern(
        change_1d_pct=changes[0], change_7d_pct=changes[1], change_30d_pct=changes[2]
    )

    assert result.label == expected


def test_range_position_is_clamped_and_flat_ranges_are_unusable():
    assert calculate_range_position_pct(last_price=12, low=8, high=10) == 100
    assert calculate_range_position_pct(last_price=7, low=8, high=10) == 0
    assert calculate_range_position_pct(last_price=10, low=10, high=10) is None


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
