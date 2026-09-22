# Copy-paste implementation prompts: market participants and equipment

Status: planning artifact, 2026-09-22. These prompts describe future work; none of
the new commands is implemented merely by creating this file. Run phases in
order. Each fenced block is a complete prompt that can start a fresh session in
`C:\git\warera-marketguide`.

Authoritative design: [implementation plan](market-participants-implementation-plan.md).
Field evidence: [retention audit](api-field-retention-audit.md).

The user selected realized trading P&L where costs are known, not net cash flow.
Do not let a phase silently change that definition. Pending equipment listings
are excluded from this implementation; do not investigate or build their importer.
Equipment collection/storage is in scope, but equipment must stay out of the
shared report item list and its cards/tables/highlights. Dedicated equipment
presentation is deferred pending a display decision; participant analysis and
equipment data exports remain in scope.

| Prompt | Value delivered | Depends on |
| --- | --- | --- |
| 0 | Verified contracts, fixtures, and explicit unavailable semantics | Existing source/audit |
| 1 | Migration and lossless normalized storage | 0 |
| 2 | New complete-field collection and recent market data | 1 |
| 3 | Full-history enrichment command and progress/retention support | 2 |
| 4 | Tested realized P&L, participant rankings, trade explanations | 0-3; full live backfill need not be finished |
| 5 | Published PNG tables and equipment/participant exports | 4 |
| 6 | Integrated validation, backup, full import, final report | 0-5 |

Keep handoffs in `docs/market-participants-implementation-log.md` as each phase
is performed. Start with an empty log if absent. Record only completed work,
exact test results, API evidence, known limits, and the next phase. Do not mark
unrun tests or unimplemented commands as working. Plan changes should explain
the concrete evidence that required them.

## Prompt 0 — Verify the contracts before spending requests on history

```text
Implement phase 0 of docs/market-participants-implementation-plan.md in this
repository. Read AGENTS.md, that plan, and docs/api-field-retention-audit.md.
This phase is contract verification and representative fixtures, not bulk import.

Verify with bounded read-only calls and first-party documentation/frontend
evidence the participant-account semantics for trading and itemMarket sales:
user actor versus economic user/MU/country owner, ambiguous simultaneous source
references, source money versus gross/net proceeds, fee payer/amount/rounding,
equipment instance-ID continuity across acquisition/resale, and pagination
ordering/page limits. Record exact evidence and uncertainty. Current profile
membership must not establish historical account ownership. Matching equipment
codes or stats alone must not establish the same physical item.

Equipment sales are accessible through the transaction endpoint. Pending
equipment listings are outside this phase and the implementation scope. Do not assume
a date filter, seek-by-ID feature, or fee rule just because it would simplify work.

Capture a small sanitized fixture set for commodity personal/institutional
trades, equipment sales with multiple skill shapes and optional fields, and
individual orders. Clearly label synthetic edge cases separately from observed
responses. No API keys or raw personal profiles in fixtures. Raw API shapes may
exist as parser test fixtures; do not persist raw JSON in the application DB.
Confirm all known scalar fields except __v are accounted for; nextCursor is
temporary pagination state. Unknown fields must not silently disappear later.

Write docs/market-api-contracts.md with verified semantics and explicit fallback
behavior for unresolved facts: unresolved owner is excluded from ownership
rankings, unverifiable acquisition identity/cost is uncosted, unknown fees mean
net P&L unavailable. Do not block collection while unavailable P&L semantics remain.
Update the implementation plan only when evidence changes a decision.

Use the existing .venv and respect the architecture boundaries. Do not start a
full download, migrate the production DB, or implement speculative endpoint
behavior. Run existing focused API parser tests if source/fixtures affect them:
.venv\Scripts\pytest tests/test_warera_api.py

Create/update docs/market-participants-implementation-log.md with evidence,
fixture coverage, tests actually run, requests made, and phase 1 handoff. Finish
with a concise list of verified decisions and any limits affecting later phases.
```

## Prompt 1 — Add the database migration and normalized storage

