# Report and Liquidity Semantics

## Current publication

The report presents the header and sync freshness, WE23 Market Index, historical watch items,
Trading Guide, Current Order Book, Activity Comparison, Item Price Context, and a footer.
Missing evidence remains unavailable. Completed transactions supply historical values; current
orders supply execution context. The lagging game price endpoint supplies neither.

Every item is retained regardless of the legacy `--top` argument. Tables follow the guide's
`short_term_rank`, then case-insensitive item name and code. Item Price Context cards are
alphabetical. Historical watch items are selected by meaningful 7D price dislocations and
available chart evidence, not by the legacy trading-attractiveness score.

## Trading Guide

Columns are Item, Signal, Buy, Sell, Median 7D, 7D VWAP, % vs 7D reference, and Price State.
BUY takes precedence when entry guidance says BUY; otherwise holder SELL becomes SELL;
other states display HOLD. SELL means an exit for an owner, never a short position.
The underlying entry and holder decisions remain separate even though the table shows one signal.

Buy and Sell are executable ask-side and bid-side VWAPs for the configured quantity. Median and
VWAP use completed transactions in the 7D window. The percentage uses the latest completed trade
and blended 7D reference, exactly as the item chart does:

```text
(latest completed trade / stable_fair_price_7d - 1) * 100
```

The reference is 50% VWAP, 30% median, and 20% average of the last five trades within the window,
with available components reweighted. The displayed 7D VWAP column is a separate statistic;
it is not the denominator of the percentage. The gap is neither a seven-day price return nor
an executable profit. Missing or invalid inputs produce an unavailable percentage.

Price State describes historical conditions; it is not the action signal. Max Buy, Rich Sell,
targets, stops, and expected holding periods are not columns in the current compact guide.
See [market semantics](market-data-model-spec.md) for underlying threshold calculations and
[project goal](project-goal.md) for broader intended decision support.

## Activity and liquidity

Activity compares completed transaction value and PP-equivalent completed volume over 7D.
Bars are normalized within the displayed report. PP-equivalent volume is completed units times
[total upstream PP per item](production-points-reference.md); it is embodied effort, not measured
production during the window. Ingredient and processed-item rows must not be summed.
Items without a mapped factory chain retain completed value but have unavailable PP fields.

The Current Order Book shows fetched open-order structure. A derived compatibility liquidity
metric remains available in the read model:

```text
depth = bid_depth + ask_depth
spread_penalty = 1 + max(spread_pct, 0.5) / 100
liquidity = depth / spread_penalty
```

The published activity bars do not use this metric. Fetched orders are not guaranteed future fills.

## Display and exports

Table prices generally use three decimals; percentage gaps use signed two-decimal values.
Counts and quantities avoid a `.000` suffix, and large quantities may use compact notation.
The primary item chart defaults to 30 days with 4h candles; WE23 defaults to 30 days.
Their display settings are independent of download scope, retention, and 7D valuation semantics.

The CLI automatically exports PNGs and an `asset_inventory.json` manifest. Tables are captured
as complete, intrinsically sized elements without section headings or surrounding whitespace,
and published HTML replaces them with static PNGs. No scrolling or clipped overflow is allowed.
Section composites, individual cards, charts, and footer exports are separate assets. CSV outputs
retain all calculated rows. The current CLI publishes market trends/scores, WE23 series/weights, and database-backed action-cost benchmarks;
retired inflation exports are not part of the current publication.
