import pandas as pd
from types import SimpleNamespace

from warera_quant.metrics import (
    FlipAssumptions,
    classify_price_dislocation,
    price_dislocation_fields,
    select_highlighted_items,
)
from warera_quant.report import generate_html_report, write_outputs



def _classified(row, *, min_tick=0.001):
    item = classify_price_dislocation(row, min_tick=min_tick)
    return {**row, **price_dislocation_fields(item)}, item


def _selected(rows):
    items = [classify_price_dislocation(row, min_tick=0.001) for row in rows]
    return select_highlighted_items(items, {}, require_chart_history=False)


def test_chart_linked_highlights_pair_cards_and_images_before_first_table(tmp_path):
    items = _selected([
        {"item_code": "bread", "item_name": "Bread", "last_trade_price": 8,
         "stable_fair_price_7d": 10, "price_p10_7d": 9, "price_p90_7d": 11},
        {"item_code": "oil", "item_name": "Oil", "last_trade_price": 13,
         "stable_fair_price_7d": 10, "price_p10_7d": 9, "price_p90_7d": 11},
    ])
    report = generate_html_report(
        pd.DataFrame([{"item_name": "Bread"}]), output_dir=tmp_path,
        highlights=[
            {"item": items[0], "chart_path": tmp_path / "charts/largest-discount-price-action.png"},
            {"item": items[1], "chart_path": tmp_path / "charts/largest-premium-price-action.png"},
        ],
    )
    section = report[report.index('<section class="highlight-section">'):
                     report.index("Trading Guide")]
    assert section.count('class="highlight-column"') == 2
    assert section.index('data-item-code="bread"') < section.index("largest-discount-price-action.png")
    assert section.index('data-item-code="oil"') < section.index("largest-premium-price-action.png")
    assert '.highlight-chart { display: block; width: 100%; height: auto;' in report
    assert '.summary-grid { grid-template-columns: 1fr; }' in report
    assert "Featured Price History" not in report


def test_explicit_zero_chart_linked_highlights_omit_empty_area():
    report = generate_html_report(pd.DataFrame([{"item_name": "Bread"}]), highlights=[])
    assert "Largest price gaps" not in report
    assert 'class="highlight-section"' in report
    assert 'class="highlight-column"' not in report
    capture = report[report.index('data-report-asset="header"'):
                     report.index("Trading Guide")]
    assert "Market intelligence, without the noise." in capture
    assert "No meaningful price dislocations" in capture
    assert "Eligible latest trades are within their normal 7D ranges or less than one tick from fair value." in capture


def test_highlight_card_remains_when_its_chart_is_unavailable(tmp_path):
    item = SimpleNamespace(item_code="bread", item_name="Bread", role="largest_discount",
                           gap_pct=-20, fair_7d=10)
    report = generate_html_report(
        pd.DataFrame([{"item_name": "Bread"}]), output_dir=tmp_path,
        highlights=[{"item": item, "chart_path": None}],
    )
    section = report[report.index('<section class="highlight-section">'):
                     report.index("Trading Guide")]
    assert 'data-item-code="bread"' in section
    assert '<img class="highlight-chart"' not in section
    assert 'signed-negative">&#9660; -20.00%' in section


def test_report_ends_with_third_party_tools_and_project_signoff():
    report = generate_html_report(pd.DataFrame(), top=0)

    assert report.index("Alternative third-party tools") < report.index("Did you know?")
    assert 'href="https://workerprofit.theorist.ninja/"' in report
    assert 'href="https://warera.unikhorne.dev/market"' in report
    assert 'href="https://github.com/sandokas/warera-marketguide"' in report
    assert "You can download the code that generated this report at:" in report
    assert "If you found it useful, don't forget to Like and Subscribe!" in report
    assert report.index("Did you know?") < report.index("</footer>")


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
    assert '<span class="chip chip-down"><span>â†“</span>Down</span>' not in report
    assert "Negative momentum" not in report
    assert "Buy near" not in report


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
    assert "Trading Guide" in report
    assert ">Forecast Exit<" not in report
    assert ">Expected Net<" not in report
    assert "No meaningful price dislocations" in report
    assert "Largest premium" not in report
    assert "Strongest upside signal" not in report
    assert "Strongest downside signal" not in report
    assert report.count('<article class="summary-card') == 1


