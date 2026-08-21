# WarEra Market Guide

WarEra Market Guide is an aggressive decision-support tool for trading in the WarEra in-game market. Its goal is to turn real completed transactions and the current order book into clear buy, sell/exit, wait, profit-target, and stop-loss guidance. The report should make the proposed action, price levels, expected opportunity, and risk understandable at a glance.

This is not a neutral price dashboard. Market history and visualizations exist to support trading decisions. Signals may be aggressive because the market is inside a game, but they must remain traceable to the underlying data and must expose missing history, poor liquidity, and other limitations.

Live data is normalized at the API boundary and stored in SQLite. Reports and charts read from that database; raw WarEra responses are not persisted. See [Project goal and data authority](docs/project-goal.md) for the governing product and price-source rules.

## Quick start

Create a virtual environment and install the dependencies:

```bash
python -m venv .venv
.venv\Scripts\pip install -e .   # Windows
# or
.venv/bin/pip install -e .      # Linux/macOS
```

Generate a report from the included sample data:

```bash
warera-marketguide --csv ./data/sample_market.csv --output output
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

Sync full visible order books, completed transactions, and the official per-item
production-point configuration, then generate a report. The game-calculated price endpoint may
still be collected for compatibility or diagnostics, but it is lagging data and must not drive
market analysis or trading signals:

```bash
warera-marketguide --live --output output
```

The default database is `data/warera_market.sqlite3`, the report lookback is 7 days, and sync requests the API maximum of 100 order-book entries per side. Requests are spaced at least 1 second apart. Override those values when needed:

```bash
warera-marketguide \
  --live \
  --market-db data/warera_market.sqlite3 \
  --lookback-days 30 \
  --order-limit 100 \
  --min-interval 1 \
  --output output
```

On incremental runs, transaction pagination stops at stored transactions or the per-item high-water mark. On a new database it continues until the API returns no cursor unless `--history-pages` sets a page cap.

Use an explicit backfill to ignore high-water marks and stop at the lookback boundary:

```bash
warera-marketguide \
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

## Database housekeeping

Housekeeping is an independent operation: it never runs as part of a live sync. Run it explicitly
whenever desired, or schedule this command separately:

```bash
warera-marketguide --housekeeping
```

The routine prunes expired transactions, price observations, and order-book observations. The
default configuration in `marketguide.toml` retains 45 days, which leaves headroom for the
report's broadest standard completed-transaction window (30 days):

```toml
[housekeeping]
enabled = true
retention_days = 45
vacuum_interval_days = 30
```

SQLite reuses pages released by pruning, bounding normal database growth. When free pages exist,
the database is compacted no more often than `vacuum_interval_days`; set that value to `0` to
disable compaction. Set `enabled = false` to make the housekeeping command a no-op. A different
configuration file can be selected with `--config PATH`, and a different database with
`--market-db PATH`.

Increasing `retention_days` affects future pruning only. Data already removed by housekeeping
cannot be recovered unless the database was backed up separately.

## Reports from an existing database

Generate a report without making API calls:

```bash
warera-marketguide \
  --from-db \
  --market-db data/warera_market.sqlite3 \
  --lookback-days 7 \
  --output output
```

When no input option is supplied, the CLI uses the default market database if it exists; otherwise it uses `data/sample_market.csv`. Prefer `--from-db` or an explicit CSV path in scripts so the input is clear.

DB-backed reports persist and display the latest completed market-sync timestamp separately from
the time the report itself was generated. A sync with item-level errors is marked as partial, and a
failed sync does not advance the stored freshness timestamp. Existing databases infer their initial
timestamp from the newest stored market observation.

Completed activity uses two bars. Completed Value sums transaction price × quantity
for every item and determines the row order. PP-equivalent Volume multiplies completed
units by the total upstream Production Points (PP) required for one item; for example,
1 Steel represents 20 total PP: 10 PP in its direct recipe plus 10 PP for its 10 Iron.
This is an embodied-effort comparison, not actual production during the report window.
Item rows must not be summed because ingredient and processed-item trades can overlap.
Items without a defined factory chain still rank by Completed Value and show `N/A` only
for PP fields.

## Charts

Charts are currently available during a live report run:

```bash
warera-marketguide \
  --live \
  --charts \
  --chart-interval 15min \
  --chart-ma-window 4 \
  --output output
```

The featured chart is written to `output/charts/featured-trade.png` and embedded in the HTML report. Use `--featured-item-code bread` to prefer a particular item and `--chart-min-range-pct 5` to set the minimum visible price range. A moving average is shown only when `--lookback-days` is greater than 1.

