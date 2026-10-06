# Phase E handoff

Status: complete. Date: 2026-10-06 (Europe/Lisbon).

## Scope and changed files

E changes src/warera_quant/market_store.py, market_data.py, cli.py,
metrics.py, tests/test_participant_market_data.py and tests/test_cli.py.
The small shared metrics change extracts the existing reducer behind two entry
points; it does not introduce a second accounting implementation. Existing C/D
workspace changes were preserved. No agents, schema migrations, indexes,
production operations, README/shared-plan edits, commits, cumulative 80% selection,
web interface or PNG changes were performed.

## Stable query and resolution contracts

- MarketStore.entity_activity_query(entity_kind: str, entity_id: str,
  start: datetime, end: datetime, *, accounting_mode: str = "window")
  -> tuple[str, tuple] returns executable SQL and its bound parameters.
- MarketStore.iter_entity_activity(entity_kind, entity_id, start, end, *,
  accounting_mode="window", batch_size=500) streams normalized parents through
  the existing four-child-table batching implementation. Batch sizes are 1..500.
- MarketStore.has_entity_reference(entity_kind, entity_id) -> bool is a bounded
  existence result using participant references, without parent materialization.
- All three methods support user, mu and country. Activity queries require
  nonempty IDs and aware increasing bounds. SQL identifies reference candidates,
  deduplicates parents and orders by exact UTC timestamp then opaque ID.
- Window mode applies both bounds before fetching/materializing parents and
  children. Full-fifo reads earlier target-reference buys AND dispositions through
  the exclusive endpoint, without expanding the counterparty population.
- resolve_player_identity(store, value) -> tuple[str | None, list[dict]] strips
  input, gives exact cached or referenced ID precedence over names, then uses
  exact cached-name COLLATE NOCASE matching. Multiple names return candidates;
  missing input returns (None, []). Historical references recognize uncached
  inactive IDs without generating rankings. Arbitrary inputs absent from both
  caches/references remain eligible for the existing exact-name public lookup.

CLI resolves ambiguity or not-found outcomes before activity analysis. Public
lookup remains in CLI through WarEraMarketApi.search_users; it never downloads
market history. A unique public identity is used for targeted SQLite analysis
and display only. Cached/reference IDs never trigger public lookup. Inactive
cached, referenced or publicly resolved identities report no seven-day activity.
Console summaries do not invoke report output/export or create output directories.

## Stable read-model, category and context contracts

load_entity_activity(store, entity_kind, entity_id, *, as_of=None, context=None,
accounting_mode=None, batch_size=500, verbose=False, progress=None) -> dict
returns the existing participant report shape with at most one economic entity,
empty rankings, and entity_kind, entity_id, coverage_scope fields. The scope is
"entity-reference-candidates": coverage counts self/unresolved trades among
candidate parents, not global market totals. Sources/source_coverage retain the
existing ingestion coverage semantics. No-activity returns entities=[]; empty
DB returns unavailable analysis/accounting and no invented clock.

A supplied C ReportContext is frozen and authoritative. Otherwise the shared
store resolver runs once. Accounting override replaces only the mode, preserving
all dates. Returned context is context.to_dict(), including data_as_of,
requested_as_of, analysis_as_of, window_end_exclusive, generated_at,
boundary_mode and accounting_mode. Default includes C through C+1us; historical
T excludes T; future requests clamp; start derives from the logical reference.
Legacy NULL created_at_us uses source-text microseconds.

calculate_entity_activity(trades, *, entity_kind, entity_id, as_of, sources=None,
window_end_exclusive=None, accounting_mode="window") uses the same internal
_reduce_participant_activity as calculate_participant_rankings. It skips economic
sides belonging to other accounts and bypasses leaderboard construction entirely.
The public ranking signature remains unchanged. Window aggregation, explicit
FIFO replay and Decimal output conversion remain shared.

Entity rows, categories[buy/sell], item_categories, top_buy/top_sell, other_buy/
other_sell, and top_items retain D's complete category and display/export
contracts. Equipment signatures and missing-value counters are unchanged.
Window comparison keys remain window_average_difference_per_unit,
window_comparison_value, window_comparison_quantity, window_net_buy_quantity
and comparison_basis, with existing compatibility aliases. Top-three summaries
and top-ten item detail selection remain unchanged for F. Enrichment uses the
existing cached display/assets path and format_player_summary consumes the same
report/entity dictionaries. Current DB adapters provide source money without
verified settlement or lineage, so source-money is the applicable nonempty
turnover basis; no new monetary evidence is invented.

