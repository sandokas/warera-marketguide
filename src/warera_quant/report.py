from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Iterable

import pandas as pd

from .metrics import MarketMetrics


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
        if any(token in label for token in ("volume", "trade")) or label in {"liquidity"}:
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


def _latest_price(row: pd.Series) -> float | None:
    return _first_number(row, "last_trade_price", "current_price", "latest_price", "quote_price")


def _price_gap_pct(latest: float | None, fair: float | None) -> float | None:
    if latest is None or fair is None or fair <= 0:
        return None
    return (latest - fair) / fair * 100


def _market_quality(row: pd.Series, window_key: str) -> tuple[str, list[str]]:
    trades = _first_number(row, f"trade_count_{window_key}", "trades_7d") or 0.0
    volume = _first_number(row, f"volume_{window_key}", f"traded_quantity_{window_key}") or 0.0
    stable_range_pct = _first_number(row, f"stable_range_pct_{window_key}", "stable_range_pct_7d")
    spread_pct = _first_number(row, "latest_spread_pct", f"average_spread_pct_{window_key}", "spread_pct")
    reasons: list[str] = []
    if trades < 3 or volume <= 0:
        reasons.append("few trades")
    if spread_pct is not None and spread_pct >= 8:
        reasons.append("wide bid/ask gap")
    if stable_range_pct is not None and stable_range_pct >= 18:
        reasons.append("price swings are large")

    if trades >= 10 and volume > 0 and (spread_pct is None or spread_pct <= 4) and (
        stable_range_pct is None or stable_range_pct <= 12
    ):
        return "strong", reasons
    if trades >= 3 and volume > 0 and (stable_range_pct is None or stable_range_pct <= 18):
        return "usable", reasons
    return "weak", reasons


def _plain_action(
    latest: float | None,
    buy_below: float | None,
    sell_above: float | None,
    fair: float | None,
    quality: str = "usable",
) -> str:
    if latest is None or buy_below is None or sell_above is None or fair is None:
        return "Check live market"
    if latest <= buy_below:
        if quality == "weak":
            return "Possible buy; confirm depth"
        return "Good buy zone"
    if latest >= sell_above:
        if quality == "weak":
            return "Possible sell; confirm bids"
        return "Good sell zone"
    gap_pct = _price_gap_pct(latest, fair)
    if gap_pct is not None:
        if gap_pct <= -1:
            return "Slightly cheap; watch buys"
        if gap_pct >= 1:
            return "Slightly rich; watch sells"
    return "Fair; balanced"


def _tomorrow_bias(row: pd.Series, window_key: str, fair: float | None) -> tuple[str, str, str]:
    latest = _latest_price(row)
    gap_pct = _price_gap_pct(latest, fair)
    momentum = _first_number(row, f"percent_change_{window_key}", "momentum_7d_pct")
    trades = _first_number(row, f"trade_count_{window_key}", "trades_7d") or 0.0
    range_pct = _first_number(row, f"stable_range_pct_{window_key}", "stable_range_pct_7d", f"range_pct_{window_key}", "range_pct") or 0.0
    spread_pct = _first_number(row, "latest_spread_pct", f"average_spread_pct_{window_key}", "spread_pct")
    tendency = str(row.get(f"tendency_labels_{window_key}") or row.get(f"tendency_{window_key}") or "")
    quality, quality_reasons = _market_quality(row, window_key)

    score = 0
    reasons: list[str] = []
    if momentum is not None:
        if momentum >= 6:
            score += 2
            reasons.append("price rose strongly")
        elif momentum >= 1:
            score += 1
            reasons.append("price is rising")
        elif momentum <= -6:
            score -= 2
            reasons.append("price fell strongly")
        elif momentum <= -1:
            score -= 1
            reasons.append("price is falling")

    if gap_pct is not None:
        if gap_pct <= -2:
            score += 1
            reasons.append("below fair value")
        elif gap_pct >= 2:
            score -= 1
            reasons.append("above fair value")

    if "Rising" in tendency:
        score += 1
        reasons.append("history points up")
    elif "Falling" in tendency:
        score -= 1
        reasons.append("history points down")

    if score >= 1:
        direction = "Up"
    elif score <= -1:
        direction = "Down"
    else:
        direction = "No clear move"

    if quality == "strong" and abs(score) >= 2 and range_pct <= 12:
        confidence = "Medium"
    elif quality in {"strong", "usable"} and abs(score) >= 1:
        confidence = "Low-medium"
    else:
        confidence = "Low"
    if spread_pct is not None and spread_pct >= 8 and confidence == "Medium":
        confidence = "Low-medium"

    if not reasons:
        reasons.append("no clear edge")
    if quality == "weak":
        reasons = [*quality_reasons, *reasons]
    else:
        reasons.extend(reason for reason in quality_reasons if reason not in reasons)
    return direction, confidence, "; ".join(reasons[:2])


