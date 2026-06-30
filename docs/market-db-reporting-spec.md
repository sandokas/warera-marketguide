# Market Database and Reporting Spec

## Goal

Persist WarEra market data locally and use it as the foundation for trend-oriented market reports.

The report generator should:

- store every fetched transaction in a local database;
- store price and order-book observations needed for offline reports;
- ignore duplicate transactions safely;
- fetch only the new pages needed since the last successful sync for each item when possible;
- continue to support full historical backfill;
- make SQLite the source of truth for market history;
- move reports and charts to typed data/query interfaces;
- focus reports on price evolution, ranges, averages, volume, and tendencies;
- keep the existing CSV/report/chart output files available.

## Non-Goals

- Replacing the WarEra API client.
- Removing the existing market-making score entirely.
- Building a multi-user service or daemon.
- Guaranteeing complete history if the upstream API deletes or mutates old transaction pages before they are fetched.
- Supporting JSON snapshots, JSON import, or JSON export.

## Storage Choice

Use SQLite through Python's standard `sqlite3` module.

Rationale:

- no new runtime dependency;
- one portable file is enough for this project;
- unique indexes and transactions give reliable dedupe;
- the data volume should remain small enough for local analytical queries.

Default path:

```text
data/warera_market.sqlite3
```

Add a CLI option to override it:

```text
--market-db data/warera_market.sqlite3
```

## Schema

### `transactions`

Stores normalized transaction records.

```sql
create table if not exists transactions (
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

create index if not exists idx_transactions_item_created
    on transactions (item_code, created_at_epoch desc, id);
```

Prefer a true upstream id if present. If no upstream id exists, derive `id` from normalized fields:

```text
sha256(item_code + createdAt + transactionType + money + quantity)
```

If later API inspection shows collisions are possible, add the required discriminator columns through a schema migration.

Known transaction fields from saved API responses:

- stored now: `_id`/`id`, `itemCode`, `money`, `quantity`, `transactionType`, `createdAt`;
- derived now: `unit_price`, `created_at_epoch`, `fetched_at`;
- not stored in schema v1: `sellerId`, `buyerId`, `offerCreatedAt`, `updatedAt`, `__v`.

Consider adding `seller_id`, `buyer_id`, `offer_created_at`, and `updated_at` before a large historical backfill if user concentration, repeat counterparties, order-fill latency, or API mutation auditing become important.

### `price_observations`

Stores the current price returned by the WarEra market price endpoint each time the sync runs.

```sql
create table if not exists price_observations (
    id integer primary key autoincrement,
    item_code text not null,
    observed_at text not null,
    observed_at_epoch integer not null,
    current_price real not null
);

create index if not exists idx_price_observations_item_time
    on price_observations (item_code, observed_at_epoch desc);
```

This table lets reports show price evolution even when there are few completed trades.

### `order_book_observations`

Stores a compact current order-book observation for each item each time the sync runs.

```sql
create table if not exists order_book_observations (
    id integer primary key autoincrement,
    item_code text not null,
    observed_at text not null,
    observed_at_epoch integer not null,
    best_bid real,
    best_ask real,
    bid_depth real,
    ask_depth real,
    spread_abs real,
    spread_pct real
);

create index if not exists idx_order_book_observations_item_time
    on order_book_observations (item_code, observed_at_epoch desc);
```

Store aggregate depth from the fetched current best bids and best asks, not the full raw order-book payload. If full depth analysis becomes useful later, add an `order_book_levels` table by migration.

Known current order-book fields from saved API responses:

- aggregated now: `price`, `quantity`, side, best bid, best ask, bid depth, ask depth, spread;
- not stored in schema v1: order `_id`, `user`, `offerAt`, `type`, `itemCode`, `__v`, and individual order levels.

Consider adding `order_book_levels` before a large historical backfill if order age, depth curve, wall detection, user concentration, or top-order churn become important.

### `item_sync_state`

Tracks the newest durable point known for each item.

