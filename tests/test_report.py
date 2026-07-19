import pandas as pd

from warera_quant.metrics import FlipAssumptions
from warera_quant.report import generate_html_report, write_outputs


def test_report_omits_redundant_price_evolution_lens():
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
            "crossing_loss_pct": 0.58,
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
    assert "Price Evolution Lens" not in report
    assert "(You Pay)" not in report
    assert "(You Receive)" not in report
    assert "Crossing Cost %" not in report
    assert "Market intelligence, without the noise." in report
    assert "Largest price gaps" in report
    assert "Flip Board" not in report
    assert "Break-even Exit VWAP" not in report
    assert "Fair Value &amp; Buy / Sell Signals" in report
    assert "Strongest upside signal" not in report
    assert "Strongest downside signal" not in report
    assert "Trust" not in report
    assert "Market-Making Score" not in report
    assert "Insufficient score data" not in report
    assert "Price is low; consider buying" not in report
    assert "Price is high; consider selling" not in report
    assert "Momentum describes history, not a trade recommendation" not in report


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
    assert report.count('<article class="summary-card') == 2
    assert "Flip Board" not in report


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
            "liquidity_7d": 250,
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
            "forecast_current_signal": "Up",
            "forecast_evidence": "Supported",
            "forecast_evaluable_samples": 42,
            "forecast_accuracy_pct": 65.0,
            "forecast_baseline_accuracy_pct": 55.0,
            "forecast_current_reason_codes": "strong_positive_momentum",
        }
    ])

    trends_path, html_path = write_outputs(
        df,
        tmp_path,
        metric_window="7D",
        data_synced_at="2026-06-30T10:00:00Z",
        data_sync_status="complete",
    )
    report = html_path.read_text(encoding="utf-8")

    assert trends_path.name == "market_trends.csv"
    assert trends_path.exists()
    assert (tmp_path / "market_scores.csv").exists()
    assert "Market intelligence, without the noise." in report
    assert "Market data synced 2026-06-30 10:00 UTC" in report
    assert "Report generated " in report
    assert "Market Signals" not in report
    assert "Strongest upside signal" not in report
    assert "Flip Board" not in report
    assert "Market Trends" in report
    assert ">Rank<" not in report
    assert 'class="col-rank"' not in report
    assert "Rising" in report
    assert "Volatile" in report
    assert "5.900" in report
    assert "Supported" not in report
    assert '<span class="chip chip-up"><span>↑</span>Up</span>' not in report
    assert "chip-up" in report
    assert "chip-hold" in report
    assert "signed-positive" in report
    assert '<div class="liquidity-fill"' not in report
    assert ">250<" not in report
    assert 'title="250"' not in report
    assert 'class="col-1d-change number"' in report
    assert "th:nth-child(2), td:nth-child(2)" not in report
    assert "th:nth-last-child(2), td:nth-last-child(2)" not in report
    assert "align-right" not in report
    assert ".compact-table .report-table" in report
    assert "width: max-content" not in report
    assert ".table-wrap { overflow: visible; }" in report
    assert ".flip-board .report-table" not in report
    assert "table-layout: fixed" in report
    assert "overflow-x: clip" not in report
    assert ".compact-table td.number" in report
    assert "font-variant-numeric: tabular-nums" in report
    assert "min-width: 220px" in report
    assert "grid-template-columns: minmax(72px, 1fr) 48px" not in report
    assert "min-width: 188px" not in report
    assert 'class="col-30d-position number"' in report
    assert 'class="activity-totals number"' in report
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
    assert "Largest discount" in report
    assert "-20.00% vs fair" in report


def test_report_formats_volume_and_trade_counts_without_decimal_suffix():
    df = pd.DataFrame([
        {
            "item_name": "Iron",
            "latest_price": 12.345,
            "current_price": 12.345,
            "stable_fair_price_7d": 12.345,
            "volume_7d": 31641,
            "trades_7d": 31641,
            "trade_count_7d": 31641,
            "spread_pct": 1.2,
            "range_pct": 3.4,
            "momentum_7d_pct": 1.5,
            "status": "OK",
        }
    ])

    report = generate_html_report(df, top=0)

    assert "12.345" in report
    assert "31641" in report
    assert "31641.000" not in report
    assert ">31.6K units<" in report
    assert "Completed Value / PP-equivalent Volume" in report
    assert ">Units / Trades<" in report


