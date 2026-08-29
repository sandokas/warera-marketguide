from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path
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
      overflow-x: hidden;
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
    .depth-label-number {{ overflow: hidden; text-align: right; text-overflow: ellipsis; white-space: nowrap; }}
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
        f'<th class="{_column_classes(column)}">{escape(str(column))}</th>'
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


def _price_guide_html(df: pd.DataFrame, display_count: int) -> str:
    rows = []
    for _, row in df.iterrows():
        entry_value = row.get("guide_entry_action")
        holder_value = row.get("guide_holder_action")
        entry_action = entry_value if isinstance(entry_value, str) and entry_value in {"BUY", "WAIT"} else "WAIT"
        holder_action = holder_value if isinstance(holder_value, str) and holder_value in {"SELL", "HOLD"} else "HOLD"
        signal = "BUY" if entry_action == "BUY" else "SELL" if holder_action == "SELL" else "WAIT"
        ask = _first_number(row, "guide_executable_ask_vwap", "latest_ask", "ask")
        bid = _first_number(row, "guide_executable_bid_vwap", "latest_bid", "bid")
        rows.append({
            "Item": row.get("item_name", "Unknown"),
            "Signal": signal,
            "Ask": ask,
            "Bid": bid,
            "Fair 1D": _strict_fair_price(row, "1D"),
            "Fair 7D": _strict_fair_price(row, "7D"),
            "Fair 30D": _strict_fair_price(row, "30D"),
            "Max Buy": _first_number(row, "guide_max_entry_price"),
            "Rich Sell": _first_number(row, "guide_rich_exit_price", "price_p90_7d"),
            "Ask Upside %": _first_number(row, "guide_net_to_fair_pct"),
            "Price State": _present(
                row.get("tendency_labels_7d"),
                _present(
                    row.get("tendency_7d"),
                    "Insufficient history",
                ),
            ),
        })
    if not rows:
        return ""
    view = pd.DataFrame(rows)
    view["_signal_rank"] = view["Signal"].map({"BUY": 0, "SELL": 1, "WAIT": 2}).fillna(3)
    view = view.sort_values(
        ["_signal_rank", "Ask Upside %", "Item"],
        ascending=[True, False, True],
        na_position="last",
    ).head(display_count).drop(columns="_signal_rank")
    return (
        '<section><div class="eyebrow">Fair-value detail</div><h2>Fair Value &amp; Buy / Sell Signals</h2>'
        '<p class="muted">Blended fair estimates provide strict 1D, 7D, and 30D completed-transaction context; Signal, Max Buy, Rich Sell, Ask Upside, and Price State use 7D guidance.</p>'
        + _compact_table_html(view, table_kind="price-guide")
        + '</section>'
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
    rows.sort(key=lambda row: (-(_number(row["Value"]) or 0.0), str(row["Item"])))
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
            f'<td class="activity-ratio number" title="{escape(ratio_title)}">{escape(ratio_label)}</td>'
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
            '<td class="activity-totals number">'
            f'<span class="activity-total-line">{escape(unit_label)} units</span>'
            f'<span class="activity-total-line">{escape(trade_label)} trades</span></td></tr>'
        )
    return (
        '<section><h2>Completed Market Activity</h2>'
        f'<p class="muted">Completed {ACTIVITY_WINDOW} trades ranked by value; PP volume estimates embodied production effort.</p>'
        '<div class="table-wrap compact-table activity-table"><table class="report-table">'
        '<thead><tr><th class="activity-item text">Item</th>'
        '<th class="activity-ratio number" title="Total upstream Production Points required for one item">Total PP : Item</th>'
        '<th class="activity-volume number">Completed Value / PP-equivalent Volume</th>'
        '<th class="activity-totals number">Units / Trades</th></tr></thead>'
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
        '<div class="table-wrap compact-table book-summary"><table class="report-table">'
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
        "SELL": ("sell", "−"),
        "WAIT": ("wait", "•"),
        "HOLD": ("wait", "•"),
    }.get(label, ("check", "•"))
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
    selected_sides: set[str] = set()
    for entry in highlights:
        item = entry.get("item")
        chart_path = entry.get("chart_path")
        if item is None:
            continue
        chart_src = _relative_chart_path(chart_path, output_dir) if chart_path is not None else None
        role = str(getattr(item, "role"))
        title = role_labels.get(role, role.replace("_", " ").title())
        tone = "down" if "discount" in role else "up"
        selected_sides.add("discount" if tone == "down" else "premium")
        icon = "↓" if tone == "down" else "↑"
        name = str(getattr(item, "item_name"))
        gap = _number(getattr(item, "raw_gap_pct", getattr(item, "gap_pct", None)))
        latest = _number(getattr(item, "latest_completed_price", None))
        fair = _number(getattr(item, "fair_7d", None))
        lower = _number(getattr(item, "price_p10_7d", None))
        upper = _number(getattr(item, "price_p90_7d", None))
        severity = _number(getattr(item, "severity", None))
        item_code = str(getattr(item, "item_code"))
        edge = "below P10" if tone == "down" else "above P90"
        gap_text = f"{gap:+.2f}% vs 7D fair" if gap is not None else "Gap versus 7D fair unavailable"
        severity_text = (
            f"{severity:.2f} normal-range widths {edge}"
            if severity is not None
            else "Normalized excess unavailable"
        )
        card = (
            f'<article class="summary-card summary-card-{tone}" data-item-code="{escape(item_code, quote=True)}" '
            f'data-highlight-role="{escape(role, quote=True)}"><span>{escape(title)}</span>'
            f'<strong><span class="summary-arrow" aria-hidden="true">{icon}</span>{escape(name)}</strong>'
            f'<span class="summary-value">{escape(gap_text)}</span>'
            f'<span class="summary-detail">Last {_fmt(latest)} | Fair {_fmt(fair)} | '
            f'Normal {_fmt(lower)}-{_fmt(upper)}</span>'
            f'<span class="summary-detail">{escape(severity_text)}</span></article>'
        )
        chart = ""
        if chart_src:
            alt = f"{name} {title.lower()} trailing 30D price-action chart"
            chart = (f'<img class="highlight-chart" src="{escape(chart_src, quote=True)}" '
                     f'alt="{escape(alt, quote=True)}">')
        columns.append(
            f'<div class="highlight-column">{card}{chart}</div>'
        )
    if not columns:
        return (
            '<section class="highlight-section"><div class="eyebrow">Strict 7D completed-trade context</div>'
            '<h2>Meaningful price dislocations</h2><span class="sr-only">Chart-linked ranking is limited to items '
            'with sufficient completed-transaction history for a price-action chart.</span>'
            '<article class="summary-card summary-card-neutral highlight-neutral">'
            '<strong>No meaningful price dislocations</strong>'
            '<span class="summary-detail">Eligible latest trades are within their normal 7D ranges or less than one tick from fair value.</span>'
            '</article></section>'
        )
    side_note = ""
    if "premium" not in selected_sides:
        side_note = '<p class="highlight-side-note">No meaningful premium; other eligible items remain within their normal 7D ranges.</p>'
    elif "discount" not in selected_sides:
        side_note = '<p class="highlight-side-note">No meaningful discount; other eligible items remain within their normal 7D ranges.</p>'
    return (
        '<section class="highlight-section"><div class="eyebrow">Strict 7D completed-trade context</div>'
        '<h2>Meaningful price dislocations</h2><span class="sr-only">Chart-linked ranking is limited to items '
        'with sufficient completed-transaction history for a price-action chart.</span>'
        '<div class="summary-grid">' + "".join(columns) + '</div>' + side_note + '</section>'
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
        f"strict 7D fair {_fmt(fair)}, latest completed trade {_fmt(latest)}."
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
        f'<span><span class="range-symbol range-symbol-fair" aria-hidden="true">◆</span>Fair <strong>{_fmt(fair)}</strong></span>'
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
        or "%" in label
        or "ask" in label
        or "bid" in label
        or label in {"last", "last trade", "samples"}
    )