```sql
create table if not exists item_sync_state (
    item_code text primary key,
    newest_created_at text,
    newest_created_at_epoch integer,
    newest_transaction_id text,
    last_successful_sync_at text,
    last_attempted_sync_at text,
    last_error text,
    pages_fetched integer not null default 0,
    transactions_inserted integer not null default 0
);
```

`newest_created_at_epoch` is the durable high-water mark. API cursors should not be stored as the primary resume marker because cursors are usually pagination-local and may expire or shift.

### `schema_meta`

Simple migration marker and database metadata store.

```sql
create table if not exists schema_meta (
    key text primary key,
    value text not null
);
```

Set `schema_meta["version"] = "1"`.

## Migration Control

Use explicit, ordered SQLite migrations managed by `market_store.py`.

Do not rely on scattered `create table if not exists` calls after version 1. They are fine for initial creation, but they become hard to reason about once columns, indexes, or backfills are needed.

Recommended implementation:

```python
LATEST_SCHEMA_VERSION = 1

MIGRATIONS = {
    1: migrate_to_v1,
}
```

On database open:

1. Ensure `schema_meta` exists.
2. Read `schema_meta["version"]`; missing means `0`.
3. If the version is greater than `LATEST_SCHEMA_VERSION`, stop with a clear error because the DB was created by newer code.
4. Run each migration from `current_version + 1` through `LATEST_SCHEMA_VERSION` in order.
5. Wrap each migration in one SQLite transaction.
6. After each migration succeeds, update `schema_meta["version"]`.
7. Set `PRAGMA user_version` to the same integer for easy inspection with external SQLite tools.

Migration files are not necessary yet; keep migration functions in `market_store.py` until the file becomes noisy. If the project grows beyond a few migrations, move them to:

```text
src/warera_quant/db_migrations/
```

Each migration must be idempotent enough to survive a partially initialized development database, but the version number remains the source of truth. Schema changes should be additive where possible:

- add nullable columns first;
- backfill data in a separate step if needed;
- create new indexes with stable names;
- avoid destructive migrations unless there is a documented backup/export path.

Future CLI helpers:

```text
--db-info
--migrate-db
```

`--db-info` prints database path, schema version, item count, transaction count, oldest transaction, newest transaction, and last successful sync. `--migrate-db` opens the database, runs pending migrations, prints the old and new versions, then exits.

## Sync Modes

### Pull Cadence

The tool should not run an internal scheduler. It should perform one sync/report pass per command invocation, then exit. Cadence belongs outside the app, via cron, systemd timers, GitHub Actions, or a long-running wrapper later.

Recommended cadence:

- Active market watching: every 15 minutes.
- Normal report usage: every 30 to 60 minutes.
- Low-interest tracking: every 6 to 24 hours.
- Historical archive only: daily.

Avoid minute-by-minute pulls by default. The current live fetch touches prices, order books, and transaction pages for every item, so a one-minute cadence can create a lot of API traffic without much extra signal unless WarEra trading volume is very high.

The default documented recommendation should be hourly:

```text
PYTHONPATH=src python3 run_report.py --live --market-db data/warera_market.sqlite3 --output output/live
```

For short-term monitoring, users can run the same command every 5 or 15 minutes. For long lookbacks, the database makes report generation less sensitive to cadence because the report can query all locally stored transactions in the requested window.

### Freshness and Lookback

Sync cadence and report lookback are separate:

- cadence controls how often new transactions are pulled;
- `--lookback-days` controls how much stored history is used for metrics and charts.

Examples:

- Pull every 15 minutes and report `--lookback-days 1` for intraday behavior.
- Pull hourly and report `--lookback-days 7` for steadier trade selection.
- Pull daily and report `--lookback-days 30` for slower markets.

If the API exposes only limited recent history, cadence matters more. The sync should warn when the oldest fetched page is still newer than the previous high-water mark, because that means a gap may exist.

Add optional freshness controls:

```text
--max-sync-age MINUTES
--allow-stale-db
```

