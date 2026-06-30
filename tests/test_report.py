import pandas as pd

from warera_quant.report import generate_html_report, write_outputs


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
    assert "Market Trends" in report
    assert "Price Evolution Lens" in report
    assert "Buy Ask" in report
    assert "Sell Bid" in report
    assert "Last" in report
    assert "Secondary Market-Making Score" in report
    assert "Cheaper; consider stockpiling" in report


def test_report_foregrounds_market_trends_and_writes_compatibility_csv(tmp_path):
    df = pd.DataFrame([
        {
            "item_name": "Bread",
            "latest_price": 6.5,
            "open_7d": 5.0,
            "close_7d": 6.5,
            "percent_change_7d": 30.0,
            "min_7d": 5.0,
            "max_7d": 6.6,
            "average_7d": 5.8,
            "vwap_7d": 5.9,
            "volume_7d": 100,
            "liquidity_7d": 95,
            "latest_spread_pct": 1.2,
            "tendency_labels_7d": "Rising, Volatile",
            "bid": 6.2,
            "ask": 6.8,
            "trades_7d": 12,
            "high_7d": 6.6,
            "low_7d": 5.0,
            "spread_pct": 1.2,
            "range_pct": 27.6,
            "momentum_7d_pct": 30.0,
            "trading_attractiveness": 0.52,
            "status": "OK",
        }
    ])

    trends_path, html_path = write_outputs(df, tmp_path, metric_window="7D")
    report = html_path.read_text(encoding="utf-8")

    assert trends_path.name == "market_trends.csv"
    assert trends_path.exists()
    assert (tmp_path / "market_scores.csv").exists()
    assert "Market History And Trends" in report
    assert "Market Trends" in report
    assert "Rising, Volatile" in report
    assert "Secondary Market-Making Score" in report
