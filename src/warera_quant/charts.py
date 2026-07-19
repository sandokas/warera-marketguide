from __future__ import annotations

from collections.abc import Mapping
import logging
import os
from pathlib import Path
from typing import Any, Iterable

os.environ.setdefault("MPLCONFIGDIR", "/tmp/warera-quant-matplotlib")

import matplotlib

matplotlib.use("Agg")
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

import mplfinance as mpf
import pandas as pd
from matplotlib import pyplot as plt


def render_table_png(
    table: pd.DataFrame,
    output_path: str | Path,
    *,
    title: str,
) -> Path:
    """Render a report table as a standalone PNG image."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    display = table.fillna("").astype(str)
    row_count = max(len(display), 1)
    column_count = max(len(display.columns), 1)
    width = min(24.0, max(8.0, column_count * 2.05))
    height = max(2.4, 1.15 + row_count * 0.42)
    figure, axis = plt.subplots(figsize=(width, height), dpi=160)
    figure.patch.set_facecolor("#f7f8fa")
    axis.set_facecolor("#f7f8fa")
    axis.axis("off")
    axis.set_title(title, loc="left", fontsize=15, fontweight="bold", color="#172033", pad=14)

    rendered = axis.table(
        cellText=display.values.tolist(),
        colLabels=[str(column) for column in display.columns],
        cellLoc="left",
        colLoc="left",
        loc="upper left",
        bbox=[0, 0, 1, 0.96],
    )
    rendered.auto_set_font_size(False)
    rendered.set_fontsize(8)
    for (row, _column), cell in rendered.get_celld().items():
        cell.set_edgecolor("#d8dde7")
        cell.set_linewidth(0.5)
        cell.PAD = 0.045
        if row == 0:
            cell.set_facecolor("#25324a")
            cell.get_text().set_color("white")
            cell.get_text().set_fontweight("bold")
        else:
            cell.set_facecolor("#ffffff" if row % 2 else "#f0f3f8")
            cell.get_text().set_color("#172033")

    figure.savefig(output, bbox_inches="tight", facecolor=figure.get_facecolor())
    plt.close(figure)
    return output


def render_trend_path_svg(
    points: Iterable[Mapping[str, Any]],
    *,
    aria_label: str,
    window_start: int,
    window_end: int,
    width: int = 96,
    height: int = 28,
) -> str | None:
    """Render a compact, time-proportional path from completed-trade observations."""
    by_timestamp: dict[int, float] = {}
    for point in points:
        try:
            timestamp = int(point.get("timestamp"))
            price = float(point.get("price"))
        except (TypeError, ValueError):
            continue
        if window_start <= timestamp <= window_end and price > 0:
            by_timestamp[timestamp] = price
    usable = sorted(by_timestamp.items())
    if len(usable) < 2 or window_end <= window_start:
        return None

    padding = 2.5
    time_span = window_end - window_start
    prices = [price for _, price in usable]
    low, high = min(prices), max(prices)
    price_span = high - low
    coordinates = []
    for timestamp, price in usable:
        x = padding + (timestamp - window_start) / time_span * (width - 2 * padding)
        y = height / 2 if price_span == 0 else padding + (high - price) / price_span * (height - 2 * padding)
        coordinates.append((x, y))
    path_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in coordinates)
    latest_x, latest_y = coordinates[-1]
    safe_label = (
        aria_label.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return (
        f'<svg class="trend-path" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{safe_label}" preserveAspectRatio="none">'
        f'<polyline class="trend-path-line" points="{path_points}" />'
        f'<circle class="trend-path-latest" cx="{latest_x:.1f}" cy="{latest_y:.1f}" r="2.2" />'
        "</svg>"
    )


def _transaction_frame(transactions: Iterable[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for transaction in transactions:
        created_at = _parse_chart_time(transaction.get("created_at"))
        price = _optional_float(transaction.get("price"))
        if created_at is None or price is None:
            continue
        quantity = transaction.get("quantity")
        try:
            volume = float(quantity)
        except (TypeError, ValueError):
            volume = 1.0
        rows.append({"created_at": created_at, "price": price, "volume": max(volume, 0.0)})

    if not rows:
        return pd.DataFrame(columns=["price", "volume"])

    df = pd.DataFrame(rows).sort_values("created_at")
    df["created_at"] = pd.to_datetime(df["created_at"], utc=True).dt.tz_convert(None)
    return df.set_index("created_at")


def _spread_frame(spread_observations: Iterable[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for observation in spread_observations:
        observed_at = _parse_chart_time(observation.get("observed_at"))
        spread = _optional_float(observation.get("spread"))
        if observed_at is None or spread is None:
            continue
        rows.append({"observed_at": observed_at, "spread": max(spread, 0.0)})

    if not rows:
        return pd.DataFrame(columns=["spread"])

    df = pd.DataFrame(rows).sort_values("observed_at")
    df["observed_at"] = pd.to_datetime(df["observed_at"], utc=True).dt.tz_convert(None)
    return df.set_index("observed_at")


def build_ohlc(transactions: Iterable[dict[str, Any]], *, interval: str = "1h") -> pd.DataFrame:
    trades = _transaction_frame(transactions)
    if trades.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    ohlc = trades["price"].resample(interval).ohlc()
    volume = trades["volume"].resample(interval).sum()
    candles = pd.concat([ohlc, volume.rename("Volume")], axis=1).dropna(subset=["open", "high", "low", "close"])
    candles = candles.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
    flat = candles["High"] == candles["Low"]
    if flat.any():
        padding = candles.loc[flat, "Close"].abs().mul(0.005).clip(lower=0.001)
        candles.loc[flat, "High"] = candles.loc[flat, "High"] + padding
        candles.loc[flat, "Low"] = (candles.loc[flat, "Low"] - padding).clip(lower=0)
    return candles


def normalize_ohlc(candles: pd.DataFrame) -> pd.DataFrame:
    if candles.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    column_map = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    }
    normalized = candles.rename(columns={key: value for key, value in column_map.items() if key in candles.columns})
    required = ["Open", "High", "Low", "Close"]
    missing = [column for column in required if column not in normalized.columns]
    if missing:
        raise ValueError(f"OHLC candles are missing required column(s): {', '.join(missing)}.")

    normalized = normalized.copy()
    if "Volume" not in normalized.columns:
        normalized["Volume"] = 0.0
    normalized = normalized[["Open", "High", "Low", "Close", "Volume"]]
    normalized.index = pd.to_datetime(normalized.index, utc=True, errors="coerce").tz_convert(None)
    return normalized.dropna(subset=["Open", "High", "Low", "Close"]).sort_index()


def build_spread_series(
    spread_observations: Iterable[dict[str, Any]],
    *,
    candle_index: pd.DatetimeIndex,
    interval: str = "1h",
) -> pd.Series:
    spreads = _spread_frame(spread_observations)
    if spreads.empty or candle_index.empty:
        return pd.Series(index=candle_index, dtype="float64", name="Spread")

    series = spreads["spread"].resample(interval).mean().reindex(candle_index).ffill()
    return series.rename("Spread")


def moving_average_breaks(candles: pd.DataFrame, *, ma_window: int = 4) -> pd.DataFrame:
    flags = candles.copy()
    flags["MA"] = flags["Close"].rolling(ma_window, min_periods=ma_window).mean()
    previous_close = flags["Close"].shift(1)
    previous_ma = flags["MA"].shift(1)
    flags["Break Up"] = (previous_close <= previous_ma) & (flags["Close"] > flags["MA"])
    flags["Break Down"] = (previous_close >= previous_ma) & (flags["Close"] < flags["MA"])
    return flags


def chart_ylim(candles: pd.DataFrame, *, min_range_pct: float = 5.0) -> tuple[float, float] | None:
    if candles.empty:
        return None

    low = float(candles["Low"].min())
    high = float(candles["High"].max())
    center = (low + high) / 2
    if center <= 0:
        return None

    actual_range = high - low
    minimum_range = center * (min_range_pct / 100)
    target_range = max(actual_range, minimum_range)
    padding = max((target_range - actual_range) / 2, target_range * 0.05)
    lower = max(0, low - padding)
    upper = high + padding
    if upper <= lower:
        return None
    return lower, upper


def plot_price_chart(
    candles: pd.DataFrame,
    *,
    item_name: str,
    output_path: str | Path,
    interval: str = "1h",
    ma_window: int = 4,
    show_moving_average: bool = True,
    show_min_max_band: bool = True,
    spread: pd.Series | None = None,
    min_range_pct: float = 5.0,
) -> Path | None:
    if candles.empty:
        return None

    flags = moving_average_breaks(candles, ma_window=ma_window) if show_moving_average else candles.copy()
    add_plots = []
    if show_min_max_band:
        add_plots.append(mpf.make_addplot(flags["High"], color="#94a3b8", width=0.8, linestyle="--"))
        add_plots.append(mpf.make_addplot(flags["Low"], color="#94a3b8", width=0.8, linestyle="--"))
    if show_moving_average:
        up_markers = flags["Close"].where(flags["Break Up"])
        down_markers = flags["Close"].where(flags["Break Down"])
        if flags["MA"].notna().any():
            add_plots.append(mpf.make_addplot(flags["MA"], color="#2563eb", width=1.2))
        if up_markers.notna().any():
            add_plots.append(mpf.make_addplot(up_markers, type="scatter", marker="^", markersize=90, color="#16a34a"))
        if down_markers.notna().any():
            add_plots.append(mpf.make_addplot(down_markers, type="scatter", marker="v", markersize=90, color="#dc2626"))
    if spread is not None and not spread.empty and spread.notna().any():
        aligned_spread = spread.reindex(flags.index).ffill()
        add_plots.append(
            mpf.make_addplot(
                aligned_spread,
                panel=2,
                color="#7c3aed",
                width=1,
                ylabel="Spread",
            )
        )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    style = mpf.make_mpf_style(
        base_mpf_style="yahoo",
        marketcolors=mpf.make_marketcolors(
            up="#16a34a",
            down="#dc2626",
            volume={"up": "#0891b2", "down": "#f97316"},
            inherit=True,
        ),
        gridstyle=":",
        facecolor="#ffffff",
        figcolor="#ffffff",
    )
    title = f"{item_name} - {interval} candles"
    if show_moving_average:
        title += f", MA {ma_window}"
    plot_kwargs = {
        "type": "candle",
        "volume": True,
        "style": style,
        "title": title,
        "ylabel": "Price",
        "ylabel_lower": "Qty",
        "datetime_format": "%m-%d %H:%M",
        "tight_layout": True,
        "savefig": dict(fname=str(output), dpi=150, bbox_inches="tight"),
    }
    ylim = chart_ylim(flags, min_range_pct=min_range_pct)
    if ylim is not None:
        plot_kwargs["ylim"] = ylim
    if add_plots:
        plot_kwargs["addplot"] = add_plots
    mpf.plot(flags, **plot_kwargs)
    return output


def render_featured_chart(
    chart_data: Mapping[str, Any] | pd.DataFrame | Iterable[dict[str, Any]],
    output_path: str | Path,
    *,
    item_name: str,
    interval: str = "1h",
    ma_window: int = 4,
    show_moving_average: bool = True,
    show_spread: bool = True,
    min_range_pct: float = 5.0,
) -> Path | None:
    candles: pd.DataFrame
    spread = None

    if isinstance(chart_data, pd.DataFrame):
        candles = normalize_ohlc(chart_data)
    elif isinstance(chart_data, Mapping):
        raw_candles = chart_data.get("candles")
        if isinstance(raw_candles, pd.DataFrame):
            candles = normalize_ohlc(raw_candles)
        else:
            candles = build_ohlc(chart_data.get("trades", []), interval=interval)
        if show_spread:
            raw_spread = chart_data.get("spread")
            if isinstance(raw_spread, pd.Series):
                spread = raw_spread
            else:
                spread = build_spread_series(
                    chart_data.get("spread_observations", []),
                    candle_index=candles.index,
                    interval=interval,
                )
    else:
        candles = build_ohlc(chart_data, interval=interval)

    if candles.empty:
        return None

    return plot_price_chart(
        candles,
        item_name=item_name,
        output_path=output_path,
        interval=interval,
        ma_window=ma_window,
        show_moving_average=show_moving_average,
        spread=spread,
        min_range_pct=min_range_pct,
    )


def featured_item_codes(df: pd.DataFrame) -> list[str]:
    if "item_code" not in df.columns:
        return []

    candidates = df[df["item_code"].notna()].copy()
    if candidates.empty:
        return []

    return [str(item_code) for item_code in candidates["item_code"].tolist()]


def _parse_chart_time(value: Any) -> pd.Timestamp | None:
    if not value:
        return None
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
