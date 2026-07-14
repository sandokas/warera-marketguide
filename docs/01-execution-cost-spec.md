# Spec 1: Order-Book Levels and Executable Cost

Status: Proposed  
Implementation order: 1 of 3  
Required by: [Spec 2](02-forecast-validation-spec.md), [Spec 3](03-profit-first-flip-report-spec.md)

## Objective

Make execution cost calculable for a requested quantity. Preserve normalized order-book levels instead of combining quantities from different prices into one ambiguous depth value.

This spec is a data and calculation foundation. It must not redesign the HTML report or invent a profitable-trade signal.

## Problem

The current order-book observation stores the best bid and ask, but `bid_depth` and `ask_depth` sum every fetched order regardless of price. A report consumer cannot determine:

- how many units are available at the displayed best price;
- the volume-weighted price for a larger order;
- whether the fetched book can fill the requested quantity;
- slippage relative to the best quote;
- how much capacity is present before a flip stops being attractive.

The current store also understands alternative API field names while parsing order payloads. API response-shape knowledge belongs in `warera_api.py`, not `market_store.py`.

## User outcomes

After implementation, downstream code can ask:

```text
What would it cost to buy 250 units from the latest fetched asks?
What would I receive by selling 250 units into the latest fetched bids?
Was the requested quantity fully executable within the fetched book?
What was the VWAP and slippage from the best quote?
How old was the snapshot?
```

No function may describe this as guaranteed execution. The result is an estimate based on the fetched snapshot.

## Architecture

Respect the repository dependency boundaries:

- `warera_api.py`: validate and normalize WarEra order payloads.
- `sync.py`: pass normalized order-book snapshots to the store.
- `market_store.py`: migrate SQLite and persist/query snapshots and levels.
- `market_data.py`: construct execution read models from stored levels.
- `metrics.py`: perform pure sweep, VWAP, and slippage calculations.
- `cli.py`: no SQL or order-book arithmetic.
- `report.py`: no work in this spec beyond preserving compatibility if needed.

Do not store raw JSON.

## Domain contracts

Add immutable domain types at the API boundary:

```python
@dataclass(frozen=True)
class OrderLevel:
    price: float
    quantity: float

@dataclass(frozen=True)
class TopOrders:
    buy_orders: list[OrderLevel]
    sell_orders: list[OrderLevel]
```

Parsing rules:

- `price` and `quantity` are required finite numbers.
- Reject negative prices and negative quantities.
- Zero-quantity levels may be discarded.
- Preserve duplicate price levels as returned; aggregation belongs in the normalized snapshot construction, not API-shape parsing.
- Sort bids descending and asks ascending before persistence.
- The first bid and ask after sorting are the best quotes.
- Error messages must identify the endpoint field and invalid entry.

If the upstream payload exposes an order identifier, do not persist it unless it is required to distinguish economically different levels. Price and quantity are sufficient for this spec.

## Database migration

Increment `LATEST_SCHEMA_VERSION` from 1 to 2 and add an ordered migration. Existing databases must upgrade without losing compact observations.

Create:

```sql
create table order_book_levels (
    id integer primary key autoincrement,
    observation_id integer not null,
    side text not null check (side in ('bid', 'ask')),
    level_position integer not null check (level_position >= 0),
    price real not null check (price >= 0),
    quantity real not null check (quantity > 0),
    foreign key (observation_id) references order_book_observations(id) on delete cascade,
    unique (observation_id, side, level_position)
);

create index idx_order_book_levels_observation_side
    on order_book_levels (observation_id, side, level_position);
```

Keep all existing columns in `order_book_observations` for compatibility. For new snapshots:

- `best_bid` and `best_ask` come from normalized sorted levels;
- `bid_depth` and `ask_depth` remain total fetched depth across each side;
- levels are inserted in the same transaction as their parent observation;
- a failed level insert rolls back the parent observation.

Old version-1 observations legitimately have no rows in `order_book_levels`. Readers must return `levels_available=False` rather than fabricating levels from aggregate depth.

## Store API

Refactor `MarketStore.insert_order_book_observations` to accept normalized domain data. It must not know `buyOrders`, `sellOrders`, `bids`, or other API aliases.

Add focused read methods, with exact names allowed to vary if repository conventions suggest better names:

```python
latest_order_book_with_levels(item_code: str) -> dict[str, Any] | None
order_book_levels(observation_id: int) -> list[dict[str, Any]]
```

The latest snapshot read model must include:

```text
observation_id
item_code
observed_at
observed_at_epoch
best_bid
best_ask
bid_depth
ask_depth
bids: [{price, quantity, level_position}]
asks: [{price, quantity, level_position}]
levels_available
```

