from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import re
from typing import Iterable, Mapping, Sequence, TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from .market_data import ActionCostResult, InflationIndexResult

from .charts import render_trend_path_svg
from .metrics import (
    FlipAssumptions,
    INSUFFICIENT_RANGE_EVIDENCE,
    MEANINGFUL_DISCOUNT,
    MEANINGFUL_PREMIUM,
    MarketMetrics,
    WITHIN_NORMAL_RANGE,
    calculate_range_position_pct,
    calculate_price_gap_pct,
    classify_market_trend_pattern,
)


_FLIP_REASON_LABELS = {
    "missing_order_book": "Current order-book levels are unavailable",
    "missing_timestamp": "The quote timestamp is unavailable",
    "missing_forecast": "A current validated forecast is unavailable",
    "missing_execution_interval": "Not enough same-size historical fills",
    "stale_quote": "The quote is older than the configured maximum",
    "insufficient_ask_depth": "The requested quantity cannot be fully filled from current asks",
    "invalid_book": "The order book or calculated prices are invalid",
    "forecast_insufficient": "Forecast evidence is insufficient",
    "forecast_not_above_baseline": "Forecast evidence does not clearly beat its baseline",
    "margin_non_positive": "Median estimated net margin is not positive",
    "margin_below_threshold": "Median estimated net margin is below the configured minimum",
    "supported_positive_margin": "Supported evidence and the configured margin threshold passed",
}

_FORECAST_REASON_LABELS = {
    "strong_positive_momentum": "Strong positive momentum",
    "positive_momentum": "Positive momentum",
    "strong_negative_momentum": "Strong negative momentum",
    "negative_momentum": "Negative momentum",
    "below_fair": "Below fair value",
    "above_fair": "Above fair value",
}

REPORT_WINDOWS = ("1D", "7D", "30D")
PRIMARY_GUIDANCE_WINDOW = "7D"
ACTIVITY_WINDOW = "7D"

def combine_market_rows_with_metrics(source: pd.DataFrame, metrics: Iterable[MarketMetrics]) -> pd.DataFrame:
    df = source.reset_index(drop=True).copy()
    metric_df = pd.DataFrame([asdict(m) for m in metrics])
    for column in metric_df.columns:
        df[column] = metric_df[column]
    if "percent_change_7d" not in df.columns and "momentum_7d_pct" in df.columns:
        df["percent_change_7d"] = df["momentum_7d_pct"]
    if "trading_attractiveness" in df.columns:
        df = df.sort_values("trading_attractiveness", ascending=False, na_position="last")
    return df


def _fmt(value: object, decimals: int = 3) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    if isinstance(value, (int, float)):
        return f"{value:.{decimals}f}"
    return str(value)


def _fmt_report_value(value: object, *, column: str | None = None) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    if isinstance(value, (int, float)):
        label = str(column or "").lower()
        if (
            "volume" in label
            or "sample" in label
            or label == "trades"
            or label.endswith("trades")
            or "trade count" in label
            or label == "liquidity"
        ):
            return _fmt(value, 0)
        if "%" in label:
            return _fmt(value, 2)
        if label in {"now", "latest", "min", "max", "fair", "buy", "sell", "last", "last trade"} or label.endswith(("low", "high")):
            return _fmt(value, 3)
        if "price" in label or label in {"open", "close", "vwap", "average", "median", "rolling average"}:
            return _fmt(value, 3)
        return _fmt(value, 3)
    return str(value)


def _fmt_compact(value: float) -> str:
    abs_value = abs(value)
    for threshold, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs_value >= threshold:
            compact = value / threshold
            return f"{compact:.1f}{suffix}"
    return _fmt(value, 0)


