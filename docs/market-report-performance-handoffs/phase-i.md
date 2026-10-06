# Phase I integration handoff

Date: 2026-10-06 (Europe/Lisbon).
Status: complete. Final focused/browser and full-suite checks pass; original
outside-column strip remains unreproduced in the documented cases.

## Scope and contract review

Read AGENTS.md, the full performance plan and phase-a.md through phase-h.md.
Reviewed final callers and tests against the plan's nine Final verification
examples. Earlier handoff signatures are historical: C's `full-fifo` default is
superseded by D's `window`; E's ten-item details are superseded by F's 80% prefix;
B's latest schema v7 is superseded by G's v8. README and the rendering contract
now describe the final implementation rather than those intermediate contracts.

Final contracts agree on newest-trade inclusion, explicit FIFO availability,
actor versus economic ownership, same-basis category denominators and publication
context restoration. Integration tests exercise actual DB preparation, CLI output,
browser export and verifier together for both endpoint modes and accounting modes.
No duplicated SQL, API parsing, attribution, calculation or report workflow was added.

One minimal rendering defect was reproduced during inspection: nowrap numeric
participant headers inherited `max-width:24ch`, letting the longer window-accounting
labels extend into adjacent columns. `report.py` now gives those headers intrinsic
width with `max-width:none`. A browser Range regression failed before the fix and
passes afterwards. Fonts, table capture target and cell contents are preserved.
Phase H's pre-existing geometry changes and tests remain intact.

## Requirements-to-test acceptance matrix

All paths below are under `tests/`. These checks were rerun on final A-H code,
not inferred solely from the earlier handoffs.

| Final criterion / requirements | Evidence | Result |
| --- | --- | --- |
| 1. Stale DB / separate generation time (R3) | `test_report_context.py::test_stale_generation_global_clock_and_reopen_freeze`, `test_generation_timestamp_is_independent_in_rendered_header`; injected generation dates 20/90 days later preserve inputs; reopened chart store cannot re-resolve | Pass |
| 2. Sync-only, offline report, shared live preparation (R2) | `test_cli.py` workflow order/equivalence, sync error/partial handling and offline sentinels; `test_report_context.py::test_live_resolves_after_sync_and_enrichment_once`; new `test_market_report_integration_i.py` real offline CLI forbids API/sync | Pass |
| 3. Exact newest/start/end ties, historical exclusion, future clamp, empty DB (R3) | `test_report_context.py` boundary serialization, observation/forecast bounds, legacy NULL microseconds, equipment ties, empty read models/CLI; targeted precision tests; new integration exports newest equipment only in default mode | Pass |
| Shared chart and WE24 context (R3) | `test_charts.py` all interval floors and partial candles; `test_report_context.py` frozen reopen and completed UTC-day midnight WE24; `test_we24.py` fixed inception, weights, missing evidence; final CLI inputs/exports use one preparation context | Pass |
| Atomic global clock (R3) | `test_market_clock.py` all source kinds/streams, unfamiliar items, legacy precision, administrative timestamps, replay/correction, rejected/partial pages, progress/observation rollback, backup/restore and pruning; `test_sync.py::test_all_sync_modes_maintain_global_clock` | Pass |
| 4. Bounded default activity and explicit full FIFO (R1) | `test_participant_market_data.py::test_default_window_never_materializes_old_active_rows_or_csv` adds 2,000 old active-account parents, retains 2 domain rows/8 child reads at batch size 1 and prohibits history; metrics window inventory sentinel; earlier buy AND disposition FIFO tests match 6 units, leave 2 uncosted and P&L 30 without old turnover | Pass |
| 5. Direct user/MU/country analysis and actor attribution (R1/R2) | `test_participant_market_data.py` targeted/full equivalence in both modes, batching, growth, precision, reference-ownership diagnostics; `test_cli.py` ambiguity-before-analysis, inactive IDs, ID precedence and exact public-name fallback; new integration compares three targeted entities and preserves MU actor metadata without personal turnover | Pass |
| 6. Exact 80% selection, unchanged top ten, complete CSVs (R6) | `test_participant_metrics.py` 80/10/10, 79/11/10, equality, 100 equal rows, decimal precision, ties/equipment signatures, denominator mismatch, missing/zero/empty; all-kind/both-mode reducer tests; `test_participant_report.py` renders 80 of 100 and exports all 100 for each kind; new DB-to-browser-to-verifier test reconciles complete category total and omitted CSV rows | Pass |
| 7. Fresh/upgrade migrations, rollback, measured equivalence (R3/R5) | `test_market_clock.py`, `test_market_migration.py`, `test_market_query_performance_g.py` fresh v8, older/v6/v7 upgrades, synchronized markers, failure rollback, read equivalence, precise/coarse bounds, selective name/latest seeks; disposable measurements below | Pass |
| 8. Complete tight PNGs/current inventory (R4) | `test_report_exports.py` actual browser old/participant tables, sparse/long rows, 80-row details, spans/rowspans/empty states, physical extra footer column/right-padding rejection; `test_display_identity.py` image/link regressions; new Range/header and actual verifier integration; viewed PNGs below | Pass for tested cases; original strip unreproduced |
| 9. Snapshot/resume/publication restoration (R3/R1) | `test_rollout_runner.py` source advancement after snapshot, interrupted retry, legacy historical metadata, both modes crossed with both boundaries; new real browser/CLI publication verifier reconstructs saved context and validates actual asset files/CSVs | Pass |

