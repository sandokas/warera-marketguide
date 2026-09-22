# Market Database and Architecture

## Purpose

The local SQLite database is the source of truth for live WarEra market history. Completed
transactions are authoritative for historical price and activity calculations. Current order-book
observations and levels are authoritative for executable prices and visible liquidity. A live run
fetches and stores normalized market facts before reports query that stored data.

The WarEra price endpoint contains a lagging value calculated by the game. It may be stored for
compatibility or diagnostics, but it is not an authoritative price and must not feed ordinary
report, fair-value, trend, target, stop-loss, or signal calculations.

The application performs one sync or report pass per invocation. Scheduling belongs outside the application.

## Data flow

```text
WarEra API
  -> api_client.py
  -> warera_api.py
  -> sync.py
  -> market_store.py (SQLite)
  -> market_data.py
  -> metrics.py / charts.py / report.py
  -> CSV, HTML, and PNG output
```

Raw API payloads are parsed at the API boundary. They are not stored as JSON or passed through the normal reporting layers.

## Module responsibilities

- `api_client.py` owns HTTP transport, authentication, base URL handling, and request pacing.
- `warera_api.py` owns WarEra endpoint names, tRPC wrappers, and response validation.
- `sync.py` coordinates API reads, pagination, deduplication, and sync state.
- `market_store.py` owns schema migration and every SQLite read or write.
- `market_data.py` constructs report and chart read models from stored records.
- `metrics.py` performs calculations on domain dictionaries.
- `charts.py` renders chart-ready data.
- `report.py` renders HTML, writes CSV output, and exports browser-rendered PNG assets and their inventory.
- `cli.py` validates arguments and orchestrates these layers.

Dependency constraints are defined in [AGENTS.md](../AGENTS.md).

## Database

The default database is:

```text
data/warera_market.sqlite3
```

Use `--market-db PATH` to select another file. `MarketStore.initialize()` creates the parent directory, initializes `schema_meta`, and applies ordered migrations through `LATEST_SCHEMA_VERSION`.

### `transactions`

Stores normalized executions:

```sql
create table transactions (
    id text primary key,
    item_code text not null,
    transaction_type text,
    created_at text not null,
    created_at_epoch integer not null,
    money real,
    quantity real,
    unit_price real,
    fetched_at text not null
);
```

`unit_price` is `money / quantity` when quantity is positive. The upstream transaction ID is preferred. If it is absent, the store hashes the normalized item code, timestamp, transaction type, money, and quantity. `insert or ignore` makes repeated and overlapping fetches safe.

### `price_observations`

Stores each value returned by the lagging game-calculated price endpoint with its observation
timestamp. These records are legacy or diagnostic data. They are not fallbacks for missing
transactions and are excluded from ordinary market analysis and trading signals.

### `order_book_observations`

Stores a snapshot containing best bid, best ask, aggregate fetched depth on each side, absolute
spread, and percentage spread.

### `order_book_levels`

Stores the normalized visible bid and ask levels belonging to an order-book observation. These
levels support quantity-aware execution, depth, and slippage calculations. They describe open
orders, not completed transaction history.

### `item_sync_state`

Stores the newest transaction timestamp and ID, the most recent attempted and successful sync times, the last error, and counts from the latest successful item sync. The transaction timestamp is the durable pagination high-water mark; API cursors are not persisted.

### `item_production_config`

Stores synchronized direct production-point configuration per item. Total upstream PP remains a
separate fixed domain mapping in `metrics.py`.

### `transaction_coverage`

Stores ingestion coverage intervals. WE24's normal report calculation uses observed completed
transaction activity for daily evidence rather than treating downloader metadata as proof of completeness.

### `schema_meta`

Stores the application schema version. SQLite `user_version` is kept aligned with it.

## Sync behavior

Each sync collects direct production-point configuration and stores lagging endpoint values for compatibility or diagnostics. It then
processes every non-excluded item:

1. Mark the item sync attempt.
2. Fetch its current order book.
3. Fetch and store transaction pages.
4. Mark success with the newest transaction, or record the item error and continue.
5. Store all successfully fetched order-book observations.

### Incremental sync

Incremental sync is the default. Transactions are inserted before the stop condition is evaluated, preserving overlap safely. Pagination stops when any of these conditions is met:

- a page reaches the stored high-water timestamp;
- a page contains a transaction already in the database;
- the configured positive `--history-pages` cap is reached;
- the API returns no next cursor.

On a new database, `--lookback-days` controls sync/backfill scope and existing chart history but does not cap the initial incremental import. Use a page cap or explicit backfill when import scope matters.

### Backfill sync

`--transaction-backfill` ignores high-water marks and duplicate-page stopping while retaining database deduplication. It stops at the `--lookback-days` boundary, a positive page cap, or the end of API pagination.

Both `--live` and `--sync` accept backfill mode. `--sync` exits after updating the database; `--live` continues to report generation.

## Query and report behavior

`market_data.py` filters stored records into requested windows and produces one row per item. Supported
internal window labels are `1D`, `7D`, `30D`, `90D`, and `1Y`; database-backed reports request `1D`,
`7D`, and `30D` together. The read model retains horizon-specific statistics; the current publication uses 7D guidance and activity.

Report generation writes:

```text
market_report.html
market_trends.csv
market_scores.csv
```

The scores CSV is retained as an identical compatibility output. The current CLI also writes
`we24_series.csv`, `we24_weights.csv`, and `asset_inventory.json`, plus
`market_action_costs.csv` for database-backed action benchmarks. DB-backed highlight charts and
all report PNG exports are automatic; `--charts` and `--table-pngs` are compatibility flags.
Published tables are static PNGs. See [README](../README.md) for display settings and output details.

The published report uses a compact 7D guide and context, current orders, completed activity,
historical watch charts, and WE24. It no longer publishes the older cross-horizon Market Trends
table or inflation overview, though compatible derived fields and legacy calculations remain.

## Operational guidance

Each invocation performs one operation; schedule sync and housekeeping externally. Housekeeping
is explicit, separate from sync, and defaults to 120-day retention with compaction at most every
30 days when free pages exist. Sync freshness and report generation time are separate; partial
syncs are disclosed and failed syncs do not advance successful freshness.

Download/backfill lookback, analytical windows, chart display periods, and retention are distinct.
The report queries 1D, 7D, and 30D statistics; guide/activity use 7D. Item charts default to 30 days
and 4h candles. WE24 uses fixed inception and preceding 28-day weighting evidence, independent of
display length. Retention can remove required inception history; the current cleanup does not
permanently preserve it. See [README](../README.md#we24-market-index).

Requests are paced at one second by default. Choose sync cadence with the duration of a complete
run in mind; stored game-calculated endpoint prices remain excluded from analytical inputs.

## Known upstream assumptions

- Transaction pages are assumed to be newest-first for high-water and backfill-boundary stopping.
- Order `quantity` is assumed to represent remaining open quantity when aggregate depth is calculated.

These assumptions should be revalidated if the WarEra API changes.
