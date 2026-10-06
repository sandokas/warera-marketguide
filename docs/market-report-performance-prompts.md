# Market report performance: implementation prompts

Date: 2026-10-06 (Europe/Lisbon).
Status: prepared; no implementation agents have been started.
Requirements: [market-report-performance-plan.md](market-report-performance-plan.md).

## How to run these prompts

Use one phase per implementation-agent session. Each fenced block is a copy-ready
prompt referring to the shared plan. Read its predecessor handoff before running
it. Agents must finish their scoped code, meaningful tests, and handoff; they must
not continue automatically into another phase or spawn additional agents.

The command split is a documented working assumption: API-to-SQLite sync plus
report generation, rather than download-files plus import-files. Sending A
authorizes that interpretation unless the user supplies a correction; no extra
confirmation round is required. A correction to file download/import requires
revising the plan and dependent contracts. The cutoff and optional-FIFO choices
are already confirmed and must not be asked again. Carry out the selected phase
without repeated approval requests for ordinary edits.

| Wave | Run | Wait for |
| --- | --- | --- |
| 1 | A | No predecessor; documented command assumption |
| 2 | B | A completed |
| 3 | C | B completed |
| 4 | D | C completed |
| 5 | E | D completed |
| 6 | F + G, or F then G | E completed; disjoint-file rules satisfied |
| 7 | H | BOTH F and G completed |
| 8 | I | H completed |

Do not start phases based on a progress update saying "mostly finished." Wait for
passing focused checks and the completed handoff, with no writer still running.
The full dependency graph and file-ownership table are in the plan. F/G are the
only permitted implementation parallel pair; if unsure, run them sequentially.

All phases follow AGENTS.md, use the existing `.venv`, and preserve unrelated
changes. No production database writes/migrations, live API sync, publication,
Git commit/history manipulation, or new subagents are part of these prompts.
Read-only investigation of existing data is allowed without invoking helpers
that initialize/migrate it. Every test writes to its own temporary paths.
README/shared-plan changes belong to I; earlier agents write only their own
handoff note under `docs/market-report-performance-handoffs/`.

## A: Shared sync and report workflows

Prerequisite: none; uses the documented command assumption. Run alone.

```text
Implement phase A of docs/market-report-performance-plan.md in
C:\git\warera-marketguide. Read AGENTS.md, Scope and decisions, Common constraints,
R2, and Execution schedule and ownership first. Use the documented sync plus report
interpretation unless the user has corrected it. Sending this prompt authorizes
that scoped assumption; do not ask for another confirmation or invent a file-
download/import pipeline. If the user instead requests file download/import,
revise the plan/contracts before implementing that incompatible design. Do not
revisit agreed clock/FIFO choices. Work only on A and do not spawn agents.

Extract the existing DB report preparation/generation into one reusable workflow.
--sync calls sync_market_data and exits before preparation/publication. --from-db
calls the shared DB report workflow. --live calls the existing shared sync once,
then that same report workflow. Share functions, not subprocesses or recursive
main calls. Keep SQL in market_store, read models in market_data, calculations in
metrics, rendering in report/charts, and API parsing/HTTP in their existing layers.
An application workflow module may orchestrate these layers only.

Preserve existing CLI compatibility, defaults, progress, argument validation,
CSV/custom endpoint handling, resync/resume/backfill behavior, partial-sync policy,
and explicit identity-refresh behavior. Make enrichment a distinct step; plain
--from-db stays offline. This is a refactor: do not implement the new clock,
window/FIFO changes, indexes, or 80% selection yet.

Own cli.py, an optional new workflow module, and focused CLI/workflow tests.
Do not edit README, shared plans, migrations, or rendering styling. Use the
existing .venv/Scripts/pytest for tests. Add meaningful checks proving identical
workflow use/order, no report work for --sync, no market API calls for --from-db,
and equivalent preparation for the same supplied DB/options/cutoff.
Use temporary databases/output; do not sync or publish production data. Preserve
unrelated work and follow AGENTS.md Git restrictions; do not commit.

Write docs/market-report-performance-handoffs/phase-a.md with the shared entry
points/signatures, preserved enrichment/partial-sync behavior, changed files,
tests/results, and any limitations. A is complete only when the focused checks
pass. Stop after delivering A; B is a separate session.
```

## B: Persist and atomically maintain the global market clock

Prerequisite: A completed. Run alone; this phase owns the first new migration.

