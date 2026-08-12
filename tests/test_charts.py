from pathlib import Path
from types import SimpleNamespace

import matplotlib.pyplot as plt
import pandas as pd

from warera_quant.charts import (
    _REPORT_CHART_WIDTHS,
    _apply_market_axis_formatters,
    _format_monetary_axis_value,
    _format_quantity_axis_value,
    _monetary_tick_step,
    _report_chart_style,
    build_ohlc,
    build_spread_series,
    chart_ylim,
    chart_viewport,
    featured_item_codes,
    moving_average_breaks,
    normalize_ohlc,
    plot_price_chart,
    render_featured_chart,
    render_highlight_price_action_chart,
    render_report_header_png,
    render_report_item_context_pngs,
    render_report_table_pngs,
    render_trend_path_svg,
)
from warera_quant.metrics import select_highlighted_items


def test_price_chart_style_matches_dark_report_palette():
    style = _report_chart_style()

    assert style["figcolor"] == "#0b1120"
    assert style["facecolor"] == "#111826"
    assert style["gridcolor"] == "#2e3a55"
    assert style["rc"]["text.color"] == "#e2e8f0"
    assert style["marketcolors"]["candle"] == {
        "up": "#6ee7b7",
        "down": "#f87171",
    }
    assert style["marketcolors"]["volume"] == style["marketcolors"]["candle"]
    assert _REPORT_CHART_WIDTHS == {"volume_linewidth": 0}


def test_monetary_axis_labels_never_exceed_three_decimal_places():
    assert _format_monetary_axis_value(0.001) == "0.001"
    assert _format_monetary_axis_value(12.34567) == "12.346"
    assert _format_monetary_axis_value(35_004_000) == "35.004M"
    assert _format_monetary_axis_value(2_500_000) == "2.5M"


def test_monetary_axis_ticks_respect_the_smallest_game_money_unit():
    assert _monetary_tick_step(0.076, 0.080) == 0.001
    assert _monetary_tick_step(10, 18) == 1


def test_market_axis_formatters_change_the_actual_rendered_tick_labels():
    figure, (price_axis, quantity_axis) = plt.subplots(2)
    price_axis.set_ylim(0.076, 0.080)
    quantity_axis.set_ylim(0, 60_000)
    axes = [price_axis, price_axis.twinx(), quantity_axis, quantity_axis.twinx()]

    _apply_market_axis_formatters(axes, has_spread=False)
    figure.canvas.draw()

    price_labels = [
        label.get_text()
        for tick, label in zip(price_axis.get_yticks(), price_axis.get_yticklabels())
        if 0.076 <= tick <= 0.080
    ]
    quantity_labels = [label.get_text() for label in quantity_axis.get_yticklabels()]
    assert price_labels == ["0.076", "0.077", "0.078", "0.079", "0.08"]
    assert all("." not in label for label in quantity_labels)
    assert all(float(tick).is_integer() for tick in quantity_axis.get_yticks())
    plt.close(figure)


def test_quantity_axis_labels_are_whole_units_or_compact_counts():
    assert _format_quantity_axis_value(7) == "7"
    assert _format_quantity_axis_value(7.4) == "7"
    assert _format_quantity_axis_value(35_004_000) == "35.004M"


def test_render_report_table_pngs_captures_only_tables(monkeypatch, tmp_path: Path):
    report = tmp_path / "market_report.html"
    report.write_text("<html><section><h2>Prices &amp; Signals</h2><table class='report-table'></table></section></html>")
    screenshots = []

    class FakeTable:
        def screenshot(self, **kwargs):
            screenshots.append(kwargs)
            Path(kwargs["path"]).write_bytes(b"png")

    class FakeSection:
        def locator(self, selector):
            if selector == "h2":
                return SimpleNamespace(first=SimpleNamespace(text_content=lambda: "Prices & Signals"))
            assert selector == "table.report-table"
            return SimpleNamespace(first=FakeTable())

    class FakeSections:
        def count(self):
            return 1

        def nth(self, index):
            assert index == 0
            return FakeSection()

    class FakePage:
        def goto(self, url, **kwargs):
            assert url == report.resolve().as_uri()
            assert kwargs == {"wait_until": "load"}

        def evaluate(self, expression):
            assert expression == "document.fonts.ready"

        def locator(self, selector):
            assert selector == "section:has(table.report-table)"
            return FakeSections()

    class FakeBrowser:
        def new_page(self, **kwargs):
            assert kwargs["viewport"] == {"width": 1440, "height": 1080}
            return FakePage()

        def close(self):
            pass

    class FakePlaywright:
        chromium = SimpleNamespace(
            launch=lambda **kwargs: FakeBrowser(),
        )

    class FakeContext:
        def __enter__(self):
            return FakePlaywright()

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakeContext())
    monkeypatch.setattr("warera_quant.charts._chrome_executable", lambda _path: "/usr/bin/google-chrome")
    outputs = render_report_table_pngs(
        report,
        tmp_path / "tables",
        browser_executable="/usr/bin/google-chrome",
    )

    assert outputs == [tmp_path / "tables" / "01-prices-and-signals.png"]
    assert outputs[0].read_bytes() == b"png"
    assert screenshots == [{"path": str(outputs[0]), "animations": "disabled"}]


