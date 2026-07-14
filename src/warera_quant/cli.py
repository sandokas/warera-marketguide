from __future__ import annotations

import argparse
import math
from pathlib import Path

import pandas as pd

from .api_client import WarEraApiClient
from .charts import featured_item_codes, render_featured_chart
from .csv_loader import load_market_csv
from .json_loader import market_json_to_dataframe
from .market_data import load_chart_data, load_market_rows, opportunity_fields
from .market_store import MarketStore
from .metrics import FlipAssumptions, calculate_flip_opportunity, calculate_metrics
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
    parser = argparse.ArgumentParser(description="Generate a WarEra Market Guide report.")
    parser.add_argument("--csv", default="data/sample_market.csv", help="Input CSV with market fields.")
    parser.add_argument("--live", action="store_true", help="Fetch live WarEra market data from the API.")
    parser.add_argument("--sync", action="store_true", help="Sync live WarEra market data into SQLite, then exit.")
    parser.add_argument(
        "--market-db",
        default="data/warera_market.sqlite3",
        help="SQLite market database path used by live sync.",
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
        default=100,
        help=(
            "Current order-book depth per item: fetch this many best bids and best asks "
            "(default and API maximum: 100). Does not limit transaction history."
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
        "--forecast-horizon-hours",
        type=float,
        default=24.0,
        help="Forecast horizon in hours (default: 24).",
    )
    parser.add_argument(
        "--forecast-target-max-lag-hours",
        type=float,
        default=6.0,
        help="Maximum lateness for the first target observation in hours (default: 6).",
    )
    parser.add_argument(
        "--forecast-min-samples",
        type=int,
        default=30,
        help="Minimum directional samples required for evidence grading (default: 30).",
    )
    parser.add_argument(
        "--trade-quantity", type=float, default=1.0,
        help="Requested quantity for executable flip estimates.",
    )
    parser.add_argument(
        "--trade-fee-pct", type=float, default=0.0,
        help="Assumed fee percent charged independently per side.",
    )
    parser.add_argument(
        "--min-net-margin-pct", type=float, default=1.0,
        help="Minimum median net margin for a Potential flip.",
    )
    parser.add_argument(
        "--max-quote-age-minutes", type=float, default=30.0,
        help="Maximum quote age eligible for a trade verdict.",
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

    selected_sources = sum(bool(value) for value in (args.live, args.sync, args.api_endpoint))
    if selected_sources > 1:
        raise SystemExit("Use only one of --live, --sync, or --api-endpoint.")
    if args.transaction_backfill and not (args.live or args.sync):
        raise SystemExit("--transaction-backfill requires --live or --sync.")
    if args.chart_ma_window < 1:
        raise SystemExit("--chart-ma-window must be at least 1.")
    if args.chart_min_range_pct < 0:
        raise SystemExit("--chart-min-range-pct cannot be negative.")
    if not math.isfinite(args.forecast_horizon_hours) or args.forecast_horizon_hours <= 0:
        raise SystemExit("--forecast-horizon-hours must be positive.")
    if not math.isfinite(args.forecast_target_max_lag_hours) or args.forecast_target_max_lag_hours < 0:
        raise SystemExit("--forecast-target-max-lag-hours cannot be negative.")
    if args.forecast_min_samples < 1:
        raise SystemExit("--forecast-min-samples must be at least 1.")
    try:
        assumptions = FlipAssumptions(
            quantity=args.trade_quantity,
            fee_pct_per_side=args.trade_fee_pct,
            minimum_net_margin_pct=args.min_net_margin_pct,
            forecast_horizon_hours=args.forecast_horizon_hours,
            max_quote_age_minutes=args.max_quote_age_minutes,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.quiet and args.verbose:
        raise SystemExit("Use only one of --quiet or --verbose.")

    if not args.live and not args.sync and not args.api_endpoint and not args.from_db:
        if args.csv == "data/sample_market.csv" and Path(args.market_db).exists():
            args.from_db = True
            if not args.quiet:
                print(f"Using local market DB at {args.market_db} because no explicit input was provided.", flush=True)

    if args.live or args.sync:
        client = WarEraApiClient(min_interval_seconds=args.min_interval)
        market_api = WarEraMarketApi(client)
        if not args.quiet:
            print(
                f"Starting live {'sync' if args.sync else 'report'}: lookback={args.lookback_days:g} day(s), "
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
            rows = load_market_rows(
                store,
                lookback_days=args.lookback_days,
                forecast_horizon_hours=args.forecast_horizon_hours,
                forecast_target_max_lag_hours=args.forecast_target_max_lag_hours,
                forecast_min_samples=args.forecast_min_samples,
                min_tick=args.min_tick,
                flip_assumptions=assumptions,
            )
        if not args.quiet:
            print(
                f"Synced {sync_result.prices_observed} price(s), "
                f"{sync_result.order_books_observed} order book(s), "
                f"{sync_result.pages_fetched} transaction page(s), "
                f"{sync_result.transactions_inserted} new transaction(s), "
                f"{sync_result.transactions_skipped} duplicate transaction(s) "
                f"to {args.market_db}.",
                flush=True,
            )
        if args.sync:
            return
        # Database read models are already normalized and include structured
        # order-book levels.  Keep those nested values intact for report
        # rendering instead of flattening them like an arbitrary JSON payload.
        df_in = pd.DataFrame(rows)
    elif args.from_db:
        with MarketStore(args.market_db) as store:
            rows = load_market_rows(
                store,
                lookback_days=args.lookback_days,
                forecast_horizon_hours=args.forecast_horizon_hours,
                forecast_target_max_lag_hours=args.forecast_target_max_lag_hours,
                forecast_min_samples=args.forecast_min_samples,
                min_tick=args.min_tick,
                flip_assumptions=assumptions,
            )
        df_in = pd.DataFrame(rows)
    elif args.api_endpoint:
        client = WarEraApiClient(min_interval_seconds=args.min_interval)
        data = client.get_json(args.api_endpoint, params=dict(args.api_param))
        df_in = market_json_to_dataframe(data, records_path=args.api_records_path)
    else:
        df_in = load_market_csv(args.csv)

    if not (args.live or args.from_db):
        compatibility_defaults = {
            "forecast_model_version": "direction-v1",
            "forecast_horizon_hours": args.forecast_horizon_hours,
            "forecast_candidate_samples": 0,
            "forecast_evaluable_samples": 0,
            "forecast_execution_evaluable_samples": 0,
            "forecast_accuracy_pct": None,
            "forecast_baseline_accuracy_pct": None,
            "forecast_current_signal": "Unavailable",
            "forecast_current_reason_codes": "",
            "forecast_evidence": "Insufficient",
        }
        for column, value in compatibility_defaults.items():
            if column not in df_in.columns:
                df_in[column] = value
        compatibility_opportunities = []
        for _, row in df_in.iterrows():
            opportunity = calculate_flip_opportunity({
                "item_code": row.get("item_code"),
                "item_name": row.get("item_name"),
                "best_bid": row.get("bid"),
                "best_ask": row.get("ask"),
                "forecast_signal": row.get("forecast_current_signal"),
                "forecast_evidence": row.get("forecast_evidence"),
                "forecast_samples": row.get("forecast_evaluable_samples"),
                "min_tick": args.min_tick,
            }, assumptions)
            compatibility_opportunities.append(opportunity_fields(opportunity))
        for index, fields in enumerate(compatibility_opportunities):
            for column, value in fields.items():
                df_in.loc[index, column] = value

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
        assumptions=assumptions,
    )
    print(f"Wrote {csv_path}")
    print(f"Wrote {report_path}")


if __name__ == "__main__":
    main()