## Commands and results

Existing `.venv/Scripts/pytest` throughout; every output/DB is isolated under
`phase-i-artifacts-20261006/`. Browser executable discovered by the normal helper:
`C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe`, headless Playwright
Chromium driver. No browser checks were skipped.

```powershell
.venv/Scripts/pytest tests/test_cli.py tests/test_report_context.py tests/test_market_clock.py tests/test_market_migration.py tests/test_market_query_performance_g.py tests/test_participant_market_data.py tests/test_participant_metrics.py tests/test_participant_report.py tests/test_rollout_runner.py tests/test_charts.py tests/test_we24.py tests/test_sync.py tests/test_market_resync.py tests/test_market_resume.py -q -p no:cacheprovider --basetemp=phase-i-artifacts-20261006/focused-verified
# 318 passed in 15.65s

.venv/Scripts/pytest tests/test_report_exports.py tests/test_display_identity.py -q -p no:cacheprovider --basetemp=phase-i-artifacts-20261006/browser
# 36 passed in 39.30s

.venv/Scripts/pytest tests/test_market_report_integration_i.py -q -p no:cacheprovider --basetemp=phase-i-artifacts-20261006/integration
# Initial four end-to-end cases: 4 passed in 15.33s

.venv/Scripts/pytest tests/test_market_report_integration_i.py tests/test_report_exports.py tests/test_display_identity.py tests/test_participant_report.py tests/test_report.py -q -p no:cacheprovider --basetemp=phase-i-artifacts-20261006/final-browser-focused
# After the header fix and regression: 88 passed in 58.31s

.venv/Scripts/pytest tests/test_market_clock.py::test_disposable_bootstrap_measurement tests/test_market_query_performance_g.py::test_measure_g tests/test_market_query_performance_g.py::test_upgrade_equivalence_cost_and_rollback_g -q -s -p no:cacheprovider --basetemp=phase-i-artifacts-20261006/measurements
# 3 passed in 4.11s

.venv/Scripts/pytest tests -q -p no:cacheprovider --basetemp=phase-i-artifacts-20261006/final-full-suite
# 736 passed in 74.62s; no skipped tests
```

Execution issues are recorded rather than omitted: the first focused invocation
used a nonexistent basetemp parent (156 passed/162 setup errors); creating the
parent and using a fresh child resolved it. An unqualified full-suite invocation
failed collection on the pre-existing inaccessible `temp_test_dir`; no tests ran.
Selecting the complete `tests` directory avoids that unrelated directory without
editing/deleting it or changing pytest configuration. That full suite passed
735 tests in 70.55s before the discovered header defect was fixed. The new header
test failed before its fix (one failure in 1.53s). Final focused checks were rerun
afterwards, followed by the final full suite above because code and test coverage
had changed. No failed browser assertion was skipped or relabeled as passing.

