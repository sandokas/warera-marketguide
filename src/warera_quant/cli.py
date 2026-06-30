from __future__ import annotations

import argparse
import json
from pathlib import Path

from .api_client import WarEraApiClient
from .charts import featured_item_codes, render_featured_snapshot_chart
from .csv_loader import load_market_csv
from .json_loader import market_json_to_dataframe
from .live_market import _display_name, fetch_live_market_rows, rows_from_market_snapshot
from .metrics import calculate_metrics
from .report import metrics_to_dataframe, write_outputs


def _parse_param(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("API params must use KEY=VALUE format.")
    key, param_value = value.split("=", 1)
    key = key.strip()
    if not key:
        raise argparse.ArgumentTypeError("API param key cannot be empty.")
    return key, param_value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a WarEra quantitative market report.")
    parser.add_argument("--csv", default="data/sample_market.csv", help="Input CSV with market fields.")
    parser.add_argument("--live", action="store_true", help="Fetch live WarEra market data from the API.")
    parser.add_argument("--from-snapshot", help="Recalculate report from a saved market_snapshot.json without API calls.")
    parser.add_argument("--api-endpoint", help="Fetch market data from this API endpoint instead of reading --csv.")
    parser.add_argument(
        "--api-param",
        action="append",
        default=[],
        type=_parse_param,
        metavar="KEY=VALUE",
        help="Query parameter for --api-endpoint. May be provided multiple times.",
    )
    parser.add_argument(
        "--api-records-path",
        help="Dot path to the list of market records inside the API JSON, such as data.items.",
    )
    parser.add_argument(
        "--snapshot",
        default="market_snapshot.json",
        help="Filename for the raw API JSON snapshot inside --output when using --api-endpoint.",
    )
    parser.add_argument("--order-limit", type=int, default=10, help="Top buy/sell orders per item in --live mode.")
    parser.add_argument(
        "--history-pages",
        type=int,
        default=0,
        help="Maximum transaction pages per item in --live mode. Use 0 to fetch until records are older than --lookback-days.",
    )
    parser.add_argument("--lookback-days", type=float, default=1.0, help="Transaction lookback window for --live mode.")
    parser.add_argument(
        "--min-tick",
        type=float,
        default=0.001,
        help="Minimum exploitable price increment to subtract from bid/ask spread before scoring.",
    )
    parser.add_argument(
        "--exclude-item-code",
        action="append",
        default=[],
        help="Item code to exclude from --live mode. May be provided multiple times.",
    )
    parser.add_argument(
        "--min-interval",
        type=float,
        default=1.0,
        help="Minimum seconds between API requests.",
    )
    parser.add_argument("--output", default="output", help="Output directory.")
    parser.add_argument("--top", type=int, default=0, help="Number of ranked items to show. Use 0 to show all.")
    parser.add_argument(
        "--charts",
        action="store_true",
        help="Render stock-style price charts from live or saved transaction snapshots.",
    )
    parser.add_argument("--chart-interval", default="15min", help="Chart candle interval, such as 15min or 1h.")
    parser.add_argument(
        "--chart-ma-window",
        type=int,
        default=4,
        help="Moving-average window in chart candles. Used only when --lookback-days is greater than 1.",
    )
    parser.add_argument(
        "--chart-min-range-pct",
        type=float,
        default=5.0,
        help="Minimum visible price range as a percent of mid price, to avoid over-zooming tiny moves.",
    )
    parser.add_argument("--featured-item-code", help="Item code to force as the featured chart, such as bread.")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    output_dir = Path(args.output)
    snapshot = None

    selected_sources = sum(bool(value) for value in (args.live, args.from_snapshot, args.api_endpoint))
    if selected_sources > 1:
        raise SystemExit("Use only one of --live, --from-snapshot, or --api-endpoint.")
    if args.chart_ma_window < 1:
        raise SystemExit("--chart-ma-window must be at least 1.")
    if args.chart_min_range_pct < 0:
        raise SystemExit("--chart-min-range-pct cannot be negative.")

    if args.live:
        client = WarEraApiClient(min_interval_seconds=args.min_interval)
        if not args.quiet:
            print(
                f"Starting live report: lookback={args.lookback_days:g} day(s), "
                f"history_pages={args.history_pages}, order_limit={args.order_limit}",
                flush=True,
            )
        rows, snapshot = fetch_live_market_rows(
            client,
            order_limit=args.order_limit,
            history_pages=args.history_pages,
            lookback_days=args.lookback_days,
            exclude_item_codes=set(args.exclude_item_code),
            progress=None if args.quiet else lambda message: print(message, flush=True),
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = output_dir / args.snapshot
        snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {snapshot_path}")
        df_in = market_json_to_dataframe(rows)
    elif args.from_snapshot:
        snapshot_path = Path(args.from_snapshot)
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        rows = rows_from_market_snapshot(snapshot, lookback_days=args.lookback_days)
        df_in = market_json_to_dataframe(rows)
    elif args.api_endpoint:
        client = WarEraApiClient(min_interval_seconds=args.min_interval)
        data = client.get_json(args.api_endpoint, params=dict(args.api_param))
        output_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = output_dir / args.snapshot
        snapshot_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {snapshot_path}")
        df_in = market_json_to_dataframe(data, records_path=args.api_records_path)
    else:
        df_in = load_market_csv(args.csv)

    metrics = []
    for _, row in df_in.iterrows():
        metric_input = row.to_dict()
        metric_input["min_tick"] = args.min_tick
        metrics.append(calculate_metrics(metric_input))
    if args.live and not args.quiet:
        print(f"Calculated metrics for {len(metrics)} goods.", flush=True)
    df_out = metrics_to_dataframe(metrics)
    metric_window = f"{args.lookback_days:g}D" if args.live or args.from_snapshot else "7D"
    chart_path = None
    chart_label = None
    chart_candidates = featured_item_codes(df_out)
    if args.charts:
        if snapshot is None:
            print("Skipped charts: charts require --live or --from-snapshot transaction data.")
        else:
            chart_label = _display_name(args.featured_item_code or chart_candidates[0]) if (args.featured_item_code or chart_candidates) else None
            chart_path = render_featured_snapshot_chart(
                snapshot,
                output_dir / "charts",
                candidate_item_codes=chart_candidates,
                featured_item_code=args.featured_item_code,
                interval=args.chart_interval,
                ma_window=args.chart_ma_window,
                show_moving_average=args.lookback_days > 1,
                min_range_pct=args.chart_min_range_pct,
            )
            if chart_path is None:
                print("Skipped featured chart: no item had enough transaction candles.")
                chart_label = None
            else:
                print(f"Wrote featured chart to {chart_path}")
    csv_path, report_path = write_outputs(
        df_out,
        output_dir,
        top=args.top,
        metric_window=metric_window,
        chart_path=chart_path,
        chart_label=chart_label,
    )
    print(f"Wrote {csv_path}")
    print(f"Wrote {report_path}")


if __name__ == "__main__":
    main()
