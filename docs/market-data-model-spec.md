# Market Data Semantics

## Price sources

The model keeps executions, quotes, and order-book prices distinct:

- `last_trade_price` is the newest executed unit price in the queried window.
- `quote_price` is the newest stored value from the WarEra price endpoint.
- `mid_price` is `(best_bid + best_ask) / 2` when both sides exist.
- `current_price` selects `last_trade_price`, then `quote_price`, then `mid_price`.
- `latest_price` is a compatibility alias for `current_price`.

`quote_gap_pct` measures quote divergence from the last trade:

```text
(quote_price - last_trade_price) / last_trade_price * 100
```

It is absent when either price is unavailable or the trade price is not positive.

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

When a window has no executions, quote observations may provide fallback values for average, minimum, maximum, and fair-price fields. They do not create trade count or volume.

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

## Tendency labels

The read model classifies market state using transaction direction, rolling-average position, stable range, trade count, volume, and spread. Labels are descriptive signals such as `Rising`, `Falling`, `Range-bound`, `Volatile`, `Thin`, and `Stable`; they are not standalone buy or sell recommendations.

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

SQLite stores normalized source facts: transactions, quote observations, compact order-book observations, and sync state. Report metrics remain derived so formula changes do not require rewriting history.

The current schema intentionally does not store raw responses, individual order-book levels, trader identities, or precomputed report metrics.

## Compatibility fields

The read model retains older flat `*_7d` names for CSV and metric compatibility. When the requested report does not include a literal 7-day window, those aliases refer to the first requested window. New consumers should prefer explicit price-source fields and window-specific fields.