```text
Implement phase B of docs/market-report-performance-plan.md. Read AGENTS.md,
Common constraints, R3 Timestamp authority and storage, phase-a.md under
docs/market-report-performance-handoffs/, and the execution schedule. Work only
on B; do not spawn agents or start C.

Add a dedicated market_data_metadata table or equivalent typed metadata in the
next migration, exposing a read-only store accessor for data_as_of. Baseline
schema is v6: inspect current code rather than hard-code the next version.
The clock is the global maximum stored transaction created timestamp and
price/order observation timestamp, including legacy precision, transaction-only
DBs, unfamiliar item codes, equipment, and both streams. It is not fetched_at,
sync time, source updated_at, profile/asset time, or configuration/progress time.
Keep last_market_sync_at/status as separate metadata.

Bootstrap existing DBs once in the migration; empty DB clock is unavailable.
Do not write metadata in its accessor or run a history-wide MAX on every read.
Update it atomically inside all eligible store fact-write transactions: normalized
ingestion/page commits, legacy transaction upserts, prices, and order books.
Rejected/rolled-back facts never advance it. Old replay cannot regress it;
unchanged replay alone cannot advance it. Partial sync can advance only to actual
committed facts. Every existing sync mode uses these same paths.
Keep supported backup/restore/pruning behavior consistent with retained facts.

Own market_store.py, necessary sync.py integration, store/sync/migration tests,
and all affected schema-version assertions, including identity/migration tests.
No report-cutoff wiring yet; no indexes except metadata's own necessary key.
Use the migration system and initialize() on isolated test databases. Never run
DDL/DML, version-marker edits, initialization, or migrations against production.
Use existing .venv/Scripts/pytest; preserve unrelated work and do not commit.

Verify fresh install, v6 upgrade, both synchronized markers, legacy/subsecond
bootstrap, global maxima, empty DB, duplicate/old replay, newer observations,
partial commit, rollback, and reopen/backup maintenance. Record bootstrap strategy
and any measured one-time cost using disposable fixtures.
Write docs/market-report-performance-handoffs/phase-b.md with schema/version,
accessor signature, timestamp types, eligible write paths, tests, and limitations.
Stop after B with its focused checks passing.
```

## C: Use one database cutoff across reports, charts, and snapshots

Prerequisite: B completed. Run alone.

```text
Implement phase C of docs/market-report-performance-plan.md. Read AGENTS.md,
Common constraints, all of R3, and phase-a.md/phase-b.md handoffs. Work only on C;
do not spawn agents or implement window accounting/index/detail-selection changes.

Introduce a shared report context and reusable DB cutoff resolver. Resolve it
once after sync/enrichment; reuse it throughout the report even if stores reopen.
Default analysis_as_of is data_as_of C, independent of wall-clock generation time.
Default 7D activity is [C-7d,C], represented as [C-7d,C+1 microsecond). Start derives
from C, not from the technical upper bound. Explicit historical --as-of T<=C keeps
[T-7d,T). Future T clamps to C with effective context recorded. Empty DB outputs
are explicitly unavailable; no implicit now fallback. Keep generated_at separate.
Use UTC and preserve legacy/source microseconds. Publish the context contract.

Pass that context/bounds to market rows, historical references/trends/forecast
inputs, action costs, participant/equipment outputs, highlights, all-item/research
charts, and WE24. Add reusable store upper-bound support before materialization;
do not merely filter full future result sets in Python. DB read-model defaults
must use the shared resolver. Calculation/rendering modules receive domain values
and never query the store themselves. Preserve latest-order/cached-name semantics
of historical --as-of; it does not promise to rewind those snapshots.

Keep chart_start=floor_to_existing_interval(analysis_as_of-period), chart_end=
analysis_as_of, supported intervals, partial candles, and WE24's fixed inception
and completed-UTC-day rules. Audit implicit clock calls, including empty chart
fallbacks, so no data-axis period silently uses now.

Update market_rollout.py to freeze cutoff from the report snapshot, retain
explicit historical semantics on resumed jobs, and persist inclusion/context
metadata. Update verify_market_publication.py to reconstruct the same context;
passing C back as ordinary --as-of would incorrectly exclude the newest trade.

Own necessary CLI/workflow/context, market_data/store-bounds, timestamp rendering,
chart, rollout/verification code and focused tests. Do not edit README/shared
plans. Use temporary DBs/outputs and the existing .venv; no production sync,
migration/publication, Git commits, or unrelated refactors.
Verify stale clocks, independent generation time, global/transaction-only/empty
DBs, newest/start/subsecond ties, upper-bound leakage, historical/future overrides,
all chart interval starts, WE24 midnight behavior, live-after-sync resolution,
and snapshot/resume context. Run focused affected suites, not speculative rewrites.

Write docs/market-report-performance-handoffs/phase-c.md with public context fields,
boundary examples, changed signatures/callers, tests, and compatibility limits.
Stop after C's required checks pass; D consumes this contract.
```