def test_market_pulse_arrow_colors_follow_direction():
    rows = [
        {
            "item_code": "discount", "item_name": "Discount", "last_trade_price": 8,
            "stable_fair_price_7d": 10, "price_p10_7d": 9, "price_p90_7d": 11,
            "forecast_current_signal": "Down", "forecast_evidence": "Supported",
        },
        {
            "item_code": "premium", "item_name": "Premium", "last_trade_price": 12,
            "stable_fair_price_7d": 10, "price_p10_7d": 9, "price_p90_7d": 11,
            "forecast_current_signal": "Up", "forecast_evidence": "Supported",
        },
    ]
    report = generate_html_report(
        pd.DataFrame(rows),
        highlights=[{"item": item, "chart_path": None} for item in _selected(rows)],
    )

    assert '&#9660; -20.00%' in report
    assert '&#9650; +20.00%' in report


def test_one_sided_highlights_use_honest_copy_and_shared_item_classification():
    row, item = _classified({
        "item_code": "bread", "item_name": "Bread", "last_trade_price": 12,
        "stable_fair_price_7d": 10, "price_p10_7d": 9, "price_p90_7d": 11,
        "bid": 11.8, "ask": 12.1,
    })
    selected = select_highlighted_items([item], {}, require_chart_history=False)
    report = generate_html_report(
        pd.DataFrame([row]),
        highlights=[{"item": selected[0], "chart_path": None}],
    )

    assert "Rich above normal range" in report
    assert "No meaningful discount;" not in report
    assert "Cheap below normal range" not in report
    context = report[report.index("<h2>Item Price Context</h2>"):report.index("Alternative third-party tools")]
    assert "Classification: <strong class=\"price-context-classification\">Rich above normal range</strong>" in context


def test_item_price_context_rail_uses_supplied_coordinates_and_accessible_exact_values():
    row, _ = _classified({
        "item_code": "rail", "item_name": "Rail Item", "last_trade_price": 14,
        "stable_fair_price_7d": 10, "price_p10_7d": 8, "price_p90_7d": 12,
        "volume_7d": 500, "trade_count_7d": 40, "momentum_7d_pct": 9,
    })
    report = generate_html_report(pd.DataFrame([row]), highlights=[])
    context = report[report.index("<h2>Item Price Context</h2>"):report.index("Alternative third-party tools")]

    assert 'data-report-asset="item-price-context-card" data-item-code="rail"' in context
    assert "7D normal range:<br><strong>8.000-12.000 (40.00%)</strong>" in context
    assert 'style="left:20.000%;width:60.000%"' in context
    assert 'price-range-marker-fair" style="left:50.000%"' in context
    assert 'price-range-marker-latest" style="left:100.000%"' in context
    assert 'aria-label="7D price range for Rail Item. P10 8.000, P90 12.000, 7D VWAP 10.000, latest completed trade 14.000."' in context
    assert "range-symbol-fair" in context and "range-symbol-latest" in context
    assert "volume" not in context.lower()
    assert "trade count" not in context.lower()
    assert "momentum" not in context.lower()
    assert "overflow-x" not in context
    assert '<svg class="trend-path"' not in context
    assert ".png" not in context


def test_item_price_context_missing_evidence_omits_rail():
    row, _ = _classified({
        "item_code": "missing", "item_name": "Missing", "last_trade_price": 10,
        "stable_fair_price_7d": 10, "price_p10_7d": None, "price_p90_7d": 11,
    })
    report = generate_html_report(pd.DataFrame([row]), highlights=[])
    context = report[report.index("<h2>Item Price Context</h2>"):report.index("Alternative third-party tools")]

    assert "Insufficient 7D range evidence" in context
    assert 'class="price-range-rail"' not in context


def test_low_priced_band_edge_is_neutral_in_header_and_item_context():
    row, item = _classified({
        "item_code": "scraps", "item_name": "Scraps", "last_trade_price": 0.079,
        "stable_fair_price_7d": 0.0781503825, "price_p10_7d": 0.077,
        "price_p90_7d": 0.079,
    })
    assert select_highlighted_items([item], {}, require_chart_history=False) == []

    report = generate_html_report(pd.DataFrame([row]), highlights=[])

    assert "No meaningful price dislocations" in report
    assert "Within normal range" in report
    assert "Rich above normal range" not in report