def _render_table_cell(column: str, value: object) -> str:
    if column == "7D Trend":
        return _trend_label(value)
    if column in {"1D Change", "7D Change", "30D Change"}:
        return _trend_change(value)
    if column == "30D Position":
        return _trend_position(value)
    if column == "30D Path":
        return _trend_path(value)
    if column == "Pattern":
        return _trend_pattern(value)
    if column == "Signal":
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
    chips = "".join(_chip(label, _market_state_tone(label)) for label in labels)
    return f'<span class="state-chips">{chips}</span>'


def _market_state_tone(label: str) -> str:
    normalized = label.lower()
    if normalized == "rising" or normalized == "stable":
        return "up"
    if normalized == "falling" or normalized == "volatile":
        return "down"
    if normalized == "thin":
        return "weak"
    return "flat"


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
    low = _number(value.get("low"))
    high = _number(value.get("high"))
    latest = _number(value.get("latest"))
    window_start = _number(value.get("window_start"))
    window_end = _number(value.get("window_end"))
    direction = str(value.get("direction") or "flat").lower()
    if low is None or high is None or latest is None or window_start is None or window_end is None:
        return "N/A"
    timestamps = sorted(
        int(point["timestamp"])
        for point in points
        if isinstance(point, dict) and _number(point.get("timestamp")) is not None
    )
    if len(timestamps) < 2:
        return "N/A"
    coverage_days = max(1, round((timestamps[-1] - timestamps[0]) / 86_400))
    window_days = max(1, round((window_end - window_start) / 86_400))
    label = (
        f"30D price path: {len(points)} daily observations spanning "
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
        'alt="Accumulated Broad Market inflation over the last 30 days">'
        '<figcaption>Signed percentage change over the latest 30 days; positive means BTC buys less.</figcaption>'
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
    inflation_results: Sequence["InflationIndexResult"] | None = None,
    inflation_chart_paths: Mapping[str, str | Path] | None = None,
) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
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
    display_count = len(df) if top <= 0 else top
    blocks: list[str] = []
    header_html = f"""    <header>
      <div class="hero-copy">
        <div class="eyebrow">WarEra Market Guide</div>
        <h1>Market intelligence, without the noise.</h1>
        <p class="muted">Completed trades provide price history; current visible orders provide executable prices and market depth.</p>
      </div>
      <div class="hero-meta"><strong>Combined 1D / 7D / 30D report</strong><span>{data_freshness}</span><span>Report generated {escape(generated)}</span></div>
</header>
"""
    highlight_html = _highlight_pairs_html(highlights or [], Path(output_dir))
    blocks.append(
        '<div class="report-header-capture" data-report-asset="header">'
        + header_html
        + highlight_html
        + '</div>'
    )
    blocks.append(_price_guide_html(df, display_count))

    blocks.append(_order_book_html(df, display_count))
    blocks.append(_activity_html(df, display_count))

    if not df.empty:
        view = df.head(display_count).copy()
        trend_rows = []
        for _, row in view.iterrows():
            changes = {
                horizon: _number(row.get(f"percent_change_{horizon.lower()}"))
                for horizon in ("1D", "7D", "30D")
            }
            # Legacy inputs may expose only the historical 7D momentum field.
            if changes["7D"] is None:
                changes["7D"] = _number(row.get("momentum_7d_pct"))
            pattern = classify_market_trend_pattern(
                change_1d_pct=changes["1D"],
                change_7d_pct=changes["7D"],
                change_30d_pct=changes["30D"],
            )
            last_trade = _number(row.get("last_trade_price"))
            low_30d = _number(row.get("min_30d"))
            high_30d = _number(row.get("max_30d"))
            position: float | str | None = None
            if changes["30D"] is not None and low_30d is not None and high_30d is not None:
                position = (
                    "Flat"
                    if high_30d == low_30d
                    else calculate_range_position_pct(last_price=last_trade, low=low_30d, high=high_30d)
                )
            path_points = row.get("trend_path_30d")
            path_direction = (
                "up"
                if changes["30D"] is not None and changes["30D"] > 0.5
                else "down"
                if changes["30D"] is not None and changes["30D"] < -0.5
                else "flat"
            )
            trend_rows.append({
                "Item": row.get("item_name", "Unknown"),
                "Last Trade": last_trade,
                "1D Change": changes["1D"],
                "7D Change": changes["7D"],
                "30D Change": changes["30D"],
                "30D Path": {
                    "points": path_points if isinstance(path_points, list) else [],
                    "low": low_30d,
                    "high": high_30d,
                    "latest": last_trade,
                    "direction": path_direction,
                    "window_start": row.get("trend_path_30d_start_epoch"),
                    "window_end": row.get("trend_path_30d_end_epoch"),
                },
                "30D Position": position,
                "Pattern": f"{pattern.label}|{pattern.description}",
            })
        trend_table = pd.DataFrame(trend_rows)
        blocks.append(
            f"""    <section>
      <h2>Market Trends</h2>
      <p class="muted">Completed-trade price changes, 30-day range position, and trend shape.</p>
      {_compact_table_html(trend_table, table_kind="market-trends")}
    </section>"""
        )

    if inflation_results:
        blocks.append(inflation_summary_html(
            inflation_results,
            chart_paths=inflation_chart_paths,
            output_dir=output_dir,
        ))

    note_rows = df.head(display_count).copy()
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
                f"<li>Strict 7D fair: <strong>{escape(_fmt(fair))}</strong></li>",
                f"<li>Latest completed trade: <strong>{escape(_fmt(latest))}</strong></li>",
                f"<li>7D normal range: <strong>{escape(_fmt(lower))}-{escape(_fmt(upper))}</strong> ({escape(_fmt(width_pct, 2))}%)</li>",
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
        blocks.append(f'<section><h2>{escape(chart_heading)}</h2><img class="chart" src="{escape(chart_src)}" alt="Featured market history chart"></section>')

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

    return _html_page("WarEra Market Guide", "\n".join(blocks))


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
    inflation_results: Sequence["InflationIndexResult"] | None = None,
    inflation_chart_paths: Mapping[str, str | Path] | None = None,
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
    export_df.to_csv(trends_csv_path, index=False)
    export_df.to_csv(scores_csv_path, index=False)
    if inflation_results is not None:
        write_inflation_csv(inflation_results, out)
    if action_cost_results:
        write_action_costs_csv(action_cost_results, out)
    html_path.write_text(
        generate_html_report(
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
            inflation_results=inflation_results,
            inflation_chart_paths=inflation_chart_paths,
        ),
        encoding="utf-8",
    )
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