```text
Implement phase 1 of docs/market-participants-implementation-plan.md. Read
AGENTS.md, the plan, docs/market-api-contracts.md, and the implementation log.
Inspect the current branch before selecting the next schema version (planned v5,
existing baseline v4). Preserve unrelated working-tree changes.

Implement the additive migration and MarketStore methods for transaction
participants, per-sale equipment snapshots with variable numeric stats,
individual order entries, typed unknown scalar fields, entity name cache,
normalization/enrichment status, and separate stream progress/coverage. Keep
existing transaction IDs, aggregate history, quote observations, and reports
compatible. Preserve source timestamps at full precision and exact monetary
representations for decimal costing. Do not store __v, nextCursor, serialized
dictionaries, or raw API JSON. Equipment item.quantity and transaction quantity
are separate facts when both occur. Item codes and sides already retained in an
equivalent form must not be misclassified as missing information.

Only market_store.py accesses SQLite; reusable domain models may live in a pure
module. Source field parsing belongs in warera_api.py, not the migration/store.
Provide consistent backup/restore support through MarketStore and an offline
--migrate-db command. Ensure schema changes and both version markers are atomic;
check executescript/transaction behavior rather than assuming rollback works.
Do not migrate or delete the real data/warera_market.sqlite3 in this phase; test
on temporary fixtures/copies. A migration cannot recover discarded identities.

Replace duplicate-ignore-only behavior with safe enrichment of existing IDs.
Commit each transaction and its children atomically. Distinguish omitted fields
from explicit null; older/partial responses cannot erase more complete or newer
facts. Return inserted/enriched/unchanged/rejected counts. Preserve old aggregate
snapshots without inventing individual orders. Keep distinct same-price orders
and derive compatible aggregate levels. Retain zero placeholder order facts
while excluding them from executable depth.

Add meaningful tests in tests/test_market_store.py and a focused migration test
file: v1/v4 upgrades, reopen/idempotency, failed-migration rollback, WAL-consistent
backup/restore, duplicate enrichment, missing optional fields, equipment snapshots
and skill preservation, unknown scalar values, and metadata exclusions. Keep
the existing architecture test passing. Test that previous row contents survive.

Run:
.venv\Scripts\pytest tests/test_market_store.py tests/test_market_migration.py
Create the new named file if needed; do not report it tested before it exists.

Update schema documentation and the implementation log with actual implemented
columns/constraints, test results, rollback evidence, and phase 2 handoff. Do not
start a bulk download or manufacture P&L data. Report the command as implemented
only after it is executable and tested.
```

## Prompt 2 — Collect complete commodity and equipment facts

```text
Implement phase 2 of docs/market-participants-implementation-plan.md. Read
AGENTS.md, the plan, verified API contracts, and the implementation log.

Extend warera_api.py to normalize all business fields from trading transactions,
itemMarket transactions, and individual top orders into the new domain models.
Keep all buyer/seller user, MU, country, and other verified references separately;
do not choose account ownership by arbitrary ID priority. Preserve nested
equipment instance details, code, optional type, every stat, condition, quantity,
and acquisition timestamp. Unknown scalar fields must be persisted with a visible
diagnostic; exclude __v and keep nextCursor only in memory. No raw payloads pass
into normal database/report layers. Live response examples belong in tests.

Refactor a reusable sync engine for two globally filtered transaction streams,
trading and itemMarket, without itemCode or the commodity-price catalog limiting
collection. Use one shared path for pagination and page commits. Validate mixed
types/codes, timestamp ordering, and optional fields at the API boundary. Keep
normal sync defaults backward compatible while including both types. Store
current commodity orders with individual identity and compatible aggregate depth.
Do not add pending equipment-listing collection.

Make legacy commodity query methods and item discovery explicitly select trading
history so equipment cannot pollute commodity VWAP, guidance, forecasts, WE24,
or item charts. Apply the selection in MarketStore queries, requested by
load_market_rows: filter the transaction branch of item_codes() and corresponding
trade queries to trading, while keeping commodity price/order-only rows. Do not
filter only rendered HTML, hardcode equipment exclusions, or restrict global
ingestion. Equipment must remain stored and separately queryable, without being
added to the shared report item list. Add a mixed commodity/equipment DB regression
that proves existing item cards and tables remain commodity-only.
Include unknown historical item codes in collection and use their
codes as display fallbacks. Resolve cached participant names only through sync,
with deduplicated best-effort lookups; --from-db must remain network-free. It is
acceptable to ship IDs first if supported name APIs have not been verified.

Provide a bounded seven-day enrichment route as described by the proposed
--sync --resync-market --history-scope 7d contract; default report window and
download scope must remain independent. Do not start a production download here.

Add parser/sync tests for field preservation, new codes, separate types,
institutional references, missing item.type, skill variants, unknown fields,
same-price orders, atomic page commits, and no leakage into commodity metrics.
Use fake clients and the existing .venv; tests must never read credentials or
call the network. Run relevant existing files:
.venv\Scripts\pytest tests/test_warera_api.py tests/test_sync.py tests/test_market_store.py tests/test_market_data.py tests/test_cli.py

Update README command semantics and the implementation log with the working
recent-data path, actual tests, and phase 3 handoff. No bulk history import yet.
```