def test_render_report_header_png_uses_explicit_target_and_hides_charts(monkeypatch, tmp_path: Path):
    report = tmp_path / "market_report.html"
    report.write_text("<html></html>")
    calls = []

    class FakeCharts:
        def evaluate_all(self, expression):
            calls.append(("hide", expression))

    class FakeTarget:
        first = None
        def __init__(self):
            self.first = self
        def count(self):
            return 1
        def locator(self, selector):
            assert selector == ".highlight-chart"
            return FakeCharts()
        def screenshot(self, **kwargs):
            calls.append(("screenshot", kwargs))
            Path(kwargs["path"]).write_bytes(b"png")

    target = FakeTarget()
    class FakePage:
        def goto(self, url, **kwargs):
            assert url == report.resolve().as_uri()
        def evaluate(self, expression):
            assert expression == "document.fonts.ready"
        def locator(self, selector):
            assert selector == '[data-report-asset="header"]'
            return target
    class FakeBrowser:
        def new_page(self, **kwargs):
            assert kwargs["viewport"] == {"width": 1440, "height": 1080}
            return FakePage()
        def close(self):
            pass
    class FakeContext:
        def __enter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kwargs: FakeBrowser()))
        def __exit__(self, *_args):
            pass

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakeContext())
    monkeypatch.setattr("warera_quant.charts._chrome_executable", lambda _path: "/chrome")
    output = render_report_header_png(report, tmp_path / "sections", browser_executable="/chrome")

    assert output == tmp_path / "sections" / "report-header.png"
    assert output.read_bytes() == b"png"
    assert calls[0][0] == "hide"
    assert calls[1] == ("screenshot", {"path": str(output), "animations": "disabled"})


def test_render_item_context_pngs_capture_one_card_per_stable_item_filename(monkeypatch, tmp_path: Path):
    report = tmp_path / "market_report.html"
    report.write_text("<html></html>")
    screenshots = []

    class FakeCard:
        def __init__(self, item_code):
            self.item_code = item_code

        def get_attribute(self, name):
            assert name == "data-item-code"
            return self.item_code

        def screenshot(self, **kwargs):
            screenshots.append(kwargs)
            Path(kwargs["path"]).write_bytes(b"png")

    class FakeCards:
        cards = [FakeCard("bread"), FakeCard("Heavy Ammo")]

        def count(self):
            return len(self.cards)

        def nth(self, index):
            return self.cards[index]

    class FakePage:
        def goto(self, url, **kwargs):
            assert url == report.resolve().as_uri()
            assert kwargs == {"wait_until": "load"}

        def evaluate(self, expression):
            assert expression == "document.fonts.ready"

        def locator(self, selector):
            assert selector == '[data-report-asset="item-price-context-card"]'
            return FakeCards()

    class FakeBrowser:
        def new_page(self, **kwargs):
            assert kwargs["viewport"] == {"width": 1440, "height": 1080}
            assert kwargs["device_scale_factor"] == 2
            return FakePage()

        def close(self):
            pass

    class FakeContext:
        def __enter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kwargs: FakeBrowser()))

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr("playwright.sync_api.sync_playwright", lambda: FakeContext())
    monkeypatch.setattr("warera_quant.charts._chrome_executable", lambda _path: "/chrome")

    outputs = render_report_item_context_pngs(
        report,
        tmp_path / "cards",
        browser_executable="/chrome",
    )

    assert outputs == [
        tmp_path / "cards" / "bread-price-context.png",
        tmp_path / "cards" / "heavy-ammo-price-context.png",
    ]
    assert all(output.read_bytes() == b"png" for output in outputs)
    assert screenshots == [
        {"path": str(outputs[0]), "animations": "disabled"},
        {"path": str(outputs[1]), "animations": "disabled"},
    ]


def _trade(created_at: str, price: float, quantity: float = 1) -> dict:
    return {
        "created_at": created_at,
        "price": price,
        "quantity": quantity,
    }


