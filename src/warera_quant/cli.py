from __future__ import annotations

import argparse
from pathlib import Path

from .api_client import WarEraApiClient
from .charts import featured_item_codes, render_featured_chart
from .csv_loader import load_market_csv
from .json_loader import market_json_to_dataframe
from .market_data import load_chart_data, load_market_rows
from .market_store import MarketStore
from .metrics import calculate_metrics
from .report import combine_market_rows_with_metrics, write_outputs
from .sync import sync_market_data
from .warera_api import WarEraMarketApi


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
    parser.add_argument(
        "--market-db",
        default="data/warera_market.sqlite3",
        help="SQLite market database path used by --live sync.",
    )
    parser.add_argument(
        "--transaction-backfill",
        action="store_true",
        help="With --live, ignore transaction high-water marks and backfill history while deduping.",
    )
    parser.add_argument(
        "--from-db",
        action="store_true",
        help="Load market rows from the local SQLite market DB instead of the sample CSV, without syncing live data.",
    )
    parser.add_argument("--api-endpoint", help="Fetch custom JSON records from this API endpoint instead of reading --csv.")
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
        help="Dot path to the list of records inside the custom API JSON, such as data.items.",
    )
    parser.add_argument(
        "--order-limit",
        type=int,
        default=10,
        help=(
            "Current order-book depth per item: fetch this many best bids and best asks. "
            "Does not limit transaction history."
        ),
    )
    parser.add_argument(
        "--history-pages",
        type=int,
        default=0,
        help="Maximum transaction pages per item in --live mode. Use 0 to fetch until records are older than --lookback-days.",
    )
    parser.add_argument("--lookback-days", type=float, default=7.0, help="Transaction lookback window for --live mode.")
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
        help="Render stock-style price charts from DB-backed live market data.",
    )
    parser.add_argument("--chart-interval", default="1h", help="Chart candle interval, such as 1h or 15min.")
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
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed importer progress for each item and transaction page.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    output_dir = Path(args.output)

    selected_sources = sum(bool(value) for value in (args.live, args.api_endpoint))
    if selected_sources > 1:
        raise SystemExit("Use only one of --live or --api-endpoint.")
    if args.transaction_backfill and not args.live:
        raise SystemExit("--transaction-backfill requires --live.")
    if args.chart_ma_window < 1:
        raise SystemExit("--chart-ma-window must be at least 1.")
    if args.chart_min_range_pct < 0:
        raise SystemExit("--chart-min-range-pct cannot be negative.")
    if args.quiet and args.verbose:
        raise SystemExit("Use only one of --quiet or --verbose.")

    if not args.live and not args.api_endpoint and not args.from_db:
        if args.csv == "data/sample_market.csv" and Path(args.market_db).exists():
            args.from_db = True
            if not args.quiet:
                print(f"Using local market DB at {args.market_db} because no explicit input was provided.", flush=True)

    if args.live:
        client = WarEraApiClient(min_interval_seconds=args.min_interval)
        market_api = WarEraMarketApi(client)
        if not args.quiet:
            print(
                f"Starting live report: lookback={args.lookback_days:g} day(s), "
                f"history_pages={args.history_pages}, order_book_depth={args.order_limit}",
                flush=True,
            )
        with MarketStore(args.market_db) as store:
            sync_result = sync_market_data(
                market_api,
                store,
                order_limit=args.order_limit,
                history_pages=args.history_pages,
                transaction_backfill=args.transaction_backfill,
                lookback_days=args.lookback_days,
                exclude_item_codes=set(args.exclude_item_code),
                progress=None if args.quiet else lambda message: print(message, flush=True),
                verbose=args.verbose,
            )
            rows = load_market_rows(store, lookback_days=args.lookback_days)
        if not args.quiet:
            print(
                f"Synced {sync_result.prices_observed} price(s), "
                f"{sync_result.order_books_observed} order book(s), "
                f"{sync_result.pages_fetched} transaction page(s), "
                f"{sync_result.transactions_inserted} new transaction(s) "
                f"to {args.market_db}.",
                flush=True,
            )
        df_in = market_json_to_dataframe(rows)
    elif args.from_db:
        with MarketStore(args.market_db) as store:
            rows = load_market_rows(store, lookback_days=args.lookback_days)
        df_in = market_json_to_dataframe(rows)
    elif args.api_endpoint:
        client = WarEraApiClient(min_interval_seconds=args.min_interval)
        data = client.get_json(args.api_endpoint, params=dict(args.api_param))
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
    df_out = combine_market_rows_with_metrics(df_in, metrics)
    metric_window = f"{args.lookback_days:g}D" if args.live else "7D"
    chart_path = None
    chart_label = None
    chart_candidates = featured_item_codes(df_out)
    if args.charts:
        if not args.live:
            print("Skipped charts: charts require DB-backed --live market data.")
        else:
            selected_chart_items = []
            if args.featured_item_code:
                selected_chart_items.append(args.featured_item_code)
            selected_chart_items.extend(
                item_code for item_code in chart_candidates if item_code not in selected_chart_items
            )
            with MarketStore(args.market_db) as store:
                for item_code in selected_chart_items:
                    label_rows = df_out[df_out["item_code"] == item_code] if "item_code" in df_out.columns else []
                    chart_label = (
                        str(label_rows.iloc[0]["item_name"])
                        if len(label_rows) and "item_name" in df_out.columns
                        else item_code
                    )
                    chart_data = load_chart_data(store, item_code=item_code, window=metric_window)
                    chart_path = render_featured_chart(
                        chart_data,
                        output_dir / "charts" / "featured-trade.png",
                        item_name=f"Featured Trade: {chart_label}",
                        interval=args.chart_interval,
                        ma_window=args.chart_ma_window,
                        show_moving_average=args.lookback_days > 1,
                        min_range_pct=args.chart_min_range_pct,
                    )
                    if chart_path is not None:
                        break
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
