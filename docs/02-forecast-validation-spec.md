# Spec 2: Walk-Forward Tomorrow-Bias Validation

Status: Proposed  
Implementation order: 2 of 3  
Depends on: [Spec 1](01-execution-cost-spec.md)  
Required by: [Spec 3](03-profit-first-flip-report-spec.md)

## Objective

Replace the report's unmeasured `Up`/`Down`/`Trust` heuristic with a reproducible 24-hour forecast record whose historical outcomes can be audited.

This spec does not promise predictive power. Its primary requirement is to distinguish supported, unsupported, and unavailable forecasts honestly.

## Problem

The current `report.py` direction score:

- assigns arbitrary points to momentum and fair-value distance;
- adds a tendency label derived partly from the same history, double-counting evidence;
- labels confidence from hand-written market-quality thresholds;
- has no explicit target timestamp or observed outcome;
- reports no historical sample count, accuracy, return distribution, or baseline comparison.

The report title says `Tomorrow Bias`, so the forecast horizon must mean a precise 24 hours rather than the selected history-window length.

## User outcomes

For each item, the read model can provide:

```text
Forecast horizon: 24h
Current signal: Up / Down / No clear move / Unavailable
Historical evaluated samples: N
Direction accuracy: X%
Baseline accuracy: Y%
Median future bid change: Z%
10th–90th percentile future bid change: [A%, B%]
Execution-adjusted gross flip return for quantity Q: [P10, median, P90]
Evidence: Supported / Limited / Weak / Insufficient
```

The report integration itself belongs to Spec 3. This spec may retain compatibility output in the current report, but must not add new trade recommendations.

## Definitions

Use these terms consistently:

- **Feature time (`t`)**: an order-book observation timestamp.
- **Horizon**: exactly 24 hours after `t` by default.
- **Target observation**: the first order-book observation at or after `t + horizon`, provided it is no more than 6 hours late.
- **Current bid**: best bid at feature time.
- **Future bid**: best bid at the target observation.
- **Realized bid return**: `(future_bid - current_bid) / current_bid * 100`.
- **Up outcome**: realized bid return is greater than one minimum tick as a percentage of current bid.
- **Down outcome**: realized bid return is less than negative one minimum tick as a percentage of current bid.
- **Flat outcome**: neither Up nor Down.
- **Evaluable prediction**: prediction and outcome are both available and the prediction is not `No clear move`.
- **Execution-evaluable sample**: both the feature and target snapshots have stored levels, and the requested quantity can be fully bought from feature-time asks and fully sold into target-time bids.
- **Realized gross flip return**: `(future sell-sweep proceeds - feature-time buy-sweep cost) / feature-time buy-sweep cost * 100`, before fees.

The target is the future executable bid, not a future last trade, because Spec 3 evaluates whether acquired inventory could be sold.

Directional accuracy uses best bids so migrated compact observations remain useful. Profit intervals use only execution-evaluable level snapshots and therefore never assume that arbitrary quantity can exit at the best bid.

## Architecture

- `market_store.py`: query normalized observations and transactions only.
- `market_data.py`: assemble chronological feature/target rows without future leakage.
- `metrics.py`: pure direction, outcome, accuracy, interval, and evidence calculations.
- `report.py`: remove ownership of forecast formulas; it may only render forecast fields.
- `cli.py`: pass forecast configuration and orchestrate.

Do not introduce SQL, scoring formulas, or time-series feature construction in `report.py` or `cli.py`.

## Configuration

Add CLI options:

```text
--forecast-horizon-hours FLOAT       default 24
--forecast-target-max-lag-hours FLOAT default 6
--forecast-min-samples INT           default 30
```

Validation:

- horizon must be positive;
- maximum lag must be non-negative;
- minimum samples must be at least 1.

CSV/custom-JSON modes may lack observation history. They must produce `Unavailable`/`Insufficient`, not fail.

## Feature construction

For every historical order-book observation with a positive best bid and ask, construct features using only records timestamped at or before that observation:

- best bid and ask;
- midpoint and spread percentage;
- fetched bid and ask depth and depth imbalance;
- trailing 7-day transaction open, close, VWAP, median, 10th/90th percentiles, volume, and count;
- trailing momentum;
- stable fair price using the existing documented formula;
- fair gap relative to the feature-time midpoint.