def test_trend_path_svg_uses_time_proportional_points_and_accessible_label():
    svg = render_trend_path_svg(
        [
            {"timestamp": 0, "price": 10},
            {"timestamp": 10, "price": 12},
            {"timestamp": 30, "price": 11},
        ],
        aria_label='30D path: low 10 & high 12',
        window_start=0,
        window_end=30,
    )

    assert svg is not None
    assert 'aria-label="30D path: low 10 &amp; high 12"' in svg
    assert 'points="2.5,25.5 32.8,2.5 93.5,14.0"' in svg
    assert '<circle class="trend-path-latest" cx="93.5" cy="14.0"' in svg


def test_trend_path_svg_requires_two_distinct_timestamps():
    assert render_trend_path_svg(
        [{"timestamp": 1, "price": 10}],
        aria_label="Insufficient",
        window_start=0,
        window_end=30,
    ) is None


def test_trend_path_svg_preserves_blank_time_without_observations():
    svg = render_trend_path_svg(
        [
            {"timestamp": 20, "price": 10},
            {"timestamp": 25, "price": 12},
        ],
        aria_label="Sparse path",
        window_start=0,
        window_end=30,
    )

    assert svg is not None
    assert 'points="63.2,25.5 78.3,2.5"' in svg


def test_build_ohlc_uses_15_minute_buckets():
    candles = build_ohlc(
        [
            _trade("2026-06-27T10:01:00Z", 10),
            _trade("2026-06-27T10:05:00Z", 12, quantity=2),
            _trade("2026-06-27T10:17:00Z", 11),
            _trade("2026-06-27T10:18:00Z", 11.5),
        ],
        interval="15min",
    )

    assert len(candles) == 2
    assert candles.iloc[0]["Open"] == 10
    assert candles.iloc[0]["High"] == 12
    assert candles.iloc[0]["Low"] == 10
    assert candles.iloc[0]["Close"] == 12
    assert candles.iloc[0]["Volume"] == 3
    assert candles.iloc[1]["Open"] == 11
    assert candles.iloc[1]["High"] == 11.5


def test_moving_average_break_flags_crosses():
    candles = build_ohlc(
        [
            _trade("2026-06-27T10:00:00Z", 10),
            _trade("2026-06-27T10:15:00Z", 9),
            _trade("2026-06-27T10:30:00Z", 8),
            _trade("2026-06-27T10:45:00Z", 11),
            _trade("2026-06-27T11:00:00Z", 7),
        ],
        interval="15min",
    )

    flags = moving_average_breaks(candles, ma_window=3)

    assert flags.iloc[3]["Break Up"]
    assert flags.iloc[4]["Break Down"]


def test_featured_item_codes_follows_report_rank_order():
    df = pd.DataFrame([
        {"item_code": "bread", "momentum_7d_pct": 1.0, "trades_7d": 100, "current_price": 1.7},
        {"item_code": "oil", "momentum_7d_pct": -5.0, "trades_7d": 20, "current_price": 0.17},
        {"item_code": "steel", "momentum_7d_pct": 5.0, "trades_7d": 40, "current_price": 1.6},
    ])

    assert featured_item_codes(df) == ["bread", "oil", "steel"]


def test_daily_chart_can_render_without_moving_average(tmp_path: Path):
    candles = build_ohlc([_trade("2026-06-27T10:00:00Z", 10)], interval="15min")
    output = plot_price_chart(
        candles,
        item_name="Featured Trade: Bread",
        output_path=tmp_path / "featured-trade.png",
        show_moving_average=False,
    )

    assert output == tmp_path / "featured-trade.png"
    assert output.exists()


def test_highlight_chart_renders_role_based_30d_asset(tmp_path: Path):
    trades = [
        _trade(f"2026-06-{day:02d}T{hour:02d}:00:00Z", 10 + index / 10, quantity=index + 1)
        for index, (day, hour) in enumerate(
            [(1, 0), (1, 4), (1, 8), (1, 12), (2, 0), (2, 4),
             (2, 8), (2, 12), (3, 0), (3, 4), (3, 8), (3, 12)]
        )
    ]
    highlight = select_highlighted_items([{
        "item_code": "bread", "item_name": "Bread", "last_trade_price": 8,
        "stable_fair_price_7d": 10, "price_p10_7d": 9, "price_p90_7d": 11,
    }], {"bread": trades})[0]
    output = render_highlight_price_action_chart(highlight, tmp_path / highlight.filename)
    assert output == tmp_path / "largest-discount-price-action.png"
    assert output.exists() and output.stat().st_size > 0