def test_report_does_not_render_opaque_liquidity_score():
    df = pd.DataFrame([
        {
            "item_name": "Deep Market",
            "latest_price": 10,
            "volume_7d": 100,
            "trade_count_7d": 8,
            "trades_7d": 8,
            "liquidity_7d": 200,
            "latest_spread_pct": 1,
            "momentum_7d_pct": 1,
            "range_pct": 2,
        },
        {
            "item_name": "Shallow Market",
            "latest_price": 5,
            "volume_7d": 50,
            "trade_count_7d": 3,
            "trades_7d": 3,
            "liquidity_7d": 50,
            "latest_spread_pct": 2,
            "momentum_7d_pct": -1,
            "range_pct": 3,
        },
    ])

    report = generate_html_report(df, top=0)

    assert '<div class="liquidity-fill"' not in report
    assert ">Liquidity<" not in report
    assert "200.000" not in report
    assert "50.000" not in report


def test_generate_html_report_keeps_compatibility_with_price_precedence_fields():
    df = pd.DataFrame([
        {
            "item_name": "Copper",
            "last_trade_price": 10.0,
            "quote_price": 10.5,
            "mid_price": 10.25,
            "current_price": 10.0,
            "quote_gap_pct": 5.0,
            "depth_imbalance_pct": -20.0,
            "bid": 10.1,
            "ask": 10.4,
            "latest_spread_pct": 2.9,
            "trades_7d": 8,
            "high_7d": 10.8,
            "low_7d": 9.7,
            "spread_pct": 2.9,
            "range_pct": 11.3,
            "momentum_7d_pct": 1.6,
            "status": "OK",
        }
    ])

    report = generate_html_report(df, top=0)

    assert "Copper" in report
    assert "Largest price gaps" in report
    assert "Market intelligence, without the noise." in report
    assert "Flip Board" not in report


def test_report_does_not_render_supplied_forecast_as_a_signal():
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
            "forecast_current_signal": "Down",
            "forecast_evidence": "Limited",
            "forecast_evaluable_samples": 12,
            "forecast_current_reason_codes": "negative_momentum",
        }
    ])

    report = generate_html_report(df, top=0)

    assert "Market Signals" not in report
    assert "Limited" not in report
    assert '<span class="chip chip-down"><span>↓</span>Down</span>' not in report
    assert "Negative momentum" not in report
    assert "Buy near" not in report


def test_fair_price_table_distinguishes_thresholds_from_historical_range():
    df = pd.DataFrame([
        {
            "item_name": "Bread",
            "last_trade_price": 1.896,
            "stable_fair_price_7d": 1.829,
            "stable_range_pct_7d": 5.8366,
            "min_7d": 1.770,
            "max_7d": 1.900,
            "trade_count_7d": 70_000,
            "volume_7d": 125_000,
            "latest_spread": 0.007,
            "latest_spread_pct": 0.37,
            "tendency_labels_7d": "Rising",
            "percent_change_7d": 5.8,
            "trades_7d": 70_000,
            "momentum_7d_pct": 5.8,
            "forecast_current_signal": "Up",
            "forecast_evidence": "Insufficient",
            "forecast_evaluable_samples": 3,
            "forecast_current_reason_codes": "positive_momentum",
        }
    ])

    report = generate_html_report(df, top=0, metric_window="7D")

    assert ">Break-even Exit VWAP<" not in report
    assert "Fair Value &amp; Buy / Sell Signals" in report
    assert "forecast evidence" not in report.lower()


def test_report_does_not_render_internal_flip_fields():
    common = {
        "bid": 9, "ask": 10, "latest_price": 9.5, "trades_7d": 10,
        "high_7d": 11, "low_7d": 8, "momentum_7d_pct": 1,
    }
    df = pd.DataFrame([
        {
            **common, "item_name": "Watch Item", "flip_verdict": "Watch",
            "flip_reason_codes": "forecast_not_above_baseline", "flip_quantity": 5,
            "flip_forecast_evidence": "Limited", "flip_forecast_samples": 50,
            "flip_net_margin_median_pct": 8,
        },
        {
            **common, "item_name": "Supported Item", "flip_verdict": "Potential flip",
            "flip_reason_codes": "supported_positive_margin", "flip_quantity": 5,
            "flip_entry_average_price": 10.2, "flip_total_entry_cost": 52,
            "flip_break_even_exit_vwap": 10.7, "flip_forecast_exit_vwap_p10": 9.8,
            "flip_forecast_exit_vwap_median": 11.5, "flip_forecast_exit_vwap_p90": 12.1,
            "flip_net_margin_p10_pct": -5, "flip_net_margin_median_pct": 7,
            "flip_net_profit_median": 3.64, "flip_forecast_evidence": "Supported",
            "flip_forecast_samples": 40, "flip_quote_age_minutes": 2,
        },
    ])

    report = generate_html_report(
        df,
        assumptions=FlipAssumptions(quantity=5, fee_pct_per_side=1.5, minimum_net_margin_pct=2),
    )

    assert "Flip Board" not in report
    assert "Downside P10 net margin" not in report
    assert "Fair Value &amp; Buy / Sell Signals" in report
    assert ">Forecast Exit<" not in report
    assert ">Expected Net<" not in report
    assert "Largest discount" in report
    assert "Largest premium" in report
    assert "Strongest upside signal" not in report
    assert "Strongest downside signal" not in report
    assert report.count('<article class="summary-card') == 2