The database-backed HTML report always combines 1D, 7D, and 30D fair-value perspectives. Guidance,
meaningful price dislocations, completed activity, and Item Price Context use an explicit 7D basis; `--lookback-days`
continues to control sync/backfill and existing chart behavior, not those report semantics.

Export every chart-capable item without adding the extra charts to the report:

```bash
warera-marketguide --from-db --charts \
  --all-price-action-charts --output output
```

The additional item-based PNGs are written sequentially under `output/charts/all/`. Items without
enough completed-transaction evidence are skipped; the report still embeds only its highlighted
price-action charts.

Export the report header, every Item Price Context card, and every table as standalone PNGs:

```bash
warera-marketguide --table-pngs --output output
```

The hero and rendered highlight cards are written to `output/sections/report-header.png`; when no
highlight qualifies, that image contains the explicit neutral dislocation state.

Each Item Price Context card is written as its own tightly cropped image under `output/cards/`,
using a stable item-based filename such as `bread-price-context.png`. The card PNG contains the
same fair value, latest completed trade, normal range, classification, execution context, and
price-range rail as the accessible HTML card.

The table images are written to `output/tables/` in the same order as the HTML report. Each PNG captures
only its browser-rendered table—without the section heading, description, or surrounding whitespace—
while preserving the dark theme, signal colors, badges, depth graphics, and other HTML styling. The
exporter uses Google Chrome or Chromium from `PATH`; set `WARERA_CHROME_PATH` if the browser
executable is installed elsewhere.

## Other input modes

### CSV

The minimum useful CSV fields are:

```text
item_name,bid,ask,trades_7d,high_7d,low_7d
```

`current_price`, `open_7d`, and `close_7d` improve compatibility-mode reports but are optional. CSV price fields must represent completed transactions supplied by the user, not the lagging game-calculated price endpoint. See `data/sample_market.csv` for a complete example.

### Custom JSON endpoint

`--api-endpoint` supports non-market JSON records through the generic API client:

```bash
warera-marketguide \
  --api-endpoint /your/custom/endpoint \
  --api-records-path result.data.items \
  --api-param limit=100 \
  --output output
```

`--api-param` may be repeated. This compatibility path does not sync data into the market database.

Run `warera-marketguide --help` for the complete option list.

## Product and market semantics

The data-source boundary is strict:

- completed transactions synced into SQLite are the source for latest traded price, price history, fair value, ranges, volume, momentum, and signal history;
- the newest order book is the source for current executable buy and sell prices, available depth, spread, pressure, and slippage;
- the WarEra price endpoint is a lagging, game-calculated value and is not an input to fair value, trends, signals, targets, or stop losses;
- missing transaction history remains missing and must not be filled with the price endpoint or the order-book midpoint.

`last_trade_price` means the newest real execution in the queried window. Best ask means the price a buyer can currently pay, and best bid means the price a seller can currently receive. These concepts must remain separate in calculations and presentation.

Transaction history drives open, high, low, close, VWAP, median, percentiles, volume, trend metrics, and historical signal inputs. Current order books are reported transparently as bid/ask quantity, monetary value, spread, pressure, cumulative levels, and fixed-budget slippage. The trading-attractiveness score remains a secondary CSV compatibility metric:

```text
Trading Attractiveness = (Effective Spread % x Window Trades) / Window Range %
```

Effective spread subtracts the minimum price tick from the raw bid/ask gap.

The intended report leads with clear actions rather than a generic market-quality score. Because
WarEra has no short selling and the report does not know the user's inventory, each item needs two
independent answers: whether a user without inventory should buy now or wait, and whether a user
holding inventory should sell now or hold. Sell guidance always means exiting owned inventory.
The decision layer should show the usable entry or exit price, how much can be traded, the profit
target, the stop-loss or invalidation level, the expected horizon, and why the signal exists.
Historical context, activity, order-book structure, and price state support that decision without
duplicating it.

More detail is available in:

- [Market database and architecture](docs/market-db-reporting-spec.md)
- [Market data semantics](docs/market-data-model-spec.md)
- [Report and liquidity semantics](docs/market-reporting-liquidity-spec.md)
- [Production Points by factory item](docs/production-points-reference.md)
- [Proposed Market Trends table](docs/market-trends-table-spec.md)
- [Project goal and data authority](docs/project-goal.md)
- [Repository architecture rules](AGENTS.md)

## Development

Run the test suite with the existing virtual environment:

```bash
.venv\Scripts\pytest      # Windows
# or
.venv/bin/pytest          # Linux/macOS
```

Only `market_store.py` accesses SQLite, only `api_client.py` performs HTTP requests, and only `warera_api.py` knows WarEra market endpoint names and payload shapes. Keep new work within those boundaries.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
