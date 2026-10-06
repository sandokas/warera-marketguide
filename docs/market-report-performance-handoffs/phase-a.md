# Phase A handoff

Status: complete. Date: 2026-10-06 (Europe/Lisbon).

## Shared entry points

All report orchestration remains in `src/warera_quant/cli.py`; no new workflow
module is needed. No subprocesses or recursive main calls are used.

- `sync_market_data(api, store, **existing_options)` remains the sole market sync
  service in `sync.py`. `--sync` returns immediately after sync/progress output.
- `prepare_db_report(store, args, assumptions, *, as_of: datetime) -> ReportPreparation`
  prepares market rows, sync metadata, WE24, action costs, participant rankings,
  optional identity enrichment, then equipment sale details, in that order.
- `run_db_report_workflow(args, assumptions, *, as_of: datetime) -> ReportPreparation`
  opens the selected store, prepares the inputs, closes the store, calls shared
  generation, and returns the preparation. Both `--live` and `--from-db` call it.
  Live first calls shared sync once and closes its sync store.
- `generate_report(args, assumptions, prepared: ReportPreparation) -> None`
  orchestrates existing calculations, chart read models/renderers, output writers,
  and asset export. CSV/custom endpoint input uses this same generation function.
- `_refresh_participant_display(store, report, *, verbose=False, progress=None)`
  remains the distinct identity/image enrichment step.

`args` is the existing parser namespace; `assumptions` is `FlipAssumptions`.
`ReportPreparation` contains `market_frame` (DataFrame preserving nested order
books), `as_of` (supplied UTC cutoff), `data_sync_metadata`, `we24`,
`action_cost_results`, `participant_report`, and `equipment_details`.
Optional fields default to None for compatibility inputs. Calculations, rendering,
SQL, HTTP, and API parsing stay in their original layers.

## Preserved behavior

Plain `--from-db` does not construct an API client or sync market data. Live still
refreshes displayed identities before output; `--from-db --refresh-identities`
still explicitly refreshes them. Existing refresh limits, forced freshness,
incomplete-refresh errors, standalone identity refresh, and player name lookup
are unchanged. The same preparation progress labels/order and summary remain.

A returned partial sync still allows live publication with partial sync metadata;
a raised sync exception prevents preparation/publication. No stricter publication
policy was added. Incremental, resync, resume, legacy backfill, pagination, pacing,
exclusions, defaults, argument checks, CSV/custom endpoint handling, implicit DB
selection, and chart options retain their existing wiring.

## Changed files

- `src/warera_quant/cli.py`: preparation context and shared preparation/generation
  workflow; removed duplicated live/offline preparation and unused local state.
- `tests/test_cli.py`: focused workflow order, equivalence, sync failure/partial
  policy, resume/backfill wiring, and custom endpoint compatibility checks.
- `docs/market-report-performance-handoffs/phase-a.md`: this handoff.

## Verification

Command:

```powershell
.venv/Scripts/pytest tests/test_cli.py tests/test_inflation_cli.py -q -p no:cacheprovider --basetemp=.phase-a-tests-4
```

Result: **44 passed in 1.64s**. All databases/output were temporary. Live API sync
and identity refresh in new composition tests were substituted; no production
collection or publication occurred. The equivalence test uses real DB read models
and compares preparation fields, calculated report inputs, and nonempty chart
history inputs for identical DB/options/cutoff. Renderer/asset export boundaries
are substituted where needed; existing tests exercise actual CSV/HTML output and
explicit identity enrichment. Existing offline tests forbid API construction.

The first run using the configured `temp_test_dir` encountered Windows permission
errors during pytest setup (20 passed, 15 setup errors). A fresh isolated basetemp
resolved this without changing project configuration or the existing directory.
An intermediate equivalence assertion was corrected to compare chart filenames
rather than different temporary output directory roots. Final focused checks pass.
Temporary `.phase-a-tests-*` directories created by this session were removed.

## Limitations and next phase

This is only Phase A. Wall-clock default cutoff capture, existing full-history
participant accounting, current chart/query boundaries, indexes, and detail
selection are unchanged. No migration, new clock, window/FIFO change, 80% selection,
rendering style, README, or shared plan changes were made. The workflow accepts
an already resolved cutoff for future context integration; it does not promise a
single database snapshot across preparation and later chart store reads. Browser
PNG geometry was outside this refactor and was not validated. No agents or commits
were created. Phase B remains a separate session.
