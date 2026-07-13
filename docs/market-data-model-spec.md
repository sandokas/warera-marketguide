# Market Data Model Spec

## Purpose

Define a financial data model and migration plan that align the market guide with accepted market-data conventions.

The current implementation treats `get_prices()` as the primary market price. That is inconsistent with a transaction-driven market model, where the executed trade price is the canonical price and quote feeds are auxiliary snapshots.

This spec describes how to migrate the code and data model so:

- executed trades become the primary source of price truth;
- quote feed snapshots remain a separate reference source;
- order-book snapshots capture liquidity and depth, not the canonical price;
- report rows clearly distinguish last trade price, quote price, and order-book mid price;
- metrics are derived from real transaction history and from order-book liquidity.

## Key Principles

1. `get_transaction_page()` is the primary price source.
2. `get_prices()` is a quote snapshot and may lag, smooth, or differ from last trade price.
3. The market database is the source of truth for historical pricing and volume.
4. Order book observations are liquidity/market-impact inputs, not the single source of truth for price.
5. Report metrics should be computed from stored trades and stored observations, not from live API payloads.
6. Backward compatibility should be preserved wherever possible by keeping alias fields.

## Data Sources

### Transaction Feed

From `WarEraMarketApi.get_transaction_page()`:

- executed trade records
- computed fields:
  - `unit_price = money / quantity`
  - `created_at_epoch`
  - `fetched_at`
- this is the canonical trade history used for open/high/low/close, VWAP, volume, and trend.

### Quote Snapshot Feed

From `WarEraMarketApi.get_prices()`:

- `current_price` values for each item
- treat as `quote_price`
- do not use as the canonical current market price unless no recent trade exists.

### Order Book Snapshot

From `WarEraMarketApi.get_top_orders(item_code, limit)`:

- best bid and best ask levels
- aggregate depth across fetched levels
- spread and mid-price
- use for liquidity, imbalance, and confidence metrics

## Schema and Storage

### `transactions`

No schema change required beyond the existing model, except to explicitly document its role as the primary price source.

Use this table to answer all trade-price questions.

### `price_observations`

Rename semantics in documentation only:

- treat as quote snapshot storage
- keep schema as-is
- use for `quote_price`
- maintain history of price feed snapshots for reference and anomaly detection

### `order_book_observations`

Keep the schema and semantics for compact book snapshots.

Add report-quality derived fields in code, not schema:

- `mid_price = (best_bid + best_ask) / 2`
- `depth_imbalance_pct = (bid_depth - ask_depth) / (bid_depth + ask_depth) * 100`

### Optional future schema: `order_book_levels`

If the project needs deeper pending-order analysis, add a normalized order-book level table by migration.

## Report Price Definitions

In the report row, expose these distinct values:

- `last_trade_price`: the most recent trade price from `transactions`
- `quote_price`: the newest `price_observations.current_price`
- `mid_price`: the latest order-book midpoint from `order_book_observations`
- `current_price`: derived from the best available source using the following precedence:
  1. `last_trade_price`
  2. `quote_price`
  3. `mid_price`

This ensures the report reflects actual executed price when available while still preserving quote and liquidity context.

## Migration Plan

### Step 1: Change report and query semantics

In `src/warera_quant/market_data.py`:

- rename `latest_price`/`current_price` usage in row construction to distinguish quote vs trade price
- add a query that returns the latest transaction per item
- compute `last_trade_price` from the newest `transactions` row for each item
- preserve `latest_price` or `current_price` as a quote alias only if needed for backwards compatibility
- add `trade_price` / `quote_price` fields to output rows

### Step 2: Add transaction-based current price logic

In the report row builder:

- `last_trade_price = latest transaction unit_price`
- if no trades exist in the reporting window, fall back to the latest quote snapshot
- set `current_price` to the precedence rule above

### Step 3: Keep quote observations as auxiliary data

- keep `insert_price_observations()` and `price_observations` storage
- call it `quote_observations` in documentation or migration notes if desired
- continue to store it for trend reference and when trades are missing

### Step 4: Update score and metric logic

In `src/warera_quant/metrics.py` and `src/warera_quant/report.py`:

- use `last_trade_price` and trade-derived metrics when computing bias/fair value
- keep `quote_price` available for divergence analysis
- add a `quote_gap_pct` or similar field when `quote_price` diverges from `last_trade_price`
- add `mid_price` and `depth_imbalance_pct` to liquidity/trust calculations

