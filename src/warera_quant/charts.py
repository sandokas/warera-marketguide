from __future__ import annotations

from collections.abc import Mapping
import logging
import os
from pathlib import Path
import shutil
from typing import Any, Iterable

os.environ.setdefault("MPLCONFIGDIR", "/tmp/warera-quant-matplotlib")

import matplotlib

matplotlib.use("Agg")
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

import mplfinance as mpf
import pandas as pd

from .metrics import HighlightedItem


_REPORT_CHART_COLORS = {
    "background": "#0b1120",
    "panel": "#111826",
    "text": "#e2e8f0",
    "muted": "#94a3b8",
    "grid": "#2e3a55",
    "accent": "#7dd3fc",
    "good": "#6ee7b7",
    "bad": "#f87171",
    "amber": "#fbbf24",
}
_REPORT_CHART_WIDTHS = {"volume_linewidth": 0}


def _report_chart_style() -> dict[str, Any]:
    """Return an mplfinance style matching the dark report palette."""
    return mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=mpf.make_marketcolors(
            up=_REPORT_CHART_COLORS["good"],
            down=_REPORT_CHART_COLORS["bad"],
            edge="inherit",
            wick="inherit",
            volume={
                "up": _REPORT_CHART_COLORS["good"],
                "down": _REPORT_CHART_COLORS["bad"],
            },
        ),
        gridstyle=":",
        gridcolor=_REPORT_CHART_COLORS["grid"],
        facecolor=_REPORT_CHART_COLORS["panel"],
        figcolor=_REPORT_CHART_COLORS["background"],
        edgecolor=_REPORT_CHART_COLORS["grid"],
        rc={
            "axes.labelcolor": _REPORT_CHART_COLORS["text"],
            "axes.titlecolor": _REPORT_CHART_COLORS["text"],
            "figure.facecolor": _REPORT_CHART_COLORS["background"],
            "legend.edgecolor": _REPORT_CHART_COLORS["grid"],
            "legend.facecolor": _REPORT_CHART_COLORS["panel"],
            "legend.labelcolor": _REPORT_CHART_COLORS["text"],
            "savefig.facecolor": _REPORT_CHART_COLORS["background"],
            "text.color": _REPORT_CHART_COLORS["text"],
            "xtick.color": _REPORT_CHART_COLORS["muted"],
            "ytick.color": _REPORT_CHART_COLORS["muted"],
        },
    )


def _chrome_executable(explicit_path: str | Path | None = None) -> str:
    if explicit_path is not None:
        executable = Path(explicit_path)
        if executable.is_file():
            return str(executable)
        raise RuntimeError(f"Chrome executable does not exist: {executable}")

    for name in ("google-chrome", "chromium", "chromium-browser"):
        executable = shutil.which(name)
        if executable:
            return executable
    raise RuntimeError(
        "Table PNG export requires Google Chrome or Chromium. "
        "Set WARERA_CHROME_PATH if the browser is installed outside PATH."
    )


def _table_image_slug(title: str) -> str:
    return "-".join(
        part for part in title.lower().replace("&", "and").split() if part.isalnum()
    ) or "table"


def render_report_table_pngs(
    report_path: str | Path,
    output_dir: str | Path,
    *,
    browser_executable: str | Path | None = None,
) -> list[Path]:
    """Render each styled report table, without surrounding section copy."""
    report = Path(report_path).resolve()
    if not report.is_file():
        raise FileNotFoundError(f"Report HTML does not exist: {report}")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Table PNG export requires Playwright. Install project dependencies with "
            "`.venv/bin/python -m pip install -r requirements.txt`."
        ) from exc

    chrome = _chrome_executable(
        browser_executable or os.environ.get("WARERA_CHROME_PATH")
    )
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=chrome,
            headless=True,
            args=["--allow-file-access-from-files"],
        )
        try:
            page = browser.new_page(
                viewport={"width": 1440, "height": 1080},
                device_scale_factor=2,
            )
            page.goto(report.as_uri(), wait_until="load")
            page.evaluate("document.fonts.ready")
            sections = page.locator("section:has(table.report-table)")
            for index in range(sections.count()):
                section = sections.nth(index)
                heading = section.locator("h2").first.text_content() or "Report Table"
                table = section.locator("table.report-table").first
                output = destination / f"{index + 1:02d}-{_table_image_slug(heading)}.png"
                table.screenshot(path=str(output), animations="disabled")
                outputs.append(output)
        finally:
            browser.close()

    return outputs


