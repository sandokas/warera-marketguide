from __future__ import annotations

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

from .live_market import _display_name, _parse_time, _price_from_transaction, _trpc_data


def _transactions_from_pages(pages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    transactions: list[dict[str, Any]] = []
    for page in pages:
        data = _trpc_data(page)
        if isinstance(data, dict):
            items = data.get("items", [])
            if isinstance(items, list):
                transactions.extend(item for item in items if isinstance(item, dict))
    return transactions


def _transaction_frame(transactions: Iterable[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for transaction in transactions:
        created_at = _parse_time(transaction.get("createdAt"))
        price = _price_from_transaction(transaction)
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


def build_ohlc(transactions: Iterable[dict[str, Any]], *, interval: str = "15min") -> pd.DataFrame:
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
    interval: str = "15min",
    ma_window: int = 4,
    show_moving_average: bool = True,
    min_range_pct: float = 5.0,
) -> Path | None:
    if candles.empty:
        return None

    flags = moving_average_breaks(candles, ma_window=ma_window) if show_moving_average else candles.copy()
    add_plots = []
    if show_moving_average:
        up_markers = flags["Close"].where(flags["Break Up"])
        down_markers = flags["Close"].where(flags["Break Down"])
        if flags["MA"].notna().any():
            add_plots.append(mpf.make_addplot(flags["MA"], color="#2563eb", width=1.2))
        if up_markers.notna().any():
            add_plots.append(mpf.make_addplot(up_markers, type="scatter", marker="^", markersize=90, color="#16a34a"))
        if down_markers.notna().any():
            add_plots.append(mpf.make_addplot(down_markers, type="scatter", marker="v", markersize=90, color="#dc2626"))

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    style = mpf.make_mpf_style(
        base_mpf_style="yahoo",
        marketcolors=mpf.make_marketcolors(up="#16a34a", down="#dc2626", inherit=True),
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


def featured_item_codes(df: pd.DataFrame) -> list[str]:
    if "item_code" not in df.columns:
        return []

    candidates = df[df["item_code"].notna()].copy()
    if candidates.empty:
        return []

    return [str(item_code) for item_code in candidates["item_code"].tolist()]


def render_featured_snapshot_chart(
    snapshot: dict[str, Any],
    output_dir: str | Path,
    *,
    candidate_item_codes: Iterable[str] | None = None,
    featured_item_code: str | None = None,
    interval: str = "15min",
    ma_window: int = 4,
    show_moving_average: bool = True,
    min_range_pct: float = 5.0,
) -> Path | None:
    transaction_snapshots = snapshot.get("transactions", {})
    if not isinstance(transaction_snapshots, dict):
        return None

    selected = []
    if featured_item_code:
        selected.append(featured_item_code)
    selected.extend(item_code for item_code in (candidate_item_codes or []) if item_code not in selected)
    if not selected:
        selected = list(transaction_snapshots)

    out = Path(output_dir)
    for item_code in selected:
        pages = transaction_snapshots.get(item_code, [])
        if not isinstance(pages, list):
            continue
        transactions = _transactions_from_pages(pages)
        candles = build_ohlc(transactions, interval=interval)
        chart_path = plot_price_chart(
            candles,
            item_name=f"Featured Trade: {_display_name(item_code)}",
            output_path=out / "featured-trade.png",
            interval=interval,
            ma_window=ma_window,
            show_moving_average=show_moving_average,
            min_range_pct=min_range_pct,
        )
        if chart_path is not None:
            return chart_path
    return None