### Step 5: Add new report fields

Suggested field names:

- `trade_price` or `last_trade_price`
- `quote_price`
- `mid_price`
- `quote_gap_pct`
- `trade_vwap_1d`, `trade_vwap_7d`, `trade_vwap_30d`
- `trade_median_1d`, `trade_median_7d`, `trade_median_30d`
- `bid_depth`, `ask_depth`, `depth_imbalance_pct`
- `latest_spread`, `latest_spread_pct`

Maintain legacy aliases for compatibility:

- `current_price` may continue to map to selected canonical price
- `latest_price` may remain an alias to `current_price`

## Recommended Financial Metrics

Use transaction history for price signal and order book for liquidity signal.

### Primary price signals

- `last_trade_price`: most recent execution
- `VWAP`: volume-weighted average price over the selected window
- `rolling_average`: moving average of recent trade prices
- `median_price`: median of recent trade prices
- `price_p10` / `price_p90`: trade price percentiles

### Order-book and quote signals

- `quote_price`: last observed quote endpoint price
- `mid_price`: (best_bid + best_ask)/2
- `spread_abs`: best ask minus best bid
- `spread_pct`: spread as percentage of midpoint
- `bid_depth`: total bid quantity in fetched top levels
- `ask_depth`: total ask quantity in fetched top levels
- `depth_imbalance_pct`: liquidity imbalance
- `quote_gap_pct`: `(quote_price - last_trade_price) / last_trade_price` if both exist

### Market quality and trust

Combine these signals into quality and bias metrics:

- `strong` when trades are active, spread is narrow, and depth is balanced
- `weak` when trade count is low, spread is wide, or quote differs strongly from trade price
- `volatile` when the stable range is wide relative to average price
- `thin` when total volume or trade count is low

Do not infer a buy/sell recommendation solely from `quote_price`.

## Implementation Changes

### `sync.py`

1. Keep fetching `get_prices()` and `get_top_orders()`.
2. Continue storing quote snapshots and order-book observations.
3. Continue storing transaction pages.
4. Do not treat `get_prices()` as the canonical current price in reporting.

Optional improvement:

- rename internal `prices` to `quote_prices` or `quote_observations` for clarity.

### `market_data.py`

1. Add a method to load the latest transaction price per item.
2. Build report rows with both trade price and quote price.
3. Derive canonical `current_price` from trade price first.
4. Use quote price and order-book mid as fallback.

### `report.py`

1. Use `last_trade_price` instead of raw `get_prices()` output for main price comparisons.
2. Expose quote divergence metrics.
3. Preserve backwards-compatible row keys where necessary.

## Database Migration Considerations

No schema migration is required solely to support this model if the implementation uses existing `transactions` and `price_observations` tables correctly.

However, if the codebase later wants to store the canonical trade price directly in a separate table or cache, use a migration such as:

```sql
alter table transactions add column trade_price real;
```

That is not necessary for the first pass.

## Acceptance Criteria

The implementation is correct when:

- `market_report.html` and `market_trends.csv` show `last_trade_price` for `paper` as `0.186` when the latest trade price is `0.186`.
- `quote_price` is still stored and visible when available.
- `current_price` is derived from the freshest transaction price, with fallback to `quote_price` or `mid_price` only if trades are absent.
- order-book snapshots continue to provide `best_bid`, `best_ask`, `bid_depth`, `ask_depth`, and spread metrics.
- backward-compatible fields such as `current_price` and `latest_price` continue working for unchanged report consumers.

## Testing Requirements

Add or update tests that cover:

- `load_market_rows()` returns `last_trade_price`, `quote_price`, and `current_price` with correct precedence.
- `transactions_for_window()` returns only trade rows in the requested window.
- a sync run with live trades stores `quote_price` and `last_trade_price` correctly.
- `quote_gap_pct` is computed and reported when quote and trade prices diverge.
- the report still works when no trade history exists and only quote snapshots are available.
- order-book derived fields `mid_price` and `depth_imbalance_pct` are computed correctly.

## Notes

This model is consistent with accepted financial-data practice because it separates execution data from quote data and uses trade history as the source of truth for price.

Price feed snapshots are still valuable, but they should be treated as additional context rather than the primary market price.
