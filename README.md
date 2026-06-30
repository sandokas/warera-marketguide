# WarEra Quant Market Report

A Python tool that ranks WarEra market goods using measurable trading statistics.

## Formula

```text
Trading Attractiveness = (Effective Spread % × Window Trades) / Window Range %
```

This rewards markets with exploitable spreads, frequent trades, and relatively stable prices.
Effective spread subtracts the minimum price tick from the raw bid/ask gap before scoring.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run with sample data

```bash
PYTHONPATH=src python3 run_report.py --csv data/sample_market.csv --output output --top 0
```

## Run with API data

Put your API settings in `.env`:

```text
WARERA_API_KEY=your_key_here
WARERA_API_BASE_URL=https://api2.warera.io/trpc
```

The script loads `.env` at runtime. Then run:

```bash
PYTHONPATH=src python3 run_report.py --live --output output --top 0
```

Live mode downloads every good returned by `itemTrading.getPrices`:

- current item prices from `itemTrading.getPrices`
- top buy/sell orders from `tradingOrder.getTopOrders`
- recent trade history from `transaction.getPaginatedTransactions`

Live mode defaults to a 1-day report. The API is rate-limited locally to 1 request per second by default. You can tune the live pull:

```bash
PYTHONPATH=src python3 run_report.py --live --lookback-days 1 --order-limit 10 --history-pages 0 --min-interval 1
```

`--history-pages 0` fetches transaction pages until records are older than `--lookback-days`. Use a positive number for a faster capped pull.
`--order-limit` only controls order-book depth per good; it does not limit how many goods appear in the report.
`--min-tick` defaults to `0.001`, so a one-tick bid/ask gap is treated as non-exploitable.

Live runs print progress as they fetch each good and transaction page. Use `--quiet` to suppress progress output.
Use `--exclude-item-code case1 --exclude-item-code case2` to remove specific non-good item codes from a live report.

To regenerate a report from a saved snapshot without downloading again:

```bash
PYTHONPATH=src python3 run_report.py --from-snapshot output/market_snapshot.json --lookback-days 1 --output output --top 0
```

To render the featured trade chart from a live run or saved snapshot:

```bash
PYTHONPATH=src python3 run_report.py --from-snapshot output/market_snapshot.json --lookback-days 1 --output output --charts --chart-interval 15min --chart-ma-window 4
```

The chart is written to `output/charts/featured-trade.png`. It uses 15-minute candles by default. One-day reports show candles and volume only; longer windows also plot a moving average and mark closes that break above or below that moving average. The automatic featured item follows the report rank order.
Use `--featured-item-code bread` to force a specific item as the featured chart.
Use `--chart-min-range-pct 5` to control the minimum visible y-axis range and avoid over-zooming tiny price moves.

For a custom endpoint, use `--api-endpoint`:

```bash
PYTHONPATH=src python3 run_report.py --api-endpoint "/itemTrading.getPrices" --output output --top 0
```

If the API returns records nested inside a wrapper object, point the loader at the list:

```bash
PYTHONPATH=src python3 run_report.py --api-endpoint "/your/market/endpoint" --api-records-path "result.data.items"
```

You can also pass query parameters:

```bash
PYTHONPATH=src python3 run_report.py --api-endpoint "/your/market/endpoint" --api-param limit=100
```

Generated files:

```text
output/market_snapshot.json
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
