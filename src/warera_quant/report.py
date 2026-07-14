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
    bid = _first_number(row, "bid", "latest_bid")
    ask = _first_number(row, "ask", "latest_ask")
    midpoint = (bid + ask) / 2 if bid is not None and ask is not None else None
    return _first_number(
        row,
        f"stable_fair_price_{window_key}",
        "stable_fair_price_7d",
        f"vwap_{window_key}",
        f"average_{window_key}",
        f"rolling_average_{window_key}",
        "last_trade_price",
        "current_price",
        "latest_price",
        "quote_price",
        "mid_price",
    ) or midpoint


def _fair_price_band(row: pd.Series, window_key: str) -> tuple[float | None, float | None, float | None]:
    fair = _fair_price(row, window_key)
    if fair is None:
        return None, None, None

    spread = _first_number(row, "latest_spread", "spread")
    stable_range_pct = _first_number(row, f"stable_range_pct_{window_key}", "stable_range_pct_7d")
    volatility_band = fair * stable_range_pct / 100 * 0.35 if stable_range_pct is not None else None
    if spread is not None and spread > 0:
        half_band = spread / 2
    else:
        half_band = max(fair * 0.01, 0.001)
    if volatility_band is not None:
        half_band = max(half_band, volatility_band)
    return fair, max(0.0, fair - half_band), fair + half_band


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
    }}
    main {{
      width: min(1180px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 32px 0 48px;
    }}
    header {{
      display: flex;
      justify-content: space-between;
      gap: 24px;
      align-items: end;
      padding: 24px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(135deg, #111826 0%, #0f172a 100%);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    h1, h2, h3, p {{ margin-top: 0; }}
    h1 {{ margin-bottom: 4px; font-size: 2rem; }}
    h2 {{ margin-bottom: 12px; font-size: 1.35rem; }}
    h3 {{ margin-bottom: 8px; font-size: 1rem; }}
    section {{ margin-top: 28px; }}
    .eyebrow {{
      margin-bottom: 8px;
      color: var(--accent);
      font-size: 0.78rem;
      font-weight: 750;
      letter-spacing: 0;
      text-transform: uppercase;
    }}
    .muted {{ color: var(--muted); }}
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
      gap: 12px;
    }}
    .summary-card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-left: 4px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }}
    .summary-card-up {{ border-left-color: var(--good); }}
    .summary-card-down {{ border-left-color: var(--bad); }}
    .summary-card-neutral {{ border-left-color: var(--amber); }}
    .summary-card-info {{ border-left-color: var(--accent); }}
    .summary-card strong {{
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 1.15rem;
    }}
    .summary-card span {{ color: var(--muted); }}
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
    .liquidity-cell {{
      display: grid;
      grid-template-columns: minmax(72px, 1fr) 48px;
      align-items: center;
      gap: 8px;
      justify-content: end;
      width: 100%;
      min-width: 0;
    }}
    .liquidity-track {{
      height: 7px;
      width: 100%;
      min-width: 72px;
      border-radius: 999px;
      background: #1e293b;
      overflow: hidden;
    }}
    .liquidity-fill {{
      height: 100%;
      border-radius: inherit;
      background: linear-gradient(90deg, var(--accent), var(--good));
    }}
    .liquidity-value {{
      color: var(--text);
      font-variant-numeric: tabular-nums;
      text-align: right;
    }}
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
      font-size: 0.92rem;
    }}
    th, td {{
      padding: 9px 10px;
      border-bottom: 1px solid var(--line);
      text-align: right;
      vertical-align: top;
      white-space: normal;
    }}
    td {{
      max-width: 220px;
    }}
    tr:last-child td {{ border-bottom: 0; }}
    th {{
      background: #172a45;
      color: #e2e8f0;
      font-weight: 650;
    }}
    .table-wrap {{ overflow-x: auto; }}
    .compact-table .report-table {{
      width: max-content;
    }}
    .compact-table th, .compact-table td {{
      font-size: 0.88rem;
      max-width: none;
      white-space: nowrap;
    }}
    .compact-table th.number,
    .compact-table td.number {{
      width: 1%;
      text-align: right;
      font-variant-numeric: tabular-nums;
    }}
    .compact-table .col-commodity,
    .compact-table .col-item,
    .compact-table .col-notes,
    .compact-table .col-read,
    .compact-table .col-market-state {{
      text-align: left;
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
    .compact-table .col-liquidity {{
      width: 188px;
      min-width: 188px;
      max-width: 188px;
    }}
    .compact-table .col-spread-pct {{
      width: 76px;
      min-width: 76px;
    }}
    .compact-table .col-volume {{
      width: 80px;
      min-width: 80px;
    }}
    .flip-board {{
      width: 100%;
      overflow: visible;
    }}
    .flip-board .report-table {{
      width: 100%;
      table-layout: fixed;
    }}
    .flip-board th,
    .flip-board td {{
      padding: 12px 10px;
      max-width: none;
      text-align: left;
      overflow-wrap: break-word;
    }}
    .flip-board .col-item {{ width: 12%; }}
    .flip-board .col-verdict {{ width: 11%; }}
    .flip-board .col-entry {{ width: 16%; }}
    .flip-board .col-forecast-exit {{ width: 16%; }}
    .flip-board .col-expected-net {{ width: 11%; }}
    .flip-board .col-evidence {{ width: 16%; }}
    .flip-board .col-why {{ width: 18%; }}
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
    .flip-board .chip {{ white-space: normal; text-align: center; }}
    .flip-why {{ color: #cbd5e1; font-size: 0.82rem; line-height: 1.4; }}
    .flip-board-notice {{
      margin: 12px 0;
      padding: 12px 14px;
      border: 1px solid rgba(251, 191, 36, 0.35);
      border-left: 4px solid var(--amber);
      border-radius: 8px;
      background: rgba(66, 49, 13, 0.34);
      color: #fde68a;
    }}
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
      h1 {{ font-size: 1.65rem; }}
      th, td {{ padding: 8px; }}
    }}
    @media (max-width: 860px) {{
      .flip-board thead {{
        position: absolute;
        width: 1px;
        height: 1px;
        overflow: hidden;
        clip: rect(0 0 0 0);
        white-space: nowrap;
      }}
      .flip-board .report-table,
      .flip-board tbody {{ display: block; }}
      .flip-board tr {{
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        margin-bottom: 12px;
        overflow: hidden;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: var(--panel);
      }}
      .flip-board td,
      .flip-board .col-item,
      .flip-board .col-verdict,
      .flip-board .col-entry,
      .flip-board .col-forecast-exit,
      .flip-board .col-expected-net,
      .flip-board .col-evidence,
      .flip-board .col-why {{
        display: block;
        width: auto;
        border-bottom: 1px solid var(--line);
      }}
      .flip-board td::before {{
        display: block;
        margin-bottom: 5px;
        color: var(--muted);
        content: attr(data-label);
        font-size: 0.68rem;
        font-weight: 750;
        letter-spacing: 0.04em;
        text-transform: uppercase;
      }}
    }}
    @media (max-width: 520px) {{
      main {{ width: min(100% - 20px, 1180px); padding-top: 10px; }}
      .flip-board tr {{ grid-template-columns: 1fr; }}
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
    kind_class = "signal-table" if table_kind == "signal" else "trend-table"
    max_liquidity = _max_numeric(df["Liquidity"]) if "Liquidity" in df.columns else None
    header = "".join(
        f'<th class="{_column_classes(column)}">{escape(str(column))}</th>'
        for column in df.columns
    )
    body_rows = []
    for _, row in df.iterrows():
        cells = "".join(
            f'<td class="{_column_classes(column)}">'
            f"{_render_table_cell(column, row[column], max_liquidity=max_liquidity)}</td>"
            for column in df.columns
        )
        body_rows.append(f"<tr>{cells}</tr>")
    table = (
        '<table class="report-table">'
        f"<thead><tr>{header}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table>"
    )
    return f'<div class="table-wrap compact-table {kind_class}">{table}</div>'


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
        f'<th class="{_column_css_class(column)}" scope="col">{escape(column)}</th>'
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
            f'<td class="{_column_css_class(column)}" data-label="{escape(column)}">{cells[column]}</td>'
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


def _column_classes(column: str) -> str:
    classes = [_column_css_class(column)]
    if _is_number_column(column):
        classes.append("number")
    return " ".join(classes)


def _is_number_column(column: str) -> bool:
    label = str(column).lower()
    return (
        label in {
            "now", "latest", "min", "max", "fair", "buy", "sell", "buy ≤", "sell ≥",
            "volume", "liquidity", "spread %",
        }
        or label.endswith("trades")
        or label.endswith("momentum %")
        or label.endswith("low")
        or label.endswith("high")
        or "%" in label
        or "ask" in label
        or "bid" in label
        or label == "last"
    )


def _render_table_cell(column: str, value: object, *, max_liquidity: float | None) -> str:
    if column == "Trust":
        return _chip(str(value), _trust_tone(value))
    if column == "Market":
        return _chip(str(value), _market_tone(value))
    if column == "Market State":
        return _market_state_chips(value)
    if column == "Liquidity":
        return _liquidity_bar(value, max_liquidity=max_liquidity)
    if column == "Volume":
        number = _number(value)
        if number is None:
            return escape(_fmt_report_value(value, column=column))
        return f'<span title="{escape(_fmt(number, 0))}">{escape(_fmt_compact(number))}</span>'
    if column in {"Gap %", "Change %"} or column.endswith("Momentum %"):
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
    return " ".join(_chip(label, _market_state_tone(label)) for label in labels)


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


def _liquidity_bar(value: object, *, max_liquidity: float | None) -> str:
    number = _number(value)
    if number is None:
        return escape(_fmt(value))
    width = 0.0
    if max_liquidity is not None and max_liquidity > 0:
        width = max(4.0, min(number / max_liquidity * 100, 100.0))
    return (
        '<div class="liquidity-cell">'
        '<div class="liquidity-track">'
        f'<div class="liquidity-fill" style="width: {width:.1f}%"></div>'
        '</div>'
        f'<span class="liquidity-value" title="{escape(_fmt(number, 0))}">{escape(_fmt_compact(number))}</span>'
        '</div>'
    )


def _max_numeric(values: Iterable[object]) -> float | None:
    numbers = [number for value in values if (number := _number(value)) is not None]
    return max(numbers, default=None)


def _relative_chart_path(chart_path: str | Path | None, output_dir: Path) -> str | None:
    if chart_path is None:
        return None
    path = Path(chart_path)
    try:
        return path.relative_to(output_dir).as_posix()
    except ValueError:
        return path.as_posix()


def _swing_read(row: pd.Series) -> str:
    momentum = row.get("momentum_7d_pct")
    trades = row.get("trades_7d")

    has_momentum = momentum is not None and not pd.isna(momentum)
    has_trades = trades is not None and not pd.isna(trades) and trades > 0

    if not has_trades:
        return "Insufficient completed trades"
    if has_momentum and momentum >= 2 and has_trades:
        return "Price rose; momentum is not a sell signal"
    if has_momentum and momentum <= -2 and has_trades:
        return "Price fell; no reversal confirmed"
    if has_momentum:
        return "Little net price change"
    return "Insufficient trend history"


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
    assumptions = assumptions or FlipAssumptions()
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    display_count = len(df) if top <= 0 else top
    window_key = _window_key(metric_window)
    blocks: list[str] = []
    blocks.append(
        f"""    <header>
      <div>
        <div class="eyebrow">WarEra Market Guide</div>
        <h1>Fair Prices And Tomorrow Bias</h1>
        <p class="muted">Executable flip opportunities ranked by forecast evidence, spread, depth, fees, and estimated net margin.</p>
      </div>
      <div class="muted">Window: {escape(metric_window)}<br>Generated: {escape(generated)}</div>
    </header>
    <div class="panel assumptions">Quantity: {escape(f'{assumptions.quantity:g}')} | Horizon: {escape(f'{assumptions.forecast_horizon_hours:g}')}h |
      Fees assumed: {assumptions.fee_pct_per_side:.2f}% per side | Minimum margin: {assumptions.minimum_net_margin_pct:.2f}% |
      Max quote age: {escape(f'{assumptions.max_quote_age_minutes:g}')}m
    </div>"""
    )

    opportunity_rows: list[dict[str, object]] = []
    for _, row in df.iterrows():
        verdict = str(_present(row.get("flip_verdict"), "Unavailable"))
        reason_value = _present(row.get("flip_reason_codes"), "missing_order_book")
        codes = [code.strip() for code in str(reason_value).split(",") if code.strip()]
        reason_labels = [_FLIP_REASON_LABELS.get(code, code.replace("_", " ")) for code in codes]
        why_addenda: list[str] = []
        if "insufficient_ask_depth" in codes:
            filled = _number(row.get("flip_entry_filled_quantity")) or 0.0
            index = codes.index("insufficient_ask_depth")
            reason_labels[index] += f" ({filled:g} of {assumptions.quantity:g} available)"
        passive = _number(row.get("flip_passive_limit_price"))
        if passive is not None and verdict != "Unavailable":
            why_addenda.append(f"Limit idea — fill not estimated: {_fmt(passive)}")
        downside = _number(row.get("flip_net_margin_p10_pct"))
        if downside is not None:
            why_addenda.append(f"Downside P10 net margin: {downside:.2f}%")
        quantity = _number(row.get("flip_quantity")) or assumptions.quantity
        entry_average = _number(row.get("flip_entry_average_price"))
        entry_cost = _number(row.get("flip_total_entry_cost"))
        break_even = _number(row.get("flip_break_even_exit_vwap"))
        exit_p10 = _number(row.get("flip_forecast_exit_vwap_p10"))
        exit_median = _number(row.get("flip_forecast_exit_vwap_median"))
        exit_p90 = _number(row.get("flip_forecast_exit_vwap_p90"))
        margin = _number(row.get("flip_net_margin_median_pct"))
        profit = _number(row.get("flip_net_profit_median"))
        forecast_samples = int(_number(row.get("flip_forecast_samples")) or 0)
        execution_sample_value = _number(row.get("forecast_execution_evaluable_samples"))
        quote_age = _number(row.get("flip_quote_age_minutes"))

        evidence = str(_present(
            row.get("flip_forecast_evidence"),
            _present(row.get("forecast_evidence"), "Insufficient"),
        ))
        why = ". ".join([*reason_labels, *why_addenda])

        opportunity_rows.append({
            "Item": row.get("item_name", "Unknown"), "Verdict": verdict,
            "Evidence": evidence,
            "Why": why,
            "Qty": quantity,
            "Break-even Exit VWAP": row.get("flip_break_even_exit_vwap"),
            "Median Net Margin %": row.get("flip_net_margin_median_pct"),
            "Quote Age": quote_age,
            "_codes": codes,
            "_reason_labels": reason_labels,
            "_why_addenda": why_addenda,
            "_entry_average": entry_average,
            "_entry_cost": entry_cost,
            "_exit_p10": exit_p10,
            "_exit_median": exit_median,
            "_exit_p90": exit_p90,
            "_margin": margin,
            "_profit": profit,
            "_p10": _number(row.get("flip_net_margin_p10_pct")),
            "_samples": forecast_samples,
            "_execution_samples": execution_sample_value,
        })
    verdict_order = {"Potential flip": 0, "Watch": 1, "No trade": 2, "Unavailable": 3}
    opportunity_rows.sort(key=lambda row: (
        verdict_order.get(str(row["Verdict"]), 4),
        -float(row["_margin"]) if row["_margin"] is not None else float("inf"),
        -float(row["_samples"]), str(row["Item"]),
    ))
    opportunity_rows = opportunity_rows[:display_count]
    show_entry = any(
        row.get("_entry_average") is not None
        or row.get("_entry_cost") is not None
        or _number(row.get("Break-even Exit VWAP")) is not None
        for row in opportunity_rows
    )
    show_forecast = any(
        row.get("_exit_p10") is not None
        or row.get("_exit_median") is not None
        or row.get("_exit_p90") is not None
        for row in opportunity_rows
    )
    show_net = any(
        row.get("_margin") is not None or row.get("_profit") is not None
        for row in opportunity_rows
    )
    has_execution_gap = bool(opportunity_rows) and all(
        "missing_execution_interval" in row["_codes"] for row in opportunity_rows
    )
    execution_notice = ""
    if has_execution_gap:
        execution_notice = (
            f'<div class="flip-board-notice"><strong>No quantity-{assumptions.quantity:g} flip can be evaluated yet.</strong> '
            'Historical order books do not contain enough fully executable entry/exit observations at that size.</div>'
        )
    flip_board = _flip_board_html(
        opportunity_rows,
        show_entry=show_entry,
        show_forecast=show_forecast,
        show_net=show_net,
        suppress_execution_reason=has_execution_gap,
    )
    blocks.append(f"""    <section>
      <h2>Flip Board</h2>
      <p class="muted">Only fully executable, fresh, supported opportunities are ranked as Potential flip. Entry combines quantity, ask VWAP, cost, and break-even; forecast exit combines median and P10–P90.</p>
      {execution_notice}
      {flip_board}
    </section>""")

    potential = [row for row in opportunity_rows if row["Verdict"] == "Potential flip"]
    def card(
        title: str,
        selected: dict[str, object] | None,
        detail: str,
        *,
        tone: str,
        icon: str,
        empty_detail: str,
    ) -> str:
        name = str(selected["Item"]) if selected else "No supported opportunity"
        value = detail.format(**selected) if selected else empty_detail
        return (
            f'<article class="summary-card summary-card-{tone}">'
            f'<span>{escape(title)}</span>'
            f'<strong><span class="summary-arrow" aria-hidden="true">{escape(icon)}</span>{escape(name)}</strong>'
            f'<span>{escape(value)}</span></article>'
        )
    margin_candidates = [row for row in potential if row["_margin"] is not None]
    break_even_candidates = [row for row in potential if _number(row["Break-even Exit VWAP"]) is not None]
    downside_candidates = [row for row in potential if row["_p10"] is not None]
    best_margin = max(margin_candidates, key=lambda row: float(row["_margin"]), default=None)
    lowest_break_even = min(
        break_even_candidates,
        key=lambda row: float(row["Break-even Exit VWAP"]),
        default=None,
    )
    best_downside = max(downside_candidates, key=lambda row: float(row["_p10"]), default=None)
    problem = next((row for row in opportunity_rows if row["Verdict"] in {"No trade", "Unavailable"}), None)
    blocks.append("    <section><div class=\"summary-grid\">" +
        card("Best Supported Net Margin", best_margin, "{Median Net Margin %:.2f}% median", tone="up", icon="↗", empty_detail="No positive, supported margin") +
        card("Lowest Break-even Exit", lowest_break_even, "{Break-even Exit VWAP:.3f} exit VWAP", tone="info", icon="↓", empty_detail="Break-even evidence unavailable") +
        card("Best Downside Profile", best_downside, "P10 margin {_p10:.2f}%", tone="up", icon="◇", empty_detail="Downside evidence unavailable") +
        card("Avoid / Data Problem", problem, "{Why}", tone="down", icon="!", empty_detail="No blocked or rejected row") + "</div></section>")

    blocks.append(f"""    <section>
      <h2>How to read the Flip Board</h2>
      <div class="panel"><p>Forecasts are historical estimates, not guarantees. Fees are assumptions supplied by the user. Order-book execution is estimated from a snapshot that may change. Passive limit orders may not fill. <strong>Potential flip</strong> means the configured rules passed, not that profit is certain.</p>
      <p>Entry uses an ask sweep for quantity {assumptions.quantity:g}; historical exits use same-quantity bid sweeps. Crossing cost and slippage are already represented in those executions and are not subtracted twice.</p></div>
    </section>""")

    trend_candidates = df.copy()
    if not trend_candidates.empty:
        change_col = _column(df, f"percent_change_{window_key}", "momentum_7d_pct")
        volume_col = _column(df, f"volume_{window_key}", f"traded_quantity_{window_key}")
        trades_col = _column(df, f"trade_count_{window_key}", "trades_7d")
        liquidity_col = _column(df, f"liquidity_{window_key}")
        sort_cols = [col for col in [liquidity_col, volume_col] if col]
        if sort_cols:
            trend_candidates = trend_candidates.sort_values(sort_cols, ascending=False, na_position="last")
        view = trend_candidates.head(display_count).copy()
        table_columns: list[tuple[str, str]] = [
            ("item_name", "Item"),
            (_column(df, "latest_price", "current_price") or "", "Latest"),
            (change_col or "", "Change %"),
            (_column(df, f"min_{window_key}", "low_7d") or "", "Min"),
            (_column(df, f"max_{window_key}", "high_7d") or "", "Max"),
            (_column(df, f"stable_fair_price_{window_key}", "stable_fair_price_7d", f"vwap_{window_key}") or "", "Fair"),
            (volume_col or "", "Volume"),
            (trades_col or "", "Trades"),
            (_column(df, "latest_spread_pct", f"average_spread_pct_{window_key}", "spread_pct") or "", "Spread %"),
            (liquidity_col or "", "Liquidity"),
            (_column(df, f"tendency_labels_{window_key}", f"tendency_{window_key}") or "", "Market State"),
        ]
        selected = [(source, label) for source, label in table_columns if source and source in view.columns]
        trend_table = view[[source for source, _ in selected]].rename(columns=dict(selected))
        blocks.append(
            f"""    <section>
      <h2>Market Trends</h2>
      <p class="muted">Compact history view: prices, traded volume, completed trades, spread, relative liquidity, and market state.</p>
      {_compact_table_html(trend_table, table_kind="trend")}
    </section>"""
        )

    swing_price_columns = [
        column for column in ["bid", "ask", "current_price", "latest_price", "low_7d", "high_7d"]
        if column in df.columns
    ]
    swing_candidates = df[df[swing_price_columns].notna().any(axis=1)].copy() if swing_price_columns else pd.DataFrame()
    if not swing_candidates.empty:
        trend = swing_candidates.copy()
        if not trend.empty:
            if "momentum_7d_pct" not in trend.columns:
                trend["momentum_7d_pct"] = None
            if "trades_7d" not in trend.columns:
                trend["trades_7d"] = None
            trend["abs_momentum"] = pd.to_numeric(trend["momentum_7d_pct"], errors="coerce").abs()
            trend = trend.sort_values(
                ["abs_momentum", "trades_7d"],
                ascending=[False, False],
                na_position="last",
            ).head(display_count)
            trend["swing_read"] = trend.apply(_swing_read, axis=1)
            last_col = _column(trend, "current_price", "latest_price")
            columns = [
                column for column in [
                    "item_name", "ask", "bid", last_col, "low_7d", "high_7d",
                    "crossing_loss_pct", "momentum_7d_pct", "trades_7d", "swing_read"
                ]
                if column and column in trend.columns
            ]
            trend_table = trend[columns].rename(columns={
                "item_name": "Item",
                "ask": "Ask (You Pay)",
                "bid": "Bid (You Receive)",
                "current_price": "Last",
                "latest_price": "Last",
                "low_7d": f"{metric_window} Low",
                "high_7d": f"{metric_window} High",
                "crossing_loss_pct": "Crossing Cost %",
                "momentum_7d_pct": f"{metric_window} Momentum %",
                "trades_7d": f"{metric_window} Trades",
                "swing_read": "Read",
            })
            blocks.append(
                f"""    <section>
      <h2>Price Evolution Lens</h2>
      <p class="muted">Ask is what you pay; bid is what you receive. Crossing Cost is the buy-at-ask, sell-at-bid cost before fees. Momentum describes history, not a trade recommendation.</p>
      {_compact_table_html(trend_table, table_kind="trend")}
    </section>"""
            )

    note_rows = df.head(display_count).copy()
    if not note_rows.empty:
        notes: list[str] = []
        for _, row in note_rows.iterrows():
            fair, buy_below, sell_above = _fair_price_band(row, window_key)
            volume = _first_number(row, f"volume_{window_key}", f"traded_quantity_{window_key}")
            trades = _first_number(row, f"trade_count_{window_key}", "trades_7d")
            liquidity = _first_number(row, f"liquidity_{window_key}")
            detail_items = [
                f"<li>Historical fair context: <strong>{escape(_fmt(fair))}</strong></li>",
                f"<li>Bid / Ask: <strong>{escape(_fmt(row.get('bid')))} / {escape(_fmt(row.get('ask')))}</strong></li>",
                f"<li>{escape(metric_window)} volume / trades: <strong>{escape(_fmt(volume, 0))} / {escape(_fmt(trades, 0))}</strong></li>",
                f"<li>Liquidity depth score: <strong>{escape(_fmt(liquidity, 1))}</strong></li>",
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
