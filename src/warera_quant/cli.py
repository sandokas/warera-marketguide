from __future__ import annotations

import argparse
import math
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from .api_client import WarEraApiClient
from .charts import (
    render_highlight_price_action_chart,
    render_report_header_png,
    render_report_item_context_pngs,
    render_report_table_pngs,
)
from .config import ConfigError, load_config
from .csv_loader import load_market_csv
from .json_loader import market_json_to_dataframe
from .market_data import load_highlight_trade_history, load_market_rows, opportunity_fields
from .market_store import MarketStore
from .metrics import (
    FlipAssumptions,
    calculate_flip_opportunity,
    calculate_metrics,
    classify_price_dislocations,
    meaningful_dislocation_item_codes,
    prepare_price_action_item,
    price_dislocation_fields,
    price_action_chart_filename,
    select_highlighted_items,
)
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
    parser.add_argument(
        "--config",
        default="marketguide.toml",
        help="TOML configuration path (default: marketguide.toml).",
    )
    parser.add_argument("--live", action="store_true", help="Fetch live WarEra market data from the API.")
    parser.add_argument("--sync", action="store_true", help="Sync live WarEra market data into SQLite, then exit.")
    parser.add_argument(
        "--housekeeping",
        action="store_true",
        help="Prune and optionally compact the market database, then exit without syncing.",
    )
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
        help="Positive minimum tradable price increment used by scoring and dislocation classification.",
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
        help="Equipment-market fee percent per side. Commodity markets have no fee and must use 0.",
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
        "--table-pngs",
        action="store_true",
        help="Export the report header, Item Price Context cards, and tables as standalone PNGs.",
    )
    parser.add_argument(
        "--charts",
        action="store_true",
        help="Render stock-style price charts from DB-backed live market data.",
    )
    parser.add_argument(
        "--all-price-action-charts",
        action="store_true",
        help="Export every chart-capable item under OUTPUT/charts/all/ without embedding them.",
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
    load_dotenv()
    args = build_parser().parse_args()
    output_dir = Path(args.output)
    data_sync_metadata = None
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc

    selected_sources = sum(
        bool(value) for value in (args.live, args.sync, args.housekeeping, args.api_endpoint)
    )
    if selected_sources > 1:
        raise SystemExit("Use only one of --live, --sync, --housekeeping, or --api-endpoint.")
    if args.transaction_backfill and not (args.live or args.sync):
        raise SystemExit("--transaction-backfill requires --live or --sync.")
    if args.chart_ma_window < 1:
        raise SystemExit("--chart-ma-window must be at least 1.")
    if args.chart_min_range_pct < 0:
        raise SystemExit("--chart-min-range-pct cannot be negative.")
    if not math.isfinite(args.min_tick) or args.min_tick <= 0:
        raise SystemExit("--min-tick must be positive.")
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

    if args.housekeeping:
        if not config.housekeeping.enabled:
            if not args.quiet:
                print("Database housekeeping is disabled in the configuration.", flush=True)
            return
        with MarketStore(args.market_db) as store:
            housekeeping_result = store.run_housekeeping(
                retention_days=config.housekeeping.retention_days,
                vacuum_interval_days=config.housekeeping.vacuum_interval_days,
            )
        if not args.quiet:
            compaction = "; compacted database" if housekeeping_result.vacuumed else ""
            print(
                f"Housekeeping retained {config.housekeeping.retention_days} day(s) and removed "
                f"{housekeeping_result.rows_deleted} expired row(s){compaction} from {args.market_db}.",
                flush=True,
            )
        return

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
                windows=("1D", "7D", "30D"),
                forecast_horizon_hours=args.forecast_horizon_hours,
                forecast_target_max_lag_hours=args.forecast_target_max_lag_hours,
                forecast_min_samples=args.forecast_min_samples,
                min_tick=args.min_tick,
                flip_assumptions=assumptions,
            )
            data_sync_metadata = store.market_sync_metadata()
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
                windows=("1D", "7D", "30D"),
                forecast_horizon_hours=args.forecast_horizon_hours,
                forecast_target_max_lag_hours=args.forecast_target_max_lag_hours,
                forecast_min_samples=args.forecast_min_samples,
                min_tick=args.min_tick,
                flip_assumptions=assumptions,
            )
            data_sync_metadata = store.market_sync_metadata()
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
    dislocations = classify_price_dislocations(
        df_out.to_dict("records"),
        min_tick=args.min_tick,
    )
    for index, item in zip(df_out.index, dislocations):
        for column, value in price_dislocation_fields(item).items():
            df_out.at[index, column] = value
    metric_window = f"{args.lookback_days:g}D" if args.live else "7D"
    chart_path = None
    chart_label = None
    rendered_highlights: list[dict[str, object]] = []
    chart_capable_highlights = []
    if args.live or args.from_db:
        rows_for_selection = df_out.to_dict("records")
        codes_by_normalized = {
            str(row.get("item_code")).strip().lower(): str(row.get("item_code")).strip()
            for row in rows_for_selection if row.get("item_code")
        }
        item_codes = [
            codes_by_normalized[code]
            for code in meaningful_dislocation_item_codes(dislocations)
            if code in codes_by_normalized
        ]
        with MarketStore(args.market_db) as store:
            histories = load_highlight_trade_history(store, item_codes=item_codes)
        chart_capable_highlights = select_highlighted_items(
            dislocations,
            histories,
            min_tick=args.min_tick,
            require_chart_history=True,
        )
        rendered_highlights = [
            {"item": item, "chart_path": None}
            for item in chart_capable_highlights
        ]
    if args.charts:
        if not (args.live or args.from_db):
            print("Skipped charts: charts require DB-backed market data.")
        else:
            rendered_highlights = []
            for highlight in chart_capable_highlights:
                output_path = output_dir / "charts" / highlight.filename
                rendered = None
                try:
                    if highlight.interval is not None:
                        rendered = render_highlight_price_action_chart(
                            highlight, output_path, min_range_pct=args.chart_min_range_pct,
                        )
                except Exception as exc:
                    if not args.quiet:
                        print(f"Skipped {highlight.item_name} chart: {exc}")
                    continue
                rendered_highlights.append({"item": highlight, "chart_path": rendered})
                if rendered is not None:
                    print(f"Wrote highlighted chart to {rendered}")
            if not any(entry["chart_path"] for entry in rendered_highlights):
                print("Skipped highlighted charts: no item had enough completed-transaction history.")
    if args.all_price_action_charts:
        if not (args.live or args.from_db):
            print("Skipped all-item charts: charts require DB-backed market data.")
        else:
            rows_by_code = {
                str(row.get("item_code")).strip(): row
                for row in df_out.to_dict("records") if row.get("item_code")
            }
            rendered_count = 0
            with MarketStore(args.market_db) as store:
                for storage_code, row in rows_by_code.items():
                    histories = load_highlight_trade_history(store, item_codes=[storage_code])
                    chart_item = prepare_price_action_item(
                        row,
                        histories.get(storage_code.lower(), ()),
                        min_tick=args.min_tick,
                    )
                    if chart_item is None:
                        continue
                    output_path = (
                        output_dir / "charts" / "all"
                        / price_action_chart_filename(storage_code)
                    )
                    try:
                        rendered = render_highlight_price_action_chart(
                            chart_item, output_path, min_range_pct=args.chart_min_range_pct,
                        )
                    except Exception as exc:
                        if not args.quiet:
                            print(f"Skipped {chart_item.item_name} all-item chart: {exc}")
                        continue
                    if rendered is not None:
                        rendered_count += 1
                        if not args.quiet:
                            print(f"Wrote all-item chart to {rendered}")
            if not args.quiet:
                print(f"Wrote {rendered_count} all-item price-action chart(s).")
    csv_path, report_path = write_outputs(
        df_out,
        output_dir,
        top=args.top,
        chart_path=chart_path,
        chart_label=chart_label,
        highlights=rendered_highlights,
        assumptions=assumptions,
        data_synced_at=data_sync_metadata.synced_at if data_sync_metadata else None,
        data_sync_status=data_sync_metadata.status if data_sync_metadata else None,
    )
    print(f"Wrote {csv_path}")
    print(f"Wrote {report_path}")
    if args.table_pngs:
        header_path = render_report_header_png(report_path, output_dir / "sections")
        print(f"Wrote {header_path}")
        for card_path in render_report_item_context_pngs(report_path, output_dir / "cards"):
            print(f"Wrote {card_path}")
        for table_path in render_report_table_pngs(report_path, output_dir / "tables"):
            print(f"Wrote {table_path}")


if __name__ == "__main__":
    main()