## Ownership and accounting

SQL user_id/mu_id/country_id predicates are candidate filters only.
resolve_market_account still decides historical economic ownership. One MU or
country reference owns the side even with a user actor; personal turnover does
not receive that side. Institution conflicts/unsupported parties/invalid or
missing references remain unresolved. Membership and cached profiles affect
only display. Institutional rows retain actor_ids. Self trades are retained in
candidate coverage and excluded from economic turnover/inventory as before.
Both sides of each candidate trade are inspected for these diagnostics.

Default window mode never runs FIFO; matched P&L/costing values remain unavailable.
Explicit full-fifo costs target sales using earlier target acquisitions and
prior dispositions, while only window activity contributes turnover. Unknown
money, quantity, fees and basis remain explicit; no source money is promoted to
verified gross. Synthetic verified settlement evidence is test-only.

## Verification and processed-row/query evidence

Existing virtual environment, isolated temporary databases/output paths:

    .venv/Scripts/pytest tests/test_participant_market_data.py tests/test_cli.py tests/test_participant_metrics.py tests/test_market_store.py tests/test_market_data.py tests/test_report_context.py tests/test_participant_report.py tests/test_report_exports.py -q -p no:cacheprovider --basetemp=.phase-e-final-check

Result: 212 passed in 11.39s, including existing browser export regressions.
Equivalence tests compare complete enriched entity dictionaries and frozen context
for all three kinds in window/full-fifo modes, including exact decimal arithmetic
and missing money/quantity. Failing sentinels prohibit global ranking calls and
global window/history iterators. Targeted batch size 2 processes 3 window parents
with 8 child queries, or 5 FIFO parents with 12 child queries; unrelated/end
parents never enter child reads. Explicit verified FIFO fixture acquires 10,
disposes 4 historically and sells 8 recently: matched 6, uncosted 2, net P&L 30,
window turnover 120. Boundary tests cover start-1us, start, end-1us and end,
normal and legacy NULL precision, historical/default/future cutoffs.
CLI tests cover ambiguous cached/public names, unique public fallback,
not-found and inactive identities, uncached active/inactive IDs, exact-ID-over-name
precedence, institutional actors and no artifact writes.

Growth fixture adds 1,000 old trades involving the target and 1,000 unrelated
window trades to one target window trade. Entity output stays identical; exactly
one parent crosses the domain boundary, with four child queries. A separate
warm-after-ingestion disposable fixture measured the same 2,001 parents:
entity user/U, [2026-09-15T00:00:00Z,2026-09-22T00:00:00.000001Z), window mode,
default batch size. Target read model took 0.028658 seconds, with 19 traced SQL
statements including coverage/cache reads and four child queries. Timing is
observational, not an absolute test threshold. No production profiling occurred.

EXPLAIN QUERY PLAN for entity_activity_query on that fixture:

    CO-ROUTINE candidate_ids
    SEARCH transaction_participants USING COVERING INDEX idx_participant_user (user_id=?)
    SCAN c
    SEARCH t USING INDEX sqlite_autoindex_transactions_1 (id=?)
    USE TEMP B-TREE FOR ORDER BY

Reference lookup is selective by ID, but includes older references; parent time
predicates are residual filters and sorting remains. Bounded Python processing
is proven, not database-size-independent execution time. Coverage/status reads
retain their existing costs. G can measure these exact query/parameter contracts
before deciding on rewrites or indexes.

## Shared-file readiness for F/G

No outstanding shared-file changes are required to finish E. The metrics reducer
extraction and targeted entry point are completed and tested; F should implement
its selection change in the shared reducer/category finalization so both entry
points inherit it. G should profile entity_activity_query and the unchanged D
window/full-history queries before considering a new migration. The query,
category and context contracts above are stable for subsequent phases. F/G have
not been started. Future verified settlement/lineage support would require an
explicit report-basis/lineage contract review; E does not enable such evidence.
