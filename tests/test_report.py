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
    assert "WarEra Market Guide" in report
    assert "Market Trends" in report
    assert "Price Evolution Lens" in report
    assert "Buy Ask" in report
    assert "Sell Bid" in report
    assert "Last" in report
    assert "Fair Prices And Tomorrow Bias" in report
    assert "What To Pay And What To Expect" in report
    assert "Fair Price" in report
    assert "Buy Below" in report
    assert "Sell Above" in report
    assert "Expected Move" in report
    assert "Signal" in report
    assert "Trust" in report
    assert "Market-Making Score" not in report
    assert "Insufficient score data" not in report
    assert "Price is low; consider buying" in report


def test_generate_html_report_shows_all_items_by_default():
    df = pd.DataFrame([
        {
            "item_name": "Corn",
            "latest_price": 1.2,
            "current_price": 1.2,
            "bid": 1.15,
            "ask": 1.25,
            "spread_pct": 2.0,
            "range_pct": 5.0,
            "momentum_7d_pct": 0.5,
            "trades_7d": 5,
            "status": "OK",
        },
        {
            "item_name": "Rice",
            "latest_price": 2.3,
            "current_price": 2.3,
            "bid": 2.25,
            "ask": 2.35,
            "spread_pct": 1.5,
            "range_pct": 3.0,
            "momentum_7d_pct": -0.7,
            "trades_7d": 4,
            "status": "OK",
        },
    ])

    report = generate_html_report(df)

    assert "Corn" in report
    assert "Rice" in report
    assert report.count("What To Pay And What To Expect") == 1


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
            "liquidity_7d": 748_508_664_657,
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
    assert "Fair Prices And Tomorrow Bias" in report
    assert "What To Pay And What To Expect" in report
    assert "Market Trends" in report
    assert ">Rank<" not in report
    assert 'class="col-rank"' not in report
    assert "Rising" in report
    assert "Volatile" in report
    assert "5.900" in report
    assert "Up" in report
    assert "Hold" in report
    assert "Sell near" in report
    assert "chip-up" in report
    assert "chip-hold" in report
    assert "signed-positive" in report
    assert "liquidity-fill" in report
    assert ">748.5B<" in report
    assert 'title="748508664657"' in report
    assert 'class="col-spread-pct number"' in report
    assert 'class="col-now number"' in report
    assert "th:nth-child(2), td:nth-child(2)" not in report
    assert "th:nth-last-child(2), td:nth-last-child(2)" not in report
    assert "align-right" not in report
    assert ".compact-table .report-table" in report
    assert "width: max-content" in report
    assert ".compact-table td.number" in report
    assert "font-variant-numeric: tabular-nums" in report
    assert "min-width: 220px" in report
    assert "grid-template-columns: minmax(72px, 1fr) 48px" in report
    assert "min-width: 188px" in report
    assert 'class="col-7d-trades number"' in report
    assert "Market-Making Score" not in report


def test_report_uses_stable_fair_price_and_softens_thin_market_actions():
    df = pd.DataFrame([
        {
            "item_name": "Copper",
            "latest_price": 8.0,
            "current_price": 8.0,
            "stable_fair_price_7d": 10.0,
            "vwap_7d": 20.0,
            "stable_range_pct_7d": 10.0,
            "trade_count_7d": 1,
            "volume_7d": 1,
            "latest_spread": 0.2,
            "latest_spread_pct": 2.0,
            "tendency_labels_7d": "Thin",
            "bid": 7.9,
            "ask": 8.1,
            "trades_7d": 1,
            "high_7d": 20.0,
            "low_7d": 1.0,
            "spread_pct": 2.0,
            "range_pct": 190.0,
            "momentum_7d_pct": -1.0,
            "status": "OK",
        }
    ])

    report = generate_html_report(df, top=0)

    assert "10.000" in report
    assert "Check depth" in report
    assert "chip-check" in report
    assert "Weak" in report
    assert "few trades" in report


def test_wait_signal_notes_show_buy_target():
    df = pd.DataFrame([
        {
            "item_name": "Coal",
            "latest_price": 10.0,
            "current_price": 10.0,
            "stable_fair_price_7d": 10.0,
            "latest_spread": 0.4,
            "trade_count_7d": 12,
            "volume_7d": 40,
            "latest_spread_pct": 2.0,
            "tendency_labels_7d": "Falling",
            "bid": 11.8,
            "ask": 12.2,
            "trades_7d": 12,
            "high_7d": 13.0,
            "low_7d": 9.5,
            "spread_pct": 2.0,
            "range_pct": 20.0,
            "momentum_7d_pct": -3.0,
            "status": "OK",
        }
    ])

    report = generate_html_report(df, top=0)

    assert "Wait" in report
    assert "Buy near" in report