def test_charted_and_non_charted_cards_render_the_same_shared_classification(tmp_path):
    row, item = _classified({
        "item_code": "bread", "item_name": "Bread", "last_trade_price": 8,
        "stable_fair_price_7d": 10, "price_p10_7d": 9, "price_p90_7d": 11,
    })
    selected = select_highlighted_items([item], {}, require_chart_history=False)[0]
    plain = generate_html_report(
        pd.DataFrame([row]), highlights=[{"item": selected, "chart_path": None}],
    )
    charted = generate_html_report(
        pd.DataFrame([row]), output_dir=tmp_path,
        highlights=[{"item": selected, "chart_path": tmp_path / selected.filename}],
    )

    for report in (plain, charted):
        assert "Cheap below normal range" in report
        assert "-20.00%" in report
        assert "vs 7D" in report
        assert "normal-range widths" not in report


def test_same_side_second_role_uses_meaningful_visible_copy():
    rows = [
        {"item_code": "alpha", "item_name": "Alpha", "last_trade_price": 12,
         "stable_fair_price_7d": 10, "price_p10_7d": 9, "price_p90_7d": 11},
        {"item_code": "beta", "item_name": "Beta", "last_trade_price": 13,
         "stable_fair_price_7d": 10, "price_p10_7d": 9, "price_p90_7d": 11},
    ]
    selected = _selected(rows)
    report = generate_html_report(
        pd.DataFrame(rows),
        highlights=[{"item": item, "chart_path": None} for item in selected],
    )

    assert 'data-highlight-role="largest_premium"' in report
    assert 'data-highlight-role="second_largest_premium"' in report
    assert '&#9650; +20.00%' in report
    assert '&#9650; +30.00%' in report
    assert "No meaningful discount" not in report


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
    }]))

    assert report.index("Historical watch items") < report.index("Trading Guide")
    assert report.index("Trading Guide") < report.index("Current Order Book")
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
    assert ".book-summary .book-pressure { white-space: nowrap; }" in report
    assert '<th class="book-wall number">Buy Wall</th>' in report
    assert '<td class="book-wall number">36.020</td>' in report
    assert '<th class="book-wall number">Sell Wall</th>' in report
    assert '<td class="book-wall number">36.200</td>' in report
    assert '.book-pressure-content {' in report
    assert "<details" not in report
    assert ".flip-board" not in report
    assert "Activity Comparison" in report
    assert "Liquidity depth score" not in report


def test_report_chart_alt_text_describes_trailing_90d_history(tmp_path):
    report = generate_html_report(
        pd.DataFrame(),
        chart_path=tmp_path / "featured.png",
        chart_label="Iron",
        output_dir=tmp_path,
    )

    assert 'alt="Featured trailing 90D market price-action chart"' in report


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
    assert "0 executable Â· 30 forecasts" not in report
    assert "Quote age 0.8m" not in report
    assert "Not enough same-size historical fills" not in report
    assert "Limit idea" not in report
    assert "Trading Guide" in report
    assert ">Forecast Exit<" not in report
    assert ">Net<" not in report
    assert "Strongest upside signal" not in report
    assert "Trend Highlights" not in report
    assert "Immediate Loss" not in report


def test_target_reading_order_and_all_item_coverage_ignore_legacy_top():
    rows = [{'item_code': f'item{i}', 'item_name': f'Item {i:02d}', 'order_book': {'best_bid': 1, 'best_ask': 2}} for i in range(23)]
    report = generate_html_report(pd.DataFrame(rows), top=1)
    headings = ['WE24 Market Index', 'Historical watch items', '<h2>Trading Guide</h2>', '<h2>Current Order Book</h2>', '<h2>Activity Comparison</h2>', '<h2>Item Price Context</h2>']
    assert [report.index(h) for h in headings] == sorted(report.index(h) for h in headings)
    assert report.count('data-report-asset="item-price-context-card"') == 23
    for i in range(23):
        assert report.count(f'Item {i:02d}') >= 4
    for retired in ('Market Trends', 'Historical Inflation', '>1D Change<', '>7D Change<', '>30D Change<', '>90D Path<', '>Ask Upside %<'):
        assert retired not in report


def test_machine_readable_history_preserved_without_visible_horizon_matrix(tmp_path):
    df = pd.DataFrame([{'item_name': 'Bread', 'percent_change_1d': -1.5, 'percent_change_7d': 2.1, 'percent_change_30d': 3.7, 'traded_quantity_7d': 31641, 'trade_count_7d': 120}])
    csv_path, html_path = write_outputs(df, tmp_path)
    exported = pd.read_csv(csv_path)
    assert exported.loc[0, 'percent_change_30d'] == 3.7
    assert exported.loc[0, 'trade_count_7d'] == 120
    assert (tmp_path / 'market_scores.csv').exists()
    assert 'Market Trends' not in html_path.read_text(encoding='utf-8')


