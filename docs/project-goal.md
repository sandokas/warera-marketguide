# Project Goal and Data Authority

## Product goal

WarEra Market Guide is an aggressive decision-support tool for trading in the WarEra in-game
market. It should provide clear, ranked guidance for buying and selling items rather than merely
describe market conditions.

For each item, the report should make these decisions clear:

- whether a user without inventory should buy now or wait for a better entry;
- whether a user holding inventory should sell now or continue holding for a better exit;
- the price at which the action becomes attractive;
- the quantity that the visible order book can support;
- the profit target or take-profit level;
- the stop-loss or market-invalidation level;
- the expected trading horizon;
- the estimated upside, downside, spread, and slippage;
- the data and reasoning behind the signal.

Signals can use aggressive thresholds because this is an in-game economy. Aggressive means a
greater willingness to act on a plausible edge; it does not mean inventing prices, hiding weak
liquidity, silently substituting one price source for another, or claiming certainty.

## Position-aware action semantics

WarEra does not support short selling. A `Sell` signal must never mean opening a short position or
selling inventory the user does not own.

Because the report does not currently know whether the user owns an item, entry and exit guidance
are separate, simultaneous questions rather than one mutually exclusive action:

- `Buy now` means the current executable ask is attractive for a user who does not own the item or
  wants to add inventory.
- `Wait to buy` means the current entry is unattractive and identifies the price or condition that
  would make it attractive.
- `Sell now` means the current executable bid is an attractive exit for a user already holding the
  item.
- `Hold` means the current exit is unattractive and identifies the target, stop, or condition that
  would change that guidance.

An item may therefore have both buyer guidance and holder guidance in the same report. Until user
inventory and cost basis are modeled, `Sell now` is a market-level exit assessment, not a claim
about the user's realized profit.

## Authoritative market data

SQLite is the source of truth for synchronized market history. Within that database, different
facts have different responsibilities:

1. Completed market transactions are authoritative for historical prices and activity.
2. The current order book is authoritative for executable prices and visible liquidity now.
3. The WarEra price endpoint is not authoritative for analysis because it is a lagging value
   calculated by the game.

Completed transactions must drive:

- latest traded price;
- open, high, low, and close;
- VWAP, averages, medians, percentiles, and fair-value estimates;
- traded quantity, traded value, and completed-trade counts;
- momentum, volatility, price state, and historical signal inputs;
- historical levels used to derive targets and stop losses.

The current order book must drive:

- the ask a buyer would pay now;
- the bid a seller would receive now;
- executable quantity and depth;
- spread, imbalance, pressure, and walls;
- slippage and size-aware entry or exit estimates.

## Prohibited price substitution

The lagging WarEra price-endpoint value must not be used as a fallback for a missing transaction
price. It must not feed fair value, price ranges, momentum, signals, targets, stop losses, or report
rankings.

The order-book midpoint must also not be presented as a completed transaction or inserted into
transaction-derived history. It is useful only as a description of the current book.

If the selected window has no completed transactions, transaction-derived fields must be marked as
unavailable or insufficient. Missing history is information; it must not be concealed with a
different kind of price.

The price-endpoint value may remain stored temporarily for compatibility, diagnostics, or migration
work, but ordinary report and signal calculations must not consume it.

## Report design rule

Every report table must have one declared decision question. Columns belong in that table only when
they help answer its question. A column that mixes valuation, execution, activity, prediction, or
risk without making the distinction explicit should be moved, renamed, or removed.

The existing visual forms may be retained when they communicate their assigned question well,
including the order-book visualization, activity bars, and price-state indicators. Visual quality
does not override semantic clarity.

## Non-goals

- No automated order placement.
- No guarantee of profit.
- No invented liquidity or extrapolated fills beyond visible depth.
- No use of the lagging game-calculated price as a substitute for real transactions.
- No short-selling recommendation or assumption that the user can sell inventory they do not own.