## Prompt 3 — Make full resync reliable and observable

```text
Implement phase 3 of docs/market-participants-implementation-plan.md. Read
AGENTS.md, the plan/contracts, and the implementation log. Build on the shared
sync engine; do not copy pagination loops into cli.py or another importer.

Implement and test these CLI contracts:
--sync --resync-market --history-scope all
--sync --resync-market --history-scope 7d
--market-sync-status
Normal --sync continues collecting both market types incrementally.

Full resync enriches retained transaction IDs and imports missing history. It
never truncates the DB, never stops at a legacy duplicate/high-water mark, and
is not silently capped by --lookback-days, report windows, exclusions, or default
page limits. Validate conflicting options before API calls. Use max supported
page sizes, paced requests, bounded transient retries, and per-stream progress.
Report inserted/enriched/unchanged/rejected counts, pages, committed oldest/newest
events, normalized coverage, errors, and actual exhaustion. Do not equate API
exhaustion with complete game history or known inventory basis.

Use full timestamp precision plus IDs and test same-timestamp page boundaries.
An arbitrary duplicate is not proof that a stream is completely enriched.
Commit page data and progress together. Fix scan anchors and run a catch-up sync
for activity arriving during the scan. Keep orders/prices/config reads outside
transaction pagination so they are not repeated once per page.

Do not persist nextCursor and do not invent cursors from event IDs. If the API
offers no verified date/ID seek, restarting after process exit must replay pages
from the start, skip unnecessary DB rewrites, and continue older traversal.
Document that repeated network cost honestly: durable progress markers do not
make opaque pagination directly resumable.

Add transaction_retention_days ("all" or positive days) independent of existing
order/price retention. Set the project's transaction policy to "all" for this
requested historical import; preserve old-config compatibility. Ensure normal
housekeeping cannot immediately delete reimported history, and optional pruning
updates child rows and enrichment coverage coherently.

Test offline: full exhaustion, both streams, no-price-list codes, exact replay,
atomic crash recovery, same-time boundaries, arrivals mid-scan, failed pages,
rate limiting/retries, partial status, conflicting flags, retention and legacy
CLI behavior. Add tests/test_market_resync.py for the integration cases.
Run:
.venv\Scripts\pytest tests/test_sync.py tests/test_market_resync.py tests/test_cli.py tests/test_config.py tests/test_market_store.py

Update README, the plan if implementation evidence changed its contract, and
the implementation log with exact runnable commands and limitations. Do not run
the full production import in this phase; phase 6 owns the operational rollout.
```

## Prompt 4 — Calculate realized outcomes and explain trading activity

