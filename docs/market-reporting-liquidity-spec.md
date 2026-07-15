# Report and Liquidity Semantics

## Report focus

The HTML report provides aggressive, clear trading guidance for the WarEra in-game market. WarEra
has no short selling, so the report must answer two position-dependent questions independently:
whether a user without inventory should buy now or wait, and whether a user holding inventory
should sell now or hold. It must show the relevant entry, target, stop-loss or invalidation level,
executable quantity, expected opportunity, and risk. A sell signal always means exiting inventory
the user already owns.

Latest execution, fair value, change, range, volume, activity, spread, liquidity, and market state
support those decisions. They are not competing goals and should not be repeated
across tables without a distinct purpose.

All historical price and activity fields come from completed transactions stored in SQLite. Current
execution and liquidity fields come from the newest order book. The lagging game-calculated price
endpoint must not appear as a fallback or signal input.

## Display conventions

- Price-like values use three decimal places.
- Volume is total traded quantity for the selected window.
- Trades is the number of completed transactions for that window.
- Counts and quantities do not receive a `.000` suffix.
- Large quantities may use compact notation such as `31.6K`.
- Signed changes use visual direction cues consistently.

## Liquidity

Liquidity is based on current fetched order-book depth and spread, not trade count multiplied by volume:

```text
depth = bid_depth + ask_depth
spread_penalty = 1 + max(spread_pct, 0.5) / 100
liquidity = depth / spread_penalty
```

Higher depth and a tighter spread increase the value. Missing depth produces a low value rather than an invented proxy.

The horizontal liquidity bar is relative to the rows currently displayed:

```text
bar width = row liquidity / maximum displayed liquidity * 100
```

It supports quick comparison within one report and should not be compared as an absolute scale across separate reports.

## Table responsibilities

Each table must state and answer one decision question. At minimum, the report should keep these
concepts visibly distinct:

- buyer action: buy now or wait to buy;
- holder action: sell now or hold;
- valuation: latest completed transaction, historical range, and transaction-derived fair value;
- execution: best ask, best bid, available quantity, spread, and slippage;
- activity: completed volume, value, transaction count, and total upstream Production Points (PP)
  per item used for embodied PP-equivalent volume comparisons;
- direction: transaction-derived momentum and price state;
- risk plan: target, stop loss or invalidation, downside, and expected horizon.

Columns that answer different questions must not be combined merely because they fit in one table.
Supporting visualizations—including the order book, activity bars, and price-state indicators—should
be retained when their calculation and purpose are clear.

The fixed PP-per-item reference values and the distinction between direct recipe PP and full
upstream PP are documented in [Production Points by Factory Item](production-points-reference.md).

The fair-value guidance table uses one single-line signal per item: `Buy` when the executable Ask
reaches `Max Buy`, `Sell` when the executable Bid reaches `Rich Sell`, and `Wait` otherwise. Sell is
explicitly holder guidance, not short-selling advice. Ask and Bid are quantity-aware executable
VWAPs, `Max Buy` is fee- and margin-adjusted against Fair and capped by P25 or P10 in risky states,
`Rich Sell` requires the configured Fair premium subject to the empirical upper range, and `Ask
Upside` compares the executable Ask with Fair after fees. A synthetic symmetric band and the return
between its endpoints must not be shown as executable room or profit. Table cells must remain one
line high.

## Output behavior

`--top 0` displays all items; a positive value limits ranked HTML sections. CSV outputs retain the full calculated data frame. The report rank follows the metric sort order, and the automatic featured chart candidate follows that same order.