def test_reference_is_strict_and_not_a_short_term_target():
    report = generate_html_report(pd.DataFrame([{'item_name': 'Legacy', 'vwap_7d': 123.456, 'stable_fair_price_1d': 234.567, 'stable_fair_price_30d': 345.678, 'short_term_reference_return_pct': -2.75}]))
    assert '7D VWAP: <strong>N/A</strong>' in report
    assert '123.456' in report  # Explicit VWAP column; never substituted for Fair 7D.
    assert '234.567' not in report and '345.678' not in report
    assert 'Short-term target: N/A BTC' not in report
    assert 'Return to 7D reference (%): -2.75' not in report
    assert 'Short-term setup unconfirmed' not in report
    assert '>Price State</th>' in report


def test_guide_uses_one_signal_and_only_full_size_execution_fields():
    report = generate_html_report(pd.DataFrame([{
        'item_name': 'Thin item', 'ask': 9.1, 'bid': 9.0,
        'short_term_entry_action': 'Wait to buy', 'short_term_holder_action': 'Hold / reassess',
        'guide_executable_ask_vwap': None, 'guide_executable_bid_vwap': None,
        'guide_net_to_fair_pct': None,
    }]), assumptions=FlipAssumptions(quantity=100, fee_pct_per_side=1.5))
    guide = report[report.index('<h2>Trading Guide</h2>'):report.index('<h2>Activity Comparison</h2>')]
    assert '>Signal</th>' in guide and 'Holder' not in guide
    assert '</span>HOLD</span>' in guide and 'WAIT' not in guide
    assert '>Size (units)</th>' not in guide and '>Setup</th>' not in guide
    assert '<p' not in guide and '<tfoot>' not in guide
    assert '>9.100</td>' not in guide and '>9.000</td>' not in guide
    assert '>Buy</th>' in guide and '>Sell</th>' in guide
    assert guide.count('>N/A</td>') == 4
    assert 'signed-neutral">&mdash;' in guide
    assert 'Return to 7D reference (%): N/A' not in report
    assert 'size 100 units; fee 1.5% per side' in report


def test_trading_guide_omits_prose_while_other_tables_retain_metadata():
    import re
    report = generate_html_report(pd.DataFrame([{'item_name': 'A very long item label with <special> & characters'}]), data_synced_at='2026-09-06T12:00:00Z', assumptions=FlipAssumptions(quantity=250, fee_pct_per_side=0.5))
    tables = re.findall(r'<table .*?</table>', report, flags=re.S)
    assert len(tables) == 2
    assert "<tfoot>" not in tables[0]
    for table in tables[1:]:
        footer = table[table.index('<tfoot>'):]
        assert '2026-09-06 12:00 UTC' in footer
        assert 'BTC/unit' in footer and '250 units' in footer and '0.5% per side' in footer
        assert 'N/A = unavailable / insufficient depth' in footer
    assert '&lt;special&gt; &amp; characters' in report
    assert '<details' not in report and '<script' not in report
    assert 'overflow-x: auto' not in report and 'text-overflow: ellipsis' not in report


def test_activity_retains_value_and_pp_bars_without_secondary_clutter():
    report = generate_html_report(pd.DataFrame([
        {'item_name': 'Steel', 'traded_value_7d': 320, 'traded_quantity_7d': 200, 'total_production_points': 20},
        {'item_name': 'Iron', 'traded_value_7d': 80, 'traded_quantity_7d': 1000, 'total_production_points': 1},
        {'item_name': 'Case', 'traded_value_7d': 1500, 'traded_quantity_7d': 500},
    ]))
    activity = report[report.index('<h2>Activity Comparison</h2>'):report.index('<h2>Item Price Context</h2>')]
    assert '1.5K completed transaction value; 100.0%' in activity
    assert '4.0K PP-equivalent completed volume; 100.0%' in activity
    assert '1.0K PP-equivalent completed volume; 25.0%' in activity
    assert 'N/A PP-equivalent completed volume' in activity
    assert '>Units / Trades<' not in activity and '>Total PP : Item<' not in activity
    assert '7D completed turnover (BTC)' in activity


def test_we24_unavailable_is_visible_without_invented_level():
    report = generate_html_report(pd.DataFrame())
    overview = report[report.index('<section class="we24-section"'):report.index('<section class="highlight-section">')]
    assert 'we24-value">&mdash;' in overview
    assert 'Unavailable' in overview
    assert '+0.00%' not in overview
    assert '<p' not in overview