## D: Window-only participant activity and explicit full FIFO

Prerequisite: C completed. Run alone.

```text
Implement phase D of docs/market-report-performance-plan.md. Read AGENTS.md,
Common constraints, R1, R3 boundary rules, and phase-c.md. Work only on D; do not
spawn agents or implement targeted-user/index/80%-selection/PNG phases.

Default participant reports must use a window-only store iterator, with both
SQL bounds applied before parent/child/domain materialization. Keep bounded
batched child queries. Add --participant-accounting {window,full-fifo}, default
window, and the corresponding read-model mode. Explicit full-fifo alone uses
the earlier-history iterator; retain older buys and dispositions and existing
chronological/ownership/lineage/fee rules. Window turnover always remains bounded.

Factor reusable aggregation/accounting helpers instead of copying the reducer.
Window mode does not perform or claim full FIFO, including in diagnostic CSV
generation. Expose accounting_mode/status. Retain diagnostic columns where
practical but make skipped realized-P&L/cost-basis/inventory fields unavailable.
Preserve valid window buy/sell averages and quantity comparisons while labeling
them as window comparisons, not realized profit or total holdings. Update CLI
summary/render/export labels only as required for this accounting distinction.

Preserve exact amounts, self trades, unresolved attribution, economic account
ownership, equipment variants, source versus gross basis, coverage diagnostics,
and the current top-ten entities/top-ten item selection until F changes details.
Do not infer membership from cached profiles. Reuse phase C's reference/start/
exclusive-end context; do not introduce another default clock.

Own necessary market_store.py, market_data.py, metrics.py, CLI mode plumbing,
accounting presentation/exports, participant/CLI tests, and runner verification
mode plumbing if needed. No README/shared-plan changes. Test only isolated data
using the existing .venv; no production writes/sync/publication or commits.

Prove with instrumentation that many older active-entity transactions are never
yielded/child-loaded in default mode. Compare window aggregation with the previous
implementation on fixed fixtures. Explicit full FIFO must still pass older-buy,
older-disposition, same-time, unknown-basis/fee, and equipment-lineage tests. A
default CSV must not invoke the history iterator. Add exact-boundary tests.

Write docs/market-report-performance-handoffs/phase-d.md with iterator/read-model
signatures, accounting/export contracts, processed-row evidence, tests/results,
and limitations. Finish D, then stop; E is a separate task.
```

## E: Resolve an entity first and query its activity directly

Prerequisite: D completed. Run alone. F and G consume this phase's stable contracts.

```text
Implement phase E of docs/market-report-performance-plan.md. Read AGENTS.md,
Common constraints, R1/R3, R5 candidate lookup notes, and phase-c.md/phase-d.md.
Work only on E; do not spawn agents or implement index/80%/PNG phases.

Refactor --player-summary to resolve an exact ID or cached name before participant
analysis. Preserve exact-ID precedence, case-insensitive exact cached-name matching,
ambiguous-name candidate reporting, uncached-ID support, existing exact-name
public lookup fallback, and no report-artifact writes for a console summary.
Name lookup may use the existing public API path; market history stays in SQLite.
Do not generate everybody's rankings to decide whether one user exists or is active.

Provide reusable targeted read models for (entity_kind,entity_id), covering user,
MU, and country, using D's window/full-fifo choice and C's cutoff. SQL reference
filters identify candidates only; existing domain ownership rules decide economic
attribution. A user acting for a MU/country must not get those trades attributed
as personal turnover. Preserve self/unresolved trades and actor diagnostics.
Reuse shared aggregation logic, batching, and export/display contracts.
Do not build a web interface or a separate accounting implementation.

Own targeted market_store.py methods, market_data.py resolution/read models,
CLI wiring, and targeted participant/CLI tests. No migration/index optimization,
README/shared-plan edits, production operations, Git commits, or unrelated changes.
Use existing .venv/Scripts/pytest and isolated fixtures.

Prove targeted results match the same entity in a complete window aggregation,
while avoiding unrelated parent materialization and global ranking calculation.
Test all entity kinds, ambiguous/uncached/no-activity names/IDs, actor ownership,
missing values, explicit full-FIFO mode, and precision/cutoff boundaries.

Write docs/market-report-performance-handoffs/phase-e.md with stable targeted query,
category, and context signatures; ownership behavior; changed files; tests; and
processed-row/query evidence. Explicitly list any shared-file changes still needed
so F/G are not started in parallel against an unstable contract. Stop after E.
```