`git diff --check` passed during documentation review before the one-line header
fix and final handoff/status edits. After the final full suite completed, shell
launches for the final whitespace/status recheck failed with Windows
`CreateProcessWithLogonW` error 1909. Retrying without login semantics gave the
same error; this did not affect the completed suite or apply_patch documentation
writes. The last successful Git status and initial status confirm the pre-existing
H files; no staging/commit commands were issued. No final Git recheck is claimed.

## PNG evidence and remaining visual unknown

Inspected actual PNGs from the current local browser runs: Trading Guide,
Current Order Book and Activity Comparison with long/sparse rows and multirow
spanning headers/footers; ordinary participant details with a long equipment stat
vector; participant country volume with a long name/sparse Bought/Sold; and the
new integration's selected user details. Root copies are retained because the
image viewer cannot read the pytest child directories directly:
`guide.png`, `book.png`, `activity.png`, `participant-details.png`,
`participant-details-fixed.png`, `country-volume.png`, `integration-details.png`.

Final raw pre-export HTML, detailed table/thead/tbody/tfoot/row/cell/style/span
geometry and PNG canvas inventory remain in
`final-browser-focused/test_h_raw_geometry_spans_spar0/`. Header text Range bounds
are in `test_participant_numeric_heade0/header-text-bounds.json` under the same
basetemp. The viewed fixed detail image separates the formerly crowded headings
at the existing font size. Mechanically checked large 80-row canvases are complete;
they were not all inspected row by row at readable size.

The alleged unused strip outside actual columns did not reproduce in these cases.
Current tests compare real header/body/footer last-cell edges with table edges,
including border/rounding tolerance, and compare both PNG dimensions to the full
table. Legitimate cell padding, bar tracks and tall equipment-row space remain.
No user-supplied failing PNG/raw HTML exists in this task to establish the original
input, browser or cause. Those remain unknown; no fix of that unobserved strip is
claimed. The country missing-identity badge still wraps its fallback label; it
remains readable. Extremely repetitive long labels can exceed the existing
section-composite width policy (H evidence); arbitrary composite sizes are not
certified by the tested table-capture cases.

## Measured query, storage and migration notes

I repeated B/G's disposable benchmarks. B bootstrap: 50,000 legacy transactions,
warm just-written fixture, v6 -> v7 initialization **0.0712s**. G fixture:
30,200 parents (30,000 old / 200 active), 40 items, repeated active references,
legacy NULL precision every seventeenth parent, 20,000 observations per table and
10,000 cached names. Window `[2026-09-15, 2026-09-22)` UTC, entity user/U, batch 500.
Default SQLite planner statistics; no ANALYZE/VACUUM. First samples are warm after
ingestion and repeats use the same connection. Trace/EXPLAIN/progress overhead is
included. Times are measurements, never hard-coded speed acceptance assertions.

| Query | Parents/results | Child reads | First / repeat ms |
| --- | --- | --- | --- |
| Window | 200 | 4 | 2.186 / 1.641 |
| Entity window | 100 | 4 | 12.728 / 12.160 |
| Entity FIFO | 15,100 | 124 | 140.345 / 164.595 |
| Global FIFO | 30,200 | 244 | 331.538 / 376.534 |
| Recent item history | 5 | 0 | 0.350 / 0.148 |
| Long item history | 755 | 0 | 6.926 / 6.859 |
| Daily aggregate | 1 | 0 | 0.163 / 0.080 |
| Exact name | 1 | 0 | 0.183 / 0.021 |
| Latest price / order | 40 / 40 | 0 | 1.169 / 0.686; 1.008 / 0.691 |
| Item / equipment discovery | 40 / 8 | 0 | 9.848 / 9.933; 5.348 / 4.774 |