def test_chart_can_render_with_spread_line(tmp_path: Path):
    output = render_featured_chart(
        {
            "trades": [
                _trade("2026-06-27T10:00:00Z", 10, quantity=2),
                _trade("2026-06-27T10:15:00Z", 11, quantity=3),
            ],
            "spread_observations": [
                {"observed_at": "2026-06-27T10:00:00Z", "spread": 0.5},
                {"observed_at": "2026-06-27T10:15:00Z", "spread": 0.25},
            ],
        },
        tmp_path / "featured-trade.png",
        item_name="Featured Trade: Bread",
        show_moving_average=False,
    )

    assert output == tmp_path / "featured-trade.png"
    assert output.exists()


def test_chart_can_render_from_normalized_trades(tmp_path: Path):
    output = render_featured_chart(
        [
            _trade("2026-06-27T10:00:00Z", 10, quantity=2),
            _trade("2026-06-27T10:15:00Z", 11, quantity=3),
        ],
        tmp_path / "featured-trade.png",
        item_name="Featured Trade: Bread",
        show_moving_average=False,
    )

    assert output == tmp_path / "featured-trade.png"
    assert output.exists()


def test_chart_can_render_from_ohlc_candles(tmp_path: Path):
    candles = pd.DataFrame(
        [
            {"open": 10, "high": 12, "low": 9, "close": 11, "volume": 5},
            {"open": 11, "high": 13, "low": 10, "close": 12, "volume": 7},
        ],
        index=pd.to_datetime(["2026-06-27T10:00:00Z", "2026-06-27T10:15:00Z"]),
    )

    output = render_featured_chart(
        candles,
        tmp_path / "featured-trade.png",
        item_name="Featured Trade: Bread",
        show_moving_average=False,
    )

    assert output == tmp_path / "featured-trade.png"
    assert output.exists()


def test_normalize_ohlc_accepts_lowercase_candle_columns():
    candles = pd.DataFrame(
        [{"open": 10, "high": 12, "low": 9, "close": 11, "volume": 5}],
        index=pd.to_datetime(["2026-06-27T10:00:00Z"]),
    )

    normalized = normalize_ohlc(candles)

    assert normalized.columns.tolist() == ["Open", "High", "Low", "Close", "Volume"]
    assert normalized.iloc[0]["Open"] == 10
    assert normalized.index.tz is None


def test_build_spread_series_aligns_observations_to_candles():
    candles = build_ohlc(
        [
            _trade("2026-06-27T10:00:00Z", 10),
            _trade("2026-06-27T10:15:00Z", 11),
        ],
        interval="15min",
    )

    spread = build_spread_series(
        [{"observed_at": "2026-06-27T10:00:00Z", "spread": 0.5}],
        candle_index=candles.index,
        interval="15min",
    )

    assert spread.tolist() == [0.5, 0.5]


def test_chart_ylim_uses_minimum_visible_range():
    candles = pd.DataFrame([
        {"Open": 36.30, "High": 36.475, "Low": 36.30, "Close": 36.40, "Volume": 10},
    ])

    lower, upper = chart_ylim(candles, min_range_pct=5)

    assert upper - lower > 1.8
    assert lower < 36.0
    assert upper > 36.7


def test_chart_viewport_clips_isolated_extreme_wick_but_keeps_candle_bodies():
    candles = pd.DataFrame([
        {"Open": 100, "High": 102, "Low": 99, "Close": 101, "Volume": 10},
        {"Open": 101, "High": 103, "Low": 10, "Close": 102, "Volume": 20},
        {"Open": 102, "High": 103, "Low": 100, "Close": 101, "Volume": 10},
    ])

    viewport = chart_viewport(candles, min_range_pct=5)

    assert viewport is not None
    assert viewport.low_positions == (1,)
    assert viewport.high_positions == ()
    assert viewport.observed_low == 10
    assert viewport.ylim[0] > 90
    assert viewport.ylim[1] > 103


def test_chart_viewport_includes_sustained_drop_recorded_in_candle_body():
    candles = pd.DataFrame([
        {"Open": 100, "High": 102, "Low": 99, "Close": 101, "Volume": 10},
        {"Open": 101, "High": 102, "Low": 49, "Close": 50, "Volume": 20},
        {"Open": 50, "High": 52, "Low": 48, "Close": 51, "Volume": 10},
    ])

    viewport = chart_viewport(candles, min_range_pct=5)

    assert viewport is not None
    assert viewport.low_positions == ()
    assert viewport.ylim[0] < 48
    assert viewport.ylim[1] > 102


def test_charts_do_not_import_api_or_db_modules():
    source = (Path(__file__).parents[1] / "src" / "warera_quant" / "charts.py").read_text(encoding="utf-8")

    forbidden = ["live_market", "warera_api", "api_client", "MarketStore", "sqlite3", "requests"]
    assert [name for name in forbidden if name in source] == []