def test_market_pulse_arrow_colors_follow_direction():
    report = generate_html_report(pd.DataFrame([
        {
            "item_name": "Discount", "latest_price": 8, "stable_fair_price_7d": 10,
            "forecast_current_signal": "Down", "forecast_evidence": "Supported",
        },
        {
            "item_name": "Premium", "latest_price": 12, "stable_fair_price_7d": 10,
            "forecast_current_signal": "Up", "forecast_evidence": "Supported",
        },
    ]))

    assert 'summary-card summary-card-down"><span>Largest discount</span>' in report
    assert 'summary-arrow" aria-hidden="true">↓</span>Discount' in report
    assert 'summary-card summary-card-up"><span>Largest premium</span>' in report
    assert 'summary-arrow" aria-hidden="true">↑</span>Premium' in report


def test_report_restores_price_guide_and_renders_transparent_market_depth():
    report = generate_html_report(pd.DataFrame([{
        "item_name": "Cocain",
        "last_trade_price": 36.02,
        "stable_fair_price_7d": 36.1,
        "stable_range_pct_7d": 2,
        "percent_change_7d": -3,
        "tendency_labels_7d": "Falling, Stable",
        "traded_value_7d": 560_862,
        "traded_quantity_7d": 15_540,
        "trade_count_7d": 9_968,
        "production_points": 100,
        "order_book": {
            "best_bid": 36.02, "best_ask": 36.2,
            "bid_quantity": 59, "ask_quantity": 46,
            "bid_value": 2_126, "ask_value": 1_665,
            "pressure_pct": 12.16, "spread_pct": .5,
            "bids": [{"price": 36.02, "quantity": 59, "order_value": 2125.18,
                      "cumulative_quantity": 59, "cumulative_value": 2125.18, "is_wall": True}],
            "asks": [{"price": 36.2, "quantity": 46, "order_value": 1665.2,
                      "cumulative_quantity": 46, "cumulative_value": 1665.2, "is_wall": True}],
        },
        "order_book_executions": [{
            "budget": 100,
            "buy": {"fully_filled": True, "average_price": 36.2, "slippage_pct": 0},
            "sell": {"fully_filled": False},
        }],
    }]))

    assert report.index("Largest price gaps") < report.index("Fair Value &amp; Buy / Sell Signals")
    assert report.index("Fair Value &amp; Buy / Sell Signals") < report.index("Current Order Book")
    assert '<th class="col-fair number">Fair</th>' in report
    assert '<th class="col-ask number">Ask</th>' in report
    assert '<th class="col-bid number">Bid</th>' in report
    assert ">Max Buy<" in report and ">Rich Sell<" in report
    assert '<span class="chip chip-down">Falling</span>' in report
    assert "Current Order Book" in report
    assert "Buy orders vs sell orders" in report
    assert '<span class="depth-label-text">Buy</span>' in report
    assert '<span class="depth-label-number">59 (2.1K value)</span>' in report
    assert '<span class="depth-label-text">Sell</span>' in report
    assert '<span class="depth-label-number">46 (1.7K value)</span>' in report
    assert 'class="depth-segment depth-segment-bid wall-segment"' in report
    assert 'class="depth-segment depth-segment-ask wall-segment"' in report
    assert 'title="Bid at 36.020: 59 units, 2125 value"' in report
    assert '<span class="book-pressure-content"><span class="book-pressure-label signed-positive">Buy-heavy</span>' in report
    assert '<span class="book-pressure-value signed-positive">+12.2%</span>' in report
    assert '.book-summary .book-pressure { width: 13%; white-space: nowrap; }' in report
    assert '<th class="book-wall number">Buy Wall</th>' in report
    assert '<td class="book-wall number">36.020</td>' in report
    assert '<th class="book-wall number">Sell Wall</th>' in report
    assert '<td class="book-wall number">36.200</td>' in report
    assert '.book-pressure-content {' in report
    assert "<details" not in report
    assert ".flip-board" not in report
    assert "Executable fixed-budget depth" not in report
    assert "Insufficient visible depth" not in report
    assert ">Ask Upside %<" in report
    assert "Completed Market Activity" in report
    assert "Liquidity depth score" not in report