Local measurement output includes exact query parameters/plans in `measurements.txt`
(PowerShell UTF-16), with compact values in `performance-summary.json`. Full-window
parents use indexed type/time bounds with residual exact-time checks and temporary
ordering. Entity candidates use indexed references then parent ID seeks, with time
filters residual: default domain/child work is bounded, total SQL cost can still
grow with old references. Latest observations enumerate distinct indexed items
then seek each item; discovery still visits retained narrow keys. Equal-second
snapshot ties keep epoch/ID semantics rather than silently changing them to source
microseconds. Explicit FIFO still expands old references and consumes dispositions.

Repeated v7 -> v8 upgrade: **0.009702s**. Allocated DB pages grew from 14,024,704 to
14,299,136 bytes, **274,432 bytes** added. Injected post-index failure rolls back
index DDL and both markers; supported initialization then upgrades successfully.
v7 supplies typed global clock metadata and a one-time narrow timestamp bootstrap;
v8 adds only the entity-kind/NOCASE-name/entity-ID index. No new fact/child index
was retained. Ordinary clock reads do not scan history; explicit pruning or a
backward correction of a current maximum reconciles retained facts.

G's recorded before/after comparisons remain the optimization baseline: latest
price first sample 17.038 -> 1.083ms, name 1.663 -> 0.151ms; per-item broad composite
and native-precision candidates were rejected without sufficient benefit. G's
five-batch normalized-ingest medians were 0.067663s v7 / 0.073585s v8; name-cache
write medians 0.145981s / 0.169352s. I did not rerun ingestion-overhead experiments;
these are explicitly G's measurements, not new I claims. No production-size
upgrade duration, cold-cache throughput or guaranteed speedup is asserted.

## Intentional compatibility changes and limitations

- Default analysis uses retained source C, includes C exactly, and does not move
  forward with wall time. Explicit historical T <= C retains end exclusion;
  future requests clamp to inclusive C. Technical epsilon never shifts starts.
- Window accounting is now the default. Skipped realized P&L/costing diagnostics
  are unavailable, not zero; full FIFO is opt-in and may still lack basis/fees.
  Window comparison CSV names are explicit, with domain legacy aliases retained.
- Detail `top_items` means the exact 80% prefix, not ten rows or every category.
  Missing money retains all rows with unknown coverage. Full item/per-side CSVs
  remain complete window exports for all analyzed entities.
- Direct entity queries return only the requested economic account; actor IDs
  do not transfer institution turnover to a person. Coverage diagnostics for a
  targeted result describe reference candidates, not the whole market.
- Publication context carries inclusion and accounting modes. Persisted C-era
  contexts explicitly marked full-fifo still replay it; legacy metadata with no
  mode defaults to window and its `as_of` remains historical-exclusive.
- Empty DB CLI returns unavailable context/no fabricated publication. Historical
  trade bounds do not rewind current quotes/orders/identity caches. Cutoff freezing
  alone cannot isolate concurrent older-fact revisions; rollout backups isolate
  report facts, while live pagination remains without a server snapshot guarantee.

## Changed files and operational boundary

Phase I changes: README.md; docs/participant-rendering-contract.md;
docs/market-report-performance-plan.md; this handoff;
tests/test_market_report_integration_i.py; one numeric-header CSS rule in
src/warera_quant/report.py. Pre-existing H changes to report.py,
tests/test_report_exports.py, phase-h.md and phase-h-artifacts-20261006 are preserved.
Phase I test/browser/measurement artifacts are uncommitted local evidence.

No production sync, migration, DDL, DML, housekeeping or publication was needed
or performed. No production database was opened for these checks. Schema/data
changes occurred only in disposable test fixtures through the tested migration
system (legacy-fixture setup remains test-only). No production rollout was launched.
No agents were spawned and no files were staged or committed. A future deployment
of v6/v7 databases would require supported initialization to v8; this task does
not execute that production migration or authorize production timings.
