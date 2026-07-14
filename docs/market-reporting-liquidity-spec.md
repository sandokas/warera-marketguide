# Standard Market-Style Reporting Spec

## Goal

Update the report so it presents liquidity and volume in a way that resembles normal market data displays, while preserving the liquidity bar because it is useful for quick scanning.

## Desired presentation

The report should present the following concepts clearly and separately:

- Price fields: current/latest/fair/buy/sell values should keep three decimal places.
- Volume: total traded quantity for the selected window.
- Trades: number of completed transactions for the selected window.
- Liquidity: a relative measure based on order-book depth and spread, shown as a bar.

## Semantics

### Price formatting

- Display price-like values with three decimal places.
- Do not append `.000` to non-price values such as volume or trade counts.
- Keep formatting consistent across the compact table and the detail cards.

### Volume

- Volume should represent the sum of traded quantity over the selected window.
- Display values as whole numbers for small values and compact notation for large values where helpful.
- Do not use a synthetic large-scale score for volume.

### Trades

- Trades should be the count of completed transactions.
- Keep it separate from volume.

### Liquidity

Liquidity should be derived from market structure, not from trade count multiplied by quantity.

Recommended inputs:

- best bid depth
- best ask depth
- current spread percentage (or spread absolute if percentage is unavailable)

Recommended formula:

```text
depth = bid_depth + ask_depth
spread_penalty = max(spread_pct, 0.5) / 100 + 1
liquidity_score = depth / spread_penalty
```

Interpretation:

- higher depth and tighter spreads increase liquidity
- wider spreads reduce the apparent liquidity score

### Liquidity bar

- Keep the horizontal bar.
- Use it to show relative liquidity across items in the report.
- Normalize the liquidity score across the current report rows so the bar reflects relative strength rather than an absolute, inflated number.
- The bar should be visually comparable across rows.

## Reporting columns

Recommended columns for the compact report:

- Item
- Latest
- Change %
- Min
- Max
- Fair
- Volume
- Trades
- Spread %
- Liquidity
- Market State

## Display rules

- Prices should use three decimals.
- Volume and trades should not include trailing `.000`.
- Liquidity should be displayed as a bar plus a compact textual value for the tooltip or summary, not as an inflated large-number score.
- If depth data is missing, fall back to a neutral or low liquidity state rather than inventing a large score.

## Acceptance criteria

A report follows this spec when:

1. Price values render with three decimal places.
2. Volume values render without `.000`.
3. Trade counts render as whole numbers.
4. Liquidity is driven by depth and spread rather than a trade-count × quantity score.
5. The liquidity bar remains present and is comparable across items.
6. The numbers look plausible for a normal market report.
