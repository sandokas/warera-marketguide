from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Iterable

import pandas as pd

from .metrics import FlipAssumptions, MarketMetrics


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
        if any(token in label for token in ("volume", "trade", "sample")) or label in {"liquidity"}:
            return _fmt(value, 0)
        if "%" in label:
            return _fmt(value, 2)
        if label in {"now", "latest", "min", "max", "fair", "buy", "sell", "last"} or label.endswith(("low", "high")):
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


def _fair_price(row: pd.Series, window_key: str) -> float | None:
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


def _latest_price(row: pd.Series) -> float | None:
    return _first_number(row, "last_trade_price", "current_price", "latest_price")


def _price_gap_pct(latest: float | None, fair: float | None) -> float | None:
    if latest is None or fair is None or fair <= 0:
        return None
    return (latest - fair) / fair * 100


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
    h1, h2, h3, p {{ margin-top: 0; }}
    h1 {{ margin-bottom: 7px; font-size: clamp(1.8rem, 4vw, 2.55rem); letter-spacing: -0.035em; }}
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
      max-width: 1040px;
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
    .hero-copy {{ max-width: 700px; }}
    .hero-copy > p {{ margin-bottom: 0; max-width: 650px; }}
    .hero-meta {{ color: var(--muted); font-size: 0.82rem; text-align: left; white-space: nowrap; }}
    .hero-meta strong {{ display: block; margin-bottom: 4px; color: var(--text); font-size: 0.92rem; }}
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
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      font-size: 0.86rem;
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
      font-size: 0.75rem;
      font-weight: 750;
      letter-spacing: 0.015em;
    }}
    tbody tr:nth-child(even) {{ background: rgba(30, 41, 59, 0.22); }}
    .table-wrap {{ overflow: visible; }}
    .compact-table .report-table {{
      width: 100%;
      table-layout: fixed;
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
      min-width: 120px;
      max-width: 200px;
    }}
    .compact-table .col-notes,
    .compact-table .col-read,
    .compact-table .col-market-state {{
      min-width: 220px;
      max-width: 340px;
    }}
    .compact-table .col-spread-pct {{
      width: 76px;
      min-width: 76px;
    }}
    .compact-table .col-volume {{
      width: 80px;
      min-width: 80px;
    }}
    .price-guide-table {{ overflow: visible; }}
    .price-guide-table .report-table {{ width: 100%; table-layout: fixed; }}
    .price-guide-table th, .price-guide-table td {{ padding: 7px 5px; }}
    .price-guide-table .col-item {{ width: 12%; min-width: 0; font-weight: 700; white-space: nowrap; }}
    .price-guide-table .col-signal {{ width: 8%; text-align: left; white-space: nowrap; }}
    .price-guide-table .col-ask,
    .price-guide-table .col-bid,
    .price-guide-table .col-fair,
    .price-guide-table .col-max-buy,
    .price-guide-table .col-rich-sell {{ width: 8%; white-space: nowrap; }}
    .price-guide-table .col-ask-upside-pct {{ width: 10%; white-space: nowrap; }}
    .price-guide-table .col-price-state {{ width: 30%; white-space: nowrap; }}
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
      font-size: 0.76rem;
      line-height: 1.35;
    }}
    .signal-help strong {{ display: block; margin-bottom: 2px; font-size: 0.8rem; }}
    .signal-help .buy-rule strong {{ color: var(--good); }}
    .signal-help .sell-rule strong {{ color: var(--bad); }}
    .signal-help .wait-rule strong {{ color: var(--amber); }}
    .signal-warning {{ margin: 0 0 10px; color: var(--muted); font-size: 0.76rem; }}
    .book-summary .report-table {{ width: 100%; table-layout: fixed; }}
    .book-summary th, .book-summary td {{ white-space: normal; vertical-align: middle; }}
    .book-summary th, .book-summary td {{ padding: 7px 5px; }}
    .book-summary .book-item {{ width: 10%; font-weight: 700; }}
    .book-summary .book-price {{ width: 7%; }}
    .book-summary .book-wall {{ width: 8%; }}
    .book-summary .book-profile-cell {{ width: 41%; }}
    .book-summary .book-pressure {{ width: 13%; white-space: nowrap; }}
    .book-summary .book-spread {{ width: 6%; }}
    .activity-table .report-table,
    .market-trends-table .report-table {{
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      scrollbar-color: var(--line) var(--bg);
    }}
    .activity-table th, .activity-table td,
    .market-trends-table th, .market-trends-table td {{ padding: 7px 6px; }}
    .activity-table .activity-item {{ width: 12%; font-weight: 700; }}
    .activity-table .activity-ratio {{ width: 12%; white-space: nowrap; }}
    .activity-table .activity-volume {{ width: 54%; }}
    .activity-table .activity-totals {{ width: 22%; }}
    .activity-volume-content {{ display: grid; gap: 4px; }}
    .activity-metric {{ display: grid; grid-template-columns: 54px minmax(0, 1fr) 58px; gap: 7px; align-items: center; }}
    .activity-metric-label {{ color: var(--muted); font-size: 0.7rem; text-align: left; white-space: nowrap; }}
    .activity-track {{ height: 12px; overflow: hidden; border-radius: 3px; background: #0b1220; }}
    .activity-fill {{ height: 100%; min-width: 1px; border-radius: 3px; background: linear-gradient(90deg, #0f766e, #6ee7b7); }}
    .activity-fill-pp {{ background: linear-gradient(90deg, #1d4ed8, #7dd3fc); }}
    .activity-number,
    .activity-total-line {{ display: block; text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
    .activity-total-line + .activity-total-line {{ margin-top: 2px; color: var(--muted); font-size: 0.75rem; }}
    .market-trends-table .col-item {{ width: 11%; font-weight: 700; }}
    .market-trends-table .col-latest {{ width: 9%; }}
    .market-trends-table .col-7d-change-pct {{ width: 9%; }}
    .market-trends-table .col-range {{ width: 14%; }}
    .market-trends-table .col-activity {{ width: 20%; }}
    .market-trends-table .col-spread-pct {{ width: 8%; min-width: 0; }}
    .market-trends-table .col-price-state {{ width: 29%; min-width: 0; max-width: none; white-space: nowrap; }}
    .state-chips {{ display: inline-flex; flex-wrap: nowrap; gap: 4px; white-space: nowrap; }}
    .depth-profile {{ width: 100%; min-width: 0; }}
    .depth-profile-labels {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin-bottom: 3px;
      color: var(--muted);
      font-size: 0.7rem;
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
      font-size: 0.64rem;
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
      font-size: 0.78rem;
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
    .readiness-note p {{ margin: 0; color: var(--muted); font-size: 0.84rem; }}
    .chart {{
      width: 100%;
      max-height: 720px;
      object-fit: contain;
      background: #0f172a;
      border: 1px solid var(--line);
      border-radius: 8px;
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
    }}
    @media (max-width: 720px) {{
      header {{ display: block; padding: 18px; }}
      .hero-meta {{ margin-top: 18px; text-align: left; }}
      h1 {{ font-size: 1.65rem; }}
      th, td {{ padding: 8px; }}
      .signal-help {{ grid-template-columns: 1fr; }}
      .price-guide-table th, .price-guide-table td {{ padding: 6px 3px; font-size: 0.68rem; }}
      .price-guide-table .chip {{ padding: 2px 4px; font-size: 0.68rem; }}
    }}
    @media (max-width: 520px) {{
      main {{ width: min(100% - 20px, 1180px); padding-top: 10px; }}
      .book-summary th, .book-summary td {{ padding-inline: 3px; font-size: 0.64rem; }}
      .book-pressure-content {{ gap: 2px; }}
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
    return f'<div class="table-wrap compact-table {kind_class}">{table}</div>'


def _trend_label(value: object) -> str:
    change = _number(value)
    if change is None:
        return "—"
    if change > 0.5:
        return _chip("Rising", "up", prefix="↑")
    if change < -0.5:
        return _chip("Falling", "down", prefix="↓")
    return _chip("Flat", "flat", prefix="→")


def _price_guide_html(df: pd.DataFrame, window_key: str, display_count: int) -> str:
    rows = []
    for _, row in df.iterrows():
        fair = _fair_price(row, window_key)
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
            "Fair": fair,
            "Max Buy": _first_number(row, "guide_max_entry_price"),
            "Rich Sell": _first_number(row, "guide_rich_exit_price", f"price_p90_{window_key}", "price_p90_7d"),
            "Ask Upside %": _first_number(row, "guide_net_to_fair_pct"),
            "Price State": _present(
                row.get("tendency_labels_7d"),
                _present(
                    row.get("tendency_7d"),
                    _present(
                        row.get(f"tendency_labels_{window_key}"),
                        _present(row.get(f"tendency_{window_key}"), "Insufficient history"),
                    ),
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
        '<p class="muted">BUY: executable Ask ≤ Max Buy, with Max Buy capped at P25 or P10 in falling/volatile markets. SELL: executable Bid ≥ Rich Sell (for existing holders). Ask Upside is the fee-adjusted return to Fair. WAIT means neither threshold is available now.</p>'
        + _compact_table_html(view, table_kind="price-guide")
        + '</section>'
    )


def _activity_html(df: pd.DataFrame, window_key: str, metric_window: str, display_count: int) -> str:
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
        f'<p class="muted">Actual completed trading over {escape(metric_window)}. Completed Value is the sum of transaction price × quantity and controls the row order for every item. Total PP : Item includes the direct recipe and all upstream ingredient production; PP-equivalent Volume is completed units × Total PP per item. It compares embodied production effort but is not actual production during this window. Item rows must not be summed because ingredient and processed-item trades can overlap. Items without a defined factory chain rank normally by value and show N/A for PP fields. Open orders are not included.</p>'
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
        '<p class="muted">Visible advertised depth from up to 100 orders per side. Green bids extend left and red asks extend right; each segment is one price level sized by order value, with the best prices nearest the centre and walls outlined. Pressure = (bid value − ask value) ÷ (bid value + ask value). Orders can be cancelled.</p>'
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


def _market_pulse_html(df: pd.DataFrame, window_key: str) -> str:
    valuation_rows: list[dict[str, object]] = []
    for _, row in df.iterrows():
        latest = _latest_price(row)
        fair = _fair_price(row, window_key)
        gap = _price_gap_pct(latest, fair)
        if gap is not None:
            valuation_rows.append({"name": row.get("item_name", "Unknown"), "gap": gap, "fair": fair})

    discount = min(valuation_rows, key=lambda item: float(item["gap"]), default=None)
    premium = max(valuation_rows, key=lambda item: float(item["gap"]), default=None)
    if discount is not None and float(discount["gap"]) >= 0:
        discount = None
    if premium is not None and float(premium["gap"]) <= 0:
        premium = None

    def valuation_card(title: str, selected: dict[str, object] | None, *, tone: str, icon: str, fallback: str) -> str:
        if selected is None:
            name, value, detail = fallback, "No clear outlier", "Current price is close to its fair-price context"
        else:
            name = str(selected["name"])
            value = f'{float(selected["gap"]):+.2f}% vs fair'
            detail = f'Fair reference {_fmt(selected["fair"])}'
        return (
            f'<article class="summary-card summary-card-{tone}"><span>{escape(title)}</span>'
            f'<strong><span class="summary-arrow" aria-hidden="true">{escape(icon)}</span>{escape(name)}</strong>'
            f'<span class="summary-value">{escape(value)}</span><span class="summary-detail">{escape(detail)}</span></article>'
        )

    return (
        '<section><div class="eyebrow">Fair-value context</div><h2>Largest price gaps</h2>'
        '<div class="summary-grid">'
        + valuation_card("Largest discount", discount, tone="down", icon="↓", fallback="No clear discount")
        + valuation_card("Largest premium", premium, tone="up", icon="↑", fallback="No clear premium")
        + '</div></section>'
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
        or "%" in label
        or "ask" in label
        or "bid" in label
        or label in {"last", "samples"}
    )


def _render_table_cell(column: str, value: object) -> str:
    if column == "7D Trend":
        return _trend_label(value)
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


def _relative_chart_path(chart_path: str | Path | None, output_dir: Path) -> str | None:
    if chart_path is None:
        return None
    path = Path(chart_path)
    try:
        return path.relative_to(output_dir).as_posix()
    except ValueError:
        return path.as_posix()


def generate_html_report(
    df: pd.DataFrame,
    *,
    top: int = 0,
    metric_window: str = "7D",
    chart_path: str | Path | None = None,
    chart_label: str | None = None,
    output_dir: str | Path = ".",
    assumptions: FlipAssumptions | None = None,
) -> str:
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    display_count = len(df) if top <= 0 else top
    window_key = _window_key(metric_window)
    blocks: list[str] = []
    blocks.append(
        f"""    <header>
      <div class="hero-copy">
        <div class="eyebrow">WarEra Market Guide</div>
        <h1>Market intelligence, without the noise.</h1>
        <p class="muted">Historical price context, completed activity, and the current visible order book in one practical view.</p>
      </div>
      <div class="hero-meta"><strong>{escape(metric_window)} analysis window</strong>Updated {escape(generated)}</div>
</header>
"""
    )
    blocks.append(_market_pulse_html(df, window_key))
    blocks.append(_price_guide_html(df, window_key, display_count))

    blocks.append(_order_book_html(df, display_count))
    blocks.append(_activity_html(df, window_key, metric_window, display_count))

    trend_candidates = df.copy()
    if not trend_candidates.empty:
        change_col = _column(df, f"percent_change_{window_key}", "momentum_7d_pct")
        volume_col = _column(df, f"volume_{window_key}", f"traded_quantity_{window_key}")
        trades_col = _column(df, f"trade_count_{window_key}", "trades_7d")
        sort_cols = [col for col in [volume_col] if col]
        if sort_cols:
            trend_candidates = trend_candidates.sort_values(sort_cols, ascending=False, na_position="last")
        view = trend_candidates.head(display_count).copy()
        trend_rows = []
        for _, row in view.iterrows():
            low = _first_number(row, f"min_{window_key}", "low_7d")
            high = _first_number(row, f"max_{window_key}", "high_7d")
            volume = _number(row.get(volume_col)) if volume_col else None
            trades = _number(row.get(trades_col)) if trades_col else None
            state_col = _column(df, f"tendency_labels_{window_key}", f"tendency_{window_key}")
            trend_rows.append({
                "Item": row.get("item_name", "Unknown"),
                "Latest": _first_number(row, "latest_price", "current_price"),
                "7D Change %": _number(row.get(change_col)) if change_col else None,
                "Range": f"{_fmt(low)} – {_fmt(high)}" if low is not None and high is not None else "N/A",
                "Activity": f"{_fmt_compact(volume)} units / {_fmt_compact(trades)} trades" if volume is not None and trades is not None else "N/A",
                "Spread %": _first_number(row, "latest_spread_pct", f"average_spread_pct_{window_key}", "spread_pct"),
                "Price State": row.get(state_col) if state_col else "Insufficient history",
            })
        trend_table = pd.DataFrame(trend_rows)
        blocks.append(
            f"""    <section>
      <h2>Market Trends</h2>
      <p class="muted">Price state describes price behaviour only. Completed activity is shown separately in units and trades so a stable price is never mistaken for an inactive market.</p>
      {_compact_table_html(trend_table, table_kind="market-trends")}
    </section>"""
        )

    note_rows = df.head(display_count).copy()
    if not note_rows.empty:
        notes: list[str] = []
        for _, row in note_rows.iterrows():
            fair = _fair_price(row, window_key)
            volume = _first_number(row, f"volume_{window_key}", f"traded_quantity_{window_key}")
            trades = _first_number(row, f"trade_count_{window_key}", "trades_7d")
            detail_items = [
                f"<li>Historical fair context: <strong>{escape(_fmt(fair))}</strong></li>",
                f"<li>Bid / Ask: <strong>{escape(_fmt(row.get('bid')))} / {escape(_fmt(row.get('ask')))}</strong></li>",
                f"<li>{escape(metric_window)} volume / trades: <strong>{escape(_fmt(volume, 0))} / {escape(_fmt(trades, 0))}</strong></li>",
                f"<li>{escape(metric_window)} range: <strong>{escape(_fmt(row.get('range_pct'), 2))}%</strong></li>",
            ]
            if row.get("momentum_7d_pct") is not None and not pd.isna(row.get("momentum_7d_pct")):
                detail_items.append(f"<li>{escape(metric_window)} momentum: <strong>{escape(_fmt(row['momentum_7d_pct'], 2))}%</strong></li>")
            notes.append(
                f"""<article class="note">
          <h3>{escape(str(row['item_name']))}</h3>
          <ul>{"".join(detail_items)}</ul>
        </article>"""
            )
        blocks.append(
            """    <section>
      <h2>Item Notes</h2>
      <div class="notes">""" + "\n".join(notes) + """</div>
    </section>"""
        )

    chart_src = _relative_chart_path(chart_path, Path(output_dir))
    if chart_src:
        chart_heading = f"Featured Price History: {chart_label}" if chart_label else "Featured Price History"
        blocks.append(f'<section><h2>{escape(chart_heading)}</h2><img class="chart" src="{escape(chart_src)}" alt="Featured market history chart"></section>')

    return _html_page("WarEra Market Guide", "\n".join(blocks))


def write_outputs(
    df: pd.DataFrame,
    output_dir: str | Path,
    *,
    top: int = 10,
    metric_window: str = "7D",
    chart_path: str | Path | None = None,
    chart_label: str | None = None,
    assumptions: FlipAssumptions | None = None,
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
    html_path.write_text(
        generate_html_report(
            export_df,
            top=top,
            metric_window=metric_window,
            chart_path=chart_path,
            chart_label=chart_label,
            output_dir=out,
            assumptions=assumptions,
        ),
        encoding="utf-8",
    )
    return trends_csv_path, html_path