When using `--offline`, fail or warn if the newest transaction per item is older than `--max-sync-age`, unless `--allow-stale-db` is set.

### Incremental Sync

Default behavior when `--live --market-db` is enabled.

For each item:

1. Read `item_sync_state.newest_created_at_epoch`.
2. Fetch transaction pages from the first page.
3. Insert each returned transaction with `insert or ignore`.
4. Continue fetching pages until one of these conditions is met:
   - no `nextCursor` is returned;
   - the page's oldest transaction is older than or equal to the stored high-water mark;
   - `--history-pages` is positive and the page cap is reached.
5. Update the item sync state only after all pages for that item are processed successfully.

Because multiple transactions can share the same timestamp, the stop condition should include a small overlap page instead of stopping before processing the page. The unique transaction id handles duplicates.

### Backfill Sync

Used for first run or explicit historical repair.

CLI:

```text
--transaction-backfill
```

Behavior:

- ignore the high-water mark;
- fetch until no cursor, until the lookback boundary, or until `--history-pages` is reached;
- still dedupe with `insert or ignore`.

## CLI

Implemented options:

```text
--market-db PATH
--transaction-backfill
```

Planned options:

```text
--offline
--report-window 1D
--report-window 7D
--report-window 30D
--max-sync-age MINUTES
--allow-stale-db
--db-info
--migrate-db
```

Recommended behavior:

- `--live` uses the market DB by default.
- `--transaction-backfill` is valid only with `--live`.
- `--offline` builds reports from SQLite only and does not call the API.
- `--report-window` may be passed multiple times. Defaults are `1D`, `7D`, and `30D`.
- `--db-info` and `--migrate-db` are maintenance commands and should exit before report generation.
- `--max-sync-age` is most useful with `--offline`, but can also warn after `--live` if the newest stored transaction is older than expected.

Validation:

- `--offline` is mutually exclusive with `--live` and `--api-endpoint`.
- `--transaction-backfill` requires `--live`.
- `--allow-stale-db` requires `--max-sync-age`.
- `--report-window` values must be duration strings such as `1D`, `7D`, `30D`, `90D`, or `1Y`.

## Architecture

The new architecture should separate acquisition, persistence, querying, analysis, and output.

```text
WarEra API
  -> API gateway
  -> sync service
  -> SQLite repositories
  -> market data/query service
  -> metrics/charts/report writers
```

Raw API responses are parsed at the API boundary. They should not be persisted as JSON or passed between normal application layers.

### Current Structure

- `api_client.py` owns generic HTTP calls and authentication.
- `warera_api.py` owns WarEra market endpoint names and response parsing.
- `sync.py` orchestrates API-to-database sync.
- `market_store.py` owns SQLite access.
- `market_data.py` owns report and chart read models.
- `metrics.py`, `charts.py`, and `report.py` own calculations and output rendering.

New code should not add direct SQL calls, direct `requests` calls, or endpoint-specific parsing outside the layers listed below.

### Dependency Rules

Allowed dependencies:

```text
cli.py
  -> sync.py
  -> market_data.py
  -> metrics.py
  -> charts.py
  -> report.py

sync.py
  -> warera_api.py
  -> market_store.py

market_data.py
  -> market_store.py

metrics.py, charts.py, report.py
  -> domain dictionaries/dataframes only
```

Forbidden dependencies:

- `metrics.py`, `charts.py`, and `report.py` must not import `sqlite3`, `requests`, `WarEraApiClient`, `MarketStore`, or `warera_api.py`.
- `market_data.py` must not call the WarEra API.
- `warera_api.py` must not write to SQLite.
- `market_store.py` must not call the WarEra API.
- `cli.py` should orchestrate modules but should not contain SQL, endpoint parsing, pagination loops, or trend formulas.

These rules are meant to prevent duplicated parsing/query logic and keep bugs from spreading across layers.

### API Gateway Layer

`src/warera_quant/warera_api.py` is the only module that knows WarEra market endpoint shapes.