def test_report_aligns_text_left_and_numbers_right_with_semantic_classes():
    report = generate_html_report(pd.DataFrame([{
        "item_name": "Iron",
        "last_trade_price": 0.08,
        "stable_fair_price_7d": 0.081,
        "stable_range_pct_7d": 2,
        "traded_value_7d": 1000,
        "traded_quantity_7d": 12500,
        "trade_count_7d": 42,
        "production_points": 1,
        "order_book": {
            "best_bid": 0.079, "best_ask": 0.081,
            "bid_quantity": 100, "ask_quantity": 200,
            "bid_value": 7.9, "ask_value": 16.2,
            "pressure_pct": -34.4, "spread_pct": 2.5,
            "bids": [{"price": 0.079, "quantity": 100, "order_value": 7.9,
                      "cumulative_quantity": 100, "cumulative_value": 7.9}],
            "asks": [{"price": 0.081, "quantity": 200, "order_value": 16.2,
                      "cumulative_quantity": 200, "cumulative_value": 16.2}],
        },
        "order_book_executions": [{
            "budget": 100,
            "buy": {"fully_filled": True, "average_price": 0.081, "slippage_pct": 0},
            "sell": {"fully_filled": False},
        }],
    }]))

    assert 'class="col-item text"' in report
    assert 'class="col-ask number"' in report
    assert 'class="activity-volume number"' in report
    assert 'class="activity-totals number"' in report
    assert '<th class="book-item text">Item</th>' in report
    assert '<th class="book-price number">Best Bid</th>' in report
    assert '<td class="book-price number">0.079</td>' in report
    assert '<th class="col-ask-upside-pct number">Ask Upside %</th>' in report
    assert "th, td {\n      padding: 8px 9px;\n      border-bottom: 1px solid var(--line);\n      text-align: left;" in report
    assert "th.number, td.number {\n      text-align: right;" in report
    assert ".flip-board th,\n    .flip-board td {\n      padding: 12px 10px;\n      max-width: none;\n      text-align: left;" not in report
    assert "th:nth-child" not in report
    assert "td:nth-child" not in report


def test_report_emits_auditable_position_specific_guidance():
    report = generate_html_report(pd.DataFrame([
        {
            "item_name": "Wait Item", "latest_price": 10,
            "latest_ask": 10.05, "latest_bid": 9.95, "stable_fair_price_7d": 10,
            "guide_entry_action": "WAIT", "guide_holder_action": "HOLD",
            "guide_max_entry_price": 9.9, "guide_rich_exit_price": 10.2,
            "guide_net_to_fair_pct": -0.5,
        },
        {
            "item_name": "Sell Item", "latest_price": 10.2,
            "latest_ask": 10.3, "latest_bid": 10.2, "stable_fair_price_7d": 10,
            "guide_entry_action": "WAIT", "guide_holder_action": "SELL",
            "guide_max_entry_price": 9.9, "guide_rich_exit_price": 10.2,
            "guide_net_to_fair_pct": -2.9,
        },
        {
            "item_name": "Buy Item", "latest_price": 9.8,
            "latest_ask": 9.8, "latest_bid": 9.7, "stable_fair_price_7d": 10,
            "guide_entry_action": "BUY", "guide_holder_action": "HOLD",
            "guide_max_entry_price": 9.9, "guide_rich_exit_price": 10.2,
            "guide_net_to_fair_pct": 2.04,
        },
    ]))

    assert "Fair Value &amp; Buy / Sell Signals" in report
    assert '<span class="chip chip-buy"><span>+</span>BUY</span>' in report
    assert '<span class="chip chip-sell"><span>−</span>SELL</span>' in report
    assert '<span class="chip chip-wait"><span>•</span>WAIT</span>' in report
    assert '<tr class="signal-buy">' in report
    assert '<tr class="signal-sell">' in report
    assert '<tr class="signal-wait">' in report
    assert "BUY means Ask is at or below Max Buy" in report
    assert "SELL means Bid is at or above Rich Sell" in report
    assert "WAIT means no buy or sell condition is currently met" in report
    assert "Ask Upside shows the percentage move from the current Ask to Fair" in report
    assert "after-fee" not in report
    assert report.index("Buy Item") < report.index("Sell Item") < report.index("Wait Item")


