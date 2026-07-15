# Proposed Market Trends Table

Status: implemented.

## Purpose

The Market Trends table answers one question:

> Is an item's recent completed-transaction price movement persistent across time horizons,
> reversing, or merely short-window noise?

Its job is historical diagnosis, not trade guidance. It must help a reader interpret a `Buy`, `Sell`,
or `Wait` result without repeating the fair-value calculation, the current order book, or completed
market activity.

## Reader workflow

The reader first uses **Fair Value & Buy / Sell Signals** to identify an actionable item. They then use
Market Trends to distinguish these materially different situations:

- a discount occurring inside a persistent decline;
- a discount whose short-term price has started to rebound;
- a premium supported by a persistent rise;
- a premium experiencing a short-term pullback;
- a flat or mixed market where a one-window move should not be treated as a durable trend.

The table also supports a fast cross-market scan: unlike the featured history chart, which shows one
item, it applies the same multi-horizon test to every displayed item.

## Unique contribution

No other report section compares direction across multiple transaction-history windows:

- **Fair Value & Buy / Sell Signals** compares executable prices with fair-value thresholds and gives
  an action. It does not establish persistence through time.
- **Current Order Book** describes cancellable bids, asks, depth, pressure, and spread at the latest
  observation. It is not completed price history.
- **Completed Market Activity** describes traded value, units, work, and transaction count. It does
  not describe price direction.
- **Item Notes** repeat selected facts for reference and must not become a second trend model.
- **Featured Price History** gives detailed history for one item, not a comparable all-item scan.

If Market Trends cannot deliver this cross-horizon comparison, it should be removed rather than
revert to a selected-window summary.

## Proposed columns

| Column | Meaning | Why it belongs |
| --- | --- | --- |
| Item | Item name | Row identity. |
| Last Trade | Newest completed-transaction price | Anchors all historical changes to a real execution, never an order-book quote. |
| 1D Change | First-to-last completed price change over 1D | Shows the immediate move. |
| 7D Change | First-to-last completed price change over 7D | Shows the intermediate move. |
| 30D Change | First-to-last completed price change over 30D | Shows the prevailing move. |
| 30D Path | Independently scaled path of the last completed trade per UTC day | Shows choppiness and the route between the 30D endpoints without replacing the precise horizon changes. |
| 30D Position | Last Trade's position between the 30D completed low and high | Distinguishes a move near its historical floor, middle, or ceiling without implying fair value. |
| Pattern | Deterministic cross-horizon classification | Summarizes persistence or reversal without issuing a recommendation. |

The table must not contain Ask, Bid, Fair, Max Buy, Rich Sell, spread, crossing cost, volume, units,
trade count, liquidity, pressure, slippage, or a Buy/Sell/Wait signal. Each belongs to another report
question.

## Calculations

All inputs come only from completed transactions stored in SQLite.

For each horizon `H`:

```text
Change H % = (last completed price H - first completed price H)
             / first completed price H * 100
```

A horizon is usable only when it contains at least two completed transactions with distinct
timestamps and a positive first price. Otherwise its change is displayed as `N/A`.

```text
30D Position % = (Last Trade - 30D Low) / (30D High - 30D Low) * 100
```

The position is clamped to `0–100%` to tolerate boundary and rounding effects. If the 30D high equals
the 30D low, display `Flat`; if 30D history is unusable, display `N/A`. The UI may pair the number with
`Near floor` (`0–33%`), `Middle` (`>33–66%`), or `Near ceiling` (`>66–100%`). These are location labels,
not valuation claims.

`30D Path` uses the last valid completed-transaction price for each UTC day in the 30D window. It is
rendered only when at least two daily observations with distinct timestamps are available. The
horizontal domain is always the full 30D window, leaving blank space where no completed daily
observation exists. Each row is independently scaled to its own observed daily-price range, so
readers compare shape, not height. The line must not be reconstructed from the horizon-change
percentages.

## Pattern classification

Each usable horizon is bucketed using a neutral band:

```text
Up      change > +0.5%
Flat    -0.5% <= change <= +0.5%
Down    change < -0.5%
```

Pattern is assigned in this order:

1. `Insufficient history`: fewer than two horizons are usable.
2. `Flat`: every usable horizon is Flat.
3. `Persistent rise`: at least two horizons are Up and none is Down.
4. `Persistent fall`: at least two horizons are Down and none is Up.
5. `Rebound`: 1D is Up, at least one longer horizon is Down, and no longer horizon is Up.
6. `Pullback`: 1D is Down, at least one longer horizon is Up, and no longer horizon is Down.
7. `Mixed`: all other combinations.

The classification must remain descriptive. It must not alter fair value, report ranking, guidance
thresholds, or signal generation.

## Presentation and ordering

- Use the same item order and `--top` selection as the main report so the reader can move between the
  guidance and context tables without re-finding items.
- Render changes as signed percentages with consistent up, down, and neutral colors.
- Render Pattern as a single compact label, with a tooltip or accessible description that lists the
  horizon directions used.
- Render 30D Position as fixed internal tracks for bar, percentage, and location label. Use spacing,
  not visible separators or internal borders.
- Render 30D Path as a neutral, time-proportional sparkline with no axes, fill, grid, or horizon
  dividers. Emphasize only the latest point and provide an accessible low/high/latest summary.
- Keep every cell to one line on the normal report width; allow horizontal scrolling on narrow screens.
- State above the table: `Completed transaction trends; descriptive context, not a trade signal.`
  Also state: `Mini paths are independently scaled; compare shape, not height.`
- Missing values must say `N/A` or `Insufficient history`; do not substitute the WarEra price endpoint,
  an order-book midpoint, bid, ask, fair value, or another horizon.

## Acceptance criteria

- A reader can distinguish persistent movement from a short-term reversal without consulting another
  item-specific chart.
- Every numeric input is transaction-derived and respects its stated horizon.
- The table contains no current execution, order-book, activity, valuation-threshold, or recommendation
  fields.
- Removing any proposed column would make persistence, reversal, or historical location harder to
  determine; adding a field already displayed elsewhere is rejected unless it changes the answer to
  the table's declared question.
- Tests cover each Pattern class, missing history, a flat 30D range, window correctness, and the ban on
  forbidden columns.
