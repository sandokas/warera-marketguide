# Phase C handoff

Status: complete. Date: 2026-10-06 (Europe/Lisbon).

## Public context contract

`src/warera_quant/report_context.py` supplies the immutable, database-free
`ReportContext` and `resolve_context(data_as_of, requested_as_of=None, *, generated_at=None)`.
`MarketStore.resolve_report_context(requested_as_of=None, *, generated_at=None)`
reads B's clock once and delegates to this shared resolver. It performs no writes,
history scan, sync-status inference, or implicit analysis-clock fallback.

Public fields (aware UTC datetimes, nullable where indicated):

| Field | Meaning |
| --- | --- |
| `data_as_of` | Nullable C: global retained market-fact ceiling from B |
| `requested_as_of` | Nullable original explicit request, normalized to UTC |
| `analysis_as_of` | Nullable effective logical reference/axis endpoint |
| `window_end_exclusive` | Nullable SQL/reducer endpoint; separate from the logical reference |
| `generated_at` | Actual report preparation/generation timestamp; independently injectable |
| `boundary_mode` | `database-inclusive`, `historical-exclusive`, or `unavailable` |
| `accounting_mode` | `full-fifo` in C, accurately describing the unchanged pre-D reducer |

`context.window_start(days=7)` derives its start from `analysis_as_of`, never the
technical endpoint. `to_dict()` serializes all fields with ISO UTC timestamps and
preserves microseconds; `ReportContext.from_dict(values)` reconstructs them.
DB reports write `report_context.json`, also included as a publication data asset.
The header displays analysis and generation separately. Participant CSVs include
logical/reference and exclusive endpoints plus data/requested cutoff, inclusion
mode and accounting mode; equipment CSVs carry the endpoint and inclusion mode.
Player-summary dates and report timestamps retain seconds and fractions.

Example C = `2026-09-23T12:18:04.123456+00:00`:

| Request | Analysis reference | Seven-day activity bounds |
| --- | --- | --- |
| Default | C | `[2026-09-16T12:18:04.123456Z, 2026-09-23T12:18:04.123457Z)` |
| Explicit T=C | C | `[2026-09-16T12:18:04.123456Z, C)` |
| Explicit T<C | T | `[T-7d, T)` |
| Explicit T>C | C, with original T recorded | Same inclusive ceiling as default |
| Empty DB | Unavailable | No window; no synthetic calendar/chart reference |

Source coverage still evaluates the logical reference, so the inclusion epsilon
does not change coverage completeness. Source observations contribute to C even
without trades; unfamiliar item codes/equipment and transaction-only databases
use the same global resolver. Administrative timestamps do not determine periods.

## Workflow and signatures/callers

- `ReportPreparation` adds `context: ReportContext | None`; its compatibility
  `as_of` field mirrors `context.analysis_as_of` for DB reports.
- `prepare_db_report(..., *, as_of=None, context=None)` and
  `run_db_report_workflow(..., *, as_of=None, context=None)` accept either an explicit
  request or an already frozen context. Both live and offline report modes use
  these functions. `cli.main(*, report_context=None)` lets rollout pass a restored
  context directly, without converting C into an ordinary `--as-of` request.
- Live sync finishes first. Requested display enrichment precedes final report
  resolution. Enrichment needs the existing displayed participant population,
  so it uses a selection-only preview from the same pure resolver and then
  refreshes profiles/assets. The final DB report context resolves once afterwards;
  every report input receives that frozen context. This enrichment prepass is
  separate from final report inputs and remains offline except for the explicitly
  authorized existing identity refresh. It currently incurs another participant
  history calculation when enrichment is enabled; ordinary offline reports do not.
- `load_market_rows`, `load_chart_trades`, `load_chart_data`,
  `load_highlight_trade_history`, `load_price_action_history` accept `context=None`.
  Their legacy `now` argument is an explicit cutoff request using the shared
  resolver, including historical exclusion and future clamping. With neither
  argument, they resolve the DB ceiling. Reopened highlight/all-item/research
  chart stores receive the preparation's context and never re-resolve it.
- `evaluate_item_forecast(..., context=None)`,
  `load_action_cost_results(..., as_of=None, context=None)`,
  `build_we24_market_index(..., as_of=None, context=None)`,
  `load_participant_report(..., as_of=None, context=None)`, and
  `iter_equipment_sale_details(..., as_of=None, context=None)` share the resolver.
  Action costs retain their completed-day representative-price convention.
  `estimate_latest_execution` also resolves its reference from the DB ceiling.
- Explicit-period inflation/history helpers `build_inflation_index_results`,
  `load_period_item_prices`, and `load_trailing_period_price_series` accept an
  optional context to constrain their already explicit periods before reading.
  They do not replace caller-supplied calendar period definitions.
