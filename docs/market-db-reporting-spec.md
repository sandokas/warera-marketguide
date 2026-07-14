# Market Database and Architecture

## Purpose

The local SQLite database is the source of truth for live WarEra market history. A live run fetches and stores normalized quotes, order-book observations, and transactions before reports query that stored data.

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
- `report.py` renders HTML and writes CSV output.
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

Stores each quote returned by the price endpoint with its observation timestamp. These quotes are fallbacks and comparison points, not canonical executed prices.

### `order_book_observations`

Stores a compact snapshot containing best bid, best ask, aggregate fetched depth on each side, absolute spread, and percentage spread. Individual order levels are not retained.

### `item_sync_state`

Stores the newest transaction timestamp and ID, the most recent attempted and successful sync times, the last error, and counts from the latest successful item sync. The transaction timestamp is the durable pagination high-water mark; API cursors are not persisted.

### `schema_meta`

Stores the application schema version. SQLite `user_version` is kept aligned with it.

## Sync behavior

Each sync first stores the current quote set. It then processes every non-excluded item:

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

On a new database, `--lookback-days` controls report queries but does not cap the initial incremental import. Use a page cap or explicit backfill when import scope matters.

### Backfill sync

`--transaction-backfill` ignores high-water marks and duplicate-page stopping while retaining database deduplication. It stops at the `--lookback-days` boundary, a positive page cap, or the end of API pagination.

Both `--live` and `--sync` accept backfill mode. `--sync` exits after updating the database; `--live` continues to report generation.

## Query and report behavior

`market_data.py` filters stored records by the requested lookback and produces one row per item. Supported internal window labels are `1D`, `7D`, `30D`, `90D`, and `1Y`; the CLI currently requests one window from `--lookback-days`.

Report generation writes:

```text
market_report.html
market_trends.csv
market_scores.csv
```

The scores CSV is retained as an identical compatibility output. During `--live --charts`, a featured PNG may also be written under `charts/` and embedded in the report.

## Operational guidance

Sync cadence and report lookback are separate. Cadence determines how often new quotes and order books are sampled; lookback determines which stored records contribute to a report. An hourly run is a reasonable starting point, with shorter intervals useful only for actively traded markets.

Because the API client spaces requests by 1 second by default and a sync touches every item, frequent full-market runs can take time and generate substantial traffic. Use `--min-interval` in accordance with upstream limits.

## Known upstream assumptions

- Transaction pages are assumed to be newest-first for high-water and backfill-boundary stopping.
- Order `quantity` is assumed to represent remaining open quantity when aggregate depth is calculated.

These assumptions should be revalidated if the WarEra API changes.
