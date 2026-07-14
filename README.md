# WarEra Market Guide

WarEra Market Guide is a local Python reporting tool for market history, price trends, liquidity, and order-book conditions. It can read the included sample CSV, sync live WarEra market data into SQLite, or build a report from an existing market database.

Live data is normalized at the API boundary and stored in SQLite. Reports and charts read from that database; raw WarEra responses are not persisted.

## Quick start

Create a virtual environment and install the dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Generate a report from the included sample data:

```bash
PYTHONPATH=src .venv/bin/python run_report.py --csv ./data/sample_market.csv --output output
```

Generated files:

```text
output/market_report.html
output/market_trends.csv
output/market_scores.csv
```

`market_scores.csv` is a compatibility copy of `market_trends.csv`.

## Live market data

Copy `.env.example` to `.env` and set your API key:

```text
WARERA_API_KEY=your_key_here
WARERA_API_BASE_URL=https://api2.warera.io/trpc
```

Do not commit or hardcode the key. Requests authenticate with the `X-Api-Key` header.

Sync current quotes, order-book observations, and transactions, then generate a report:

```bash
PYTHONPATH=src .venv/bin/python run_report.py --live --output output
```

The default database is `data/warera_market.sqlite3`, the report lookback is 7 days, the order-book depth is 10 orders per side, and requests are spaced at least 1 second apart. Override those values when needed:

```bash
PYTHONPATH=src .venv/bin/python run_report.py \
  --live \
  --market-db data/warera_market.sqlite3 \
  --lookback-days 30 \
  --order-limit 20 \
  --min-interval 1 \
  --output output
```

On incremental runs, transaction pagination stops at stored transactions or the per-item high-water mark. On a new database it continues until the API returns no cursor unless `--history-pages` sets a page cap.

Use an explicit backfill to ignore high-water marks and stop at the lookback boundary:

```bash
PYTHONPATH=src .venv/bin/python run_report.py \
  --sync \
  --transaction-backfill \
  --lookback-days 30
```

Useful sync options:

- `--sync` updates SQLite without generating report files.
- `--history-pages N` caps transaction pages per item; `0` means no page cap.
- `--exclude-item-code CODE` excludes an item and may be repeated.
- `--quiet` suppresses progress; `--verbose` shows page-level import details.
- `--min-tick` changes the price increment removed from the raw spread when calculating trading attractiveness. It defaults to `0.001`.

## Reports from an existing database

Generate a report without making API calls:

```bash
PYTHONPATH=src .venv/bin/python run_report.py \
  --from-db \
  --market-db data/warera_market.sqlite3 \
  --lookback-days 7 \
  --output output
```

When no input option is supplied, the CLI uses the default market database if it exists; otherwise it uses `data/sample_market.csv`. Prefer `--from-db` or an explicit CSV path in scripts so the input is clear.

## Charts

Charts are currently available during a live report run:

```bash
PYTHONPATH=src .venv/bin/python run_report.py \
  --live \
  --charts \
  --chart-interval 15min \
  --chart-ma-window 4 \
  --output output
```

The featured chart is written to `output/charts/featured-trade.png` and embedded in the HTML report. Use `--featured-item-code bread` to prefer a particular item and `--chart-min-range-pct 5` to set the minimum visible price range. A moving average is shown only when `--lookback-days` is greater than 1.

## Other input modes

### CSV

The minimum useful CSV fields are:

```text
item_name,bid,ask,trades_7d,high_7d,low_7d
```

`current_price`, `open_7d`, and `close_7d` improve the report but are optional. See `data/sample_market.csv` for a complete example.

### Custom JSON endpoint

`--api-endpoint` supports non-market JSON records through the generic API client:

```bash
PYTHONPATH=src .venv/bin/python run_report.py \
  --api-endpoint /your/custom/endpoint \
  --api-records-path result.data.items \
  --api-param limit=100 \
  --output output
```

`--api-param` may be repeated. This compatibility path does not sync data into the market database.

Run `PYTHONPATH=src .venv/bin/python run_report.py --help` for the complete option list.

## Market semantics

Executed transactions are the primary price source. The report distinguishes:

- `last_trade_price`: newest execution in the queried window;
- `quote_price`: newest price-endpoint observation;
- `mid_price`: midpoint of the newest best bid and ask;
- `current_price`: last trade, then quote, then midpoint as fallback.

Transaction history drives open, high, low, close, VWAP, median, percentiles, volume, and trend metrics. Order-book depth and spread drive liquidity. The trading-attractiveness score remains a secondary compatibility metric:

```text
Trading Attractiveness = (Effective Spread % x Window Trades) / Window Range %
```

Effective spread subtracts the minimum price tick from the raw bid/ask gap.

More detail is available in:

- [Market database and architecture](docs/market-db-reporting-spec.md)
- [Market data semantics](docs/market-data-model-spec.md)
- [Report and liquidity semantics](docs/market-reporting-liquidity-spec.md)
- [Repository architecture rules](AGENTS.md)

## Development

Run the test suite with the existing virtual environment:

```bash
PYTHONPATH=src .venv/bin/python -m pytest
```

Only `market_store.py` accesses SQLite, only `api_client.py` performs HTTP requests, and only `warera_api.py` knows WarEra market endpoint names and payload shapes. Keep new work within those boundaries.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
