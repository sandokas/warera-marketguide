import pandas as pd

from warera_quant.report import generate_html_report


def test_swing_lens_shows_actionable_prices_without_score():
    df = pd.DataFrame([
        {
            "item_name": "Oil",
            "bid": None,
            "ask": None,
            "current_price": 0.1722,
            "trades_7d": 100,
            "high_7d": 0.174,
            "low_7d": 0.171,
            "spread_pct": 0.58,
            "range_pct": 1.74,
            "momentum_7d_pct": -3.5,
            "trading_attractiveness": None,
            "status": "Insufficient score data",
        }
    ])

    report = generate_html_report(df, top=0)

    assert "<!doctype html>" in report
    assert "Swing Trading Lens" in report
    assert "Buy Ask" in report
    assert "Sell Bid" in report
    assert "Last" in report
    assert "Market-Making Score" not in report
    assert "Cheaper; consider stockpiling" in report
