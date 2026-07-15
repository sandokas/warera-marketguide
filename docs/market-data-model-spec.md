# Market Data Semantics

## Price authority

The model keeps completed transactions and current order-book prices distinct:

- `last_trade_price` is the newest real executed unit price in the queried window and is the latest
  historical price.
- `best_ask` is the lowest visible order-book price currently available to a buyer.
- `best_bid` is the highest visible order-book price currently available to a seller.
- `mid_price` is `(best_bid + best_ask) / 2` when both sides exist and describes the book only.
- compatibility fields such as `current_price` and `latest_price`, where retained, must resolve to a
  completed transaction price and must not fall back to the game-calculated endpoint price or the
  midpoint.

The WarEra price endpoint is a lagging value calculated by the game. A stored `quote_price` is
legacy or diagnostic data, not a market-analysis input. It must not feed transaction metrics, fair
value, market state, signals, targets, stop losses, or rankings.

## Transaction metrics

For each report window, stored executions provide:

- trade and priced-trade counts;
- traded quantity and traded value;
- open, close, minimum, maximum, and percentage change;
- arithmetic average and volume-weighted average price (VWAP);
- median, 10th percentile, and 90th percentile;
- rolling average and distance from that average;
- a stable fair price and stable range.

Volume is the sum of transaction quantities. VWAP weights unit price by positive quantity. The stable fair price combines VWAP (50%), median (30%), and rolling average (20%), reweighting the available inputs. The stable range is the 10th-to-90th percentile span as a percentage of the median.

When a window has no executions, transaction-derived prices and metrics are unavailable. Neither a
price-endpoint observation nor an order-book midpoint may fill average, minimum, maximum, fair-price,
momentum, or other transaction-derived fields.

## Order-book metrics

The newest compact order-book observation provides:

- best bid and ask;
- aggregate fetched bid and ask depth;
- absolute and percentage spread;
- midpoint;
- depth imbalance.

Depth imbalance is:

```text
(bid_depth - ask_depth) / (bid_depth + ask_depth) * 100
```

Positive values indicate more fetched bid depth; negative values indicate more fetched ask depth. It is absent when total depth is zero.

Average spread fields use all stored order-book observations inside the selected window.

## Market state and trading guidance

The read model classifies market state using completed-transaction direction, rolling-average
position, stable range, trade count, volume, and current spread. Labels such as `Rising`, `Falling`,
`Range-bound`, `Volatile`, `Thin`, and `Stable` summarize supporting evidence.

The product's decision layer combines that evidence with transaction-derived valuation and current
executable order-book conditions. It separately issues entry guidance (`Buy now` or `Wait to buy`)
for users without inventory and exit guidance (`Sell now` or `Hold`) for users who already own the
item, with targets and stop-loss or invalidation levels. `Sell now` never represents a short-selling
recommendation. Market-state labels must not be confused with either action.

## Liquidity and attractiveness

Liquidity describes current market structure. It uses fetched depth with a spread penalty:

```text
depth = bid_depth + ask_depth
spread_penalty = 1 + max(spread_pct, 0.5) / 100
liquidity = depth / spread_penalty
```

The report normalizes liquidity bars against the strongest displayed row.

Trading attractiveness is a separate compatibility metric intended for market-making comparisons:

```text
effective_spread = max(raw_spread - min_tick, 0)
trading_attractiveness = effective_spread_pct * trades / range_pct
```

It rewards effective spread and activity while penalizing a wide historical range. The report foregrounds history, fair value, and market state instead of this score.

## Stored versus derived data

SQLite stores normalized source facts: completed transactions, order-book observations and levels,
legacy or diagnostic quote observations, and sync state. Report metrics remain derived so formula
changes do not require rewriting history. Ordinary calculations must ignore the lagging endpoint
quote.

The schema intentionally does not store raw responses, trader identities, or precomputed report
metrics.

## Compatibility fields

The read model retains older flat `*_7d` names for CSV and metric compatibility. When the requested report does not include a literal 7-day window, those aliases refer to the first requested window. New consumers should prefer explicit price-source fields and window-specific fields.
