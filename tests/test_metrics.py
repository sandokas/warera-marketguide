from warera_quant.metrics import calculate_metrics


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