## F: Select detail rows covering at least 80% of turnover

Prerequisite: E completed. May run alongside G only within the disjoint ownership.

```text
Implement phase F of docs/market-report-performance-plan.md. Read AGENTS.md,
Common constraints, R6, the F/G parallel rules, and phase-d.md/phase-e.md. Work
only on F; do not spawn agents. If G is active, strictly obey the ownership below.

Replace item_categories[:10] with the smallest leading set covering at least
80% of the entity's monetary turnover. Detail rows aggregate commodity/equipment
categories with buy+sell combined. Use exactly the entity's report money basis.
Sort descending by turnover with a deterministic signature tie break. Include
the crossing row, then stop; there is no ten-row cap and no tie expansion beyond
the minimal prefix. Use Fraction/Decimal arithmetic and threshold total*4/5,
not rounded display values or floating-point thresholds.

Reuse one helper for users, MUs, countries, and targeted detail display. Preserve
complete item_categories/per-side categories and complete window CSV exports;
top_items may remain the selected-prefix compatibility key. Preserve top-ten
entity leaderboards and compact top-three Bought/Sold summaries. Add useful
detail_selection coverage/status metadata. Missing money makes the true
denominator unknown: keep affected entity rows complete and mark coverage partial/
unknown rather than claim 80%. Handle empty/zero totals without division or loops.
Keep equipment signatures distinct, identities/icons intact, and escaping/precision.

Own ONLY metrics.py, report.py, tests/test_participant_metrics.py,
tests/test_participant_report.py, and your unique phase-f.md handoff. Do not edit
market_store.py, market_data.py, CLI, schema assertions, browser tests, README,
shared plans, or G's fixtures. If a necessary change crosses that boundary while
G runs, report the collision and schedule sequential work before touching it.
Use temporary test/output paths unique to F. Do not run a shared publication,
production operation, or Git commit/staging while G runs.

Test 80M first row, 79M then crossing row, exact equality, more than ten small
rows, decimal threshold, deterministic ties, all entity kinds, target display,
missing/partial amounts, zero/empty totals, and complete CSV retention. Use the
existing .venv/Scripts/pytest on participant metrics/report suites. Do not change
the query or accounting semantics established by D/E.

Write docs/market-report-performance-handoffs/phase-f.md with selection signatures/
fields, completeness fallback, examples, changed files, tests/results, and any
pending cross-file integration. Stop after F; H waits for BOTH F and G.
```

## G: Measure expensive queries and apply justified optimizations

Prerequisite: E completed. May run alongside F only within the disjoint ownership.

```text
Implement phase G of docs/market-report-performance-plan.md. Read AGENTS.md,
Common constraints, R5, the F/G parallel rules, and phase-b.md/phase-c.md/phase-e.md.
Work only on G; do not spawn agents. F is not a dependency, but may be active.

Measure the final default-window and targeted queries, per-item histories/daily
aggregates, name lookup, latest observations, item discovery, and explicit FIFO
history. Keep SQL/SQLite/profiling helpers in market_store.py. Record EXPLAIN QUERY
PLAN, fixture size/window/entity, parent/child-read counts, elapsed time, and cache
conditions. Explain selective searches versus residual filters, full/index scans,
and temporary sort/group operations. Existing index use is not proof of efficiency.

Test selective item/type/time indexes, exact-time ordering with legacy precision,
name COLLATE NOCASE lookup, latest-row seeks, and distinct item discovery. Prefer
removing unnecessary work before adding wide/redundant indexes. Preserve results,
time/ID ties, upper bounds, ownership, decimal evidence, and all full-FIFO facts.
Do not denormalize attribution, index a custom timestamp function blindly, or
require a particular index name instead of meaningful plan behavior.

Retain only measured improvements. Add indexes through the next migration after
B, without editing released migrations. Update ALL affected schema assertions.
Test fresh installs/upgrades/rollback and equivalence, and record added storage,
upgrade time, and ingestion impact using disposable DBs. Production profiling
is read-only: no initialization/migration, DDL/DML, ANALYZE, VACUUM, live sync,
or production publication. Avoid hard-coded timing pass/fail thresholds.

Own ONLY market_store.py, store/query/migration tests, affected schema-version
assertions (including identity tests), unique performance fixtures, and phase-g.md.
Do not edit metrics.py, report.py, participant-metrics/report tests, market_data.py,
CLI, README, shared plans, or F's fixtures. Cross-scope work while F runs must be
scheduled sequentially before editing. Use separate temporary paths; no shared
publication job or Git staging/commit while F runs. Follow AGENTS.md and use .venv.

Run focused affected suites and write docs/market-report-performance-handoffs/
phase-g.md with before/after measurements/plans, accepted/rejected candidates,
new schema version, migration/storage costs, changed files, tests, and limitations.
Do not claim a measured production speedup from synthetic timings alone. Stop
after G; H waits for BOTH F and G.
```