`api_client.py` should remain a low-level HTTP client:

- base URL normalization;
- authentication header;
- rate limiting;
- JSON request/response transport.

`warera_api.py` should wrap `WarEraApiClient` and expose typed/domain-oriented methods:

```python
class WarEraMarketApi:
    def get_prices(self) -> dict[str, float]: ...
    def get_top_orders(self, item_code: str, limit: int) -> TopOrders: ...
    def get_transaction_page(
        self,
        item_code: str,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> TransactionPage: ...
```

API gateway responsibilities:

- know WarEra market endpoint names;
- parse tRPC response wrappers;
- normalize response fields into dataclasses or plain typed dictionaries;
- own cursor handling data structures;
- raise clear domain errors for malformed API responses.

No other module should call `client.get_json()` directly for WarEra market data.

### Persistence Layer

`src/warera_quant/market_store.py` owns persistence.

Suggested public functions:

```python
class MarketStore:
    def __init__(self, path: str | Path): ...
    def initialize(self) -> None: ...
    def get_item_state(self, item_code: str) -> ItemSyncState | None: ...
    def upsert_transactions(self, item_code: str, transactions: list[dict[str, Any]]) -> InsertSummary: ...
    def insert_price_observations(self, prices: dict[str, float], observed_at: datetime) -> None: ...
    def insert_order_book_observations(self, orders: dict[str, Any], observed_at: datetime) -> None: ...
    def mark_item_sync_success(self, item_code: str, summary: SyncSummary) -> None: ...
    def mark_item_sync_failure(self, item_code: str, error: Exception) -> None: ...
    def transactions_for_window(self, item_code: str, since_epoch: int) -> list[dict[str, Any]]: ...
```

Persistence layer responsibilities:

- initialize and migrate the SQLite schema;
- normalize API transactions into database rows;
- enforce dedupe;
- expose query methods that return typed records or plain domain dictionaries;
- hide SQL from report/chart code.
- be the only module that imports `sqlite3`.

### Sync Layer

Add `src/warera_quant/sync.py`.

Sync layer responsibilities:

- fetch current item prices;
- fetch current order-book bid/ask depth;
- fetch paginated transactions;
- call repositories to persist normalized data;
- update per-item sync state;
- return a sync summary for CLI output.

API cursors stay inside this layer. They are not the durable data model.

The sync layer should not calculate report metrics. It only moves fresh market data from the API gateway into the repository layer.

### Query Layer

Add `src/warera_quant/market_data.py`.

Suggested public functions:

```python
def load_market_rows(
    store: MarketStore,
    *,
    windows: list[str],
) -> list[dict[str, Any]]: ...

def load_chart_trades(
    store: MarketStore,
    *,
    item_code: str,
    window: str,
) -> list[dict[str, Any]]: ...
```

Query layer responsibilities:

- turn stored transactions into report-ready rows;
- compute open/high/low/close inputs from SQLite records;
- provide chart-ready trade series without exposing raw API pages;
- keep lookback filtering in one place.
- centralize all read-model construction so report/chart modules do not repeat query logic.

### Analysis and Output Layer

Existing modules should remain mostly focused:

- `metrics.py`: scoring and market metrics.
- `charts.py`: chart construction from trade records or candle frames.
- `report.py`: HTML/CSV/Markdown output.
- `cli.py`: argument parsing and orchestration only.

Chart code receives normalized trades from `market_data.py` or receives already-built OHLC candles.

## Report Strategy

The main report should be a market history and trend report, not primarily a day-trading picker.

Default report windows:

```text
1D, 7D, 30D
```

Optional longer windows when the database has enough history:

```text
90D, 1Y
```

Per item, compute these fields for each report window:

- last trade price;
- latest observed current price;
- trade count;
- traded quantity;
- traded value;
- min trade price;
- max trade price;
- average trade price;
- volume-weighted average price;
- median trade price if cheap enough to compute;
- open price and close price for the window;
- absolute and percent change from open to close;
- rolling average price;
- distance from rolling average;
- best bid, best ask, and spread from the latest order-book observation;
- average spread over the window if order observations exist;
- liquidity score from trade count, traded quantity, and spread.

