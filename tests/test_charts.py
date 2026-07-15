from pathlib import Path

import pandas as pd

from warera_quant.charts import (
    build_ohlc,
    build_spread_series,
    chart_ylim,
    featured_item_codes,
    moving_average_breaks,
    normalize_ohlc,
    plot_price_chart,
    render_featured_chart,
    render_trend_path_svg,
)


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


def test_charts_do_not_import_api_or_db_modules():
    source = (Path(__file__).parents[1] / "src" / "warera_quant" / "charts.py").read_text(encoding="utf-8")

    forbidden = ["live_market", "warera_api", "api_client", "MarketStore", "sqlite3", "requests"]
    assert [name for name in forbidden if name in source] == []
