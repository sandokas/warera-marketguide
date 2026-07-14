# Spec 3: Profit-First Flip Board and Report

Status: Proposed  
Implementation order: 3 of 3  
Depends on: [Spec 1](01-execution-cost-spec.md), [Spec 2](02-forecast-validation-spec.md)

## Objective

Make the report honor its mission by ranking potential flips on executable, cost-adjusted outcomes rather than historical cheapness or raw momentum.

The report must help the user answer:

```text
Can I buy the requested quantity now?
What would I actually pay?
What size-adjusted exit VWAP does history suggest at the selected horizon?
What exit VWAP is required to break even after configured fees?
Is the historical evidence strong enough to consider the flip?
What can go wrong?
```

The report must prefer `No trade` over an unsupported recommendation.

## Product principles

1. Use executable sides: ask sweeps for entry and future bid sweeps for exit.
2. Show all configured costs and assumptions.
3. Never call historical momentum a buy or sell signal by itself.
4. Separate a marketable buy from a passive limit idea.
5. Never imply that a limit order will fill.
6. Rank only comparable, fully executable opportunities.
7. Missing or stale data produces `Unavailable` or `No trade`, not optimistic fallback prices.
8. Use `Potential flip`, never `Guaranteed`, `Winner`, or an imperative `Buy`.

## Configuration

Add CLI options:

```text
--trade-quantity FLOAT          default 1.0
--trade-fee-pct FLOAT           default 0.0
--min-net-margin-pct FLOAT      default 1.0
--max-quote-age-minutes FLOAT   default 30.0
```

Definitions:

- `trade_fee_pct` is charged independently on gross buy value and gross sell value. If WarEra later proves to use a different fee basis, add separate buy/sell options in a follow-up spec rather than silently changing this definition.
- The generated report must display `Fees assumed: 0.00% per side` when the default is used. Zero must never be described as `no fees exist`.
- Quantity must be finite and positive.
- Fee must be finite and in `[0, 100)`.
- Minimum margin and maximum quote age must be finite and non-negative.

Pass a structured report configuration object through orchestration instead of adding many unrelated positional parameters.

## Opportunity model

Add pure immutable types in `metrics.py` (names may vary slightly while preserving semantics):

```python
@dataclass(frozen=True)
class FlipAssumptions:
    quantity: float
    fee_pct_per_side: float
    minimum_net_margin_pct: float
    forecast_horizon_hours: float
    max_quote_age_minutes: float

@dataclass(frozen=True)
class FlipOpportunity:
    item_code: str | None
    item_name: str
    verdict: str
    reason_codes: tuple[str, ...]
    quantity: float
    snapshot_at: str | None
    quote_age_minutes: float | None
    entry_fully_filled: bool
    entry_average_price: float | None
    entry_worst_price: float | None
    entry_gross_value: float | None
    entry_fee: float | None
    total_entry_cost: float | None
    break_even_exit_vwap: float | None
    forecast_signal: str
    forecast_evidence: str
    forecast_samples: int
    forecast_exit_vwap_p10: float | None
    forecast_exit_vwap_median: float | None
    forecast_exit_vwap_p90: float | None
    net_margin_p10_pct: float | None
    net_margin_median_pct: float | None
    net_margin_p90_pct: float | None
    net_profit_median: float | None
    passive_limit_price: float | None
```

Keep calculations independent of Pandas, HTML, SQLite, and API clients.

## Taker-entry formulas

Use the quantity-aware ask sweep from Spec 1.

```text
entry_gross       = ask sweep gross value
entry_fee         = entry_gross * fee_pct / 100
total_entry_cost  = entry_gross + entry_fee

break_even_exit_vwap = total_entry_cost / (quantity * (1 - fee_pct / 100))
```

Spec 2 provides execution-adjusted historical gross flip-return percentiles for the same requested quantity. For each percentile `P`:

```text
exit_gross_P    = entry_gross * (1 + gross_flip_return_Pct / 100)
exit_vwap_P     = exit_gross_P / quantity
exit_fee_P      = exit_gross_P * fee_pct / 100
net_profit_P    = exit_gross_P - exit_fee_P - total_entry_cost
net_margin_Pct  = net_profit_P / total_entry_cost * 100
```

Do not use best-bid return percentiles for profit calculations. Do not multiply a future best bid by the requested quantity. Only the historical buy-sweep-to-future-sell-sweep distribution from Spec 2 is eligible because it accounts for entry and exit depth at that quantity.

Do not subtract `Immediate Loss %` separately; the historical entry/exit sweeps already incorporate spread and slippage. Doing so would double-count transaction cost.

Do not extrapolate an entry price for an unfilled remainder. Partial entry capacity makes the requested opportunity non-comparable and therefore not eligible for ranking.

## Verdict rules

Evaluate blocking rules first, in this order:

1. `Unavailable`: missing current bid, asks, timestamp, forecast result, or execution-adjusted return interval.
2. `No trade`: quote age exceeds configured maximum.
3. `No trade`: fetched asks cannot fully fill requested quantity.
4. `No trade`: crossed/invalid book, non-positive price, or invalid calculation.
5. `No trade`: median net margin is less than or equal to zero.
6. `Watch`: forecast evidence is `Insufficient`, `Weak`, or `Limited`.
7. `Watch`: median net margin is positive but below configured minimum.
8. `Potential flip`: forecast evidence is `Supported` and median net margin meets or exceeds configured minimum.

`Potential flip` remains conditional. A negative p10 margin must be displayed prominently as downside; it does not automatically block the verdict in version 1.

Reason codes must be stable machine-readable strings such as:

```text
missing_order_book
stale_quote
insufficient_ask_depth
invalid_book
forecast_insufficient
forecast_not_above_baseline
margin_non_positive
margin_below_threshold
supported_positive_margin
```

Human explanations are rendered from reason codes in `report.py`.

## Passive-limit scenario

Show a passive candidate for context, but do not include it in profitability rankings because fill probability is not modeled.

Version-1 rule:

- candidate limit price is the current best bid plus one minimum tick, capped strictly below the best ask;
- if there is no valid price between bid and ask, use the current best bid;
- label it `Limit idea — fill not estimated`;
- do not calculate expected profit as though it filled;
- do not describe it as the recommended entry.

Future work may estimate fill rate only after sufficient order-lifecycle evidence exists.

## Read-model construction

`market_data.py` builds one opportunity input per item from:

- latest normalized order-book snapshot and levels;
- requested quantity sweep;
- snapshot timestamp and report-generation timestamp;
- Spec 2 current forecast and historical evidence;
- item identity and minimum tick.

`metrics.py` turns that domain dictionary plus `FlipAssumptions` into `FlipOpportunity`.

`report.py` only sorts, formats, explains reason codes, and renders. It must not calculate fees, break-even prices, forecast exit VWAPs, margins, or verdicts.

CSV/custom JSON inputs without levels or validated forecasts can still render descriptive history. Their Flip Board verdict is `Unavailable`.

## Report information architecture

Retain the title:

```text
Fair Prices And Tomorrow Bias
```

Replace the subtitle with:

```text
Executable flip opportunities ranked by forecast evidence, spread, depth, fees, and estimated net margin.
```

Directly under the header, show assumptions:

```text
Quantity: 100 | Horizon: 24h | Fees assumed: 0.00% per side |
Minimum margin: 1.00% | Max quote age: 30m
```

### Primary section: Flip Board

This becomes the first and dominant table:

```text
Item
Verdict
Qty
Ask VWAP
Entry Cost
Break-even Exit VWAP
24h Exit VWAP P10
24h Exit VWAP Median
24h Exit VWAP P90
Median Net Margin %
Median Net Profit
Evidence
Samples
Quote Age
Why
```

Sort order:

1. verdict: `Potential flip`, `Watch`, `No trade`, `Unavailable`;
2. median net margin descending;
3. forecast samples descending;
4. item name ascending.

Rows with partial entry fills must show the available filled quantity in `Why` but no entry-cost or margin value for the requested trade.

### Summary cards

Replace `Cheapest vs fair`, `Richest vs fair`, `Best upside bias`, and `Best downside bias` with:

- Best supported net margin
- Lowest break-even cost
- Best downside profile (highest p10 margin)
- Avoid / data problem

If no `Potential flip` exists, the first three cards say `No supported opportunity`; do not promote a Watch row.

### Supporting sections

Keep, in this order:

1. Flip Board
2. How to read the Flip Board
3. Market Trends
4. Price Evolution Lens
5. Item Notes

Remove `Trend Highlights` winners/losers as a separate section; raw momentum leaderboards distract from net-profit decisions. Momentum remains available in Market Trends.

Replace the existing fair-threshold action table or reduce it to a clearly descriptive `Valuation Context` table. It must not contain imperative `Buy`, `Sell`, `Hold`, or `Wait` labels.

The Price Evolution Lens keeps:

- `Ask (You Pay)`;
- `Bid (You Receive)`;
- historical prices and momentum;
- crossing loss for education.

Rename `Immediate Loss %` to `Crossing Cost %`, which more clearly describes a transaction cost rather than an intended loss.

### Explanatory copy

The report must state:

- forecasts are historical estimates, not guarantees;
- fees are assumptions supplied by the user;
- order-book execution is estimated from a snapshot that may change;
- passive limit orders may not fill;
- `Potential flip` means the configured rules passed, not that profit is certain.

## CSV outputs

Continue writing `market_trends.csv` and the compatibility `market_scores.csv`.

Append opportunity fields without deleting existing columns:

```text
flip_verdict
flip_reason_codes
flip_quantity
flip_snapshot_at
flip_quote_age_minutes
flip_entry_fully_filled
flip_entry_average_price
flip_total_entry_cost
flip_break_even_exit_vwap
flip_forecast_exit_vwap_p10
flip_forecast_exit_vwap_median
flip_forecast_exit_vwap_p90
flip_net_margin_p10_pct
flip_net_margin_median_pct
flip_net_margin_p90_pct
flip_net_profit_median
flip_forecast_evidence
flip_forecast_samples
flip_passive_limit_price
```

CSV values must remain numeric where appropriate. Do not put currency symbols, percent signs, or explanatory prose into numeric cells.

## Tests

### Metrics tests

- Fee and break-even formula with zero and non-zero fees.
- Multi-level ask VWAP feeds entry cost correctly.
- Execution-adjusted gross-return percentiles convert to future exit VWAP correctly.
- Best-bid return intervals cannot be used as a profit interval.
- Spread is not double-counted.
- Every verdict rule and its precedence.
- Partial fill cannot become `Potential flip`.
- Stale quote cannot become `Potential flip`.
- Evidence below `Supported` cannot become `Potential flip`.
- Positive and negative p10/median/p90 margins.
- Passive limit price respects bid, ask, and tick.
- Inputs are not mutated.

### Read-model tests

- Latest snapshot, sweep, forecast, and item identity combine correctly.
- Forecast evaluation receives the same requested quantity as the current entry sweep.
- Missing levels and missing forecast degrade explicitly.
- Quote age uses the injected report time, not wall-clock time inside pure calculations.
- Requested quantity is propagated unchanged.

### Report tests

- New subtitle and assumptions are visible.
- Flip Board is the first data section.
- Required columns render and format correctly.
- No `Buy`, `Sell`, `Hold`, or `Wait` signal labels remain.
- No supported opportunity produces honest empty summary cards.
- `Potential flip` row includes evidence, samples, break-even, and downside.
- Partial-fill and stale rows explain their block.
- Crossing Cost wording replaces Immediate Loss wording.
- Trend Highlights winner/loser section is absent.
- Descriptive sections continue to render for compatibility inputs.

### CLI/end-to-end tests

- New options validate bounds and reach report configuration.
- A synthetic supported opportunity ranks above Watch and No trade rows.
- Non-zero fee changes break-even and verdict as expected.
- Larger quantity can turn a fully filled opportunity into insufficient depth.
- CSV mode completes with an unavailable Flip Board rather than crashing.
- Existing database migration and sync/report flows remain operational.

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest
```

## Acceptance criteria

- No item is called cheap using a last trade when its executable ask makes the flip unprofitable.
- No `Potential flip` exists without full requested ask depth, a fresh quote, supported forecast evidence, and sufficient median net margin.
- Break-even and net-margin calculations include configured per-side fees exactly once.
- Modeled exits use same-quantity historical sell sweeps, not unbounded top-of-book prices.
- Rankings use net margin, never raw momentum or the compatibility attractiveness score.
- Report formulas remain outside `report.py` and orchestration remains outside `metrics.py`.
- Missing data fails closed.
- All assumptions are visible in HTML and numeric fields are exported to CSV.
- The full test suite passes.

## Non-goals

- No guarantee of profit.
- No automated order placement.
- No passive-order fill probability.
- No inventory portfolio accounting or user cost-basis tracking.
- No stop-loss optimizer.
- No model fitting or threshold tuning.
- No external financial-market data.

## Focused implementation prompt

```text
Implement docs/03-profit-first-flip-report-spec.md completely in /home/david/src/warera-marketguide after specs 01 and 02 are complete.

Read AGENTS.md and all three specs fully before editing. Add validated trade assumptions, pure fee/break-even/forecast-exit-VWAP/net-margin/verdict calculations in metrics.py, compose opportunity inputs in market_data.py from the quantity-aware order book and validated execution-adjusted forecast, and make report.py render rather than calculate. Pass the exact configured quantity into forecast evaluation; never use a future best bid as if unlimited quantity could exit there. Implement the fail-closed verdict precedence exactly. Redesign the top of the HTML into the specified Flip Board, replace old summary cards, remove imperative Buy/Sell/Hold/Wait signals and the momentum winner/loser section, rename Immediate Loss to Crossing Cost, and retain descriptive compatibility sections. Append typed opportunity columns to both CSV outputs. Add comprehensive metrics, read-model, report, CLI, and end-to-end tests, including fee, stale quote, partial entry/exit depth, unsupported forecast, quantity, ranking, and compatibility cases. Use apply_patch and the existing .venv, then run PYTHONPATH=src .venv/bin/python -m pytest. Do not add automated trading, passive fill claims, ML tuning, depth extrapolation, or profit guarantees. Report formulas, fail-closed behavior, changed files, and final test results.
```