def _trade_signal(
    *,
    latest: float | None,
    buy_below: float | None,
    sell_above: float | None,
    expected_move: str,
    quality: str,
) -> str:
    if latest is None or buy_below is None or sell_above is None:
        return "Check market"
    if quality == "weak":
        return "Check depth"
    if latest <= buy_below and expected_move != "Down":
        return "Buy"
    if latest >= sell_above and expected_move != "Up":
        return "Sell"
    if expected_move == "Up":
        return "Hold"
    if expected_move == "Down":
        return "Wait"
    return "Hold"


def _next_step_note(
    *,
    signal: str,
    buy_below: float | None,
    sell_above: float | None,
    reason: str,
) -> str:
    if signal == "Buy":
        return "Already at buy zone"
    if signal == "Sell":
        return "Already at sell zone"
    if signal == "Hold" and sell_above is not None:
        return f"Sell near {_fmt(sell_above)}"
    if signal == "Wait" and buy_below is not None:
        return f"Buy near {_fmt(buy_below)}"
    if signal == "Check depth":
        return reason
    if buy_below is not None and sell_above is not None:
        return f"Trade near {_fmt(buy_below)}-{_fmt(sell_above)}"
    return reason


def _signal_summary(row: pd.Series) -> str:
    parts = [
        f"momentum {_fmt(row.get('momentum_7d_pct'), 2)}%",
        f"trades {_fmt(row.get('trades_7d'), 0)}",
        f"stable range {_fmt(row.get('stable_range_pct_7d', row.get('range_pct')), 2)}%",
        f"spread {_fmt(row.get('spread_pct'), 2)}%",
    ]
    return ", ".join(parts)


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
        label in {"now", "latest", "min", "max", "fair", "buy", "sell", "volume", "liquidity", "spread %"}
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
    if column == "Signal":
        return _chip(str(value), _signal_tone(value))
    if column == "Expected Move":
        return _chip(str(value), _move_tone(value), prefix=_move_arrow(value))
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


def _signal_tone(value: object) -> str:
    normalized = str(value).strip().lower()
    if normalized == "buy":
        return "buy"
    if normalized == "sell":
        return "sell"
    if normalized == "wait":
        return "wait"
    if normalized == "check depth" or normalized == "check market":
        return "check"
    return "hold"


def _move_tone(value: object) -> str:
    normalized = str(value).strip().lower()
    if normalized == "up":
        return "up"
    if normalized == "down":
        return "down"
    return "flat"


def _move_arrow(value: object) -> str:
    normalized = str(value).strip().lower()
    if normalized == "up":
        return "&uarr;"
    if normalized == "down":
        return "&darr;"
    return "&rarr;"


def _summary_card_class(value: object) -> str:
    tone = _move_tone(value)
    if tone == "up":
        return "summary-card-up"
    if tone == "down":
        return "summary-card-down"
    return "summary-card-neutral"


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
    spread = row.get("spread_pct")
    trades = row.get("trades_7d")

    has_momentum = momentum is not None and not pd.isna(momentum)
    has_spread = spread is not None and not pd.isna(spread)
    has_trades = trades is not None and not pd.isna(trades) and trades > 0

    if has_momentum and momentum >= 2 and has_trades:
        return "Price is high; consider selling"
    if has_momentum and momentum <= -2 and has_trades:
        return "Price is low; consider buying"
    if has_spread and spread >= 1 and has_trades:
        return "Wide bid/ask; use limit orders"
    if has_momentum:
        return "Quiet market; trade near the range edges"
    return "Check live order book"


