import pytest

from warera_quant.metrics import calculate_liquidity_score, calculate_metrics, classify_tendency


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
