# WarEra Market Guide

A Python tool for WarEra market analysis.

The current implementation can generate reports from CSV or live API data. Live market runs sync current prices, order-book observations, and transaction history into SQLite, then build reports and charts from that database.

See:

- [AGENTS.md](AGENTS.md) for repository architecture rules.
- [docs/market-db-reporting-spec.md](docs/market-db-reporting-spec.md) for the market database and reporting spec.

## License

This project is shared under the MIT License. See [LICENSE](LICENSE) for the full text.

## Architecture

The market database architecture is layered:

- `api_client.py`: low-level HTTP only.
- `warera_api.py`: WarEra endpoint parsing only.
- `sync.py`: API-to-database sync orchestration.
- `market_store.py`: the only SQLite/database access layer.
- `market_data.py`: read models for reports and charts.
- `metrics.py`, `charts.py`, `report.py`: calculations and output only.

SQLite is the source of truth for live market history. Do not add JSON snapshot-based market workflows.

The report direction is market history rather than day-trading picks: price evolution, min/max ranges, averages, VWAP, tendencies, volume, liquidity, spreads, and multi-window trends.

## Formula

The existing report includes a market-making score:

```text
Trading Attractiveness = (Effective Spread % × Window Trades) / Window Range %
```

This rewards markets with exploitable spreads, frequent trades, and relatively stable prices.
Effective spread subtracts the minimum price tick from the raw bid/ask gap before scoring.

This score is secondary to trend/history reporting.

## Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Run with sample data

```bash
PYTHONPATH=src .venv/bin/python run_report.py --csv data/sample_market.csv --output output --top 0
```

## Run With API Data

Put your API settings in `.env`:

```text
WARERA_API_KEY=your_key_here
WARERA_API_BASE_URL=https://api2.warera.io/trpc
```

The script loads `.env` at runtime. Then run:

```bash
PYTHONPATH=src .venv/bin/python run_report.py --live --output output --top 0
```

Current live mode syncs every returned market good into `data/warera_market.sqlite3` by default:

- current item prices
- current order-book bid/ask observations
- recent trade history

Live mode defaults to a 1-day report. The API is rate-limited locally to 1 request per second by default. You can tune the live pull:

```bash
PYTHONPATH=src .venv/bin/python run_report.py --live --lookback-days 1 --order-limit 10 --history-pages 0 --min-interval 1
```

`--history-pages 0` fetches transaction pages until records are older than `--lookback-days`. Use a positive number for a faster capped pull.
`--order-limit` only controls current order-book depth per good: `10` means the 10 best bids and 10 best asks right now. It does not limit historical transactions or how many goods appear in the report.
`--min-tick` defaults to `0.001`, so a one-tick bid/ask gap is treated as non-exploitable.
Use `--market-db path/to/market.sqlite3` to choose a different market database.

Live runs print progress as they fetch each good and transaction page. Use `--quiet` to suppress progress output.
Use `--verbose` during imports to print each transaction page, cursor state, fetched row count, inserted/skipped rows, and stop reason.
Use `--exclude-item-code case1 --exclude-item-code case2` to remove specific non-good item codes from a live report.

To sync the market database without generating report files:

```bash
PYTHONPATH=src .venv/bin/python run_report.py --sync --verbose
```

To render the featured trade chart from a live run:

```bash
PYTHONPATH=src .venv/bin/python run_report.py --live --lookback-days 1 --output output --charts --chart-interval 15min --chart-ma-window 4
```

The chart is written to `output/charts/featured-trade.png`. It uses 15-minute candles by default. One-day reports show candles and volume only; longer windows also plot a moving average and mark closes that break above or below that moving average. The automatic featured item follows the report rank order.
Use `--featured-item-code bread` to force a specific item as the featured chart.
Use `--chart-min-range-pct 5` to control the minimum visible y-axis range and avoid over-zooming tiny price moves.

For custom non-market JSON records, use `--api-endpoint`:

```bash
PYTHONPATH=src .venv/bin/python run_report.py --api-endpoint "/your/custom/endpoint" --output output --top 0
```

If the API returns records nested inside a wrapper object, point the loader at the list:

```bash
PYTHONPATH=src .venv/bin/python run_report.py --api-endpoint "/your/custom/endpoint" --api-records-path "result.data.items"
```

You can also pass query parameters:

```bash
PYTHONPATH=src .venv/bin/python run_report.py --api-endpoint "/your/custom/endpoint" --api-param limit=100
```

Generated files:

```text
output/market_trends.csv
output/market_scores.csv
output/market_report.html
```

## Input CSV columns

Minimum recommended columns:

```text
item_name,bid,ask,trades_7d,high_7d,low_7d,current_price,open_7d,close_7d
```

`open_7d`, `close_7d`, and `current_price` are useful but optional.

## API key

Do not hardcode your key. Put these in `.env`:

```text
WARERA_API_KEY=your_key_here
WARERA_API_BASE_URL=https://api2.warera.io/trpc
```

The API client uses:

```http
X-Api-Key: <WARERA_API_KEY>
```

It sleeps 1 second between requests by default, keeping usage below 200 requests/minute.

## Tests

```bash
PYTHONPATH=src .venv/bin/python -m pytest
```