```text
Implement phase 4 of docs/market-participants-implementation-plan.md. Read
AGENTS.md, the full metrics/attribution contract in the plan, API-contract findings,
and the implementation log. The user explicitly chose realized trading P&L where
cost is known. Do not substitute sales minus purchases or call purchases losses.

Provide efficient bounded/streamed source queries in market_store.py, read-model
assembly in market_data.py, and pure costing/attribution/ranking calculations in
metrics.py. No SQL outside MarketStore, network in report/read models, formulas
in cli.py, or precomputed leaderboard facts masquerading as source history.

Resolve each transaction side once to its verified economic account. Retain
actor IDs, never double-credit a user acting for a country/MU, never infer owner
from current citizenship/membership. Conflicting/unresolved references are
excluded with explicit unassigned counts/value. Same-account self-trades remain
stored but are excluded from ranked volume/P&L. Parties have no requested board.

Compute commodity FIFO over all available earlier buys AND sells, not just seven
days. Use exact decimal allocation; realized P&L recognizes only in-window sold
quantities with known acquisition costs and verified fee treatment. Handle partial
lots, prior dispositions, unsold inventory, equal timestamps, unknown basis, and
fees already included in source money. No future purchases filling old sales.
Describe results as observed market FIFO P&L on matched sales, not complete wealth
or guaranteed full inventory accounting. Unknown cost/fee is not zero.

Equipment uses verified specific-item lineage only. Matching code/stats is not
proof of identity. Without reliable lineage/cost/fees retain equipment turnover
and descriptions but mark net realized P&L unavailable. Preserve each sale's
condition/stat snapshot. Do not block report delivery when only volume is known.

For each of user, mu, country return three independent top-ten lists: largest
negative matched realized net P&L, largest positive matched realized net P&L,
and greatest gross buy+sell monetary turnover. No top-volume prefilter for P&L.
Use deterministic tie breaks, no padding, and no-basis distinct from zero profit.
All outputs share [as_of-7d, as_of). Include matched/uncosted/unknown-fee coverage
and source completeness separately, with partial outcomes explicitly labelled.

For each ranked entity explain the top three buys and sells by money with units,
shares, and other totals. Equipment categories include the full sorted stats and
condition; category grouping is never identity matching. Provide a separate
equipment-sale detail export read model without commodity trading signals. Do
not design new equipment cards/tables or add equipment to the shared item list;
its dedicated visual display is deferred.

Add tests/test_participant_metrics.py and tests/test_participant_market_data.py.
Use hand-calculated fixtures from the plan plus institutional ownership, gaps,
multiple lots, known-zero vs unknown costs, equipment ID uncertainty, fees,
ties, UTC boundaries, top category percentages, and unseen item codes.
Run these and impacted commodity regressions with the existing .venv:
.venv\Scripts\pytest tests/test_participant_metrics.py tests/test_participant_market_data.py tests/test_market_data.py tests/test_inflation_market_data.py

Record calculation conventions, measured query behavior, exact tests, coverage
limits, and phase 5 handoff in the implementation log. Do not invent profitable
equipment matches simply to populate a leaderboard.
```

## Prompt 5 — Publish the nine leaderboards and data exports