Trend labels should be descriptive rather than prescriptive:

- `Rising`: close is above open and above the rolling average.
- `Falling`: close is below open and below the rolling average.
- `Range-bound`: price stayed inside a narrow min/max band.
- `Volatile`: wide min/max range relative to average price.
- `Thin`: low trade count or stale observations.
- `Stable`: low range, reasonable volume, and small spread.

The report should highlight:

- strongest risers over 7D and 30D;
- strongest fallers over 7D and 30D;
- most volatile items;
- most stable/liquid items;
- items near their 30D low;
- items near their 30D high;
- items with widening or narrowing spreads;
- items with rising volume.

The current market-making/trading-attractiveness score can remain as a secondary column or separate section, but it should not dominate the report. The main user question becomes "how has this market been behaving?" rather than "what should I flip today?"

### Report Outputs

Keep writing:

```text
market_report.html
market_scores.csv
```

Rename or add richer outputs when implemented:

```text
market_history.csv
market_trends.csv
```

`market_scores.csv` can remain for backward compatibility with the existing code, but the primary table should become `market_trends.csv`.

### Charts

Charts should support:

- selected item price history;
- min/max band over the selected window;
- rolling average line;
- volume bars;
- optional bid/ask spread line when order observations exist.

The first chart in the HTML report should show a representative market-history view, not only the highest-ranked trade candidate.

## Dedupe Rules

Primary dedupe is the `transactions.id` primary key.

The sync loop must still be safe if:

- a previous run crashed after inserting transactions but before updating `item_sync_state`;
- the API returns overlapping pages;
- `--transaction-backfill` is run multiple times;
- two transactions have the same `createdAt`.

All inserts should happen in database transactions. Use `insert or ignore`; do not delete rows during normal sync.

## Error Handling

Per item:

- set `last_attempted_sync_at` before fetching;
- on success, clear `last_error` and update high-water fields;
- on failure, keep inserted rows but do not advance the high-water mark beyond what was successfully committed for the item unless the whole item sync completed.

The CLI should print a short summary:

```text
Bread: fetched 2 pages, inserted 37 new transactions, skipped 163 duplicates
```

If an item fails, continue with the next item unless the API/client error indicates authentication or global service failure.

## Tests

Add focused tests for `market_store.py`:

- initializes schema;
- inserts transactions and computes unit price;
- ignores duplicate transaction ids;
- derives stable ids when upstream id is missing;
- returns only rows inside a lookback window;
- updates sync state only on success.

Add live-market tests with a fake client:

- first sync fetches pages and stores transactions;
- second sync stops after reaching the previous high-water mark;
- overlapping pages do not duplicate trades;
- `--history-pages` still caps fetches;
- DB-backed rows match the current metric inputs for the same transaction data.

Add architecture tests:

- chart builders accept normalized trades or OHLC candles;
- report generation can run from `market_data.py` rows;
- normal DB-backed runs do not write JSON snapshots;
- CLI no longer exposes snapshot import/export options.
- only `market_store.py` imports `sqlite3`;
- only `api_client.py` imports `requests`;
- only `warera_api.py` knows WarEra market endpoint names;
- `metrics.py`, `charts.py`, and `report.py` do not import API or repository modules.

Add report tests:

- computes min, max, average, VWAP, open, close, and percent change per item/window;
- labels rising, falling, range-bound, volatile, thin, and stable markets;
- ranks strongest risers, fallers, volatile items, stable/liquid items, and near-high/near-low items;
- builds charts from database-backed trade and observation queries.

## Open Questions

- Confirm whether transaction pages are returned newest-first. The current code assumes this because it treats the last item in a page as the oldest.
- Confirm whether current order-book `quantity` always represents remaining open quantity, so `bid_depth` and `ask_depth` are computed correctly.