def _number(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if pd.notna(parsed) else None


def _present(value: object, fallback: object) -> object:
    return fallback if value is None or (not isinstance(value, (list, tuple, dict)) and pd.isna(value)) else value


def _first_number(row: pd.Series, *columns: str) -> float | None:
    for column in columns:
        if column and column in row.index:
            value = _number(row.get(column))
            if value is not None:
                return value
    return None


def _strict_fair_price(row: pd.Series, window: str) -> float | None:
    """Return only the transaction-derived fair value for the labelled horizon."""
    return _first_number(row, f"stable_fair_price_{_window_key(window)}")


def _compatibility_fair_price(row: pd.Series, window_key: str = "7d") -> float | None:
    """Resolve older flat/CSV inputs for legacy 7D-oriented contexts only."""
    return _first_number(
        row,
        "guide_fair_price",
        f"stable_fair_price_{window_key}",
        "stable_fair_price_7d",
        f"vwap_{window_key}",
        f"median_{window_key}",
        f"average_{window_key}",
        f"rolling_average_{window_key}",
    )


def _primary_guidance_fair_price(row: pd.Series) -> float | None:
    """Use strict 7D data for windowed rows, retaining legacy-only compatibility."""
    strict_columns = tuple(f"stable_fair_price_{_window_key(window)}" for window in REPORT_WINDOWS)
    if any(column in row.index for column in strict_columns):
        return _strict_fair_price(row, PRIMARY_GUIDANCE_WINDOW)
    return _compatibility_fair_price(row)


def _window_key(metric_window: str) -> str:
    return metric_window.strip().lower()


def _column(df: pd.DataFrame, *names: str) -> str | None:
    return next((name for name in names if name in df.columns), None)


def _html_page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0b1120;
      --panel: #111826;
      --text: #e2e8f0;
      --muted: #94a3b8;
      --line: #2e3a55;
      --accent: #7dd3fc;
      --accent-soft: #153b57;
      --good: #6ee7b7;
      --good-soft: #0f766e;
      --bad: #f87171;
      --bad-soft: #3f1f1f;
      --amber: #fbbf24;
      --amber-soft: #42310d;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.45;
      overflow-x: visible;
    }}
    main {{
      width: min(1280px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 32px 0 48px;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 24px;
      align-items: center;
      padding: 28px 30px;
      background:
        radial-gradient(circle at 82% 12%, rgba(56, 189, 248, 0.15), transparent 34%),
        linear-gradient(135deg, #111b2e 0%, #0e1729 100%);
      border: 1px solid var(--line);
      border-radius: 16px;
      box-shadow: 0 18px 50px rgba(0, 0, 0, 0.2);
    }}
    main > header {{ width: 100%; }}
    h1, h2, h3, p {{ margin-top: 0; }}
    h1 {{ margin-bottom: 7px; font-size: clamp(1.8rem, 4vw, 2.55rem); letter-spacing: -0.035em; }}
    header h1 {{
      font-size: clamp(2rem, 3vw, 2.8rem);
      line-height: 1.08;
    }}
    h2 {{
      margin-bottom: 8px;
      padding-left: 10px;
      border-left: 3px solid var(--accent);
      font-size: 1.28rem;
      line-height: 1.2;
    }}
    h3 {{ margin-bottom: 8px; font-size: 1rem; }}
    section {{ margin-top: 26px; }}
    section > .muted {{
      max-width: 65ch;
      margin-bottom: 11px;
      font-size: 0.82rem;
      line-height: 1.4;
    }}
    .eyebrow {{
      margin-bottom: 8px;
      color: var(--accent);
      font-size: 0.78rem;
      font-weight: 750;
      letter-spacing: 0;
      text-transform: uppercase;
    }}
    .muted {{ color: var(--muted); }}
    .hero-copy {{ flex: 1 1 auto; min-width: 0; max-width: none; }}
    .hero-copy > p {{ margin-bottom: 0; max-width: 70ch; font-size: 1rem; line-height: 1.45; }}
    .hero-meta {{ flex: 0 0 auto; color: var(--muted); font-size: 0.82rem; text-align: left; white-space: nowrap; }}
    .hero-meta strong {{ display: block; margin-bottom: 4px; color: var(--text); font-size: 0.92rem; }}
    .hero-meta span {{ display: block; }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 16px;
    }}
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
    }}
    .highlight-column {{ display: grid; gap: 12px; min-width: 0; }}
    .highlight-chart {{ display: block; width: 100%; height: auto; border-radius: 10px; }}
    .sr-only {{
      position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
      overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
    }}
    .summary-card {{
      position: relative;
      min-height: 154px;
      overflow: hidden;
      background: linear-gradient(145deg, rgba(24, 35, 54, 0.98), rgba(15, 23, 42, 0.98));
      border: 1px solid var(--line);
      border-radius: 13px;
      padding: 17px;
    }}
    .summary-card::after {{
      position: absolute;
      inset: auto -30px -45px auto;
      width: 110px;
      height: 110px;
      border-radius: 999px;
      background: var(--card-glow, rgba(148, 163, 184, 0.08));
      content: "";
      filter: blur(2px);
    }}
    .summary-card-up {{ --card-glow: rgba(110, 231, 183, 0.12); border-color: rgba(110, 231, 183, 0.22); }}
    .summary-card-down {{ --card-glow: rgba(248, 113, 113, 0.12); border-color: rgba(248, 113, 113, 0.22); }}
    .summary-card-neutral {{ --card-glow: rgba(251, 191, 36, 0.1); border-color: rgba(251, 191, 36, 0.2); }}
    .summary-card-info {{ --card-glow: rgba(125, 211, 252, 0.12); border-color: rgba(125, 211, 252, 0.22); }}
    .summary-card strong {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 12px;
      color: #f8fafc;
      font-size: 1.18rem;
      line-height: 1.25;
    }}
    .summary-card > span {{ color: var(--muted); font-size: 0.78rem; }}
    .summary-value {{ display: block; margin-top: 12px; color: var(--text) !important; font-size: 1rem !important; font-weight: 750; }}
    .summary-detail {{ display: block; margin-top: 3px; line-height: 1.35; }}
    .summary-arrow {{
      display: inline-grid;
      place-items: center;
      width: 22px;
      height: 22px;
      border-radius: 999px;
      background: #1e293b;
      color: var(--muted);
      font-weight: 800;
      flex: 0 0 auto;
    }}
    .summary-card-up .summary-arrow {{ background: var(--good-soft); color: var(--good); }}
    .summary-card-down .summary-arrow {{ background: var(--bad-soft); color: var(--bad); }}
    .summary-card-neutral .summary-arrow {{ background: var(--amber-soft); color: var(--amber); }}
    .summary-card-info .summary-arrow {{ background: var(--accent-soft); color: var(--accent); }}
    .pill {{
      display: inline-block;
      padding: 3px 8px;
      border-radius: 999px;
      font-weight: 700;
      font-size: 0.78rem;
      white-space: nowrap;
    }}
    .pill-up {{ background: var(--good-soft); color: var(--good); }}
    .pill-down {{ background: var(--bad-soft); color: var(--bad); }}
    .pill-flat {{ background: var(--amber-soft); color: var(--amber); }}
    .chip {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 5px;
      min-height: 24px;
      padding: 3px 8px;
      border-radius: 999px;
      border: 1px solid transparent;
      font-size: 0.78rem;
      font-weight: 750;
      white-space: nowrap;
    }}
    .price-guide-table .col-signal .chip {{
      min-width: 70px;
      min-height: 28px;
      border-radius: 5px;
      font-size: 0.8rem;
      letter-spacing: 0.035em;
    }}
    .chip-buy, .chip-up, .chip-strong {{
      color: var(--good);
      background: rgba(15, 118, 110, 0.28);
      border-color: rgba(110, 231, 183, 0.28);
    }}
    .chip-sell, .chip-down {{
      color: var(--bad);
      background: rgba(63, 31, 31, 0.72);
      border-color: rgba(248, 113, 113, 0.28);
    }}
    .chip-wait, .chip-low, .chip-weak {{
      color: var(--amber);
      background: rgba(66, 49, 13, 0.72);
      border-color: rgba(251, 191, 36, 0.28);
    }}
    .chip-hold, .chip-flat, .chip-usable, .chip-medium, .chip-low-medium {{
      color: var(--accent);
      background: rgba(21, 59, 87, 0.72);
      border-color: rgba(125, 211, 252, 0.28);
    }}
    .chip-check {{
      color: #cbd5e1;
      background: rgba(71, 85, 105, 0.32);
      border-color: rgba(148, 163, 184, 0.28);
    }}
    .signed-positive {{ color: var(--good); font-weight: 700; }}
    .signed-negative {{ color: var(--bad); font-weight: 700; }}
    .signed-neutral {{ color: var(--muted); }}
    ul {{ margin: 0; padding-left: 1.2rem; }}
    li + li {{ margin-top: 6px; }}
    code {{
      display: inline-block;
      padding: 3px 6px;
      border-radius: 6px;
      background: #1e293b;
      color: #e2e8f0;
    }}
    table {{
      width: max-content;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      font-size: 1.1rem;
      line-height: 1.18;
    }}
    th, td {{
      padding: 8px 9px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      white-space: normal;
    }}
    th.number, td.number {{
      text-align: right;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }}
    th.text, td.text {{ text-align: left; }}
    td {{
      max-width: 220px;
    }}
    tr:last-child td {{ border-bottom: 0; }}
    th {{
      background: #172a45;
      color: #e2e8f0;
      font-size: 1rem;
      font-weight: 750;
      letter-spacing: 0.015em;
    }}
    tbody tr:nth-child(even) {{ background: rgba(30, 41, 59, 0.22); }}
    .table-wrap {{ width: max-content; overflow: visible; }}
    .compact-table .report-table {{
      width: max-content;
      table-layout: auto;
    }}
    .compact-table th, .compact-table td {{
      max-width: none;
      white-space: normal;
    }}
    .compact-table th.number,
    .compact-table td.number {{
      text-align: right;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }}
    .compact-table .col-commodity,
    .compact-table .col-item,
    .compact-table .col-notes,
    .compact-table .col-read,
    .compact-table .col-market-state {{
      white-space: normal;
    }}
    .compact-table .col-commodity,
    .compact-table .col-item {{
      min-width: 0;
    }}
    .compact-table .col-notes,
    .compact-table .col-read,
    .compact-table .col-market-state {{
      min-width: 0;
    }}
    .price-guide-table .report-table {{ width: max-content; table-layout: auto; }}
    .price-guide-table th, .price-guide-table td {{ padding: 7px 5px; }}
    .price-guide-table .col-item {{ min-width: 0; font-weight: 700; white-space: nowrap; }}
    .price-guide-table .col-signal {{ text-align: left; white-space: nowrap; }}
    .price-guide-table .col-ask,
    .price-guide-table .col-bid,
    .price-guide-table .col-fair,
    .price-guide-table .col-max-buy,
    .price-guide-table .col-rich-sell {{ white-space: nowrap; }}
    .price-guide-table .col-ask-upside-pct {{ white-space: nowrap; }}
    .price-guide-table .col-price-state {{ white-space: nowrap; }}
    .price-guide-table tr.signal-buy {{ background: linear-gradient(90deg, rgba(15, 118, 110, 0.22), transparent 32%); }}
    .price-guide-table tr.signal-sell {{ background: linear-gradient(90deg, rgba(127, 29, 29, 0.23), transparent 32%); }}
    .signal-help {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin: 0 0 8px;
    }}
    .signal-help span {{
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: rgba(17, 24, 38, 0.8);
      color: var(--muted);
      font-size: 0.92rem;
      line-height: 1.4;
    }}
    .signal-help strong {{ display: block; margin-bottom: 2px; font-size: 1rem; }}
    .signal-help .buy-rule strong {{ color: var(--good); }}
    .signal-help .sell-rule strong {{ color: var(--bad); }}
    .signal-help .wait-rule strong {{ color: var(--amber); }}
    .signal-warning {{ margin: 0 0 10px; color: var(--muted); font-size: 0.92rem; line-height: 1.4; }}
    .book-summary .report-table {{ width: max-content; table-layout: auto; }}
    .book-summary th, .book-summary td {{ white-space: normal; vertical-align: middle; }}
    .book-summary th, .book-summary td {{ padding: 7px 5px; }}
    .book-summary .book-item {{ font-weight: 700; }}
    .book-summary .book-pressure {{ white-space: nowrap; }}
    .activity-table .report-table {{
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .market-trends-table {{
      overflow: visible;
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .market-trends-table .report-table {{ border: 0; border-radius: 0; }}
    .activity-table th, .activity-table td,
    .market-trends-table th, .market-trends-table td {{ padding: 7px 6px; }}
    .activity-table .activity-item {{ font-weight: 700; }}
    .activity-table .activity-ratio {{ white-space: nowrap; }}
    .activity-volume-content {{ display: grid; gap: 4px; }}
    .activity-metric {{ display: grid; grid-template-columns: 54px minmax(0, 1fr) 58px; gap: 7px; align-items: center; }}
    .activity-metric-label {{ color: var(--muted); font-size: 0.82rem; text-align: left; white-space: nowrap; }}
    .activity-track {{ height: 12px; overflow: hidden; border-radius: 3px; background: #0b1220; }}
    .activity-fill {{ height: 100%; min-width: 1px; border-radius: 3px; background: linear-gradient(90deg, #0f766e, #6ee7b7); }}
    .activity-fill-pp {{ background: linear-gradient(90deg, #1d4ed8, #7dd3fc); }}
    .activity-number,
    .activity-total-line {{ display: block; text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
    .activity-total-line + .activity-total-line {{ margin-top: 2px; color: var(--muted); font-size: 0.86rem; }}
    .market-trends-table th,
    .market-trends-table td {{ padding: 6px 8px; vertical-align: middle; white-space: nowrap; }}
    .market-trends-table .col-item {{ font-weight: 700; white-space: nowrap; }}
    .trend-change {{ display: inline-flex; align-items: center; justify-content: flex-end; gap: 4px; min-width: 66px; }}
    .trend-arrow {{ width: 0.9em; text-align: center; font-size: 0.78rem; }}
    .position-cell {{ display: grid; grid-template-columns: 56px 36px minmax(72px, 1fr); align-items: center; column-gap: 8px; width: 100%; }}
    .position-track {{ position: relative; width: 56px; height: 4px; border-radius: 999px; background: #334155; }}
    .position-dot {{ position: absolute; top: 50%; width: 7px; height: 7px; border-radius: 50%; background: var(--accent); transform: translate(-50%, -50%); }}
    .position-value {{ text-align: right; font-variant-numeric: tabular-nums; }}
    .position-empty {{ color: var(--muted); text-align: center; }}
    .position-label {{ color: var(--muted); font-size: 0.82rem; }}
    .trend-path {{ display: block; width: 96px; height: 28px; overflow: visible; color: var(--muted); }}
    .trend-path-line {{ fill: none; stroke: currentColor; stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; vector-effect: non-scaling-stroke; }}
    .trend-path-latest {{ fill: var(--accent); stroke: var(--panel); stroke-width: 1; vector-effect: non-scaling-stroke; }}
    .pattern-label {{ display: inline-flex; align-items: center; gap: 6px; font-weight: 700; }}
    .pattern-mark {{ width: 7px; height: 7px; border-radius: 50%; background: var(--muted); }}
    .pattern-rise .pattern-mark, .pattern-rebound .pattern-mark {{ background: var(--good); }}
    .pattern-fall .pattern-mark, .pattern-pullback .pattern-mark {{ background: var(--bad); }}
    .pattern-flat .pattern-mark {{ background: var(--amber); }}
    .pattern-mixed .pattern-mark {{ background: var(--accent); }}
    .state-chips {{ display: inline-flex; flex-wrap: nowrap; gap: 4px; white-space: nowrap; }}
    .depth-profile {{ width: 100%; min-width: 0; }}
    .depth-profile-labels {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin-bottom: 3px;
      color: var(--muted);
      font-size: 0.82rem;
      font-variant-numeric: tabular-nums;
    }}
    .depth-profile-label {{
      display: flex;
      justify-content: space-between;
      gap: 4px;
      min-width: 0;
    }}
    .depth-profile-labels .buy-label {{ color: var(--good); }}
    .depth-profile-labels .sell-label {{ color: var(--bad); }}
    .depth-label-text {{ text-align: left; }}
    .depth-label-number {{ overflow: visible; text-align: right; white-space: normal; }}
    .depth-profile-track {{
      position: relative;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 4px;
      height: 19px;
      border-radius: 4px;
      background: #0b1220;
      overflow: hidden;
    }}
    .depth-profile-half {{ display: flex; min-width: 0; }}
    .depth-profile-fill {{ display: flex; min-width: 1px; }}
    .depth-profile-bids {{ justify-content: flex-end; }}
    .depth-profile-bids .depth-profile-fill {{ margin-left: auto; }}
    .depth-profile-asks .depth-profile-fill {{ margin-right: auto; }}
    .depth-segment {{
      flex-basis: 0;
      min-width: 1px;
      border-inline: 1px solid rgba(11, 17, 32, 0.72);
    }}
    .depth-segment-bid {{ background: var(--good-soft); }}
    .depth-segment-ask {{ background: var(--bad-soft); }}
    .depth-segment.wall-segment {{
      box-shadow: inset 0 0 0 1px rgba(251, 191, 36, 0.8);
      filter: brightness(1.25);
    }}
    .depth-profile-spread {{
      margin-top: 3px;
      color: var(--muted);
      font-size: 0.8rem;
      text-align: right;
    }}
    .book-pressure-content {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 5px;
      white-space: nowrap;
    }}
    .book-pressure-value {{ font-variant-numeric: tabular-nums; text-align: right; }}
    .book-pressure-label {{ text-align: left; }}
    .flip-item {{
      color: #f8fafc;
      font-weight: 750;
      line-height: 1.25;
    }}
    .metric-primary {{
      color: var(--text);
      font-weight: 700;
      font-variant-numeric: tabular-nums;
      line-height: 1.3;
    }}
    .metric-detail {{
      margin-top: 4px;
      color: var(--muted);
      font-size: 0.88rem;
      font-variant-numeric: tabular-nums;
      line-height: 1.35;
    }}
    .metric-label {{ color: #cbd5e1; font-weight: 650; }}
    .flip-why {{ color: #cbd5e1; font-size: 0.82rem; line-height: 1.4; }}
    .readiness-note {{
      display: grid;
      grid-template-columns: auto 1fr;
      gap: 12px;
      align-items: start;
      margin-top: 24px;
      padding: 14px 16px;
      border: 1px solid rgba(125, 211, 252, 0.2);
      border-radius: 10px;
      background: rgba(21, 59, 87, 0.25);
    }}
    .readiness-icon {{
      display: grid;
      place-items: center;
      width: 28px;
      height: 28px;
      border-radius: 8px;
      background: var(--accent-soft);
      color: var(--accent);
      font-weight: 800;
    }}
    .readiness-note strong {{ display: block; margin-bottom: 2px; }}
    .readiness-note p {{ margin: 0; color: var(--muted); font-size: 0.95rem; line-height: 1.45; }}
    .chart {{
      width: 100%;
      max-height: 720px;
      object-fit: contain;
      background: #0f172a;
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    figure.inflation-chart {{
      width: 100%;
      min-width: 0;
      margin: 16px 0 0;
      break-inside: avoid;
      page-break-inside: avoid;
    }}
    figure.inflation-chart img {{
      display: block;
      width: 100%;
      max-width: 100%;
      height: auto;
      object-fit: contain;
      border-radius: 6px;
    }}
    figure.inflation-chart figcaption {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 0.82rem;
      line-height: 1.4;
    }}
    .notes {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: 12px;
    }}
    .note {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      min-width: 0;
    }}
    .price-context-list {{ display: grid; gap: 7px; margin: 0; padding: 0; list-style: none; }}
    .price-context-list li {{ margin: 0; }}
    .price-context-classification {{ font-weight: 750; }}
    .price-range-wrap {{ margin-top: 13px; }}
    .price-range-title {{ margin-bottom: 7px; color: var(--muted); font-size: 0.82rem; font-weight: 700; }}
    .price-range-rail {{ min-width: 0; padding: 10px 4px 4px; }}
    .price-range-track {{
      position: relative;
      width: 100%;
      height: 8px;
      border-radius: 999px;
      background: #334155;
    }}
    .price-range-band {{
      position: absolute;
      top: 0;
      height: 100%;
      border-radius: 999px;
      background: var(--accent-soft);
      border: 1px solid var(--accent);
      transform: translateX(0);
    }}
    .price-range-band-collapsed {{ width: 8px !important; transform: translateX(-4px); }}
    .price-range-marker {{
      position: absolute;
      top: 50%;
      z-index: 2;
      display: block;
      transform: translate(-50%, -50%);
    }}
    .price-range-marker-fair {{
      width: 10px;
      height: 10px;
      background: var(--amber);
      border: 1px solid var(--bg);
      transform: translate(-50%, -50%) rotate(45deg);
    }}
    .price-range-marker-latest {{
      width: 0;
      height: 0;
      border-left: 6px solid transparent;
      border-right: 6px solid transparent;
      border-bottom: 11px solid var(--text);
      transform: translate(-50%, -105%);
    }}
    .price-range-values {{
      display: flex;
      flex-wrap: wrap;
      gap: 5px 12px;
      margin-top: 10px;
      color: var(--muted);
      font-size: 0.78rem;
      font-variant-numeric: tabular-nums;
    }}
    .price-range-values strong {{ color: var(--text); }}
    .range-symbol {{ display: inline-block; width: 0.9em; margin-right: 3px; text-align: center; }}
    .range-symbol-fair {{ color: var(--amber); transform: rotate(45deg); }}
    .range-symbol-latest {{ color: var(--text); }}
    .insufficient-range {{ margin: 10px 0 0; color: var(--muted); font-weight: 700; }}
    .highlight-neutral {{ min-height: 0; }}
    .highlight-side-note {{ margin: 10px 0 0; color: var(--muted); font-size: 0.86rem; }}
    .report-footer {{
      margin-top: 34px;
      padding: 22px 24px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: linear-gradient(145deg, rgba(17, 24, 38, 0.98), rgba(15, 23, 42, 0.98));
      color: var(--muted);
      font-size: 0.92rem;
    }}
    .report-footer h2 {{
      margin-bottom: 12px;
      color: var(--text);
      font-size: 1.08rem;
    }}
    .report-footer a {{ color: var(--accent); }}
    .third-party-tools {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 10px;
      margin-bottom: 20px;
    }}
    .third-party-tool {{
      padding: 12px 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(11, 17, 32, 0.48);
    }}
    .third-party-tool a {{ display: inline-block; margin-bottom: 3px; font-weight: 750; }}
    .third-party-tool p, .report-signoff p {{ margin-bottom: 0; }}
    .report-signoff {{
      padding-top: 18px;
      border-top: 1px solid var(--line);
    }}
    .report-signoff strong {{ color: var(--text); }}
    .report-signoff p + p {{ margin-top: 8px; }}
    @media (max-width: 720px) {{
      header {{ display: block; padding: 18px; }}
      .hero-meta {{ margin-top: 18px; text-align: left; }}
      h1 {{ font-size: 1.65rem; }}
      th, td {{ padding: 8px; }}
      .signal-help {{ grid-template-columns: 1fr; }}
      .price-guide-table th, .price-guide-table td {{ padding: 6px 3px; }}
      .price-guide-table .chip {{ padding: 2px 4px; font-size: 1rem; }}
      .summary-grid {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 520px) {{
      main {{ width: min(100% - 8px, 1180px); padding-top: 10px; }}
      .book-summary th, .book-summary td {{ padding-inline: 3px; }}
      .book-pressure-content {{ gap: 2px; }}
      .notes {{ grid-template-columns: minmax(0, 1fr); }}
      .price-range-values {{ gap: 4px 8px; font-size: 0.74rem; }}
    }}
    @media print {{
      @page {{ size: landscape; margin: 10mm; }}
      :root {{ color-scheme: dark; }}
      * {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
      body {{ background: var(--bg); }}
      main {{ width: 100%; padding: 0; }}
      header {{ padding: 16px 18px; border-radius: 8px; box-shadow: none; }}
      h1 {{ font-size: 1.7rem; }}
      section {{ margin-top: 18px; }}
      h2 {{ font-size: 1.08rem; }}
      thead {{ display: table-header-group; }}
      tr, .note, .summary-card {{ break-inside: avoid; page-break-inside: avoid; }}
      .report-table {{ font-size: 8.5pt; }}
      .chart {{ max-height: 165mm; }}
      figure.inflation-chart img {{ max-height: 150mm; object-fit: contain; }}
    }}
    .highlight-section .summary-grid {{ grid-template-columns: minmax(0, 1fr); }}
    .highlight-chart {{ width: 100%; max-height: none; }}
    .sr-only {{ position: static; width: auto; height: auto; overflow: visible; clip: auto; white-space: normal; }}
    .note, .summary-card {{ overflow: visible; overflow-wrap: anywhere; }}
    .trading-guide-table .report-table {{ width: max-content; table-layout: auto; }}
    .trading-guide-table td, .trading-guide-table th {{ white-space: nowrap; max-width: none; }}
    .quote-gap {{ display: block; font-size: 12px; margin-top: 4px; }}
    .quote-gap-label {{ display: block; font-size: 10px; font-weight: 400; color: var(--muted); }}
    .table-wrap {{ max-width: none; }}
    .report-table tfoot td {{ white-space: normal; text-align: left; font-size: 12px; padding: 10px; }}
    .we24-summary h2 {{ margin: 0 0 12px; font-size: 16px; color: var(--muted); }}
    .we24-stats {{ display: flex; align-items: baseline; flex-wrap: wrap; gap: 16px; margin-bottom: 8px; }}
    .we24-value {{ font-size: 48px; line-height: 1.1; font-variant-numeric: tabular-nums; }}
    .we24-delta {{ font-size: 24px; font-variant-numeric: tabular-nums; }}
    .we24-delta small {{ font-size: 12px; color: var(--muted); }}
    .we24-status {{ color: #fbbf24; font-size: 12px; }}
    .we24-chart {{ display: block; width: 100%; }}
    .summary-card::after {{ content: none; }}
    .book-item small {{ display: block; font-size: 10px; white-space: normal; overflow-wrap: anywhere; }}
  </style>
</head>
<body>
  <main>
{body}
  </main>
</body>
</html>
"""


def _compact_table_html(df: pd.DataFrame, *, table_kind: str = "trend") -> str:
    kind_class = f"{table_kind}-table"
    header = "".join(
        f'<th class="{_column_classes(column)}">{escape(str(column))}'
        + ('<small class="quote-gap-label">vs 7D reference</small>' if column == "%" else '')
        + '</th>'
        for column in df.columns
    )
    body_rows = []
    for _, row in df.iterrows():
        cells = "".join(
            f'<td class="{_column_classes(column)}">'
            f"{_render_table_cell(column, row[column])}</td>"
            for column in df.columns
        )
        row_class = ""
        if table_kind == "price-guide":
            signal = str(row.get("Signal", "")).strip().lower()
            if signal in {"buy", "sell", "wait"}:
                row_class = f' class="signal-{signal}"'
        body_rows.append(f"<tr{row_class}>{cells}</tr>")
    table = (
        '<table class="report-table">'
        f"<thead><tr>{header}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table>"
    )
    accessibility = (
        ' role="region" aria-label="Market trends table"'
        if table_kind == "market-trends"
        else ""
    )
    return f'<div class="table-wrap compact-table {kind_class}"{accessibility}>{table}</div>'


def _trend_label(value: object) -> str:
    change = _number(value)
    if change is None:
        return "—"
    if change > 0.5:
        return _chip("Rising", "up", prefix="↑")
    if change < -0.5:
        return _chip("Falling", "down", prefix="↓")
    return _chip("Flat", "flat", prefix="→")


def _ordered_report_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Use the guide's priority and name order throughout the publication."""
    if df.empty:
        return df.copy()
    return df.iloc[sorted(range(len(df)), key=lambda position: (
        _number(df.iloc[position].get("short_term_rank"))
        if _number(df.iloc[position].get("short_term_rank")) is not None else 2,
        str(df.iloc[position].get("item_name", "Unknown")).casefold(),
        str(df.iloc[position].get("item_code", "")).casefold(),
    ))].copy()


def _price_guide_html(df: pd.DataFrame, display_count: int) -> str:
    rows = []
    for _, row in df.head(display_count).iterrows():
        entry = str(row.get("guide_entry_action", "")).strip().upper()
        holder = str(row.get("guide_holder_action", "")).strip().upper()
        median = _first_number(row, "median_7d")
        ask = _first_number(row, "guide_executable_ask_vwap")
        bid = _first_number(row, "guide_executable_bid_vwap")
        vwap = _first_number(row, "vwap_7d")
        rows.append({
            "Item": row.get("item_name", "Unknown"),
            "Signal": "BUY" if entry == "BUY" else "SELL" if holder == "SELL" else "HOLD",
            "Buy": ask,
            "Sell": bid,
            "Median 7D": median,
            "7D VWAP": vwap,
            "%": calculate_price_gap_pct(row.get("last_trade_price"), row.get("stable_fair_price_7d")),
            "Price State": _present(row.get("tendency_labels_7d"),
                                    _present(row.get("tendency_7d"), "Insufficient history")),
        })
    if not rows:
        return ""
    return (
        '<section><h2>Trading Guide</h2>'
        + _compact_table_html(pd.DataFrame(rows), table_kind="trading-guide") + '</section>'
    )


def _activity_html(df: pd.DataFrame, display_count: int) -> str:
    window_key = _window_key(ACTIVITY_WINDOW)
    rows: list[dict[str, object]] = []
    for _, row in df.iterrows():
        units = _first_number(row, f"traded_quantity_{window_key}", f"volume_{window_key}")
        total_production_points = _first_number(row, "total_production_points")
        rows.append({
            "Item": row.get("item_name", "Unknown"),
            "Value": _first_number(row, f"traded_value_{window_key}"),
            "Units": units,
            "Trades": _first_number(row, f"trade_count_{window_key}", "trades_7d"),
            "Total Production Points": total_production_points,
            "PP-equivalent Volume": (
                units * total_production_points
                if units is not None and total_production_points is not None
                else None
            ),
        })
    if not rows:
        return ""
    rows.sort(key=lambda row: (-(_number(row["Value"]) or 0.0), str(row["Item"]).casefold()))
    rows = rows[:display_count]
    pp_volumes = [
        _number(row["PP-equivalent Volume"]) or 0.0
        for row in rows
        if _number(row["PP-equivalent Volume"]) is not None
    ]
    max_pp_volume = max(pp_volumes, default=0.0)
    max_turnover = max((_number(row["Value"]) or 0.0 for row in rows), default=0.0)
    body = []
    for row in rows:
        units = _number(row["Units"])
        value = _number(row["Value"])
        trades = _number(row["Trades"])
        total_production_points = _number(row["Total Production Points"])
        pp_volume = _number(row["PP-equivalent Volume"])
        pp_width = pp_volume / max_pp_volume * 100 if pp_volume is not None and max_pp_volume > 0 else 0.0
        turnover_width = value / max_turnover * 100 if value is not None and max_turnover > 0 else 0.0
        unit_label = _fmt_compact(units) if units is not None else "N/A"
        pp_label = _fmt_compact(pp_volume) if pp_volume is not None else "N/A"
        value_label = _fmt_compact(value) if value is not None else "N/A"
        trade_label = _fmt_compact(trades) if trades is not None else "N/A"
        ratio_label = f"{total_production_points:g} : 1" if total_production_points is not None else "N/A"
        ratio_title = (
            f"{total_production_points:g} total upstream Production {'Point' if total_production_points == 1 else 'Points'} (PP) per item"
            if total_production_points is not None
            else "Total upstream PP-per-item ratio unavailable"
        )
        body.append(
            '<tr>'
            f'<td class="activity-item text">{escape(str(row["Item"]))}</td>'
            '<td class="activity-volume number"><div class="activity-volume-content">'
            '<div class="activity-metric"><span class="activity-metric-label">Value</span>'
            f'<div class="activity-track" role="img" aria-label="{escape(value_label)} completed transaction value; {turnover_width:.1f}% of the highest-value market">'
            f'<div class="activity-fill" style="width: {turnover_width:.1f}%"></div></div>'
            f'<span class="activity-number">{escape(value_label)}</span></div>'
            '<div class="activity-metric"><span class="activity-metric-label">PP equiv.</span>'
            f'<div class="activity-track" role="img" aria-label="{escape(pp_label)} PP-equivalent completed volume; {pp_width:.1f}% of the busiest comparable item">'
            f'<div class="activity-fill activity-fill-pp" style="width: {pp_width:.1f}%"></div></div>'
            f'<span class="activity-number" title="{escape(_fmt(pp_volume, 0) if pp_volume is not None else "N/A")}">{escape(pp_label)}</span></div>'
            '</div></td>'
            '</tr>'
        )
    return (
        '<section><h2>Activity Comparison</h2>'
        f'<p class="muted">Completed {ACTIVITY_WINDOW} turnover in BTC; PP-equivalent volume estimates production effort, not executable capacity or exit time.</p>'
        '<div class="table-wrap compact-table activity-table"><table class="report-table" data-report-table="activity-comparison">'
        '<thead><tr><th class="activity-item text">Item</th>'
        '<th class="activity-volume number">7D completed turnover (BTC) / PP-equivalent volume</th>'
        '</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div></section>'
    )


def _pressure_label(value: object) -> str:
    pressure = _number(value)
    if pressure is None:
        return "—"
    if pressure > 5:
        label, tone = "Buy-heavy", "positive"
    elif pressure < -5:
        label, tone = "Sell-heavy", "negative"
    else:
        label, tone = "Balanced", "neutral"
    return (
        '<span class="book-pressure-content">'
        f'<span class="book-pressure-label signed-{tone}">{label}</span>'
        f'<span class="book-pressure-value signed-{tone}">{pressure:+.1f}%</span></span>'
    )


def _depth_profile_html(book: dict[str, object]) -> str:
    bids = [level for level in book.get("bids", ()) if isinstance(level, dict)] if isinstance(book.get("bids"), (list, tuple)) else []
    asks = [level for level in book.get("asks", ()) if isinstance(level, dict)] if isinstance(book.get("asks"), (list, tuple)) else []
    bid_value = _number(book.get("bid_value")) or 0.0
    ask_value = _number(book.get("ask_value")) or 0.0
    bid_quantity = _number(book.get("bid_quantity")) or 0.0
    ask_quantity = _number(book.get("ask_quantity")) or 0.0
    scale = max(bid_value, ask_value, 1.0)

    def segments(levels: list[dict[str, object]], side: str) -> str:
        ordered = list(reversed(levels)) if side == "bid" else levels
        parts = []
        for level in ordered:
            value = _number(level.get("order_value")) or 0.0
            if value <= 0:
                continue
            price = _number(level.get("price"))
            quantity = _number(level.get("quantity"))
            wall_class = " wall-segment" if level.get("is_wall") else ""
            title = (
                f'{side.title()} at {_fmt(price)}: {_fmt(quantity, 0)} units, '
                f'{_fmt(value, 0)} value'
            )
            parts.append(
                f'<span class="depth-segment depth-segment-{side}{wall_class}" '
                f'style="flex-grow: {value:.6g}" title="{escape(title)}"></span>'
            )
        return "".join(parts)

    bid_width = bid_value / scale * 100
    ask_width = ask_value / scale * 100
    spread = _number(book.get("spread_pct"))
    aria = (
        f'Buy orders: {_fmt(bid_quantity, 0)} units worth {_fmt(bid_value, 0)}. '
        f'Sell orders: {_fmt(ask_quantity, 0)} units worth {_fmt(ask_value, 0)}.'
    )
    spread_text = f'{_fmt(spread, 2)}% spread' if spread is not None else "Spread unavailable"
    return (
        f'<div class="depth-profile" role="img" aria-label="{escape(aria)}">'
        '<div class="depth-profile-labels">'
        f'<span class="depth-profile-label buy-label"><span class="depth-label-text">Buy</span>'
        f'<span class="depth-label-number">{_fmt_compact(bid_quantity)} ({_fmt_compact(bid_value)} value)</span></span>'
        f'<span class="depth-profile-label sell-label"><span class="depth-label-text">Sell</span>'
        f'<span class="depth-label-number">{_fmt_compact(ask_quantity)} ({_fmt_compact(ask_value)} value)</span></span>'
        '</div><div class="depth-profile-track">'
        '<div class="depth-profile-half depth-profile-bids">'
        f'<div class="depth-profile-fill" style="width: {bid_width:.1f}%">{segments(bids, "bid")}</div></div>'
        '<div class="depth-profile-half depth-profile-asks">'
        f'<div class="depth-profile-fill" style="width: {ask_width:.1f}%">{segments(asks, "ask")}</div></div>'
        f'</div><div class="depth-profile-spread">{escape(spread_text)}</div></div>'
    )


def _order_book_html(df: pd.DataFrame, display_count: int) -> str:
    summaries = []
    for _, row in df.head(display_count).iterrows():
        book = row.get("order_book")
        if not isinstance(book, dict):
            continue
        name = str(row.get("item_name", "Unknown"))
        bids = [level for level in book.get("bids", ()) if isinstance(level, dict)] if isinstance(book.get("bids"), (list, tuple)) else []
        asks = [level for level in book.get("asks", ()) if isinstance(level, dict)] if isinstance(book.get("asks"), (list, tuple)) else []

        def wall_price(levels: list[dict[str, object]]) -> object:
            walls = [level for level in levels if level.get("is_wall")]
            if not walls:
                return None
            wall = max(walls, key=lambda level: _number(level.get("order_value")) or 0.0)
            return wall.get("price")

        summaries.append(
            '<tr>'
            f'<td class="book-item text">{escape(name)}</td>'
            f'<td class="book-price number">{escape(_fmt(book.get("best_bid")))}</td>'
            f'<td class="book-wall number">{escape(_fmt(wall_price(bids)))}</td>'
            f'<td class="book-profile-cell text">{_depth_profile_html(book)}</td>'
            f'<td class="book-wall number">{escape(_fmt(wall_price(asks)))}</td>'
            f'<td class="book-price number">{escape(_fmt(book.get("best_ask")))}</td>'
            f'<td class="book-pressure text">{_pressure_label(book.get("pressure_pct"))}</td>'
            f'<td class="book-spread number">{escape(_fmt(book.get("spread_pct"), 2))}%</td></tr>'
        )
    if not summaries:
        return ""
    return (
        '<section><h2>Current Order Book</h2>'
        '<p class="muted">Visible bids and asks, depth, walls, spread, and market pressure.</p>'
        '<div class="table-wrap compact-table book-summary"><table class="report-table" data-report-table="current-order-book">'
        '<thead><tr><th class="book-item text">Item</th><th class="book-price number">Best Bid</th>'
        '<th class="book-wall number">Buy Wall</th><th class="book-profile-cell text">Buy orders vs sell orders</th>'
        '<th class="book-wall number">Sell Wall</th><th class="book-price number">Best Ask</th>'
        '<th class="book-pressure text">Pressure</th><th class="book-spread number">Spread</th></tr></thead>'
        f'<tbody>{"".join(summaries)}</tbody></table></div></section>'
    )


def _profit_signal_chip(value: object) -> str:
    label = str(value)
    tone, icon = {
        "BUY": ("buy", "+"),
        "SELL": ("sell", "&#8722;"),
        "WAIT": ("wait", "&#9679;"),
        "HOLD": ("flat", "&#9679;"),
    }.get(label, ("check", "&#8212;"))
    return _chip(label, tone, prefix=icon)


def _forecast_reason_text(value: object) -> str:
    if value is None or pd.isna(value):
        return "No dominant driver"
    codes = [part.strip() for part in str(value).split(",") if part.strip()]
    return ", ".join(_FORECAST_REASON_LABELS.get(code, code.replace("_", " ").title()) for code in codes) or "No dominant driver"


def _signal_rank(row: pd.Series) -> tuple[int, float, float]:
    evidence_rank = {"Supported": 3, "Limited": 2, "Weak": 1, "Insufficient": 0}
    evidence = evidence_rank.get(str(row.get("forecast_evidence")), -1)
    accuracy = _number(row.get("forecast_accuracy_pct"))
    baseline = _number(row.get("forecast_baseline_accuracy_pct"))
    edge = accuracy - baseline if accuracy is not None and baseline is not None else float("-inf")
    samples = _number(row.get("forecast_evaluable_samples")) or 0.0
    return evidence, edge, samples


def _highlight_pairs_html(highlights: list[dict[str, object]], output_dir: Path) -> str:
    role_labels = {
        "largest_premium": "Rich above normal range",
        "largest_discount": "Cheap below normal range",
        "second_largest_premium": "Second-richest above normal range",
        "second_largest_discount": "Second-cheapest below normal range",
    }
    columns = []
    for entry in highlights:
        item = entry.get("item")
        chart_path = entry.get("chart_path")
        if item is None:
            continue
        chart_src = _relative_chart_path(chart_path, output_dir) if chart_path is not None else None
        role = str(getattr(item, "role"))
        title = role_labels.get(role, role.replace("_", " ").title())
        tone = "down" if "discount" in role else "up"
        name = str(getattr(item, "item_name"))
        gap = _number(getattr(item, "raw_gap_pct", getattr(item, "gap_pct", None)))
        latest = _number(getattr(item, "latest_completed_price", None))
        item_code = str(getattr(item, "item_code"))
        gap = round(gap, 2) if gap is not None else None
        gap_text = f"{gap:+.2f}%" if gap else "0.00%" if gap == 0 else "\u2014"
        symbol = "&#9650;" if gap is not None and gap > 0 else "&#9660;" if gap is not None and gap < 0 else ""
        signal_tone = "positive" if gap is not None and gap > 0 else "negative" if gap is not None and gap < 0 else "neutral"
        card = (
            f'<article class="summary-card summary-card-{tone}" data-item-code="{escape(item_code, quote=True)}" '
            f'data-highlight-role="{escape(role, quote=True)}">'
            f'<strong>{escape(name)}</strong>'
            f'<span class="summary-value">{_fmt(latest)} BTC &nbsp; <span class="signed-{signal_tone}">{symbol} {escape(gap_text)}</span> <small>vs 7D</small></span>'
            '</article>'
        )
        chart = ""
        if chart_src:
            alt = f"{name}: {_fmt(latest)} BTC, {gap_text} vs 7D; {title.lower()}"
            chart = (f'<img class="highlight-chart" src="{escape(chart_src, quote=True)}" '
                     f'alt="{escape(alt, quote=True)}">')
        columns.append(
            f'<div class="highlight-column" data-item-code="{escape(item_code, quote=True)}" data-highlight-role="{escape(role, quote=True)}">{chart if chart_src else card}</div>'
        )
    if not columns:
        return (
            '<section class="highlight-section"><div class="eyebrow">7D historical price context</div>'
            '<h2>Historical watch items</h2><span class="sr-only">Chart-linked ranking is limited to items '
            'with sufficient completed-transaction history for a price-action chart.</span>'
            '<article class="summary-card summary-card-neutral highlight-neutral">'
            '<strong>No meaningful price dislocations</strong>'
            '<span class="summary-detail">Eligible latest trades are within their normal 7D ranges or less than one tick from fair value.</span>'
            '</article></section>'
        )
    return (
        '<section class="highlight-section"><h2>Historical watch items</h2>'
        '<div class="summary-grid">' + "".join(columns) + '</div></section>'
    )


def _classification_label(value: object) -> str:
    return {
        MEANINGFUL_PREMIUM: "Rich above normal range",
        MEANINGFUL_DISCOUNT: "Cheap below normal range",
        WITHIN_NORMAL_RANGE: "Within normal range",
        INSUFFICIENT_RANGE_EVIDENCE: "Insufficient 7D range evidence",
    }.get(str(value), "Insufficient 7D range evidence")


def _price_range_rail_html(row: pd.Series) -> str:
    classification = str(row.get("dislocation_classification") or INSUFFICIENT_RANGE_EVIDENCE)
    lower = _number(row.get("price_p10_7d"))
    upper = _number(row.get("price_p90_7d"))
    fair = _number(row.get("stable_fair_price_7d"))
    latest = _number(row.get("last_trade_price"))
    start = _number(row.get("dislocation_normal_band_start_position"))
    end = _number(row.get("dislocation_normal_band_end_position"))
    fair_position = _number(row.get("dislocation_fair_position"))
    latest_position = _number(row.get("dislocation_latest_position"))
    values = (lower, upper, fair, latest, start, end, fair_position, latest_position)
    if classification == INSUFFICIENT_RANGE_EVIDENCE or any(value is None for value in values):
        return '<p class="insufficient-range">Insufficient 7D range evidence</p>'
    assert lower is not None and upper is not None and fair is not None and latest is not None
    assert start is not None and end is not None and fair_position is not None and latest_position is not None
    name = str(row.get("item_name") or "item")
    aria = (
        f"7D price range for {name}. P10 {_fmt(lower)}, P90 {_fmt(upper)}, "
        f"7D VWAP {_fmt(fair)}, latest completed trade {_fmt(latest)}."
    )
    collapsed_class = " price-range-band-collapsed" if start == end else ""
    return (
        '<div class="price-range-wrap"><div class="price-range-title">7D price range</div>'
        f'<div class="price-range-rail" role="img" aria-label="{escape(aria, quote=True)}">'
        '<div class="price-range-track">'
        f'<span class="price-range-band{collapsed_class}" style="left:{start:.3f}%;width:{end - start:.3f}%" aria-hidden="true"></span>'
        f'<span class="price-range-marker price-range-marker-fair" style="left:{fair_position:.3f}%" aria-hidden="true"></span>'
        f'<span class="price-range-marker price-range-marker-latest" style="left:{latest_position:.3f}%" aria-hidden="true"></span>'
        '</div><div class="price-range-values">'
        f'<span><span class="range-symbol range-symbol-fair" aria-hidden="true">◆</span>VWAP <strong>{_fmt(fair)}</strong></span>'
        f'<span><span class="range-symbol range-symbol-latest" aria-hidden="true">▲</span>Latest <strong>{_fmt(latest)}</strong></span>'
        f'<span>Normal <strong>{_fmt(lower)}-{_fmt(upper)}</strong></span>'
        '</div></div></div>'
    )


def _flip_verdict_chip(verdict: object) -> str:
    label = str(verdict)
    tone, icon = {
        "Potential flip": ("strong", "↗"),
        "Watch": ("wait", "◷"),
        "No trade": ("sell", "×"),
        "Unavailable": ("weak", "!"),
    }.get(label, ("check", "•"))
    return _chip(label, tone, prefix=icon)


def _evidence_chip(evidence: object) -> str:
    label = str(evidence)
    normalized = label.strip().lower()
    tone = {
        "supported": "strong",
        "strong": "strong",
        "usable": "usable",
        "medium": "medium",
        "limited": "low",
        "low": "low",
        "weak": "weak",
        "insufficient": "weak",
    }.get(normalized, "check")
    return _chip(label, tone)


def _flip_board_html(
    rows: list[dict[str, object]],
    *,
    show_entry: bool,
    show_forecast: bool,
    show_net: bool,
    suppress_execution_reason: bool,
) -> str:
    columns = ["Item", "Verdict"]
    if show_entry:
        columns.append("Entry")
    if show_forecast:
        columns.append("Forecast Exit")
    if show_net:
        columns.append("Expected Net")
    columns.extend(["Evidence", "Why"])

    header = "".join(
        f'<th class="{_flip_column_classes(column)}" scope="col">{escape(column)}</th>'
        for column in columns
    )
    body_rows: list[str] = []
    for row in rows:
        item = f'<strong class="flip-item">{escape(str(row["Item"]))}</strong>'
        verdict = _flip_verdict_chip(row["Verdict"])

        entry_average = _number(row.get("_entry_average"))
        entry_cost = _number(row.get("_entry_cost"))
        break_even = _number(row.get("Break-even Exit VWAP"))
        quantity = _number(row.get("Qty"))
        entry = '<span class="metric-primary">—</span>'
        if entry_average is not None:
            qty_text = f'{quantity:g} @ ' if quantity is not None else ""
            entry = f'<div class="metric-primary">{escape(qty_text + _fmt(entry_average))}</div>'
        details = []
        if entry_cost is not None:
            details.append(f'<span class="metric-label">Cost</span> {escape(_fmt(entry_cost))}')
        if break_even is not None:
            details.append(f'<span class="metric-label">BE</span> {escape(_fmt(break_even))}')
        if details:
            entry += f'<div class="metric-detail">{" · ".join(details)}</div>'

        exit_p10 = _number(row.get("_exit_p10"))
        exit_median = _number(row.get("_exit_median"))
        exit_p90 = _number(row.get("_exit_p90"))
        forecast = '<span class="metric-primary">—</span>'
        if exit_median is not None:
            forecast = (
                f'<div class="metric-primary"><span class="metric-label">Median</span> '
                f'{escape(_fmt(exit_median))}</div>'
            )
        forecast_details = []
        if exit_p10 is not None:
            forecast_details.append(f'<span class="metric-label">P10</span> {escape(_fmt(exit_p10))}')
        if exit_p90 is not None:
            forecast_details.append(f'<span class="metric-label">P90</span> {escape(_fmt(exit_p90))}')
        if forecast_details:
            forecast += f'<div class="metric-detail">{" · ".join(forecast_details)}</div>'

        margin = _number(row.get("_margin"))
        profit = _number(row.get("_profit"))
        net = '<span class="metric-primary">—</span>'
        if margin is not None:
            tone = "positive" if margin > 0 else "negative" if margin < 0 else "neutral"
            net = f'<div class="metric-primary signed-{tone}">{margin:+.2f}%</div>'
        if profit is not None:
            tone = "positive" if profit > 0 else "negative" if profit < 0 else "neutral"
            net += (
                f'<div class="metric-detail"><span class="metric-label">Profit</span> '
                f'<span class="signed-{tone}">{escape(_fmt(profit))}</span></div>'
            )

        execution_samples = _number(row.get("_execution_samples"))
        forecast_samples = int(_number(row.get("_samples")) or 0)
        execution_text = "—" if execution_samples is None else str(int(execution_samples))
        evidence = _evidence_chip(row["Evidence"])
        evidence += (
            f'<div class="metric-detail">{execution_text} executable · '
            f'{forecast_samples} forecasts</div>'
        )
        quote_age = _number(row.get("Quote Age"))
        if quote_age is not None:
            evidence += f'<div class="metric-detail">Quote age {_fmt(quote_age, 1)}m</div>'

        reason_labels = list(row.get("_reason_labels", []))
        if suppress_execution_reason:
            reason_labels = [
                label for code, label in zip(row.get("_codes", []), reason_labels)
                if code != "missing_execution_interval"
            ]
        why_parts = [*reason_labels, *list(row.get("_why_addenda", []))]
        why = escape(". ".join(part for part in why_parts if part)) if why_parts else "—"
        cells = {
            "Item": item,
            "Verdict": verdict,
            "Entry": entry,
            "Forecast Exit": forecast,
            "Expected Net": net,
            "Evidence": evidence,
            "Why": f'<span class="flip-why">{why}</span>',
        }
        body_rows.append("<tr>" + "".join(
            f'<td class="{_flip_column_classes(column)}" data-label="{escape(column)}">{cells[column]}</td>'
            for column in columns
        ) + "</tr>")

    return (
        '<div class="flip-board">'
        '<table class="report-table">'
        f'<thead><tr>{header}</tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody>'
        '</table></div>'
    )


def _column_css_class(column: str) -> str:
    label = str(column).replace("%", " pct ")
    slug = "".join(character.lower() if character.isalnum() else "-" for character in label)
    slug = "-".join(part for part in slug.split("-") if part)
    return f"col-{slug or 'value'}"


def _flip_column_classes(column: str) -> str:
    kind = "number" if column in {"Entry", "Forecast Exit", "Expected Net"} else "text"
    return f"{_column_css_class(column)} {kind}"


def _column_classes(column: str) -> str:
    classes = [_column_css_class(column)]
    if _is_number_column(column):
        classes.append("number")
    else:
        classes.append("text")
    return " ".join(classes)


def _is_number_column(column: str) -> bool:
    label = str(column).lower()
    return (
        label in {
            "now", "latest", "min", "max", "fair", "buy", "sell", "buy ≤", "sell ≥",
            "max entry", "rich ≥", "max buy", "rich sell",
            "volume", "liquidity", "spread %", "units", "trades", "rank",
            "range", "activity",
        }
        or label.endswith("trades")
        or label.endswith("momentum %")
        or label.endswith("low")
        or label.endswith("high")
        or label.endswith("traded value")
        or label.endswith("change")
        or label.endswith("position")
        or label.startswith("fair ")
        or label.startswith("median ")
        or label.endswith("vwap")
        or "%" in label
        or "ask" in label
        or "bid" in label
        or label in {"last", "last trade", "samples"}
    )


def _render_table_cell(column: str, value: object) -> str:
    if column == "%":
        gap = _number(value)
        tone, label = "neutral", "&mdash;"
        if gap is not None:
            gap = round(gap, 2)
            tone = "positive" if gap > 0 else "negative" if gap < 0 else "neutral"
            label = f"{gap:+.2f}%" if gap else "0.00%"
        return f'<span class="signed-{tone}">{label}</span>'
    if column == "7D Trend":
        return _trend_label(value)
    if column in {"1D Change", "7D Change", "30D Change"}:
        return _trend_change(value)
    if column == "30D Position":
        return _trend_position(value)
    if column == "90D Path":
        return _trend_path(value)
    if column == "Pattern":
        return _trend_pattern(value)
    if column in {"Signal", "Holder (owned only)"}:
        return _profit_signal_chip(value)
    if column == "Evidence":
        return _evidence_chip(value)
    if column == "Trust":
        return _chip(str(value), _trust_tone(value))
    if column == "Market":
        return _chip(str(value), _market_tone(value))
    if column in {"Market State", "Price State"}:
        return _market_state_chips(value)
    if column == "Volume" or column == "Units" or column.endswith("Traded Value"):
        number = _number(value)
        if number is None:
            return escape(_fmt_report_value(value, column=column))
        return f'<span title="{escape(_fmt(number, 0))}">{escape(_fmt_compact(number))}</span>'
    if column in {"Gap %", "Change %", "Net to Fair %", "Ask Upside %"} or column.endswith("Change %"):
        return _signed_number(value, invert=column == "Gap %")
    return escape(_fmt_report_value(value, column=column))


def _chip(label: str, tone: str, *, prefix: str | None = None) -> str:
    safe_label = escape(label)
    safe_prefix = f"<span>{prefix}</span>" if prefix else ""
    return f'<span class="chip chip-{tone}">{safe_prefix}{safe_label}</span>'


def _trust_tone(value: object) -> str:
    return str(value).strip().lower().replace(" ", "-")


def _market_tone(value: object) -> str:
    return str(value).strip().lower()


def _market_state_chips(value: object) -> str:
    if value is None or pd.isna(value):
        return escape(_fmt(value))
    labels = [part.strip() for part in str(value).split(",") if part.strip()]
    if not labels:
        return "N/A"
    symbols = {"rising": "&#8593;", "falling": "&#8595;", "stable": "&#8594;",
               "flat": "&#8594;", "volatile": "&#8597;", "thin": "!"}
    chips = "".join(_chip(label, _market_state_tone(label), prefix=symbols.get(label.lower())) for label in labels)
    return f'<span class="state-chips">{chips}</span>'


def _market_state_tone(label: str) -> str:
    return {"rising": "up", "falling": "down", "volatile": "wait",
            "thin": "weak"}.get(label.lower(), "flat")


def _signed_number(value: object, *, invert: bool = False) -> str:
    number = _number(value)
    if number is None:
        return escape(_fmt_report_value(value))
    if abs(number) < 0.005:
        css_class = "signed-neutral"
    elif (number > 0 and not invert) or (number < 0 and invert):
        css_class = "signed-positive"
    else:
        css_class = "signed-negative"
    return f'<span class="{css_class}">{escape(_fmt(number))}</span>'


def _trend_change(value: object) -> str:
    number = _number(value)
    if number is None:
        return "N/A"
    if number > 0.5:
        css_class, arrow = "signed-positive", "▲"
    elif number < -0.5:
        css_class, arrow = "signed-negative", "▼"
    else:
        css_class, arrow = "signed-neutral", "●"
    return (
        f'<span class="trend-change {css_class}">'
        f'<span class="trend-arrow" aria-hidden="true">{arrow}</span>{number:+.2f}%</span>'
    )


def _trend_position(value: object) -> str:
    if value == "Flat":
        return _empty_trend_position("Flat")
    number = _number(value)
    if number is None:
        return _empty_trend_position("N/A")
    label = "Near floor" if number <= 33 else "Middle" if number <= 66 else "Near ceiling"
    return (
        f'<span class="position-cell" title="{number:.1f}% through the 30D completed-trade range">'
        '<span class="position-track" aria-hidden="true">'
        f'<span class="position-dot" style="left:{number:.1f}%"></span></span>'
        f'<span class="position-value">{number:.0f}%</span>'
        f'<span class="position-label">{label}</span></span>'
    )


def _empty_trend_position(label: str) -> str:
    return (
        f'<span class="position-cell" aria-label="30D position: {escape(label)}">'
        '<span class="position-empty" aria-hidden="true">—</span>'
        '<span class="position-value position-empty" aria-hidden="true">—</span>'
        f'<span class="position-label">{escape(label)}</span></span>'
    )


def _trend_path(value: object) -> str:
    if not isinstance(value, dict):
        return "N/A"
    points = value.get("points")
    if not isinstance(points, list) or len(points) < 2:
        return "N/A"
    window_start = _number(value.get("window_start"))
    window_end = _number(value.get("window_end"))
    direction = str(value.get("direction") or "flat").lower()
    if window_start is None or window_end is None:
        return "N/A"
    usable = sorted(
        (int(point["timestamp"]), float(point["price"]))
        for point in points
        if (
            isinstance(point, dict)
            and _number(point.get("timestamp")) is not None
            and _number(point.get("price")) is not None
            and float(point["price"]) > 0
        )
    )
    if len(usable) < 2:
        return "N/A"
    timestamps = [timestamp for timestamp, _ in usable]
    prices = [price for _, price in usable]
    low = min(prices)
    high = max(prices)
    latest = prices[-1]
    coverage_days = max(1, round((timestamps[-1] - timestamps[0]) / 86_400))
    window_days = max(1, round((window_end - window_start) / 86_400))
    reported_count = _number(value.get("observation_count"))
    observation_count = int(reported_count) if reported_count is not None else len(timestamps)
    label = (
        f"90D rolling price path: {observation_count} daily observations spanning "
        f"{coverage_days} of {window_days} days; "
        f"low {_fmt(low)}, high {_fmt(high)}, latest {_fmt(latest)}; overall {direction}."
    )
    return render_trend_path_svg(
        points,
        aria_label=label,
        window_start=int(window_start),
        window_end=int(window_end),
    ) or "N/A"


def _trend_pattern(value: object) -> str:
    label, _, description = str(value).partition("|")
    tone = {
        "Persistent rise": "rise",
        "Persistent fall": "fall",
        "Rebound": "rebound",
        "Pullback": "pullback",
        "Flat": "flat",
        "Mixed": "mixed",
    }.get(label, "insufficient")
    title = escape(description or label, quote=True)
    accessible_name = escape(
        f"{label} — {description}" if description else label,
        quote=True,
    )
    return (
        f'<span class="pattern-label pattern-{tone}" title="{title}" aria-label="{accessible_name}">'
        f'<span class="pattern-mark" aria-hidden="true"></span>{escape(label)}</span>'
    )


def _relative_chart_path(chart_path: str | Path | None, output_dir: Path) -> str | None:
    if chart_path is None:
        return None
    path = Path(chart_path)
    try:
        return path.relative_to(output_dir).as_posix()
    except ValueError:
        return path.as_posix()


def inflation_summary_html(
    results: Sequence["InflationIndexResult"],
    *,
    chart_paths: Mapping[str, str | Path] | None = None,
    output_dir: str | Path = ".",
) -> str:
    """Render one decision-oriented BTC purchasing-power summary and chart."""
    enabled = [result for result in results if result.definition.enabled]
    if not enabled:
        return ""
    charts = chart_paths or {}
    broad = next(
        (result for result in enabled if result.definition.key == "broad_market"), enabled[0]
    )
    primary = next(
        (change for change in broad.changes if change.period_label.upper() == "30D"), None
    )
    change = primary.change_pct if primary is not None else None
    purchasing_power = primary.purchasing_power_change_pct if primary is not None else None
    horizon = primary.period_label if primary is not None else "current"
    classification = primary.classification.lower() if primary is not None else "insufficient data"
    if change is None:
        tone, arrow = "info", "?"
        regime = "Insufficient history"
        message = "BTC purchasing-power direction is not available yet"
    elif classification == "inflation":
        tone, arrow = "down", "&#9650;"
        regime = "Inflationary"
        message = "BTC is losing purchasing power"
    elif classification == "deflation":
        tone, arrow = "up", "&#9660;"
        regime = "Deflationary"
        message = "BTC is gaining purchasing power"
    else:
        tone, arrow = "neutral", "&#9679;"
        regime = "Broadly stable"
        message = "BTC purchasing power is broadly stable"
    dated = [observation.as_of for observation in broad.observations if observation.level is not None]
    span_days = (max(dated) - min(dated)).days if len(dated) >= 2 else 0
    if span_days >= 90:
        evidence = "Strong historical context"
    elif span_days >= 60:
        evidence = "Established history"
    elif span_days >= 30:
        evidence = "Moderate history"
    else:
        evidence = "Early signal"
    annualized = primary.annualized_change_pct if primary is not None else None
    annualized_line = (
        f'<span class="summary-detail">Annualized pace {_format_signed_pct(annualized)} '
        '(if the latest monthly pace continued)</span>'
        if annualized is not None else ""
    )
    card = (
        f'<article class="summary-card summary-card-{tone}">'
        f'<span>{escape(horizon)} market-price signal &middot; {escape(evidence)} ({span_days} days)</span>'
        f'<strong><span class="summary-arrow" aria-hidden="true">{arrow}</span>{escape(message)}</strong>'
        f'<span class="summary-value">{escape(regime)} &middot; prices {_format_signed_pct(change)}</span>'
        f'<span class="summary-detail">BTC purchasing power {_format_signed_pct(purchasing_power)}</span>'
        f'{annualized_line}</article>'
    )
    chart_path = _relative_chart_path(charts.get("overview"), Path(output_dir))
    chart = (
        '<figure class="panel inflation-chart">'
        f'<img src="{escape(chart_path, quote=True)}" '
        'alt="Broad Market Inflation: rolling 30D inflation values over the last 90 days; positive means BTC buys less">'
        '<figcaption>Rolling 30D broad-market inflation values across the last 90 calendar days; '
        'positive means BTC buys less. Dates without an authentic 30-day comparison are omitted.</figcaption>'
        '</figure>'
        if chart_path else ""
    )
    return (
        '<div class="inflation-section"><h2>Historical Inflation</h2>'
        '<p class="muted">The headline shows whether market prices are rising or falling in BTC. '
        'It is decision context, not a deterministic buy or sell signal.</p>'
        f'<div class="summary-grid inflation-summary-grid">{card}</div>{chart}</div>'
    )


def _format_signed_pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value:+.2f}%"


def inflation_export_frame(results: Sequence["InflationIndexResult"]) -> pd.DataFrame:
    """Return normalized index-observation/comparison rows without JSON fields."""
    rows: list[dict[str, object]] = []
    for result in results:
        if not result.definition.enabled:
            continue
        changes_by_end: dict[datetime, list[object]] = {}
        for change in result.changes:
            changes_by_end.setdefault(change.end_at, []).append(change)
        if not result.observations:
            for label in ("7D", "30D", "90D"):
                rows.append(_inflation_export_row(result, None, None, label=label))
            continue
        for observation in result.observations:
            changes = changes_by_end.get(observation.as_of) or [None]
            for change in changes:
                rows.append(_inflation_export_row(result, observation, change))
    return pd.DataFrame(rows, columns=[
        "index_key", "index_name", "index_version", "base_period_start", "base_period_end",
        "as_of", "is_provisional", "level", "coverage_pct", "eligible_component_count",
        "priced_component_count", "period_label", "comparison_start_at", "change_pct",
        "classification", "matched_coverage_pct", "purchasing_power_change_pct", "availability_status",
    ])


def _inflation_export_row(result: "InflationIndexResult", observation: object | None,
                          change: object | None, *, label: str | None = None) -> dict[str, object]:
    level = getattr(observation, "level", None)
    change_pct = getattr(change, "change_pct", None)
    if observation is None:
        status = "definition_unavailable"
    elif level is None:
        status = "level_unavailable"
    elif change is None or change_pct is None:
        status = "comparison_unavailable"
    else:
        status = "available"
    return {
        "index_key": result.definition.key,
        "index_name": result.definition.name,
        "index_version": result.definition.version,
        "base_period_start": result.definition.base_period_start.isoformat(),
        "base_period_end": result.definition.base_period_end.isoformat(),
        "as_of": getattr(observation, "as_of", None).isoformat() if observation else None,
        "is_provisional": getattr(observation, "is_provisional", None),
        "level": level,
        "coverage_pct": getattr(observation, "coverage_pct", None),
        "eligible_component_count": getattr(observation, "eligible_component_count", None),
        "priced_component_count": getattr(observation, "priced_component_count", None),
        "period_label": getattr(change, "period_label", None) or label,
        "comparison_start_at": getattr(change, "start_at", None).isoformat() if change else None,
        "change_pct": change_pct,
        "classification": getattr(change, "classification", None) or "Insufficient data",
        "matched_coverage_pct": getattr(change, "matched_coverage_pct", None),
        "purchasing_power_change_pct": getattr(change, "purchasing_power_change_pct", None),
        "availability_status": status,
    }


def write_inflation_csv(
    results: Sequence["InflationIndexResult"], output_dir: str | Path
) -> Path:
    """Write the dedicated normalized inflation export."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "market_inflation.csv"
    inflation_export_frame(results).to_csv(path, index=False)
    return path


def action_cost_export_frame(results: Sequence["ActionCostResult"]) -> pd.DataFrame:
    """Return one normalized CSV row per required action-cost component."""
    rows: list[dict[str, object]] = []
    for result in results:
        definition = result.definition
        components = {component.item_code: component for component in result.components}
        missing = set(result.missing_item_codes)
        for item_code, quantity in definition.quantities:
            component = components.get(item_code)
            rows.append({
                "action_key": definition.key,
                "action_name": definition.name,
                "category": definition.category,
                "action_description": definition.action_description,
                "unit_description": definition.unit_description,
                "total_cost": result.total_cost,
                "coverage_pct": result.coverage_pct,
                "required_component_count": result.required_component_count,
                "priced_component_count": result.priced_component_count,
                "availability_status": "available" if result.total_cost is not None else "unavailable",
                "item_code": item_code,
                "quantity": quantity,
                "representative_price": getattr(component, "representative_price", None),
                "component_cost": getattr(component, "cost", None),
                "component_status": "missing" if item_code in missing or component is None else "available",
                "source_url": definition.source_url,
                "source_published_at": definition.source_published_at,
                "provenance": definition.provenance,
            })
    return pd.DataFrame(rows, columns=[
        "action_key", "action_name", "category", "action_description", "unit_description",
        "total_cost", "coverage_pct", "required_component_count", "priced_component_count",
        "availability_status", "item_code", "quantity", "representative_price",
        "component_cost", "component_status", "source_url", "source_published_at", "provenance",
    ])


def write_action_costs_csv(
    results: Sequence["ActionCostResult"], output_dir: str | Path
) -> Path:
    """Write the normalized current action-cost export."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "market_action_costs.csv"
    action_cost_export_frame(results).to_csv(path, index=False, float_format="%.15g")
    return path


def _we24_html(index: dict | None, chart_path: str | Path | None, output_dir: Path) -> str:
    index = index or {}
    chart = _relative_chart_path(chart_path, output_dir)
    level = _number(index.get('latest_level'))
    change = _number(index.get('change_7d_pct'))
    value = f"{level:,.2f}" if level is not None else "&mdash;"
    tone, symbol = "neutral", ""
    delta = "&mdash;"
    if change is not None and level is not None:
        change = round(change, 2)
        tone = "positive" if change > 0 else "negative" if change < 0 else "neutral"
        symbol = "&#9650; " if change > 0 else "&#9660; " if change < 0 else ""
        delta = f"{change:+.2f}%" if change else "0.00%"
    status = index.get('coverage_status', 'unavailable')
    warning = "Unavailable" if level is None else "Partial" if status != 'complete' else ""
    note = index.get('reason') if level is None else index.get('membership_note')
    detail = f'<p>{escape(str(note))}</p>' if note else ''
    badge = f'<span class="we24-status">&#9888; {warning}</span>' if warning else ""
    return ('<section class="we24-section" aria-label="WE24 Market Index">'
            '<div data-report-asset="we24-summary" class="panel we24-summary">'
            '<h2>WE24 Market Index</h2><div class="we24-stats">'
            f'<strong class="we24-value">{value}</strong>'
            f'<span class="we24-delta signed-{tone}" aria-label="7-day change">{symbol}{delta} <small>7D</small></span>'
            f'{badge}</div>{detail}</div>'
            + (f'<img class="we24-chart" src="{escape(chart)}" alt="WE24 daily index history">' if chart else '')
            + '</section>')


def generate_html_report(
    df: pd.DataFrame,
    *,
    top: int = 0,
    metric_window: str = "7D",
    chart_path: str | Path | None = None,
    chart_label: str | None = None,
    highlights: list[dict[str, object]] | None = None,
    output_dir: str | Path = ".",
    assumptions: FlipAssumptions | None = None,
    data_synced_at: str | None = None,
    data_sync_status: str | None = None,
    we24: dict | None = None,
    we24_chart_path: str | Path | None = None,
    participant_report: dict | None = None,
    as_of: datetime | None = None,
    inflation_results: Sequence["InflationIndexResult"] | None = None,
    inflation_chart_paths: Mapping[str, str | Path] | None = None,
) -> str:
    assumptions = assumptions or FlipAssumptions()
    generated = (as_of or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    sync_timestamp = _display_report_timestamp(data_synced_at)
    sync_status = (
        " (partial)"
        if data_sync_status == "partial"
        else " (inferred)"
        if data_sync_status == "inferred"
        else ""
    )
    data_freshness = (
        f"Market data synced {escape(sync_timestamp + sync_status)}"
        if sync_timestamp is not None
        else "Market data sync time unavailable"
    )
    df = _ordered_report_rows(df)
    display_count = len(df)  # All-item coverage is mandatory in the published report.
    blocks: list[str] = []
    header_html = f"""    <header>
      <div class="hero-copy">
        <div class="eyebrow">WarEra Market Guide</div>
        <h1>Market intelligence, without the noise.</h1>
        <p class="muted">Completed trades provide price history; current visible orders provide executable prices and market depth.</p>
      </div>
      <div class="hero-meta"><strong>Short-term trader report | UTC</strong><span>{data_freshness}</span><span>Report generated {escape(generated)}</span></div>
</header>
"""
    highlight_html = _highlight_pairs_html(highlights or [], Path(output_dir))
    blocks.append(
        '<div class="report-header-capture" data-report-asset="header">'
        + header_html
        + '</div>'
    )
    blocks.append(_we24_html(we24, we24_chart_path, Path(output_dir)))
    blocks.append(highlight_html)
    blocks.append(_price_guide_html(df, display_count))

    blocks.append(_order_book_html(df, display_count))
    blocks.append(_activity_html(df, display_count))
    blocks.append(_participant_html(participant_report))

    note_rows = df.head(display_count).copy()
    if not note_rows.empty:
        note_rows = note_rows.sort_values("item_name", key=lambda names: names.astype(str).str.casefold(), kind="stable")
    if not note_rows.empty:
        notes: list[str] = []
        for _, row in note_rows.iterrows():
            item_code = str(row.get("item_code") or row.get("item_name") or "item")
            fair = _strict_fair_price(row, "7D")
            latest = _first_number(row, "last_trade_price")
            lower = _first_number(row, "price_p10_7d")
            upper = _first_number(row, "price_p90_7d")
            width_pct = _first_number(row, "dislocation_band_width_pct")
            classification = _classification_label(row.get("dislocation_classification"))
            detail_items = [
                f"<li>7D VWAP: <strong>{escape(_fmt(fair))}</strong></li>",
                f"<li>Latest completed trade: <strong>{escape(_fmt(latest))}</strong></li>",
                f"<li>7D normal range:<br><strong>{escape(_fmt(lower))}-{escape(_fmt(upper))} ({escape(_fmt(width_pct, 2))}%)</strong></li>",
                f'<li>Classification: <strong class="price-context-classification">{escape(classification)}</strong></li>',
                f"<li>Current execution context — Bid: <strong>{escape(_fmt(row.get('bid')))}</strong>; Ask: <strong>{escape(_fmt(row.get('ask')))}</strong></li>",
            ]
            notes.append(
                f"""<article class="note" data-report-asset="item-price-context-card" data-item-code="{escape(item_code, quote=True)}">
          <h3>{escape(str(row['item_name']))}</h3>
          <ul class="price-context-list">{"".join(detail_items)}</ul>
          {_price_range_rail_html(row)}
        </article>"""
            )
        blocks.append(
            """    <section>
      <h2>Item Price Context</h2>
      <p class="muted">Strict 7D completed-trade valuation position, with current bid and ask shown separately as execution context.</p>
      <div class="notes">""" + "\n".join(notes) + """</div>
    </section>"""
        )

    chart_src = _relative_chart_path(chart_path, Path(output_dir)) if highlights is None else None
    if chart_src:
        chart_heading = f"Featured Price History: {chart_label}" if chart_label else "Featured Price History"
        blocks.append(f'<section><h2>{escape(chart_heading)}</h2><img class="chart" src="{escape(chart_src)}" alt="Featured trailing 90D market price-action chart"></section>')

    blocks.append(
        """    <footer class="report-footer">
      <h2>Alternative third-party tools</h2>
      <div class="third-party-tools">
        <article class="third-party-tool">
          <a href="https://workerprofit.theorist.ninja/" target="_blank" rel="noopener noreferrer">Factory Worker Profitability</a>
          <p>Compare market prices, recipes, wages, and production bonuses to estimate factory and worker profitability.</p>
        </article>
        <article class="third-party-tool">
          <a href="https://warera.unikhorne.dev/market" target="_blank" rel="noopener noreferrer">WarEra Market</a>
          <p>Explore WarEra market information in an alternative market view.</p>
        </article>
      </div>
      <div class="report-signoff">
        <p><strong>Did you know?</strong></p>
        <p>You can download the code that generated this report at:
          <a href="https://github.com/sandokas/warera-marketguide" target="_blank" rel="noopener noreferrer">github.com/sandokas/warera-marketguide</a>
        </p>
        <p>If you found it useful, don't forget to Like and Subscribe!</p>
      </div>
    </footer>"""
    )

    body = "\n".join(blocks)
    table_note = (f"Observed: {sync_timestamp or 'sync time unavailable'}{sync_status}. "
                  f"Prices BTC/unit; size {assumptions.quantity:g} units; fee {assumptions.fee_pct_per_side:g}% per side; quote limit {assumptions.max_quote_age_minutes:g} min. "
                  "N/A = unavailable / insufficient depth. Current orders do not guarantee future fills.")
    import re
    def annotate_table(match):
        table = match.group(0)
        if 'data-report-table="current-order-book"' in table or 'data-report-table="activity-comparison"' in table or 'data-table-id="participants-' in table or '<tfoot>' in table or ('col-signal' in table and 'col-price-state' in table):
            return table
        columns = table.split("</thead>")[0].count("<th ")
        return table.replace("</table>", f'<tfoot><tr><td colspan="{max(columns, 1)}">{escape(table_note)}</td></tr></tfoot></table>')
    body = re.sub(r'<table class="report-table"[^>]*>.*?</table>', annotate_table, body, flags=re.S)
    return _html_page("WarEra Market Guide", body)


def write_outputs(
    df: pd.DataFrame,
    output_dir: str | Path,
    *,
    top: int = 10,
    metric_window: str = "7D",
    chart_path: str | Path | None = None,
    chart_label: str | None = None,
    highlights: list[dict[str, object]] | None = None,
    assumptions: FlipAssumptions | None = None,
    data_synced_at: str | None = None,
    data_sync_status: str | None = None,
    we24: dict | None = None,
    we24_chart_path: str | Path | None = None,
    participant_report: dict | None = None,
    as_of: datetime | None = None,
    inflation_results: Sequence["InflationIndexResult"] | None = None,
    inflation_chart_paths: Mapping[str, str | Path] | None = None,
    equipment_details: Iterable[dict] | None = None,
    action_cost_results: Sequence["ActionCostResult"] | None = None,
) -> tuple[Path, Path]:
    assumptions = assumptions or FlipAssumptions()
    export_df = df.copy()
    compatibility_defaults = {
        "flip_verdict": "Unavailable",
        "flip_reason_codes": "missing_order_book",
        "flip_quantity": assumptions.quantity,
        "flip_snapshot_at": None,
        "flip_quote_age_minutes": None,
        "flip_entry_fully_filled": False,
        "flip_entry_average_price": None,
        "flip_total_entry_cost": None,
        "flip_break_even_exit_vwap": None,
        "flip_forecast_exit_vwap_p10": None,
        "flip_forecast_exit_vwap_median": None,
        "flip_forecast_exit_vwap_p90": None,
        "flip_net_margin_p10_pct": None,
        "flip_net_margin_median_pct": None,
        "flip_net_margin_p90_pct": None,
        "flip_net_profit_median": None,
        "flip_forecast_evidence": export_df["forecast_evidence"] if "forecast_evidence" in export_df.columns else "Insufficient",
        "flip_forecast_samples": export_df["forecast_evaluable_samples"] if "forecast_evaluable_samples" in export_df.columns else 0,
        "flip_passive_limit_price": None,
    }
    for column, value in compatibility_defaults.items():
        if column not in export_df.columns:
            export_df[column] = value
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    trends_csv_path = out / "market_trends.csv"
    scores_csv_path = out / "market_scores.csv"
    html_path = out / "market_report.html"
    participant_paths = _write_participant_exports(out, participant_report, equipment_details)
    spreadsheet_df = export_df.apply(lambda column: column.map(_spreadsheet_value))
    spreadsheet_df.to_csv(trends_csv_path, index=False)
    spreadsheet_df.to_csv(scores_csv_path, index=False)
    if action_cost_results:
        write_action_costs_csv(action_cost_results, out)
    pd.DataFrame([{key: value for key, value in point.items() if key != "weights"} for point in (we24 or {}).get("observations", [])], columns=["as_of", "level", "priced_count", "component_count", "coverage_pct", "reason", "is_rebalance"]).to_csv(out / "we24_series.csv", index=False)
    pd.DataFrame((we24 or {}).get("weight_history", []), columns=["effective_at", "reference_start", "reference_end", "item_code", "weight"]).to_csv(out / "we24_weights.csv", index=False)
    html_path.write_text(
        _materialize_display_assets(generate_html_report(
            export_df,
            top=top,
            metric_window=metric_window,
            chart_path=chart_path,
            chart_label=chart_label,
            highlights=highlights,
            output_dir=out,
            assumptions=assumptions,
            data_synced_at=data_synced_at,
            data_sync_status=data_sync_status,
            participant_report=participant_report,
            as_of=as_of,
            we24=we24,
            we24_chart_path=we24_chart_path,
        ), out),
        encoding="utf-8",
    )
    if participant_paths:
        text = html_path.read_text(encoding="utf-8")
        links = "".join(f'<link data-report-data href="{path.name}">' for path in participant_paths)
        html_path.write_text(text.replace("</head>", links + "</head>"), encoding="utf-8")
    return trends_csv_path, html_path


def _display_report_timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def export_report_assets(
    report_path: str | Path, output_dir: str | Path, *, extra_paths: Sequence[Path] = (),
    data_paths: Sequence[Path] = (),
    browser_executable: str | Path | None = None,
) -> list[dict]:
    """Capture current DOM targets only, then publish tables as static PNGs.

    The explicit inventory is authoritative. Archived files in reused output
    directories are never discovered by globbing or included in a bundle.
    """
    import json
    import re
    from playwright.sync_api import sync_playwright
    from .charts import _chrome_executable

    report = Path(report_path).resolve()
    destination = Path(output_dir).resolve()
    inventory: list[dict] = []
    def record(path: Path, kind: str, selector: str | None = None):
        relative = path.resolve().relative_to(destination).as_posix()
        if not any(entry["path"] == relative for entry in inventory):
            inventory.append({"path": relative, "kind": kind, "selector": selector})
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=_chrome_executable(browser_executable), headless=True,
                                               args=["--allow-file-access-from-files"])
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1080}, device_scale_factor=2)
            page.goto(report.as_uri(), wait_until="load")
            page.evaluate("document.fonts.ready")
            page.evaluate("""async () => { await Promise.all(Array.from(document.images).map(async image => {
                try { await image.decode(); } catch (_) { image.removeAttribute('src'); }
            })); }""")
            for src in page.locator("img[src]").evaluate_all("els => els.map(e => e.getAttribute('src'))"):
                if src.startswith("data:"):
                    continue
                path = (report.parent / src).resolve()
                if path.is_file():
                    record(path, "chart")
            for href in page.locator("link[data-report-data]").evaluate_all("els => els.map(e => e.getAttribute('href'))"):
                record(report.parent / href, "data")
            targets = [
                ('table.report-table', 'tables', 'table'),
                ('header', 'sections', 'header'),
                ('[data-report-asset="we24-summary"]', 'sections', 'we24-summary'),
                ('.summary-card', 'cards', 'highlight'),
                ('.highlight-column', 'sections', 'highlight-pair'),
                ('[data-report-asset="item-price-context-card"]', 'cards', 'item'),
                ('section', 'sections', 'composite'),
                ('footer', 'sections', 'footer'),
            ]
            table_paths = []
            for selector, folder, kind in targets:
                elements = page.locator(selector)
                for index in range(elements.count()):
                    element = elements.nth(index)
                    table_id = element.get_attribute("data-table-id")
                    name = table_id or element.get_attribute("data-item-code")
                    if not name:
                        heading = element.locator("h2, h3")
                        name = heading.first.inner_text() if heading.count() else f"{index + 1:02d}"
                    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
                    path = destination / folder / f"{kind}-{index + 1:02d}-{slug}.png"
                    if kind == "table" and table_id:
                        path = destination / "participant_rankings_7d" / f"{table_id}.png"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    element.screenshot(path=str(path), animations="disabled")
                    geometry = element.evaluate("""e => {
                        const r=e.getBoundingClientRect();
                        const cells=[...e.querySelectorAll('th,td')];
                        return {width:r.width,height:r.height,scrollWidth:e.scrollWidth,scrollHeight:e.scrollHeight,
                            cellsOutside:cells.filter(c => {const b=c.getBoundingClientRect();
                                return b.left<r.left-1 || b.right>r.right+1 || b.top<r.top-1 || b.bottom>r.bottom+1;}).length,
                            minCellFont:cells.length ? Math.min(...cells.map(c => parseFloat(getComputedStyle(c).fontSize))) : null};
                    }""")
                    if geometry["cellsOutside"] or geometry["scrollWidth"] > geometry["width"] + 2 or geometry["scrollHeight"] > geometry["height"] + 2:
                        raise RuntimeError(f"Incomplete capture: {selector} {index}: {geometry}")
                    record(path, kind, f"{selector} >> nth={index}")
                    inventory[-1]["css_size"] = geometry
                    if table_id:
                        inventory[-1]["table_id"] = table_id
                    if kind == "table":
                        table_paths.append(path)
            # Replace only after capturing complete table elements and composites.
            for index, path in enumerate(table_paths):
                table = page.locator("table.report-table").first
                table.evaluate("""(e, src) => {
                    const rect=e.getBoundingClientRect();
                    const links=[...e.querySelectorAll('a[href]')].map(a => {
                        const r=a.getBoundingClientRect();
                        return {href:a.href,label:a.innerText,left:100*(r.left-rect.left)/rect.width,
                            top:100*(r.top-rect.top)/rect.height,width:100*r.width/rect.width,height:100*r.height/rect.height};
                    });
                    const wrapper=document.createElement('div');
                    wrapper.className='published-table-container';
                    wrapper.style.cssText=`position:relative;width:${rect.width}px;max-width:100%`;
                    const img=document.createElement('img'); img.src=src; img.alt=e.innerText;
                    img.className='published-table'; img.style.cssText='display:block;width:100%;height:auto';
                    wrapper.append(img);
                    for(const link of links){
                        const a=document.createElement('a'); a.href=link.href;
                        a.target='_blank'; a.rel='noopener noreferrer'; a.title=link.label;
                        a.setAttribute('aria-label',link.label); a.className='published-identity-link';
                        a.style.cssText=`position:absolute;left:${link.left}%;top:${link.top}%;width:${link.width}%;height:${link.height}%`;
                        wrapper.append(a);
                    }
                    e.parentElement.style.cssText='width:auto;max-width:100%'; e.replaceWith(wrapper);
                }""", path.relative_to(destination).as_posix())
            report.write_text(page.content(), encoding="utf-8")
        finally:
            browser.close()
    for path in extra_paths:
        record(Path(path), "research-chart")
    record(report, "html")
    for path in data_paths:
        if Path(path).is_file():
            record(Path(path), "data")
    for entity_kind in ("user", "mu", "country"):
        for obsolete_board in ("losses", "profits", "coverage"):
            obsolete = destination / "participant_rankings_7d" / f"participants-{entity_kind}-{obsolete_board}.png"
            if obsolete.is_file() and not obsolete.is_symlink():
                obsolete.unlink()
    (destination / "asset_inventory.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    return inventory

def _spreadsheet_value(value):
    """Escape formula-leading source text only; leave numeric values numeric."""
    if isinstance(value, str) and (value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@"))):
        return "'" + value
    return value


def _participant_value(value):
    """Format accounting decimals without a float conversion."""
    if value is None:
        return "N/A"
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _category_description(category, *, include_condition=False):
    signature = category["category"]
    text = str(category["item_code"])
    if signature[0] == "equipment-v1":
        stats = signature[2]
        text += "; stats: " + ("unknown" if stats is None else ", ".join(f"{k}={v}" for k, v in stats) or "none")
        if include_condition:
            text += f"; condition {_participant_value(signature[3])}/{_participant_value(signature[4])}"
    return text


def _participant_amount(amount, missing, count):
    """Display known subtotals without presenting absent observations as zero."""
    if amount is None or (missing and missing == count):
        return "Unknown"
    text = format(amount, ",.6f").rstrip("0").rstrip(".") or "0"
    return f"{text} known ({missing} missing)" if missing else text


def _materialize_display_assets(html: str, output_dir: Path) -> str:
    """Write each supplied image once; keep large breakdowns portable and compact."""
    import base64
    import hashlib
    cache = {}
    def replace(match):
        src = match.group(1)
        if src not in cache:
            header, encoded = src.split(",", 1)
            content = base64.b64decode(encoded, validate=True)
            suffix = ".svg" if "svg+xml" in header else ".png"
            relative = Path("display-assets") / (hashlib.sha256(content).hexdigest() + suffix)
            path = output_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            cache[src] = relative.as_posix()
        return 'src="' + cache[src] + '"'
    return re.sub(r'src="(data:image/(?:png|svg\+xml);base64,[A-Za-z0-9+/=]+)"', replace, html)


class _DisplayHtml(str):
    """Only constructed from escaped display data by helpers below."""


def _display_image(src, alt, css_class):
    if not isinstance(src, str) or not src.startswith(("data:image/png;base64,", "data:image/svg+xml;base64,")):
        return ""
    return f'<img class="{css_class}" src="{escape(src, quote=True)}" alt="{escape(alt, quote=True)}">'


def _identity_html(row):
    identity = row.get("identity", {})
    label = {"user": "User", "mu": "Military unit", "country": "Country"}.get(row.get("entity_kind"), "Entity")
    name = identity.get("display_name") or row.get("name") or f"{label} {row['entity_id']}"
    if name == row['entity_id']:
        name = f"{label} {name}"
    icon = _display_image(identity.get("image_src"), f"{label} image", "identity-image")
    if not icon:
        icon = '<span class="identity-placeholder">' + escape(label) + '</span>'
    badges = ""
    if row.get("entity_kind") == "user":
        level = identity.get("level")
        if type(level) is int and level >= 0:
            badges += f'<span class="identity-level" aria-label="Level {level}">{level}</span>'
        badges += _display_image(identity.get("citizenship_image_src"),
            identity.get("citizenship_name") or "Citizenship", "identity-citizenship")
    from urllib.parse import quote
    kind = row.get("entity_kind")
    content = '<span class="identity-avatar">' + icon + badges + '</span><span>' + escape(name) + '</span>'
    if kind in {"user", "mu", "country"}:
        href = f"https://app.warera.io/{kind}/{quote(str(row['entity_id']), safe='')}"
        content = f'<a class="identity-link" href="{escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">{content}</a>'
    return _DisplayHtml(content)


def _category_html(category):
    metadata = category.get("display") or {}
    signature = category["category"]
    equipment = signature[0] == "equipment-v1"
    stats = signature[2] if equipment else ()
    values = "Unknown" if stats is None else "/".join(_participant_value(v) for _, v in stats)
    icon = _display_image(metadata.get("image_src"), category["item_code"], "equipment-image")
    if icon:
        colors = [metadata.get(key, "") for key in ("frame_color", "frame_end")]
        if all(re.fullmatch(r"#[0-9a-fA-F]{6}", color) for color in colors):
            icon = (f'<span class="equipment-frame" style="border-color:{colors[0]};'
                    f'background:linear-gradient(45deg,{colors[0]},{colors[1]})">{icon}</span>')
        return _DisplayHtml(icon + (" " + escape(values) if values else ""))
    return _DisplayHtml(escape(str(category["item_code"])) + (" " + escape(values) if values else ""))



def _participant_html(report):
    """Shared presentation: volume top ten and their complete ordered categories.

    Identity enrichment may add display fields to entity rows; ownership, ranking,
    category signatures and diagnostics remain supplied domain data.
    """
    if report is None:
        return ""
    prefix = "gross" if report["turnover_basis"] == "gross" else "source"

    def table(identifier, title, headers, rows):
        body = ''.join('<tr>' + ''.join(f'<td>{c if isinstance(c, _DisplayHtml) else escape(str(c))}</td>' for c in row) + '</tr>' for row in rows)
        if not body:
            body = f'<tr><td colspan="{len(headers)}">No qualifying observed activity.</td></tr>'
        return (f'<section class="participant-section"><h2>{escape(title)}</h2><div class="table-wrap participant-table">'
                f'<table class="report-table" data-table-id="{identifier}"><thead><tr>'
                + ''.join(f'<th>{escape(h)}</th>' for h in headers) + '</tr></thead><tbody>' + body
                + '</tbody></table></div></section>')

    blocks = ['<style>.participant-section {width:max-content;max-width:none} .participant-table table {width:max-content;table-layout:auto;font-size:16px} '
              '.participant-table th,.participant-table td {white-space:normal;max-width:24ch;overflow-wrap:anywhere;text-align:left}'
              '.identity-link{display:inline-flex;align-items:center;gap:10px;color:inherit;text-decoration:none}'
              '.identity-avatar{position:relative;display:inline-block;flex-shrink:0;width:44px;height:44px;margin:4px 0 6px 10px}'
              '.identity-image{width:44px;height:44px;object-fit:contain;vertical-align:middle;border-radius:4px}'
              '.identity-level{position:absolute;left:-10px;top:50%;transform:translateY(-50%);background:#203539;color:#eff4f8;border:1px solid #68838a;border-radius:3px;padding:0 3px;font-size:12px;font-weight:bold;line-height:17px}'
              '.identity-citizenship{position:absolute;left:-4px;bottom:-4px;width:20px;height:15px;object-fit:contain;border-radius:2px;box-shadow:0 0 0 1px #101820}'
              '.identity-placeholder{display:inline-block;font-size:11px;padding:3px;border:1px solid #8899aa;margin-right:6px}'
              '.equipment-frame{display:inline-block;width:42px;height:42px;border:1px solid;border-bottom-width:2px;border-radius:5px;vertical-align:middle;margin:3px 7px 3px 0}'
              '.equipment-image{width:42px;height:42px;object-fit:contain;vertical-align:middle}</style>']
    for kind, label in (("user", "Users"), ("mu", "Military Units"), ("country", "Countries")):
        entries = report['rankings'][kind]['volume']
        rows, details = [], []
        for rank, row in enumerate(entries, 1):
            name = _identity_html(row)
            count = sum(c['trade_count'] for cats in row['categories'].values() for c in cats)
            tops = [_DisplayHtml('<br>'.join(_category_html(c) for c in row['top_' + side]) or 'No observed activity') for side in ('buy', 'sell')]
            rows.append([rank, name,
                         _participant_amount(row[prefix + '_turnover'], row['missing_money_count'], count), *tops])
            for side in ('buy', 'sell'):
                for c in row['categories'][side]:
                    share = 'Unknown' if c['share'] is None else f"{c['share'] * 100:.2f}%"
                    details.append([name, side.title(), _category_html(c),
                        _participant_amount(c['money'], c['missing_money_count'], c['trade_count']),
                        _participant_amount(c['quantity'], c['missing_quantity_count'], c['trade_count']),
                        share, c['trade_count']])
        blocks.append(table(f'participants-{kind}-volume', f'{label} - monetary turnover',
            ['Rank', {'user': 'User', 'mu': 'MU', 'country': 'Country'}[kind], '7D Turnover BTC', 'Mostly bought (details below)', 'Mostly sold (details below)'], rows))
        blocks.append(table(f'participants-{kind}-explanations', f'{label} - buy/sell breakdown',
            [{'user': 'User', 'mu': 'MU', 'country': 'Country'}[kind], 'Side', 'Item / full stats', '7D Value BTC', 'Units', 'Side share', 'Trades'], details))
    return ''.join(blocks)


def _write_participant_exports(out, report, equipment_details):
    """Flatten domain results; spreadsheet protection only at the output boundary."""
    import csv
    def flat(value, prefix=''):
        result = {}
        for key, item in value.items():
            name = prefix + key
            if isinstance(item, dict):
                result.update(flat(item, name + '_'))
            elif isinstance(item, (list, tuple, set)):
                result[name] = repr(item)
            else:
                result[name] = item
        return result
    def safe(value):
        if value is None:
            return ''
        return _participant_value(_spreadsheet_value(value))
    paths = []
    def write(name, rows, required):
        path = out / name
        columns = list(dict.fromkeys(required + [key for row in rows for key in row]))
        with path.open('w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=columns)
            writer.writeheader()
            writer.writerows({k: safe(v) for k, v in row.items()} for row in rows)
        paths.append(path)
    if report is not None:
        common = {k: report[k] for k in ('as_of', 'window_start', 'method', 'turnover_basis', 'limitations')}
        common.update(flat({'source_coverage': report['source_coverage'], 'attribution': report['coverage']}))
        rankings = []
        for kind, boards in report['rankings'].items():
            for board, rows in boards.items():
                for rank, row in enumerate(rows, 1):
                    rankings.append({**common, 'ranking_kind': board, 'rank': rank, **flat({k:v for k,v in row.items() if k not in ('categories','top_buy','top_sell','other_buy','other_sell','identity')}), 'name':row.get('name') or row['entity_id']})
        write('participant_rankings_7d.csv', rankings, ['entity_kind','entity_id','name','ranking_kind','rank','as_of','matched_net_pnl'])
        breakdown = []
        stats = []
        for row in report['entities']:
            for side, categories in row['categories'].items():
                for index, c in enumerate(categories, 1):
                    key = {'entity_kind':row['entity_kind'], 'entity_id':row['entity_id'], 'side':side, 'category_index':index}
                    sig = c['category']
                    breakdown.append({**common, **key, 'name':row.get('name') or row['entity_id'], **{k:v for k,v in c.items() if k not in ('category','display')}, 'signature_version':sig[0], 'state':sig[3] if len(sig)>2 else None, 'max_state':sig[4] if len(sig)>2 else None, 'description':_category_description(c, include_condition=True)})
                    if sig[0] == 'equipment-v1' and sig[2] is not None:
                        stats.extend({**key,'skill_code':k,'value':v} for k,v in sig[2])
        write('participant_trade_breakdown_7d.csv', breakdown, ['entity_kind','entity_id','side','category_index','as_of'])
        write('participant_trade_stats_7d.csv', stats, ['entity_kind','entity_id','side','category_index','skill_code','value'])
    if equipment_details is not None:
        sales, stats = [], []
        for sale in equipment_details:
            equipment = sale.get('equipment') or {}
            sales.append(flat({**sale, 'equipment':{k:v for k,v in equipment.items() if k != 'stats'}, 'equipment_stats_status':'unknown' if equipment.get('stats') is None else 'observed'}))
            stats.extend({'transaction_id':sale['id'], 'skill_code':k, 'value':v} for k,v in (equipment.get('stats') or {}).items())
        write('equipment_sales_7d.csv', sales, ['id','as_of','window_start','net_realized_pnl','basis_status','fee_status'])
        write('equipment_sale_stats_7d.csv', stats, ['transaction_id','skill_code','value'])
    return paths