- Store `transactions_for_window`, `price_observations_for_window`,
  `order_book_observations_for_window`, and `order_book_history_with_levels` add
  keyword `end: datetime | None`; omitted endpoints resolve the DB ceiling.
  `transactions_for_period` adds keyword `end` for a stricter supplied ceiling.
  Bounds apply in SQL before parent/domain materialization and before the batched
  forecast level read. Fractional lower bounds are supported. Exact integer
  microsecond transaction predicates use the legacy source-text fallback;
  observation bounds/order use parsed source microseconds. No new indexes/migration.
- `calculate_participant_rankings(..., as_of, window_end_exclusive=None, sources=None)`
  receives domain bounds only. Direct calculation callers keep their old exclusive
  behavior unless supplying the endpoint. Start and source coverage use logical
  `as_of`. FIFO/accounting logic and detail/leaderboard selection remain unchanged.
- `generate_html_report` and `write_outputs` receive optional domain `context`;
  they never access a store. The empty inflation-chart fallback no longer calls
  the clock. Timestamp ordering/forecast lookbacks retain source fractions rather
  than merging distinct timestamps in the same integer second.

Price-action charts retain all supported intervals (`1h`, `2h`, `4h`, `1D`), with
`chart_start = existing_interval_floor(analysis_as_of-period)` and
`chart_end = analysis_as_of`. The technical epsilon never moves the axis, start,
or partial-candle boundary. WE24 keeps fixed inception/weights and excludes the
unfinished UTC day relative to that reference, including exact-midnight behavior.
An empty DB CLI emits a clear unavailable result and its context JSON, without
creating fabricated charts or tables.

## Snapshot/resume and verification

Rollout resolves C from the completed report snapshot, not job start, sync status,
or wall time. Job and publication JSON persist the whole context, inclusion mode,
and accounting mode. Report retries preserve the frozen data/request/reference
and SQL endpoint while recording a fresh actual `generated_at`.

`market_data.resolve_publication_context(store, metadata)` is the shared restore
helper used by rollout and verification. New metadata reconstructs the context
without re-reading the cutoff; legacy `as_of`-only metadata remains an explicit
historical request. The verifier checks output context JSON against publication
metadata and rebuilds participant/equipment inputs with the context. Default C
is never replayed as an ordinary exclusive `--as-of C`.

## Validation

Final focused command (existing virtual environment, isolated DBs/outputs):

```powershell
.venv/Scripts/pytest tests/test_report_context.py tests/test_market_data.py tests/test_market_store.py tests/test_market_clock.py tests/test_participant_market_data.py tests/test_participant_metrics.py tests/test_cli.py tests/test_rollout_runner.py tests/test_charts.py tests/test_we24.py tests/test_report.py tests/test_report_exports.py tests/test_participant_report.py tests/test_inflation_market_data.py tests/test_inflation_action_costs.py tests/test_inflation_cli.py tests/test_inflation_report.py tests/test_inflation_charts.py -q -p no:cacheprovider --basetemp=.phase-c-final
```

Result: **332 passed in 12.93s**, including actual browser export regression checks.
`git diff --check` passed. New focused context and rollout tests cover independent
stale generation dates; rendered analysis/generated timestamps; UTC offsets;
global observation/transaction-only/empty clocks; start/newest/subsecond/ID ties;
legacy NULL precision; SQL upper bounds on transactions, observations and batched
forecast levels; forecast future exclusion; frozen context across reopened stores;
historical/future requests; completed-day WE24 midnight boundaries; no clock-based
empty chart fallback; resolution after live sync and enrichment; snapshot cutoff,
interrupted report resume after source advancement, inclusion metadata round trips,
and legacy historical jobs. Existing geometry tests cover every interval start and
partial candles. A final ordering audit added reverse-inserted same-second
observation coverage. No production sync, migration, DB change or publication ran.
Session-created temporary test directories/results were removed after validation.

## Compatibility limits / D dependency

This is only C. Schema remains v7. No participant window-accounting change,
query-index migration, targeted entity selection, cumulative 80% detail selection,
README/shared-plan edit, agents, staging, or commits. D must change the context's
accounting default and reducer/read-model implementation together; C intentionally
records the existing `full-fifo` behavior. Publication restoration currently rejects
an unsupported accounting mode instead of silently running FIFO under a different
label; D must extend this check when adding `window`.

Historical `--as-of` bounds trade statistics and forecast backtests. Cached names,
latest price quotes and latest executable order snapshots retain current snapshot
semantics; it does not promise to rewind these caches. The frozen context is a
stable cutoff, not a database/server snapshot guarantee: concurrent writers may
revise/add older eligible facts. Rollout's backup isolates its report facts; live
API pagination still does not promise a server snapshot. CSV/custom-endpoint
compatibility inputs remain outside the database-clock contract. Empty DB output
is an unavailable context/result, rather than a complete publication inventory.