## H: Diagnose table right space and validate complete PNGs

Prerequisite: BOTH F and G completed. Run alone; F owns the renderer before H.

```text
Implement phase H of docs/market-report-performance-plan.md. Read AGENTS.md,
Common constraints, R4, and phase-c.md/phase-d.md/phase-f.md/phase-g.md. Work only
on H; do not spawn agents or change query, clock, FIFO, or selection contracts.

Investigate Trading Guide, Current Order Book, and Activity Comparison PNGs and
compare their paths with participant tables. Existing investigation did not
reproduce the strip; do not invent a cause. Use real browser rendering of raw
pre-export HTML and examine computed table/row/header/body/cell geometry, column
counts, colspans, footer contribution, sizing rules, and actual image edges.
Preserve intrinsic table sizing, legible fonts, and table-only element capture.

If reproduced, fix DOM/CSS/column sizing at its source. Do not crop screenshots,
capture only cells, force widths, shrink fonts, hide rows, clip overflow, or add
scrolling. Intentional padding/bar tracks/space inside a valid last column are
not unused canvas outside table columns.

Extend capture geometry checks/inventory to compare the table right edge with
actual last header/body cell bounds. Allow documented border/fractional rounding
and handle legitimate spanning cells/multirow headers/empty tables. Validate full
PNG canvas dimensions and existing cellsOutside/overflow checks. Preserve static
HTML table replacement, current-run inventory, identity links/assets, and decoded
fonts/images. Keep section composites distinct from table-only PNGs.

Own report.py, browser-export tests, and identity-export tests only as necessary.
Do not edit database/migrations/metrics/CLI or README/shared plans. Use existing
.venv and supported local browser, generating unique temporary output; no
production sync/publication/database writes or Git commit. Test representative
long/sparse rows and footer/span cases in all three old tables plus participants.
View actual PNGs; a DOM-only check is insufficient. Do not silently skip browser
checks or claim that a strip was fixed if it could not be reproduced.

Write docs/market-report-performance-handoffs/phase-h.md with reproduction result,
cause/fix if proven, geometry tolerances, viewed artifact paths, tests/results,
changed files, and any unresolved visual evidence. Stop after H; I integrates.
```

## I: Integration, documentation, and final requirements review

Prerequisite: H completed, with all A-G handoffs complete. Run alone.

```text
Complete phase I of docs/market-report-performance-plan.md. Read AGENTS.md, the
full plan, and phase-a.md through phase-h.md under the handoff directory. Work
only on integration/documentation; do not spawn agents or begin unrelated work.

Verify the final command workflows, atomic global clock, precise default versus
historical boundaries, stale/empty DB behavior, shared chart/WE24 context, default
bounded activity, optional full FIFO, direct entity analysis, 80% detail prefixes,
full window CSVs, measured indexes/migrations, tight complete PNGs, and rollout/
snapshot verification. Use the plan's Final verification and completion criteria
as the acceptance matrix. Review A-H contracts for mismatches, especially newest
trade inclusion, full-FIFO availability, actor attribution, category denominators,
and publication context reconstruction. Fix minimal integration defects at the
proper layer; do not duplicate logic or rewrite unrelated architecture.

Run appropriate cross-layer checks and the full suite once using the existing
.venv/Scripts/pytest after focused tests pass. Run actual browser export tests
with isolated fixtures/output and inspect representative PNGs. Do not skip failed
browser checks and mark completion. Confirm no production sync/migration/DDL/DML/
publication is needed or performed. Preserve unrelated changes and Git rules;
do not stage/commit unless the user separately requests it.

Update README with command examples, the database-derived clock and generation
time distinction, exact boundary/default/historical semantics, full-FIFO option
and unavailable diagnostics, targeted summaries, and cumulative 80% details.
Update docs/participant-rendering-contract.md where the old complete-detail
presentation conflicts with the new selection. Keep full CSV semantics clear.
Update this plan's status from evidence, not just completed-agent claims.

Write docs/market-report-performance-handoffs/phase-i.md with requirements-to-test
mapping, full-suite/browser results, measured performance/storage/migration notes,
intentional compatibility changes, changed files, and remaining limitations.
Report completion only if all required work passes. If a visual defect remains
unreproduced, say exactly which evidence was checked and what is still unknown.
Finish with a concise user-facing result; do not launch a production rollout.
```