def test_report_is_print_first_without_price_evolution_styles():
    report = generate_html_report(pd.DataFrame([{
        "item_name": "Iron",
        "latest_price": 0.08,
        "latest_ask": 0.079,
        "latest_bid": 0.078,
        "stable_fair_price_7d": 0.081,
    }]))

    assert "<details" not in report
    assert ".table-wrap { overflow: visible; }" in report
    assert ".price-evolution-table" not in report
    assert "overflow-x: auto" in report
    assert "@media print" in report
    assert "@page { size: landscape; margin: 10mm; }" in report
    assert "thead { display: table-header-group; }" in report
    assert ".signal-help {" in report
    assert '<div class="table-wrap compact-table price-guide-table">' in report
    assert ".price-guide-table .col-signal .chip" in report
    assert ".price-guide .col-signal" not in report


def test_market_trends_uses_cross_horizon_completed_transaction_fields():
    report = generate_html_report(pd.DataFrame([{
        "item_name": "Scraps",
        "last_trade_price": 0.212,
        "percent_change_1d": -1.0,
        "percent_change_7d": 1.92,
        "percent_change_30d": 4.0,
        "min_30d": 0.180,
        "max_30d": 0.220,
        "trend_path_30d": [
            {"timestamp": 1_780_000_000, "price": 0.190},
            {"timestamp": 1_780_518_400, "price": 0.205},
            {"timestamp": 1_781_036_800, "price": 0.212},
        ],
        "trend_path_30d_start_epoch": 1_780_000_000,
        "trend_path_30d_end_epoch": 1_782_592_000,
    }]))

    assert '<div class="table-wrap compact-table market-trends-table" role="region" aria-label="Market trends table" tabindex="0">' in report
    assert '<th class="col-last-trade number">Last Trade</th>' in report
    assert '<th class="col-1d-change number">1D Change</th>' in report
    assert '<th class="col-7d-change number">7D Change</th>' in report
    assert '<th class="col-30d-change number">30D Change</th>' in report
    assert '<th class="col-30d-path text">30D Path</th>' in report
    assert '<th class="col-30d-position number">30D Position</th>' in report
    assert '<th class="col-pattern text">Pattern</th>' in report
    assert "Pullback" in report
    assert 'aria-label="Pullback — 1D: Down, 7D: Up, 30D: Up"' in report
    assert "Near ceiling" in report
    assert ".market-trends-table .col-item { width: 14%; font-weight: 700; white-space: nowrap; }" in report
    assert "grid-template-columns: 56px 36px minmax(72px, 1fr)" in report
    assert '<svg class="trend-path"' in report
    assert "3 daily observations spanning 12 of 30 days" in report
    assert "Price changes and mini charts use completed trades." in report
    assert "Each mini chart has its own scale" in report
    trends_section = report[report.index("<h2>Market Trends</h2>"):report.index("<h2>Item Notes</h2>")]
    for forbidden in (">Spread %<", ">Activity<", ">Range<", ">Price State<"):
        assert forbidden not in trends_section
    assert "Price / 7D Change" not in report
    assert '<div class="table-wrap compact-table price-evolution-table">' not in report


def test_market_trends_renders_flat_30d_range_without_a_position_graphic():
    report = generate_html_report(pd.DataFrame([{
        "item_name": "Oil",
        "last_trade_price": 0.17,
        "percent_change_1d": 0.0,
        "percent_change_7d": 0.0,
        "percent_change_30d": 0.0,
        "min_30d": 0.17,
        "max_30d": 0.17,
    }]))

    trends_section = report[report.index("<h2>Market Trends</h2>"):report.index("<h2>Item Notes</h2>")]
    assert '<span class="position-label">Flat</span>' in trends_section
    assert "position-track" not in trends_section


def test_market_trends_preserves_last_trade_price_precision():
    report = generate_html_report(pd.DataFrame([{
        "item_name": "Petroleum",
        "last_trade_price": 0.1722,
    }]))

    trends_section = report[report.index("<h2>Market Trends</h2>"):report.index("<h2>Item Notes</h2>")]
    assert '<td class="col-last-trade number">0.172</td>' in trends_section


