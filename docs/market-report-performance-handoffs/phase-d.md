# Phase D handoff

Status: complete. Date: 2026-10-06 (Europe/Lisbon).

## Scope and changed files

Only D is implemented. Changes cover market_store.py, market_data.py,
metrics.py, cli.py, report_context.py, accounting labels/CSV fields in report.py,
scripts/market_rollout.py, scripts/verify_market_publication.py, and
participant/CLI/runner tests.
No agents, production writes/sync/publication, schema/index migration, README or
shared-plan edits, staging or commits. E and later phases remain separate.

## Iterator and read-model contracts

- MarketStore.iter_participant_window(start: datetime, end: datetime, *,
  batch_size: int = 500) yields normalized parent dictionaries in exact UTC
  timestamp/opaque-ID order for [start,end), restricted to trading/itemMarket.
  Aware increasing bounds and batch sizes 1..500 are required.
- Both SQL bounds apply before fetching/materializing parents. Integer epoch
  predicates supply coarse bounds; microsecond predicates enforce exact bounds
  with coalesce(created_at_us,source_timestamp_us(created_at)) for legacy rows.
  Only those parents enter _iter_source_query, which retains four batched child
  reads per batch (participants, equipment, stats, field presence).
- iter_participant_history(start, end, *, batch_size=500) is unchanged and is
  selected only for explicit full-fifo. Its active-reference expansion keeps
  older buys AND dispositions; inactive unrelated histories remain excluded.
- load_participant_report(store, *, as_of=None, context=None, batch_size=500,
  verbose=False, progress=None, accounting_mode: str | None=None) uses the
  supplied mode when present, otherwise the frozen context's mode. New contexts
  default to window; invalid modes fail. Overrides update the returned context's
  mode without changing dates.
- calculate_participant_rankings(trades, *, as_of, sources=None,
  window_end_exclusive=None, accounting_mode="window") consumes ordered domain
  records. Both modes share aggregate_window_side and call the extracted
  account_side helper only in full-fifo. No reducer is duplicated. Direct window
  calculation also ignores older records if supplied by a caller.
- CLI --participant-accounting {window,full-fifo} defaults to window and reaches
  DB preparation, player summaries and identity-selection previews. A supplied
  frozen report context remains authoritative for report preparation.
- Rollout records the selected mode in new job metadata; snapshot/publication
  context and retries preserve it. Verification restores both supported modes.
  Legacy metadata without any mode defaults to window; persisted C-era contexts
  explicitly containing full-fifo still replay full-fifo.

C's analysis reference/start/exclusive endpoint remain authoritative. Default
includes C via C+1 microsecond; historical T excludes T; future requests clamp.
No new analysis clock is introduced. Coverage evaluates the logical reference.

## Accounting and export contracts

Reports and entity rows expose accounting_mode and accounting_status.
Window reports use not_calculated; full-fifo uses calculated, meaning replay
ran, not that basis, fees or source history are complete. Existing per-entity
result_status/costing_status describe FIFO availability/partial results in
full-fifo; both are not_calculated in window mode.
Empty DB reports expose the selected mode and not_calculated.

In window mode these retained entity diagnostics are None (blank CSV):
matched net/gross P&L, matched/net-matched/uncosted/unknown-fee quantities,
matched/net-matched/uncosted/unknown-fee source sale values, matched gross sale
value, and matched source/gross value coverage. Profit/loss diagnostic boards
remain present but empty. Inventory lots are not replayed even for window buys.

Window totals, economic account ownership, actor IDs, self trades, unresolved
attribution, equipment signatures, source-versus-verified-gross report basis and
source coverage remain shared with the previous FIFO reducer. Profile enrichment
remains display-only. Top-ten entities, top-ten items and compact top-three
bought/sold summaries remain unchanged.

Item rows retain valid window averages, compared quantities and net buys.
Calculations keep Fraction arithmetic until the existing Decimal output boundary;
missing money/quantity invalidates affected comparisons instead of creating an
average or net quantity from incomplete facts.
Explicit item keys: window_average_difference_per_unit, window_comparison_value,
window_comparison_quantity, window_net_buy_quantity, comparison_basis.
Legacy domain keys profit_loss_per_unit, profit_loss_btc, matched_quantity,
unmatched_buy_quantity and unmatched_sell_quantity remain compatibility aliases
for window comparisons, never FIFO or total holdings.

