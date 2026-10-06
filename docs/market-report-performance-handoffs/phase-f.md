# Phase F handoff

Status: complete. Date: 2026-10-06 (Europe/Lisbon).

## Scope and ownership

Changed only src/warera_quant/metrics.py, src/warera_quant/report.py,
tests/test_participant_metrics.py, tests/test_participant_report.py and this note.
F used unique .phase-f-* temporary output/test paths. No agents, store/read-model/
CLI changes, schema assertions, G fixtures, browser-test edits, README/shared-plan
edits, staging, commits, production operations or publication ran. Ownership
remains disjoint from G whether or not G runs concurrently. No H work started.

## Selection signature and contracts

metrics.select_participant_details(item_categories, *, entity_total,
turnover_basis, missing_money_count=0) -> tuple[list[dict], dict]
returns selected detail rows and detail_selection metadata. It does not mutate
the input rows/list. Inputs use the entity's existing report basis (gross or
source-money), not an independently chosen per-category basis. Numeric domain
inputs are Fractions, Decimals or exact values accepted by the existing parser.

Selection sorts by descending buy_total_value + sell_total_value, then repr of
the complete category signature. Commodity and equipment signatures remain
separate, including equipment skills and condition distinctions. The threshold
is exactly entity_total * Fraction(4,5). The shortest prefix includes the crossing
row, then stops, without a count cap or expansion of equal ties. No displayed
rounding or binary-float threshold enters the decision. Known complete category
turnover must reconcile exactly with entity_total; mismatch raises ValueError
instead of selecting against a different denominator.

The shared participant reducer calls this helper for users, MUs and countries;
E's calculate_entity_activity uses that same reducer. All query, ownership,
window/full-fifo and monetary-evidence semantics from D/E remain unchanged.
Complete item_categories now also use the deterministic turnover/signature order.
Per-side categories remain complete. top_items is the selected-prefix compatibility
key. Top-ten entity leaderboards and top-three Bought/Sold summaries are unchanged.

## detail_selection fields

Each entity, including projected leaderboard rows, exposes:

- status: complete, partial, zero-total or empty.
- completeness: complete or partial (knowledge of the monetary denominator).
- turnover_basis: gross or source-money, inherited from the report.
- target_share: exact 4/5 (Decimal 0.8 at the existing output projection).
- threshold: exact 4/5 of the known entity denominator, otherwise None.
- total_turnover: known denominator, otherwise None.
- observed_total_turnover: sum of available same-basis category amounts.
- selected_turnover: same-basis available subtotal of selected rows.
- selected_share: selected_turnover / total_turnover only for a known positive
  denominator; None for partial, zero-total and empty results.
- selected_count and total_row_count.
- missing_money_count: the supplied entity diagnostic count.

The helper computes with Fraction; existing participant Decimal conversion
projects metadata and amounts along with the rest of the report. Availability
flags are authoritative; observed subtotals do not become verified totals.

## Completeness fallback and display/export integration

If the entity count reports missing money, any category money is unavailable,
or category missing-money counters are nonzero, the true denominator is unknown.
Every category is selected, including zero/unknown categories; status/completeness
are partial, and total_turnover, threshold and selected_share are None. Available
amounts remain explicitly observed subtotals; no 80% coverage is claimed. Missing
quantity alone does not invalidate known monetary coverage and still leaves
quantity-dependent comparisons unavailable.

Known zero turnover selects no positive-volume prefix and reports zero-total;
empty categories with known zero total report empty. Neither path divides by
zero or enters a selection loop. Complete arrays remain available even when
selected details are empty.

HTML detail tables continue consuming top_items. A short coverage-policy note
sits outside tables, preserving table-only capture and avoiding footer bands.
format_player_summary now consumes top_items (with complete-array compatibility
fallback for older dictionaries), showing selected count/share or an explicit
unknown-coverage/no-positive-turnover note. Targeted CLI display therefore
inherits the same helper through E without CLI/read-model edits. Identity/icon
rendering, escaping and existing display precision remain intact.

Complete participant_item_breakdown_7d.csv and participant_trade_breakdown_7d.csv
continue iterating complete arrays, not selected rows. Ranking CSVs automatically
flatten the new detail_selection metadata. Existing CSV protection and exact
amount output are unchanged; exports do not trigger additional accounting.

## Examples and tests

- 100M total with 80M,10M,10M selects one row.
- 100M with 79M,11M,10M selects two, including the crossing row.
- 100M with 50M,30M,20M selects two at exact equality.
- 100 equal 1M categories selects exactly 80, without tie expansion or ten-row cap.
- 0.08,0.01,0.01 selects one; a first row just below 0.08 at 50-digit decimal
  precision selects two, regardless of identical rounded display values.
- Five equal equipment variants select four, in signature order even when
  chronological encounter order changes; all five variants remain complete.
- Settled source amounts 80,10,10 with a verified buyer fee of 5 on the first
  category yield gross 75,10,10 and require two rows (threshold 76). The unverified
  source-money case uses 80,10,10 and selects one. Both accounting modes and all
  entity kinds exercise the same basis/targeted contracts.
- Missing money retains all rows, including a 13-category report whose known
  dominant row would otherwise cross 80%; complete CSVs retain all 13.
- All-missing, known-zero and empty inputs have explicit statuses and no share.

Focused command, existing virtual environment and F-only temporary paths:

    .venv/Scripts/pytest tests/test_participant_metrics.py tests/test_participant_report.py -q -p no:cacheprovider --basetemp=.phase-f-final

Result: 82 passed in 1.08s. Tests cover exact decimal thresholds, minimal prefixes,
reconciliation failures, deterministic commodity/equipment ties, combined buy/sell,
all entity kinds, both accounting modes, report money basis, targeted results and
console display, missing money versus missing quantity, zero/empty totals,
escaping/icons/identities, unchanged leaderboards/compact summaries and complete
CSV retention/precision. A 100-category fixture renders 80 detail rows and exports
100 item rows plus 100 per-side rows for each kind; metadata is present in ranking
CSV. No browser geometry tests were changed or added. git diff --check passed.
Session-created F temporary paths were removed after validation.

## Pending integration

None required across file boundaries to complete F. Selection fields and helper
signature are stable. G's query/index work can remain independent; H must wait
for BOTH F and G to finish before geometry/layout validation. F does not certify
G completion or large-prefix PNG geometry; those belong to subsequent phases.
