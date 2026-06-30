from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Iterable

import pandas as pd

from .metrics import MarketMetrics


def metrics_to_dataframe(metrics: Iterable[MarketMetrics]) -> pd.DataFrame:
    df = pd.DataFrame([asdict(m) for m in metrics])
    if "trading_attractiveness" in df.columns:
        df = df.sort_values("trading_attractiveness", ascending=False, na_position="last")
    return df


def _fmt(value: object, decimals: int = 3) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    if isinstance(value, (int, float)):
        return f"{value:.{decimals}f}"
    return str(value)


def _signal_summary(row: pd.Series, *, include_score: bool = False) -> str:
    parts = [
        f"momentum {_fmt(row.get('momentum_7d_pct'), 2)}%",
        f"trades {_fmt(row.get('trades_7d'), 0)}",
        f"range {_fmt(row.get('range_pct'), 2)}%",
        f"spread {_fmt(row.get('spread_pct'), 2)}%",
    ]
    if include_score:
        parts.append(f"score {_fmt(row.get('trading_attractiveness'), 1)}")
    return ", ".join(parts)


def _html_page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --text: #17202a;
      --muted: #5f6b7a;
      --line: #d9dee7;
      --accent: #2563eb;
      --good: #138a44;
      --bad: #c24135;
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
      padding-bottom: 20px;
      border-bottom: 1px solid var(--line);
    }}
    h1, h2, h3, p {{ margin-top: 0; }}
    h1 {{ margin-bottom: 4px; font-size: 2rem; }}
    h2 {{ margin-bottom: 12px; font-size: 1.35rem; }}
    h3 {{ margin-bottom: 8px; font-size: 1rem; }}
    section {{ margin-top: 28px; }}
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
    ul {{ margin: 0; padding-left: 1.2rem; }}
    li + li {{ margin-top: 6px; }}
    code {{
      display: inline-block;
      padding: 3px 6px;
      border-radius: 6px;
      background: #eef2f7;
      color: #1f2937;
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
      white-space: nowrap;
    }}
    th:first-child, td:first-child,
    th:nth-child(2), td:nth-child(2),
    td:last-child {{ text-align: left; }}
    tr:last-child td {{ border-bottom: 0; }}
    th {{
      background: #eef2f7;
      color: #334155;
      font-weight: 650;
    }}
    .table-wrap {{ overflow-x: auto; }}
    .chart {{
      width: 100%;
      max-height: 720px;
      object-fit: contain;
      background: #fff;
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
    .warning {{ border-left: 4px solid var(--bad); }}
  </style>
</head>
<body>
  <main>
{body}
  </main>
</body>
</html>
"""


def _table_html(df: pd.DataFrame) -> str:
    table = df.to_html(
        index=False,
        escape=True,
        border=0,
        classes="report-table",
        float_format=lambda value: f"{value:.3f}",
    )
    return '<div class="table-wrap">' + table + "</div>"


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
        return "More expensive; sell into bids"
    if has_momentum and momentum <= -2 and has_trades:
        return "Cheaper; consider stockpiling"
    if has_spread and spread >= 1 and has_trades:
        return "Wide bid/ask; use limit orders"
    if has_momentum:
        return "Stable; buy near low, sell near high"
    return "Check live order book"


def generate_html_report(
    df: pd.DataFrame,
    *,
    top: int = 10,
    metric_window: str = "7D",
    chart_path: str | Path | None = None,
    chart_label: str | None = None,
    output_dir: str | Path = ".",
) -> str:
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    scored = df[df["trading_attractiveness"].notna()].copy()
    display_count = len(scored) if top <= 0 else top
    swing_count = len(df) if top <= 0 else top
    trend_count = min(swing_count, 10)

    blocks: list[str] = []
    blocks.append(
        f"""    <header>
      <div>
        <h1>WarEra Quantitative Market Report</h1>
        <p class="muted">Generated: {escape(generated)}</p>
      </div>
      <div class="muted">Window: {escape(metric_window)}</div>
    </header>"""
    )

    chart_src = _relative_chart_path(chart_path, Path(output_dir))
    if chart_src:
        chart_heading = "Featured Trade Chart"
        if chart_label:
            chart_heading = f"Featured Trade Chart: {chart_label}"
        blocks.append(
            f"""    <section>
      <h2>{escape(chart_heading)}</h2>
      <img class="chart" src="{escape(chart_src)}" alt="Featured trade candlestick chart">
    </section>"""
        )

    signal_items: list[str] = []

    if scored.empty:
        signal_items.append("<p>No ranked market-making signals available.</p>")
    else:
        items = []
        for _, row in scored.head(3).iterrows():
            items.append(f"<li><strong>{escape(str(row['item_name']))}</strong>: {escape(_signal_summary(row, include_score=True))}</li>")
        signal_items.append("<h3>Liquidity plays</h3><ul>" + "".join(items) + "</ul>")

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
      <h2>Day Trade Signals</h2>
      <div class="panel">""" + "\n".join(signal_items) + """</div>
    </section>"""
    )

    blocks.append(
        f"""    <section>
      <h2>Reading The Signals</h2>
      <div class="grid">
        <div class="panel">
          <h3>Market-making score</h3>
          <p><code>Trading Attractiveness = (Effective Spread % x {escape(metric_window)} Trades) / {escape(metric_window)} Range %</code></p>
          <ul>
            <li><strong>Good:</strong> high effective spread, high trades, low range.</li>
            <li><strong>Avoid:</strong> one-tick spreads, low trades, wide ranges.</li>
            <li><strong>Use for:</strong> placing buy/sell orders where capital can rotate.</li>
          </ul>
        </div>
        <div class="panel">
          <h3>Swing signal</h3>
          <ul>
            <li><strong>Good:</strong> strong {escape(metric_window)} momentum with enough trades to exit.</li>
            <li><strong>Risk:</strong> strong momentum with high {escape(metric_window)} range can mean a late or unstable move.</li>
            <li><strong>Use for:</strong> trend scouting, not passive market making.</li>
          </ul>
        </div>
      </div>
      <p class="muted">Effective spread subtracts the minimum price tick from the raw bid/ask gap. A one-tick gap is treated as non-exploitable.</p>
    </section>"""
    )

    if scored.empty:
        top_opportunities = "<p>No items had enough data to calculate a score.</p>"
    else:
        view = scored.head(display_count).copy()
        view.insert(0, "rank", range(1, len(view) + 1))
        table = view[[
            "rank", "item_name", "bid", "ask", "spread_pct", "trades_7d",
            "range_pct", "momentum_7d_pct", "trading_attractiveness"
        ]].rename(columns={
            "rank": "Rank",
            "item_name": "Item",
            "bid": "Bid",
            "ask": "Ask",
            "spread_pct": "Effective Spread %",
            "trades_7d": f"{metric_window} Trades",
            "range_pct": f"{metric_window} Range %",
            "momentum_7d_pct": f"{metric_window} Momentum %",
            "trading_attractiveness": "Score",
        })
        top_opportunities = _table_html(table)

    blocks.append(
        f"""    <section>
      <h2>Top Opportunities</h2>
      {top_opportunities}
    </section>"""
    )

    swing_price_columns = ["bid", "ask", "current_price", "low_7d", "high_7d"]
    swing_candidates = df[df[swing_price_columns].notna().any(axis=1)].copy()
    if not swing_candidates.empty:
        trend = swing_candidates.copy()
        if not trend.empty:
            trend["abs_momentum"] = pd.to_numeric(trend["momentum_7d_pct"], errors="coerce").abs()
            trend = trend.sort_values(
                ["abs_momentum", "trades_7d"],
                ascending=[False, False],
                na_position="last",
            ).head(trend_count)
            trend.insert(0, "rank", range(1, len(trend) + 1))
            trend["swing_read"] = trend.apply(_swing_read, axis=1)
            trend_table = trend[[
                "rank", "item_name", "ask", "bid", "current_price", "low_7d", "high_7d",
                "momentum_7d_pct", "trades_7d", "swing_read"
            ]].rename(columns={
                "rank": "Rank",
                "item_name": "Item",
                "ask": "Buy Ask",
                "bid": "Sell Bid",
                "current_price": "Last",
                "low_7d": f"{metric_window} Low",
                "high_7d": f"{metric_window} High",
                "momentum_7d_pct": f"{metric_window} Momentum %",
                "trades_7d": f"{metric_window} Trades",
                "swing_read": "Read",
            })
            blocks.append(
                f"""    <section>
      <h2>Swing Trading Lens</h2>
      <p class="muted">Price-first view for deciding what to ask, what to bid, and whether the item is getting more expensive.</p>
      {_table_html(trend_table)}
    </section>"""
            )

    if not scored.empty:
        notes: list[str] = []
        for _, row in scored.head(display_count).iterrows():
            detail_items = [
                f"<li>Bid / Ask: <strong>{escape(_fmt(row['bid']))} / {escape(_fmt(row['ask']))}</strong></li>",
                f"<li>Effective spread: <strong>{escape(_fmt(row['spread_pct'], 2))}%</strong></li>",
                f"<li>{escape(metric_window)} trades: <strong>{escape(_fmt(row['trades_7d'], 0))}</strong></li>",
                f"<li>{escape(metric_window)} range: <strong>{escape(_fmt(row['range_pct'], 2))}%</strong></li>",
            ]
            if row.get("momentum_7d_pct") is not None and not pd.isna(row.get("momentum_7d_pct")):
                detail_items.append(f"<li>{escape(metric_window)} momentum: <strong>{escape(_fmt(row['momentum_7d_pct'], 2))}%</strong></li>")
            detail_items.append(f"<li>Trading attractiveness: <strong>{escape(_fmt(row['trading_attractiveness'], 2))}</strong></li>")
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

    blocks.append(
        """    <section class="panel warning">
      <h2>Warning</h2>
      <p>This is a statistical filter, not a profit guarantee. Orders may not fill, prices may move, other players may undercut, and inventory can get stuck.</p>
    </section>"""
    )
    return _html_page("WarEra Quantitative Market Report", "\n".join(blocks))


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
    csv_path = out / "market_scores.csv"
    html_path = out / "market_report.html"
    df.to_csv(csv_path, index=False)
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
    return csv_path, html_path