No SQLite import is allowed outside `market_store.py`.

## Execution calculation

Add a frozen calculation result in `metrics.py`:

```python
@dataclass(frozen=True)
class SweepResult:
    side: str                 # "buy" or "sell"
    requested_quantity: float
    filled_quantity: float
    unfilled_quantity: float
    fully_filled: bool
    gross_value: float
    average_price: float | None
    best_price: float | None
    worst_price: float | None
    slippage_abs: float | None
    slippage_pct: float | None
```

Expose a pure function accepting domain dictionaries or `OrderLevel` values, but not store objects:

```python
calculate_book_sweep(levels, *, side: str, quantity: float) -> SweepResult
```

Rules:

- A buy consumes asks from lowest price upward.
- A sell consumes bids from highest price downward.
- Reject a non-finite or non-positive requested quantity.
- Ignore no levels silently except zero quantities already removed at parsing; invalid calculation inputs should raise `ValueError`.
- `gross_value` is the sum of `filled_at_level * level_price`.
- `average_price = gross_value / filled_quantity` when filled quantity is positive.
- Buy slippage is `(average_price - best_ask) / best_ask * 100`.
- Sell slippage is `(best_bid - average_price) / best_bid * 100`.
- Slippage is non-negative for a correctly sorted uncrossed book.
- Partial fills retain their calculated VWAP and set `fully_filled=False`.
- An empty book returns zero filled quantity and `None` price fields.
- Do not extrapolate beyond fetched depth.

Add a read-model helper in `market_data.py` that combines the latest stored levels and the pure calculation for a requested quantity. It must also return snapshot age/freshness inputs, but freshness classification belongs to Spec 3.

## Compatibility

- Existing compact order-book queries and report fields must keep working.
- Version-1 databases must migrate in place.
- CSV and JSON compatibility input paths must not be required to provide level data.
- Missing levels must degrade to `levels_available=False`; they must not crash report generation.
- Preserve the current best-bid, best-ask, spread, and aggregate-depth semantics for existing consumers.

## Tests

Add or update tests in the layer they exercise.

### API tests

- Valid orders become sorted `OrderLevel` objects.
- Numeric strings are accepted if current endpoint conventions accept them.
- Missing, non-numeric, infinite, or negative price/quantity values fail clearly.
- Zero quantity is discarded.

### Store tests

- A new database initializes directly at schema version 2.
- A handcrafted version-1 database migrates to version 2 without losing observations.
- Parent snapshots and all levels are inserted atomically.
- Bid and ask positions are deterministic.
- Latest snapshot queries return levels and `levels_available=True`.
- Migrated legacy snapshots return no levels and `levels_available=False`.
- Only `market_store.py` imports `sqlite3`.

### Metrics tests

- One-level full buy and sell.
- Multi-level VWAP buy and sell.
- Partial fill.
- Empty book.
- Invalid quantity.
- Correct buy and sell slippage direction.
- Input levels are not mutated.

### Integration tests

- Sync passes normalized levels from `warera_api.py` through `sync.py` into SQLite.
- Existing sync progress and transaction behavior remain intact.

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest
```

## Acceptance criteria

- Individual levels are available for every newly synced snapshot.
- A requested quantity produces deterministic fill, VWAP, and slippage results.
- Insufficient fetched depth is explicit and never extrapolated.
- Legacy databases and compatibility report paths still work.
- API shape parsing is removed from `market_store.py`.
- The full test suite passes.

## Non-goals

- No profitability verdict.
- No fee model.
- No fill-probability estimate for passive orders.
- No forecast or backtest.
- No HTML redesign.
- No order placement or automated trading.

## Focused implementation prompt

```text
Implement docs/01-execution-cost-spec.md completely in /home/david/src/warera-marketguide.

Read AGENTS.md and the entire spec before editing. Preserve the layered architecture: normalize WarEra payloads in warera_api.py, orchestrate in sync.py, keep all SQLite work in market_store.py, construct read models in market_data.py, and keep sweep arithmetic pure in metrics.py. Add the version-2 migration, persist normalized order-book levels atomically, expose latest levels, and implement quantity-aware VWAP/fill/slippage calculations. Preserve all version-1 behavior and compatibility paths. Add the specified unit, migration, and integration tests. Use apply_patch for edits and the existing .venv. Run the focused tests while developing and finish by running PYTHONPATH=src .venv/bin/python -m pytest. Do not redesign the report, add forecasts, or invent profitability signals in this task. Report changed files, migration behavior, and test results.
```