def generate_html_report(
    df: pd.DataFrame,
    *,
    top: int = 0,
    metric_window: str = "7D",
    chart_path: str | Path | None = None,
    chart_label: str | None = None,
    output_dir: str | Path = ".",
) -> str:
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    display_count = len(df) if top <= 0 else top
    swing_count = len(df) if top <= 0 else top
    trend_count = min(swing_count, 10)
    window_key = _window_key(metric_window)

    blocks: list[str] = []
    blocks.append(
        f"""    <header>
      <div>
        <div class="eyebrow">WarEra Market Guide</div>
        <h1>Fair Prices And Tomorrow Bias</h1>
        <p class="muted">A practical read on what is cheap, what is expensive, and which way prices may lean next.</p>
      </div>
      <div class="muted">Window: {escape(metric_window)}<br>Generated: {escape(generated)}</div>
    </header>"""
    )

    chart_src = _relative_chart_path(chart_path, Path(output_dir))
    if chart_src:
        chart_heading = "Featured Price History"
        if chart_label:
            chart_heading = f"Featured Price History: {chart_label}"
        blocks.append(
            f"""    <section>
      <h2>{escape(chart_heading)}</h2>
      <img class="chart" src="{escape(chart_src)}" alt="Featured market history chart">
    </section>"""
        )

    fair_candidates = df.copy()
    if not fair_candidates.empty:
        fair_rows = []
        for _, row in fair_candidates.iterrows():
            fair, buy_below, sell_above = _fair_price_band(row, window_key)
            latest = _latest_price(row)
            gap_pct = _price_gap_pct(latest, fair)
            direction, confidence, reason = _tomorrow_bias(row, window_key, fair)
            quality, _ = _market_quality(row, window_key)
            signal = _trade_signal(
                latest=latest,
                buy_below=buy_below,
                sell_above=sell_above,
                expected_move=direction,
                quality=quality,
            )
            next_step = _next_step_note(
                signal=signal,
                buy_below=buy_below,
                sell_above=sell_above,
                reason=reason,
            )
            fair_rows.append({
                "item_name": row.get("item_name"),
                "latest_price": latest,
                "fair_price": fair,
                "buy_below": buy_below,
                "sell_above": sell_above,
                "gap_pct": gap_pct,
                "signal": signal,
                "action": _plain_action(latest, buy_below, sell_above, fair, quality),
                "tomorrow_bias": direction,
                "confidence": confidence,
                "why": next_step,
                "market_quality": quality.title(),
                "latest_bid": row.get("latest_bid", row.get("bid")),
                "latest_ask": row.get("latest_ask", row.get("ask")),
                "tendency": row.get(f"tendency_labels_{window_key}"),
            })
        fair_view = pd.DataFrame(fair_rows)
        fair_view = fair_view[fair_view["fair_price"].notna()].copy()
        if not fair_view.empty:
            summary_view = fair_view.copy()
            cheap = summary_view[summary_view["gap_pct"].notna() & (summary_view["gap_pct"] <= -1)].sort_values("gap_pct").head(1)
            expensive = summary_view[summary_view["gap_pct"].notna() & (summary_view["gap_pct"] >= 1)].sort_values("gap_pct", ascending=False).head(1)
            likely_up = summary_view[summary_view["tomorrow_bias"] == "Up"].head(1)
            likely_down = summary_view[summary_view["tomorrow_bias"] == "Down"].head(1)

            def summary_card(title: str, row_df: pd.DataFrame, fallback: str) -> str:
                if row_df.empty:
                    return (
                        '<div class="summary-card summary-card-neutral">'
                        f'<span>{escape(title)}</span>'
                        f'<strong><span class="summary-arrow">&rarr;</span>{escape(fallback)}</strong>'
                        '</div>'
                    )
                row = row_df.iloc[0]
                gap = _fmt(row.get("gap_pct"), 2)
                move = str(row.get("tomorrow_bias") or "No clear move")
                card_class = _summary_card_class(move)
                arrow = _move_arrow(move)
                return (
                    f'<div class="summary-card {card_class}"><span>{escape(title)}</span>'
                    f'<strong><span class="summary-arrow">{arrow}</span>{escape(str(row.get("item_name")))}</strong>'
                    f'<span>{escape(str(row.get("signal")))}; {escape(str(row.get("tomorrow_bias")))}; gap {escape(gap)}%</span></div>'
                )

            blocks.append(
                """    <section>
      <div class="summary-grid">"""
                + summary_card("Cheapest vs fair", cheap, "No clear discount")
                + summary_card("Richest vs fair", expensive, "No clear premium")
                + summary_card("Best upside bias", likely_up, "No clear up bias")
                + summary_card("Best downside bias", likely_down, "No clear down bias")
                + """</div>
    </section>"""
            )

            fair_view = fair_view.sort_values(["gap_pct", "item_name"], na_position="last").head(display_count)
            table = fair_view[[
                "item_name", "latest_price", "fair_price", "buy_below", "sell_above", "gap_pct",
                "signal", "tomorrow_bias", "confidence", "market_quality", "why"
            ]].rename(columns={
                "item_name": "Commodity",
                "latest_price": "Now",
                "fair_price": "Fair",
                "buy_below": "Buy",
                "sell_above": "Sell",
                "gap_pct": "Gap %",
                "signal": "Signal",
                "tomorrow_bias": "Expected Move",
                "confidence": "Trust",
                "market_quality": "Market",
                "why": "Notes",
            })
            blocks.append(
                f"""    <section>
      <h2>What To Pay And What To Expect</h2>
      <p class="muted">Buy below the buy line, sell above the sell line, and treat thin or volatile markets as confirm-live-depth signals.</p>
      {_compact_table_html(table, table_kind="signal")}
    </section>"""
            )

    trend_candidates = df.copy()
    if not trend_candidates.empty:
        change_col = _column(df, f"percent_change_{window_key}", "momentum_7d_pct")
        volume_col = _column(df, f"volume_{window_key}", f"traded_quantity_{window_key}")
        trades_col = _column(df, f"trade_count_{window_key}", "trades_7d")
        liquidity_col = _column(df, f"liquidity_{window_key}")
        sort_cols = [col for col in [liquidity_col, volume_col] if col]
        if sort_cols:
            trend_candidates = trend_candidates.sort_values(sort_cols, ascending=False, na_position="last")
        view = trend_candidates.head(swing_count).copy()
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

    signal_items: list[str] = []

    momentum = df[df["momentum_7d_pct"].notna()].copy()
    if not momentum.empty:
        winners = momentum[momentum["momentum_7d_pct"] > 0].sort_values(
            ["momentum_7d_pct", "trades_7d"], ascending=[False, False]
        ).head(3)
        losers = momentum[momentum["momentum_7d_pct"] < 0].sort_values(
            ["momentum_7d_pct", "trades_7d"], ascending=[True, False]
        ).head(3)

        winner_items = []
        if winners.empty:
            winner_items.append("<li>None with positive momentum.</li>")
        else:
            for _, row in winners.iterrows():
                winner_items.append(f"<li><strong>{escape(str(row['item_name']))}</strong>: {escape(_signal_summary(row))}</li>")

        loser_items = []
        if losers.empty:
            loser_items.append("<li>None with negative momentum.</li>")
        else:
            for _, row in losers.iterrows():
                loser_items.append(f"<li><strong>{escape(str(row['item_name']))}</strong>: {escape(_signal_summary(row))}</li>")
        signal_items.append(
            '<div class="grid">'
            f'<div><h3>Biggest winners</h3><ul>{"".join(winner_items)}</ul></div>'
            f'<div><h3>Biggest losers</h3><ul>{"".join(loser_items)}</ul></div>'
            "</div>"
        )

    blocks.append(
        """    <section>
      <h2>Trend Highlights</h2>
      <div class="panel">""" + "\n".join(signal_items) + """</div>
    </section>"""
    )

    blocks.append(
        f"""    <section>
      <h2>Reading The Signals</h2>
      <div class="grid">
        <div class="panel">
          <h3>Fair prices</h3>
          <ul>
            <li><strong>Fair:</strong> the report's best estimate of a normal price, smoothed so one odd trade matters less.</li>
            <li><strong>Buy Below / Sell Above:</strong> suggested limit prices around Fair, widened when the market is jumpy.</li>
            <li><strong>Use for:</strong> quick limit-order levels before checking live depth.</li>
          </ul>
        </div>
        <div class="panel">
          <h3>Tomorrow bias</h3>
          <ul>
            <li><strong>Expected Move:</strong> Up means prices may rise, Down means prices may fall, No clear move means mixed evidence.</li>
            <li><strong>Signal:</strong> Buy, Sell, Hold, Wait, or Check depth based on price, direction, and market quality.</li>
            <li><strong>Trust:</strong> higher when trades, spread, volume, and price stability support the signal.</li>
          </ul>
        </div>
        <div class="panel">
          <h3>Market state</h3>
          <ul>
            <li><strong>Rising/Falling:</strong> recent trades moved mostly one way.</li>
            <li><strong>Range-bound/Volatile:</strong> prices are staying tight or swinging widely.</li>
            <li><strong>Thin/Stable:</strong> there are few trades, or enough activity to trust the prices more.</li>
          </ul>
        </div>
      </div>
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
            trend["abs_momentum"] = pd.to_numeric(trend["momentum_7d_pct"], errors="coerce").abs()
            trend = trend.sort_values(
                ["abs_momentum", "trades_7d"],
                ascending=[False, False],
                na_position="last",
            ).head(trend_count)
            trend["swing_read"] = trend.apply(_swing_read, axis=1)
            last_col = _column(trend, "current_price", "latest_price")
            columns = [
                column for column in [
                    "item_name", "ask", "bid", last_col, "low_7d", "high_7d",
                    "momentum_7d_pct", "trades_7d", "swing_read"
                ]
                if column and column in trend.columns
            ]
            trend_table = trend[columns].rename(columns={
                "item_name": "Item",
                "ask": "Buy Ask",
                "bid": "Sell Bid",
                "current_price": "Last",
                "latest_price": "Last",
                "low_7d": f"{metric_window} Low",
                "high_7d": f"{metric_window} High",
                "momentum_7d_pct": f"{metric_window} Momentum %",
                "trades_7d": f"{metric_window} Trades",
                "swing_read": "Read",
            })
            blocks.append(
                f"""    <section>
      <h2>Price Evolution Lens</h2>
      <p class="muted">Price-first view of bid, ask, last price, range, change, and trade count.</p>
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
                f"<li>Fair / Buy below / Sell above: <strong>{escape(_fmt(fair))} / {escape(_fmt(buy_below))} / {escape(_fmt(sell_above))}</strong></li>",
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

    return _html_page("WarEra Market Guide", "\n".join(blocks))


def write_outputs(
    df: pd.DataFrame,
    output_dir: str | Path,
    *,
    top: int = 10,
    metric_window: str = "7D",
    chart_path: str | Path | None = None,
    chart_label: str | None = None,
) -> tuple[Path, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    trends_csv_path = out / "market_trends.csv"
    scores_csv_path = out / "market_scores.csv"
    html_path = out / "market_report.html"
    df.to_csv(trends_csv_path, index=False)
    df.to_csv(scores_csv_path, index=False)
    html_path.write_text(
        generate_html_report(
            df,
            top=top,
            metric_window=metric_window,
            chart_path=chart_path,
            chart_label=chart_label,
            output_dir=out,
        ),
        encoding="utf-8",
    )
    return trends_csv_path, html_path