def render_report_header_png(
    report_path: str | Path,
    output_dir: str | Path,
    *,
    browser_executable: str | Path | None = None,
) -> Path:
    """Render the hero and rendered highlight cards as one publication asset."""
    report = Path(report_path).resolve()
    if not report.is_file():
        raise FileNotFoundError(f"Report HTML does not exist: {report}")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Header PNG export requires Playwright. Install project dependencies with "
            "`.venv/bin/python -m pip install -r requirements.txt`."
        ) from exc

    chrome = _chrome_executable(
        browser_executable or os.environ.get("WARERA_CHROME_PATH")
    )
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / "report-header.png"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=chrome,
            headless=True,
            args=["--allow-file-access-from-files"],
        )
        try:
            page = browser.new_page(
                viewport={"width": 1440, "height": 1080},
                device_scale_factor=2,
            )
            page.goto(report.as_uri(), wait_until="load")
            page.evaluate("document.fonts.ready")
            target = page.locator('[data-report-asset="header"]').first
            if target.count() != 1:
                raise RuntimeError("Report header capture target is missing or ambiguous.")
            # Charts remain paired with cards in the responsive report DOM, but are not
            # part of the independently publishable header asset.
            target.locator(".highlight-chart").evaluate_all(
                "elements => elements.forEach(element => element.style.display = 'none')"
            )
            target.screenshot(path=str(output), animations="disabled")
        finally:
            browser.close()

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
        for boundary in ("High", "Low"):
            add_plots.append(
                mpf.make_addplot(
                    flags[boundary],
                    color=_REPORT_CHART_COLORS["muted"],
                    width=0.8,
                    linestyle="--",
                )
            )
    if show_moving_average:
        up_markers = flags["Close"].where(flags["Break Up"])
        down_markers = flags["Close"].where(flags["Break Down"])
        if flags["MA"].notna().any():
            add_plots.append(
                mpf.make_addplot(
                    flags["MA"], color=_REPORT_CHART_COLORS["accent"], width=1.2
                )
            )
        if up_markers.notna().any():
            add_plots.append(
                mpf.make_addplot(
                    up_markers,
                    type="scatter",
                    marker="^",
                    markersize=90,
                    color=_REPORT_CHART_COLORS["good"],
                )
            )
        if down_markers.notna().any():
            add_plots.append(
                mpf.make_addplot(
                    down_markers,
                    type="scatter",
                    marker="v",
                    markersize=90,
                    color=_REPORT_CHART_COLORS["bad"],
                )
            )
    if spread is not None and not spread.empty and spread.notna().any():
        aligned_spread = spread.reindex(flags.index).ffill()
        add_plots.append(
            mpf.make_addplot(
                aligned_spread,
                panel=2,
                color=_REPORT_CHART_COLORS["amber"],
                width=1,
                ylabel="Spread",
            )
        )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    style = _report_chart_style()
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
        "update_width_config": _REPORT_CHART_WIDTHS,
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


def render_highlight_price_action_chart(
    highlight: HighlightedItem,
    output_path: str | Path,
    *,
    min_range_pct: float = 5.0,
) -> Path | None:
    """Render prepared analytical candles without mutating their OHLC values."""
    analytical = normalize_ohlc(highlight.candles)
    if analytical.empty:
        return None
    visual = analytical.copy()
    flat = visual["High"] == visual["Low"]
    if flat.any():
        padding = visual.loc[flat, "Close"].abs().mul(0.005).clip(lower=0.001)
        visual.loc[flat, "High"] += padding
        visual.loc[flat, "Low"] = (visual.loc[flat, "Low"] - padding).clip(lower=0)

    add_plots = []
    sma = highlight.sma_7d.copy()
    sma.index = pd.to_datetime(sma.index, utc=True, errors="coerce").tz_convert(None)
    sma = sma.reindex(visual.index)
    if sma.notna().any():
        add_plots.append(
            mpf.make_addplot(
                sma,
                color=_REPORT_CHART_COLORS["accent"],
                width=1.3,
                label="7D SMA",
            )
        )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    style = _report_chart_style()
    role_label = highlight.role.replace("_", " ").title()
    title = f"{highlight.item_name} — {role_label}"
    subtitle = f"Trailing 30D · {highlight.interval} candles · {highlight.history_span}"
    kwargs = {
        "type": "candle", "volume": True, "style": style,
        "ylabel": "Price", "ylabel_lower": "Units traded", "datetime_format": "%m-%d",
        "tight_layout": False,
        "update_width_config": _REPORT_CHART_WIDTHS,
        "savefig": dict(fname=str(output), dpi=150, bbox_inches="tight"),
    }
    if add_plots:
        kwargs["addplot"] = add_plots
    ylim = chart_ylim(analytical, min_range_pct=min_range_pct)
    if ylim is not None:
        kwargs["ylim"] = ylim
    figure, axes = mpf.plot(visual, returnfig=True, **{k: v for k, v in kwargs.items() if k != "savefig"})
    figure.suptitle(f"{title}\n{subtitle}", fontsize=14, fontweight="bold", y=0.98)
    figure.subplots_adjust(top=0.84, left=0.10, right=0.91, bottom=0.12)
    axes[0].axhline(
        highlight.fair_7d,
        color=_REPORT_CHART_COLORS["amber"],
        linestyle="--",
        linewidth=1.2,
        label="Strict 7D fair",
    )
    axes[0].legend(loc="best")
    figure.savefig(output, dpi=150, bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(figure)
    return output


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