def test_tables_follow_guide_order_and_item_cards_are_alphabetical():
    import re
    rows = [
        {'item_code': 'z', 'item_name': 'Zulu', 'short_term_rank': 0, 'traded_value_7d': 1},
        {'item_code': 'b', 'item_name': 'Bravo', 'short_term_rank': 2, 'traded_value_7d': 999},
        {'item_code': 'a', 'item_name': 'Alpha', 'short_term_rank': 2, 'traded_value_7d': 50},
    ]
    for row in rows:
        row['order_book'] = {'best_bid': 1, 'best_ask': 2}
        row['flip_snapshot_at'] = '2026-09-08T09:12:34Z'
    report = generate_html_report(pd.DataFrame(rows))
    tables = re.findall(r'<table .*?</table>', report, flags=re.S)
    assert len(tables) == 3
    for index, table in enumerate(tables):
        body = re.search(r'<tbody>(.*?)</tbody>', table, flags=re.S).group(1)
        names = [re.sub(r'<[^>]+>', '', cell).split('Timestamp')[0]
                 for cell in re.findall(r'<tr[^>]*><td[^>]*>(.*?)</td>', body, flags=re.S)]
        assert names == (['Bravo', 'Alpha', 'Zulu'] if index == 2 else ['Zulu', 'Alpha', 'Bravo'])
    assert 'Timestamp N/A' not in report
    assert '2026-09-08T09:12:34Z' not in report
    cards = re.findall(r'data-report-asset="item-price-context-card" data-item-code="([^"]+)"', report)
    assert cards == ['a', 'b', 'z']


def test_signal_and_restored_price_state_preserve_supplied_guidance():
    report = generate_html_report(pd.DataFrame([
        {'item_name': 'A', 'guide_entry_action': 'WAIT',
         'guide_holder_action': 'SELL', 'tendency_labels_7d': 'Rising, Thin'},
        {'item_name': 'B', 'guide_entry_action': 'BUY', 'short_term_entry_action': 'Wait to buy',
         'tendency_7d': 'Falling'},
        {'item_name': 'C', 'tendency_labels_7d': 'Stable'},
    ]))
    guide = report.split('<h2>Trading Guide</h2>')[1].split('</section>')[0]
    assert 'chip-flat"><span>&#9679;</span>HOLD' in guide
    assert 'WAIT' not in guide
    assert 'chip-sell"><span>&#8722;</span>SELL' in guide
    assert 'chip-buy"><span>+</span>BUY' in guide
    assert 'chip-up"><span>&#8593;</span>Rising' in guide
    assert 'chip-down"><span>&#8595;</span>Falling' in guide
    assert 'chip-flat"><span>&#8594;</span>Stable' in guide
    assert 'chip-weak"><span>!</span>Thin' in guide


def test_execution_prices_and_weekly_vwap_remain_distinct_from_fair():
    report = generate_html_report(pd.DataFrame([
        {'item_name': 'Discount', 'stable_fair_price_7d': 100, 'median_7d': 85, 'vwap_7d': 88,
         'guide_executable_ask_vwap': 90, 'guide_executable_bid_vwap': 80, 'last_trade_price': 95},
        {'item_name': 'Premium', 'stable_fair_price_7d': 100, 'vwap_7d': 99,
         'guide_executable_ask_vwap': 120, 'guide_executable_bid_vwap': 110, 'last_trade_price': 115},
    ]))
    guide = report.split('<h2>Trading Guide</h2>')[1].split('</section>')[0]
    assert 'signed-negative">-5.00%' in guide
    assert 'signed-positive">+15.00%' in guide
    assert '>Buy</th>' in guide and '>Sell</th>' in guide
    assert '>90.000</td>' in guide and '>80.000</td>' in guide
    assert 'VWAP (BTC)' not in guide
    assert '>%<small' in guide
    assert '>vs 7D reference</small>' in guide
    assert '>7D VWAP</th>' in guide
    assert '>Median 7D</th>' in guide and '>85.000</td>' in guide
    assert 'Fair 7D' not in guide


def test_we24_summary_uses_weekly_change_without_duplicate_date():
    from warera_quant.report import _we24_html
    from pathlib import Path
    summary = _we24_html({'latest_level': 110, 'change_1d_pct': 2, 'change_7d_pct': 10,
                          'display_end': '2026-09-08T00:00:00Z', 'coverage_status': 'complete'}, None, Path('.'))
    assert '+10.00%' in summary and '<small>7D</small>' in summary
    assert '+2.00%' not in summary and '2026-09-08' not in summary