```text
Implement phase 5 of docs/market-participants-implementation-plan.md. Read
AGENTS.md, the report/read-model contracts in the plan, and the implementation
log. Use the existing report/export pipeline, not a separate reporting app.

Wire the participant read model through CLI orchestration into
generate_html_report/write_outputs, and wire equipment details only into data
exports. Keep the shared item list, item cards/tables, highlights, and charts
commodity-only through the query scope established in phase 2. Do not add a
dedicated equipment summary or equipment item cards/tables: that presentation is
deferred until the user chooses a design. Add the nine seven-day leaderboards (loss,
profit, turnover for users, MUs, countries) with top buy/sell explanations,
equipment stats/condition, monetary volume, matched P&L, and explicit basis/fee
coverage. Results are on matched sales; do not relabel partial results as total
account net profit. Missing names fall back to IDs. No API calls when rendering
from the DB. Add --as-of for one reproducible UTC boundary.

Publish each table as a static PNG capturing only its table element, with its
own correct footer for method/window/units/coverage. Fix the current generic
commodity quote/fee footer injection so it does not contaminate participant or
equipment tables. Keep intrinsic legible sizing, wrapping, and complete capture:
no horizontal scrolling, clipped overflow, tiny fonts, or interactive tables.
Use companion explanation tables if required, not hidden hover content.

Add the plan's participant rankings/breakdown CSVs and equipment sales/stats CSVs,
stable table identifiers and explicit asset_inventory entries. Preserve every
equipment stat in the detailed exports even when summaries need separate rows.
Escape external names/labels and prevent spreadsheet formula injection without
altering preserved source data. Preserve existing commodity, chart, and WE24
output behavior. Old CSV input with no participant data shows unavailable or
omits the new section appropriately; it must not crash or imply zero outcomes.

Test populated/empty/partial boards, fewer than ten, correct ordering, repeated
entity types, unknown names, long stat vectors, method footers, exports and a
reproducible cutoff. Extend a real browser export test with complete table bounds
and PNG dimensions; visually inspect representative output for legibility.
Add tests/test_participant_report.py and extend tests/test_report_exports.py.
Run:
.venv\Scripts\pytest tests/test_participant_report.py tests/test_report.py tests/test_report_exports.py tests/test_cli.py

Update README/report documentation and the implementation log with output paths,
tests/visual verification performed, remaining limits, and phase 6 handoff.
No full production download in this phase.
```

## Prompt 6 — Validate, migrate, import all available history, and publish

```text
Execute phase 6 of docs/market-participants-implementation-plan.md. This prompt
authorizes the actual implementation rollout and full available-market-history
import, after required validation. Read AGENTS.md, the plan/contracts, and every
phase handoff in docs/market-participants-implementation-log.md. Verify that the
commands exist; do not assume planned code was implemented.

Run the full offline test suite once using the existing .venv:
.venv\Scripts\pytest
Resolve relevant failures before production mutation. Do not overwrite unrelated
worktree changes or require a clean tree just to run read-only validation.
Rehearse migration, interruption/replay, coverage, and report export on a temporary
legacy fixture/copy. Verify a consistent backup can restore the original data.

Inspect current DB/schema, concurrent writers, available disk, and transaction
retention policy. Use the implemented MarketStore backup/migration command to
create a consistent backup and migrate data/warera_market.sqlite3 without deletion.
Record the backup path and before/after counts. Quiesce only the conflicting
application writer if necessary; do not kill unrelated processes. Never replace
the database merely because migration/backfill is inconvenient.

Perform a small bounded live smoke of commodity/equipment transactions and order
storage, checking source fields, stats, identity attribution, counts, and that
existing IDs enrich instead of duplicating. No pending equipment-listing calls.

Run the full command implemented in phase 3 (planned contract):
.venv\Scripts\warera-marketguide --sync --resync-market --history-scope all --market-db data/warera_market.sqlite3

Observe the import to completion, handle transient errors through the bounded
retry policy, and surface genuine external blockers. No invented ETA, silent
7-day cap, destructive resets, or persisted cursors. If the process is interrupted,
document the committed progress and replay cost accurately. If a hidden background
helper is needed on Windows, use Start-Process -WindowStyle Hidden with logs;
do not treat launching a job as proof it finished. Keep the user informed during
long work. Network escalation may be required by the execution environment.

At available-history exhaustion for both streams, run normal catch-up sync and
offline status. Reconcile counts, enrichment version, oldest/newest event, gaps,
unattributed value, unmatched cost, and unknown fees. An exhausted API does not
prove all historical inventory or equipment lineage is known. Publish truthful
partial matched-sale P&L and volume even where exact P&L is unavailable.

Generate the report and PNG/CSV assets with a recorded common as_of. Check all
nine boards, equipment stats in the database/detail exports, stable output
inventory, and complete table crops. Confirm the shared item list/cards/tables
contain only commodities and no dedicated equipment presentation was introduced.
Report backup path, import
counts/request totals, dates reached, output links, tests, and residual coverage
limits. Pending equipment listings are not part of rollout or acceptance.

Update the implementation log and current architecture/data-model docs with what
actually shipped, not the aspirational plan. Do not delete backups or old aggregate
history as cleanup. Only claim completion for work actually completed.
```