Rendered labels: Window Avg Difference/Unit, Window Comparison BTC, Window Net
Buy Qty in both modes. Player summaries show accounting mode/status and
unavailable realized P&L when skipped. Item CSV replaces misleading old
profit/matched/net column names with the explicit window names above and includes
comparison_basis. Ranking diagnostics retain columns with blanks when unavailable.
Participant exports carry mode/status and C's context fields. Exports consume the
prepared domain report; they never request history.

Verifier reconciliation preserves skipped accounting totals as JSON null, even
for empty populations, instead of treating unavailable values as zero. Expected
participant asset IDs now match the existing volume/explanations tables; geometry
checks and PNG rendering are unchanged.

## Processed-row and correctness evidence

test_default_window_never_materializes_old_active_rows_or_csv traces SQL and
instruments the source-to-domain boundary. With two window parents, batch size 1,
before and after adding 2,000 older transactions involving the SAME active accounts:
- Exactly 2 parent/domain rows: start and sale.
- Exactly 8 child queries: four per one-parent batch.
- No older IDs in any child query.
- Identical window entities, rankings, attribution and source coverage.

The history iterator is replaced with a failing sentinel for that entire test,
including default diagnostic CSV generation. CSV diagnostics are blank and mode/
status are window/not_calculated. An inventory-allocation sentinel additionally
proves window mode does not replay commodity or equipment lots.

The explicit read-model test consumes old-buy, old-sale and recent-sale in order:
10 units acquired for 100, 4 previously disposed, then 8 sold for 120. It matches
6 units, leaves 2 uncosted, produces matched net P&L 30, and counts only the recent
120 sale in turnover. Context restoration and explicit mode override are covered.

Paired fixed-fixture tests compare window aggregation against the preserved
pre-D full-fifo reducer in verified-gross and source-money cases, including old
history, start/end rows, self trades, unresolved ownership and equipment variants.
Complete categories, item arithmetic, totals, actor IDs, counts and coverage agree;
FIFO availability is the intentional difference. Existing explicit full-fifo
regressions retain older-buy/disposition, same-time, unknown basis/fees, ownership,
exact fractional allocation, and equipment lineage behavior.

Exact-boundary tests cover start-1us, start, end-1us, end and end+1us, with
database-inclusive and historical-exclusive contexts. C's context tests continue
covering legacy NULL precision, newest timestamp ties and offsets. CLI tests cover
both modes. Runner snapshot/resume tests cross both modes with historical/default
endpoints; verifier tests forbid default history and preserve unavailable totals.
Verifier-only fixtures mock exported PNG metadata; they do not assert fresh
rendering geometry.

## Validation

Existing virtual environment; isolated temporary databases/output only:

    .venv/Scripts/pytest tests/test_participant_metrics.py tests/test_participant_market_data.py tests/test_participant_report.py tests/test_cli.py tests/test_report_context.py tests/test_rollout_runner.py tests/test_market_store.py tests/test_market_data.py tests/test_report_exports.py tests/test_display_identity.py tests/test_report.py -q -p no:cacheprovider --basetemp=.phase-d-complete

Result: 256 passed in 14.83s, including existing actual-browser export regressions.
git diff --check passed. Session-created test directories were removed afterwards.

## Limitations

Evidence bounds Python parent/domain processing and child loading, not total
SQLite execution cost: existing coverage/status aggregates, parent filtering/
sorting and indexes may still depend on database size. No production profiling or
index optimization ran; that belongs to G. Explicit FIFO still incurs earlier-
history expansion and in-memory lots. Observed FIFO remains incomplete wealth/
inventory accounting with existing basis, fee, lineage and same-time limits.

Targeted summaries still calculate the full window population until E. Explicit
identity enrichment can still calculate its selection population separately,
but its default path is now window-only. Frozen context remains a cutoff, not a
guarantee against concurrent eligible-fact revisions. CSV consumers must handle
unavailable cells and renamed item comparison columns.
No cumulative-80% selection or PNG/layout changes were implemented.