def test_completed_activity_bar_uses_production_adjusted_work_and_excludes_unknown_factors():
    report = generate_html_report(pd.DataFrame([
        {
            "item_name": "Iron", "traded_quantity_7d": 1_000,
            "traded_value_7d": 80, "trade_count_7d": 40,
            "production_points": 1, "total_production_points": 1,
        },
        {
            "item_name": "Steel", "traded_quantity_7d": 200,
            "traded_value_7d": 320, "trade_count_7d": 20,
            "production_points": 10, "total_production_points": 20,
        },
        {
            "item_name": "Case1", "traded_quantity_7d": 500,
            "traded_value_7d": 1_500, "trade_count_7d": 5,
            "production_points": None, "total_production_points": None,
        },
    ]))

    assert "ranked by total traded value (price × quantity)" in report
    assert '>Total PP : Item</th>' in report
    assert 'title="1 total upstream Production Point (PP) per item">1 : 1</td>' in report
    assert 'title="20 total upstream Production Points (PP) per item">20 : 1</td>' in report
    assert 'title="Total upstream PP-per-item ratio unavailable">N/A</td>' in report
    assert 'aria-label="1.5K completed transaction value; 100.0% of the highest-value market"' in report
    assert 'aria-label="1.0K PP-equivalent completed volume; 25.0% of the busiest comparable item"' in report
    assert 'aria-label="4.0K PP-equivalent completed volume; 100.0% of the busiest comparable item"' in report
    assert 'aria-label="N/A PP-equivalent completed volume; 0.0% of the busiest comparable item"' in report
    assert report.index('aria-label="1.5K completed transaction value') < report.index('aria-label="320 completed transaction value')
    assert report.index('aria-label="320 completed transaction value') < report.index('aria-label="80 completed transaction value')
    assert "500 units" in report
    assert "5 trades" in report

    top_report = generate_html_report(pd.DataFrame([
        {"item_name": "Iron", "traded_quantity_7d": 1_000, "traded_value_7d": 80,
         "production_points": 1, "total_production_points": 1},
        {"item_name": "Steel", "traded_quantity_7d": 200, "traded_value_7d": 320,
         "production_points": 10, "total_production_points": 20},
    ]), top=1)
    assert 'aria-label="4.0K PP-equivalent completed volume; 100.0%' in top_report
    assert 'aria-label="1.0K PP-equivalent completed volume' not in top_report


def test_scraps_is_never_described_as_inactive_when_price_is_stable():
    report = generate_html_report(pd.DataFrame([{
        "item_name": "Scraps",
        "current_price": 0.212,
        "latest_price": 0.212,
        "momentum_7d_pct": 1.92,
        "traded_value_7d": 2_500_000,
        "traded_quantity_7d": 11_900_000,
        "trade_count_7d": 28_000,
        "trades_7d": 28_000,
        "tendency_labels_7d": "Range-bound, Stable",
        "production_points": None,
    }]))

    assert 'aria-label="2.5M completed transaction value; 100.0% of the highest-value market"' in report
    assert '<span class="trend-change signed-positive">' in report
    assert "11.9M units / 28.0K trades" not in report
    assert "Little net price change" not in report
    assert ">Pattern<" in report


def test_report_ignores_unavailable_internal_flip_data():
    report = generate_html_report(pd.DataFrame([{
        "item_name": "Heavy Ammo",
        "flip_verdict": "Unavailable",
        "flip_reason_codes": "missing_execution_interval",
        "flip_quantity": 100,
        "flip_forecast_evidence": "Weak",
        "flip_forecast_samples": 30,
        "forecast_execution_evaluable_samples": 0,
        "flip_quote_age_minutes": 0.83,
        "flip_passive_limit_price": 2.39,
    }]), assumptions=FlipAssumptions(quantity=100))

    assert "Flip Board" not in report
    assert "Flip ranking is collecting execution history" not in report
    assert "quantity-100 entry and exit observations" not in report
    assert "0 executable · 30 forecasts" not in report
    assert "Quote age 0.8m" not in report
    assert "Not enough same-size historical fills" not in report
    assert "Limit idea" not in report
    assert "Fair Value &amp; Buy / Sell Signals" in report
    assert ">Forecast Exit<" not in report
    assert ">Net<" not in report
    assert "Strongest upside signal" not in report
    assert "Trend Highlights" not in report
    assert "Immediate Loss" not in report
