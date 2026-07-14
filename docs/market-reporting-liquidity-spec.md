# Report and Liquidity Semantics

## Report focus

The HTML report presents market history and market quality: latest execution, fair value, change, range, volume, activity, spread, liquidity, and tendency. Trade-action labels are supporting interpretations, not guarantees or financial advice.

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

## Compact market table

The main table keeps these concepts separate:

- item;
- latest price;
- percentage change;
- historical minimum and maximum;
- stable fair price;
- traded volume;
- completed trade count;
- latest spread percentage;
- relative liquidity;
- market state.

The detail sections add fair-price thresholds, tomorrow bias, action context, and item notes where enough data is available.

## Output behavior

`--top 0` displays all items; a positive value limits ranked HTML sections. CSV outputs retain the full calculated data frame. The report rank follows the metric sort order, and the automatic featured chart candidate follows that same order.