Do not use the current report-generation time to build a historical feature. Do not include a transaction, quote, or order-book observation after feature time.

The implementation should avoid an SQL query per feature row. Fetch bounded chronological records once per item and calculate rolling windows in `market_data.py` or via pure helpers.

Prior compact observations from schema version 1 remain valid because this spec needs their best quotes, not individual levels.

## Direction model

Move the direction calculation out of `report.py` and implement one deterministic pure model in `metrics.py`.

Version the model with a constant such as:

```python
FORECAST_MODEL_VERSION = "direction-v1"
```

`direction-v1` deliberately preserves the explainable parts of the existing heuristic while removing duplicated tendency evidence:

```text
score = 0

momentum >=  6% => +2
momentum >=  1% => +1
momentum <= -6% => -2
momentum <= -1% => -1

fair gap <= -2% => +1
fair gap >=  2% => -1

score >=  1 => Up
score <= -1 => Down
otherwise    => No clear move
```

Do not add the `Rising`/`Falling` tendency label to the score because it overlaps momentum. Return structured reason codes as well as readable labels, for example `strong_positive_momentum` and `below_fair`.

This fixed model is evaluated, not trained. Do not tune thresholds on the same evaluation history in this spec.

## Walk-forward evaluation

For each item:

1. Sort feature observations chronologically.
2. Produce the direction signal at feature time using only trailing data.
3. Locate the target observation according to the horizon/maximum-lag definition.
4. Calculate realized future-bid return and outcome.
5. Record prediction, outcome, correctness, model version, feature timestamp, target timestamp, and return.
6. Exclude `No clear move` from directional accuracy but retain it in coverage counts.
7. When normalized levels exist at both timestamps, sweep feature-time asks and target-time bids for the requested evaluation quantity.
8. Record realized gross flip return only when both sweeps fill completely; never extrapolate missing depth.

The evaluation API must accept a positive `quantity` parameter and default it to `1.0` for compatibility during this spec. Spec 3 passes the user's configured trade quantity. Do not add a second competing CLI quantity option.

Do not persist derived evaluation rows in SQLite in this version. They are recalculable from normalized history. If performance becomes unacceptable, document measurements before proposing a cache migration.

## Statistics and evidence labels

Expose a frozen result type with at least:

```text
item_code
model_version
horizon_hours
candidate_samples
evaluable_samples
execution_evaluable_samples
up_predictions
down_predictions
correct_predictions
accuracy_pct
baseline_accuracy_pct
median_future_bid_return_pct
p10_future_bid_return_pct
p90_future_bid_return_pct
gross_flip_return_p10_pct
gross_flip_return_median_pct
gross_flip_return_p90_pct
current_signal
current_reason_codes
evidence_label
```

The baseline predicts the more frequent non-flat outcome observed in the evaluation rows. If Up and Down are tied, baseline accuracy is 50%. Flat outcomes do not count toward model or baseline directional accuracy.

Evidence labels use sample count and comparison with baseline:

- `Insufficient`: evaluable samples `< forecast_min_samples`.
- `Weak`: enough samples, but model accuracy is less than or equal to baseline accuracy.
- `Limited`: enough samples and model accuracy is above baseline, but by less than 5 percentage points.
- `Supported`: enough samples and model accuracy beats baseline by at least 5 percentage points.

These are evidence labels, not probability statements or guarantees. Do not call them `High confidence`.

Return percentiles over realized future-bid returns for historical rows that had the same predicted direction as the current signal. If fewer than 10 matching outcomes exist, return no forecast interval even when overall evidence has enough samples.

Separately return gross-flip-return percentiles over execution-evaluable rows with the same predicted direction as the current signal and the same requested quantity. Require at least 10 such outcomes. These execution-adjusted percentiles are the only return distribution Spec 3 may use for a `Potential flip` verdict.

## Current forecast

Build the current feature row at the newest available observation. It must use trailing data ending at that observation. Attach the historical statistics calculated only from completed historical feature/target pairs.

Required behavior:

- No current order book: `current_signal=Unavailable`.
- Missing trailing metrics: use only available model inputs; zero score becomes `No clear move`.
- Insufficient evaluation history: retain the descriptive current signal but label evidence `Insufficient` and do not expose it as actionable.
- Stale observations are retained with their timestamp; Spec 3 decides whether they are too stale for a flip.

## Report compatibility during this spec

Remove `_tomorrow_bias` calculation ownership from `report.py`. The report should consume supplied fields when available. Until Spec 3 is implemented:

- render `Up`, `Down`, or `No clear move` descriptively;
- replace `Trust` values with the evidence label and sample count;
- never convert `Supported` into `Buy` or `Sell` here;
- fall back to `Unavailable` for CSV inputs without evaluation data.

Existing tests that assert heuristic `Buy`, `Sell`, or `Trust` labels must be updated to the new descriptive contract, not preserved blindly.

## Tests

### Store/read-model tests

- Historical observations are returned in deterministic chronological order.
- Feature windows exclude future transactions and observations.
- Target selection chooses the first observation after the horizon.
- Targets beyond maximum lag are rejected.
- Version-1 compact snapshots remain evaluable.
- Construction does not issue one query per feature row; test through a bounded store API rather than mocking implementation details excessively.

### Metrics tests

- Every threshold boundary in `direction-v1`.
- Momentum and fair gap combine as specified.
- Tendency labels cannot affect the score.
- Up, Down, and Flat outcome boundaries use minimum tick.
- Accuracy excludes no-signal predictions.
- Baseline accuracy is correct for imbalanced and tied outcomes.
- Evidence-label boundaries.
- Matching-direction percentiles require at least 10 outcomes.
- Execution-adjusted return percentiles require full entry and exit sweeps at the requested quantity.
- Partial future depth is excluded rather than extrapolated.
- Input sequences are not mutated.

### End-to-end tests

- Synthetic chronological data with a known 24-hour mapping produces expected samples and statistics.
- Synthetic level history produces expected buy-sweep-to-sell-sweep gross returns.
- Increasing evaluation quantity can reduce execution-evaluable sample count and change return percentiles.
- No-lookahead test: changing a future transaction cannot alter an earlier prediction.
- Sparse history yields `Insufficient` without crashing.
- CSV mode yields `Unavailable` fields.
- Report rendering does not recreate forecast formulas.

Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest
```

## Acceptance criteria

- `Tomorrow` means a configured horizon defaulting to exactly 24 hours.
- Every evaluated prediction has auditable feature and target timestamps.
- Report-level direction arithmetic is removed.
- Evidence is based on walk-forward outcomes and a stated baseline.
- Profit-return intervals use historical executable sweeps for the same requested quantity, never top-of-book multiplication.
- Insufficient history is explicit.
- No future record influences an earlier feature.
- The full test suite passes.

## Non-goals

- No machine-learning dependency.
- No threshold optimization.
- No claim of causal prediction.
- No automated trading.
- No profit verdict; Spec 3 combines forecast outcomes with execution cost.
- No passive-limit fill model.

## Focused implementation prompt

```text
Implement docs/02-forecast-validation-spec.md completely in /home/david/src/warera-marketguide after docs/01-execution-cost-spec.md is complete.

Read AGENTS.md and both dependency/spec documents fully before editing. Build chronological feature and target read models without lookahead, move the deterministic direction-v1 calculation out of report.py into pure metrics code, evaluate it against the first eligible future best bid at the configured 24-hour horizon, and calculate sample counts, accuracy, baseline accuracy, matching-signal bid-return intervals, execution-adjusted gross flip returns for a passed quantity, and evidence labels exactly as specified. Historical flip returns must sweep feature asks and future bids fully and exclude partial-depth samples. Keep SQLite access in market_store.py and time-series read-model construction in market_data.py. Add validated CLI options and make sparse/CSV inputs degrade to Unavailable or Insufficient. Update report compatibility so it renders supplied forecast evidence but does not issue trade recommendations. Add threshold, no-lookahead, sparse-data, quantity/depth, and end-to-end tests. Run the full suite with the existing .venv. Do not tune the model, add ML packages, persist derived forecasts, extrapolate depth, or implement the final Flip Board in this task. Report the data contract, leakage protections, changed files, and test results.
```
