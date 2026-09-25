# Market participants implementation log

## Phase 0 — 2026-09-22

Completed contract verification and representative fixtures. Read `AGENTS.md`,
the implementation plan and field-retention audit (including its scalar-path
inventory). Existing staged application/docs changes and pre-existing deleted
test artifacts were left in place. No application module was changed by this
phase. No production DB was opened/migrated, no sync command or bulk download
was run, and no pending equipment listing API was called.

Deliverables:

- [Contracts and unresolved-fact fallbacks](market-api-contracts.md).
- [Machine-readable evidence ledger](market-api-evidence.json): precise request
  inputs/times/results, scalar inventory, relevant official schemas, frontend
  URLs and SHA-256 hashes. Cursor values and live source IDs are not recorded.
- [Fixture coverage and sanitization](../tests/fixtures/market_contracts/README.md):
  five commodity trades, five equipment sales, six individual orders; six separate
  synthetic transaction cases and one synthetic zero-order case.
- Offline fixture inventory guards in `tests/test_market_contract_fixtures.py`.

### Requests actually made

All API data calls were sequential GETs through the existing client, using its
one-second minimum interval. There were no retries of market API requests.
Existing credentials were used only on the configured official API host, pinned
to `https://api2.warera.io/trpc` for the market probe.

| Request | Input | Result |
| --- | --- | --- |
| transaction.getPaginatedTransactions | trading, limit 100, no item filter | 100 rows, HTTP 200 |
| transaction.getPaginatedTransactions | trading, limit 5, returned cursor | 5 rows, HTTP 200 |
| transaction.getPaginatedTransactions | itemMarket, limit 100, no item filter | 100 rows, HTTP 200 |
| transaction.getPaginatedTransactions | itemMarket, limit 5, returned cursor | 5 rows, HTTP 200 |
| transaction.getPaginatedTransactions | type array [trading, itemMarket], limit 3 | 3 rows containing both types, HTTP 200 |
| tradingOrder.getTopOrders | steel, limit 3 | Three bids + three asks, HTTP 200 |

Total: six authenticated read-only requests, 213 returned transaction rows (not
a unique-history count), six orders. Only representative selections were saved,
with consistent replacement IDs. Original responses existed in process memory;
no raw response dump or personal profile was written. Cursors existed only in
memory and were removed before fixture serialization.

Public documentation/frontend reads, without API credentials:

1. Web-tool open of official Swagger JS failed because its JavaScript content
   type was unsupported; public `/market` page open returned no extracted text.
2. Local urllib Swagger request was blocked by the network sandbox. Escalated
   retry reached the host but returned HTTP 403. Both attempts stopped before
   the second URL in that command; they did not request the market page.
3. Existing HTTP client's session, with its API header removed, fetched Swagger
   JS and `/market`, both HTTP 200. This successful transport was then used for
   public assets; no access restriction was bypassed using another identity.
4. Four assets discovered from the market HTML: shared `_app`, chunk `8147`,
   market page, build manifest; all HTTP 200.
5. One equipment page JS asset discovered from the build manifest, HTTP 200,
   inspected for money/tax display only. No listing endpoint request followed.

Public asset inspection copies and a one-off probe remain under ignored
`output/phase0-evidence/`; checked-in ledger/contracts contain the reproducible
evidence. The probe is a research artifact, not an application ingestion path.
No profile/name lookup, unsupported date/seek filter, mutation, limit-overflow
probe or exhaustion scan was made.

### Findings and evidence limits

- Live commodity examples and the frontend inventory/transaction components
  support personal versus single institutional account attribution, with actor
  references retained separately. Current membership is never evidence.
- No authoritative priority exists for simultaneous institution references;
  unresolved sides are excluded from ownership rankings with coverage counts.
- Frontend party side field names are confirmed; live market occurrence is not.
  Seller MU and order MU are now directly observed in the relevant endpoints.
- Source money is displayed per unit by dividing by quantity. Its relationship
  to gross consideration/net proceeds remains unverified. Current equipment
  purchase UI adds viewer-country market tax; fee payer, historical rates,
  settlement rounding and source inclusion are not established for either type.
  Unknown fees prohibit net P&L. Unknown gross meaning also prohibits claiming
  gross P&L/turnover; source-money totals remain usable and explicitly labelled.
- Equipment instance continuity is unavailable: all 105 sampled instance IDs
  differ. Current inventory ID lookup and `lastAcquisitionAt` do not prove a
  resale chain or acquisition cost. Equipment remains uncosted. Matching code
  or skills is never a substitute for identity.
- Official max page size 100 works for both transaction types. Both sampled
  page boundaries descend by millisecond timestamp, with no overlapping IDs.
  Upstream equal-time ordering, retention/exhaustion, snapshot stability and
  cursor lifetime remain unknown. No date/ID seek is documented.
- All known scalar fields from the audit and live sample have explicit retention
  destinations or unknown-field extension handling in the contract; `__v` is
  excluded and envelope `nextCursor` is temporary. This does not claim the old
  runtime parser already implements lossless retention.

### Tests actually run

Using the existing `.venv`, no installation/recreation:

| Command | Result |
| --- | --- |
| `.venv\Scripts\pytest tests/test_warera_api.py tests/test_market_contract_fixtures.py` | 30 passed (25 existing API tests + 5 new fixture guards), 0.04s |
| `.venv\Scripts\pytest` | 418 passed, 6.82s |

Tests use only checked-in synthetic/sanitized data and make no live calls. The
new tests compare selected scalar paths against the full bounded-sample inventory,
verify individual same-price order identity, optional equipment type and skill
shapes, and explicit synthetic fallback expectations. They are contract guards,
not tests of an unimplemented normalization/attribution engine.

Additional local checks: all fixture JSON parsed, observed 24-hex IDs use the
replacement namespace, and fixtures contain no credential-header names, profile
username/avatar fields or cursor fields. Scoped `git diff --check` for the plan
passed. Repository-wide diff checking encountered permission errors on the five
pre-existing `.pytest-tmp` tracked artifacts; those unrelated files were not changed.

### Phase 1 handoff

1. Inspect the current schema version before assigning v5. Implement additive
   normalized storage and domain models in the layers specified by AGENTS.md;
   consistent backup/migration belongs to `market_store.py` only. Do not migrate
   production as an incidental test. Test upgrades, rollback and replay on fixtures.
2. Retain all source references, including optional frontend-confirmed party
   references, exact numeric values, timestamp text/precision and per-sale item
   snapshots. Preserve omission versus null during enrichment. No raw JSON in DB.
3. Implement scalar-extension extraction and diagnostics at the API boundary;
   use synthetic unknown paths/arrays/nulls as losslessness tests. Known fixture
   paths cannot be the complete future schema. Decimal token preservation needs
   attention before the current client's float decoding loses source precision.
4. Keep source money, verified gross/net amounts, fee status, owner resolution
   and acquisition-basis status separate. Null/unavailable is not zero. Do not
   enable equipment costing, net profit/loss ranking, or gross labels from these
   fixtures alone. Collect and expose source-money volume/stats in the meantime.
5. Later ingestion uses two global transaction streams, at most 100 per page,
   memory-only opaque cursors and honest head replay on restart. Name lookup is
   optional and must not introduce profile-based historical ownership inference.
   Pending equipment listings stay outside every phase.

The implementation plan changed only where evidence affected decisions: phase 0
status, supported account interpretation/party names, verified limits/type array,
and source-money labelling while monetary semantics remain unavailable. The
target nine rankings and storage/import phases are not implemented in phase 0.

### Follow-up clarification — 2026-09-22

The user explicitly confirmed that commodity MU/country side references identify
the economic buyer/seller even though a player acts for the institution. Updated
contracts and plan to distinguish that confirmed product rule from unresolved
conflicting references. The user's suspicion that institutions cannot buy/use
equipment remains a hypothesis; the sample establishes only user-only occurrence,
not a capability restriction. Preserve any institutional equipment references
without adding speculative eligibility rules. Documentation-only update; no new
API requests, application changes or tests run for this clarification.

The user further emphasized independent buyer/seller attribution: a player can
buy from an MU, and different entity kinds across sides are not conflicts.
Contracts and plan now give explicit examples and retain a resolved counterparty
even when the other side is unresolved. Documentation-only clarification; no
additional requests or tests.


## Phase 1 ? 2026-09-23

Implemented additive schema v5, normalized market facts, safe enrichment,
consistent backup/restore, and executable offline `--migrate-db`. Read AGENTS.md,
the implementation plan, API contracts, and this log before editing. Inspected
`master` and confirmed the existing latest version was v4 before assigning v5.
Preserved the staged phase 0 documents/fixtures and unrelated pre-existing deleted
`.pytest-tmp` artifacts; no staging, reset, or commit was performed.

### Implemented schema and boundaries

The complete actual column/key/CHECK/FK inventory and behavior contract are in
[Market schema v5](market-schema-v5.md), linked from the existing database spec.

- Retained every v4 transaction column and ID. Added source `offer_created_at`,
  `updated_at`, `created_at_us`, `updated_at_us`, first/last fetch timestamps,
  exact `money_decimal`/`quantity_decimal`, per-number precision provenance,
  normalization version/status. Legacy version is 0; no migration guesses source
  decimals or identities. The ID-less fallback hash retains its pre-v5 inputs,
  including when request context now supplies an omitted trading type.
- Added participant rows keyed by transaction/side (buy/sell CHECK), equipment
  snapshots keyed by transaction, and open-ended numeric skill rows keyed by
  transaction/skill. All cascade through their parent. Equipment item quantity
  remains independent from transaction quantity. Institutions and actor IDs
  coexist without storage imposing account precedence or equipment eligibility.
- Added individual order entries keyed by observation/side/source position,
  preserving order ID, owner references, source type, exact numerics/provenance
  and offer timestamp. Side and nonnegative position have CHECK constraints.
  Existing aggregates are unchanged; no historical entries are fabricated.
  Fresh normalized entries derive compatible aggregate levels. Distinct orders
  at the same price and zero placeholders survive; only executable depth drops
  zero price/quantity.
- Added transaction/order presence tables and typed scalar extensions. Extensions
  CHECK null/string/number/boolean and null/value consistency. Escaped paths and
  array indices avoid collisions. Unknown fields produce diagnostics and stored
  `extensions` status. Nested `__v`, pagination envelopes, containers and raw JSON
  are excluded. Known item codes and envelope sides are already represented and
  are not diagnosed as missing/unknown fields.
- Added entity name cache keyed by kind/ID, including separate name-observed and
  lookup-attempt timestamps. Old attempts cannot overwrite newer names; failed
  lookups do not redate an old name.
- Added separate per-stream progress (mode, anchor, oldest/newest event markers,
  normalization version, attempts/pages/outcome counters/status/error) and
  enrichment interval coverage with source/reason/observation timestamp. Stream,
  mode, status, positive version and nonnegative counters have CHECK constraints.
  No cursor is stored and old price coverage is not promoted to enrichment.

`market_models.py` is pure domain data. `warera_api.py` parses source fields and
unknown leaves; HTTP JSON fractions now decode as Decimal before conversion.
Already-decoded float inputs are labelled `decoded_float`, not claimed as newly
recovered source tokens. New timestamp text is retained unchanged; integer
microseconds supplement, rather than replace, full source text.

Only MarketStore accesses SQLite. Normal API pages now expose normalized facts;
the legacy commodity `upsert_transactions` adapter delegates parsing to the API
boundary for existing dictionary callers. It preserves explicit type values and
uses commodity request context only for an omitted type. Read models explicitly
select trading; equipment and unclassified null-type rows remain stored but are
excluded from commodity histories/WE24/item discovery. Existing price/order-only
commodity codes still work. Old null types are not guessed during migration.

### Atomicity and merge behavior

Removed executescript usage from v1-v3 migrations. `initialize()` uses one
explicit `BEGIN IMMEDIATE` around all pending migrations and both version
markers. The failure test injects an exception **after** v5 DDL, a legacy money
UPDATE, `schema_meta.version=5`, and `PRAGMA user_version=5`. For both starting
v1 and v4 fixtures, every prior table/column value and both markers match the
pre-migration snapshot afterward; no v5 tables remain. Reopening and integrity
checking succeeds, and retry upgrades to v5 while preserving original contents.
This is observed rollback evidence, not an assumption about context managers.

Normalized transaction ingestion uses per-record savepoints inside a page
transaction. Invalid children roll back their parent and all siblings for that
record. Progress and coverage commit with the accepted page; a failed coverage
write rolls back the entire page. Rejected rows cannot establish exhaustion or
enrichment coverage. The existing sync path now records a failure instead of
mistaking rejected rows for duplicates and claiming scan coverage.

Returns inserted/enriched/unchanged/rejected counts, plus legacy skipped/newest
summary fields. Omission never clears prior values. Conflicting known fields
require a strictly newer source revision; newer explicit scalar null is an
explicit correction, while equal/older null cannot erase populated data. Partial
skill maps preserve other skills. Presence and per-field revisions distinguish
missing fields from null and protect newer corrections against stale replay.
Existing retention now cascades through children and trims new coverage; the
independent all-history retention setting remains phase 3 work.

### Backup and command evidence

`MarketStore.backup` uses SQLite online backup, includes committed WAL pages, and
refuses overwrite/self-target/uncommitted source state. `restore` validates the
backup and restores through SQLite backup without copying/deleting WAL files;
it preserves the backup's schema version until explicit initialization.
Applications must stop other writers before restore.

The WAL test holds a separate reader snapshot, commits a changed money value in
the WAL writer, backs up while connections remain open, upgrades/deletes the
fixture, then restores. The restored database is v4 and contains the WAL-committed
value; re-upgrade succeeds. This verifies the backup includes data outside the
main file, rather than testing only a closed-file copy.

The installed `.venv\Scripts\warera-marketguide.exe --migrate-db --market-db <temporary v4 fixture>`
was invoked by subprocess twice. Both runs exited 0, printed a backup path and
schema v5, retained the old transaction, and created distinct backups. The first
backup was inspected without initialization and remained v4. The CLI exits before
configuration/report/API processing. Migration failure retains the backup and
identifies its path in the error.

### Tests actually run

Used the existing `.venv`; no environment recreation or package installation.
Created `tests/test_market_migration.py` before running the requested command.

| Command | Final result |
| --- | --- |
| `.venv\Scripts\pytest tests/test_market_store.py tests/test_market_migration.py` | **39 passed**, 1.90s (33 store tests, 6 migration tests) |
| `.venv\Scripts\pytest` | **441 passed**, 8.41s |

The architecture check remains passing. Tests cover both legacy upgrade paths,
all old column contents, repeat/reopen migration, both version markers, injected
rollback/retry, WAL backup/restore, installed CLI execution, duplicate enrichment,
partial/null/stale updates, transaction-child/page atomicity, exact numeric token
precision, full timestamp text, same-instance separate-sale snapshots, missing
optional equipment type, retained skills, all observed fixture mappings, unknown
scalar paths/arrays/booleans/nulls, metadata exclusions, individual same-price and
zero orders, commodity-only queries, name cache freshness, retention cascades,
and the historical ID-less hash.

Intermediate runs found and fixed order validation-message compatibility, updated
schema-table expectations, stale item-code-selector test doubles, and a cursor
log assertion. A Windows default-encoding issue introduced during a test edit was
corrected, preserving the existing BOM and Unicode assertion. The rejection test
was aligned with existing sync behavior (returned item error instead of raising).
Broader reruns followed those failures/changes; final counts above are passing.
Scoped `git diff --check -- src tests docs` passed after removing a trailing blank
line. Unrelated deleted test artifacts remain untouched.

### Phase 2 handoff and explicit limits

1. Implement two global transaction streams, including delisted codes, and wire
   `ingest_transactions(..., progress=..., coverage=...)` into page orchestration.
   The old item-based stopping rules are not proof of normalized overlap. Replace
   them with tested stream-specific overlap/replay rules before claiming complete
   enrichment. Cursor values stay in memory; verbose logs now describe continuation
   without exposing the opaque cursor itself.
2. Use the normalized models and Decimal transport. Keep explicit null/omission
   and field revisions intact. Reject/quarantine unsupported source shapes visibly;
   use returned rejection counts to prevent false coverage. Unknown scalar paths
   remain source facts needing review, never automatic economic semantics.
3. Keep equipment storage separate from commodity reporting. Optional names are
   display cache data; no profile membership may rewrite historical ownership.
   Existing null-type rows require evidence to classify; migration cannot recover
   discarded participants or equipment identities.
4. Global bulk resync/status commands, all-history transaction retention, live
   name lookup, economic attribution/rankings, FIFO/equipment costing and PNG
   participant reports remain later phases. Source money semantics, fees and
   equipment lineage remain unresolved exactly as in phase 0. No P&L data was
   created and no gross/net profit claims were introduced.

**Operational scope:** no real `data/warera_market.sqlite3` connection, migration,
restore, deletion, bulk download, live API call, or production report generation occurred.
All database and command verification used temporary fixtures. Phase 1 provides
storage and executable migration tooling; it does not claim a completed history
import or restored legacy identities.


## Phase 2 ? 2026-09-23

Implemented complete-field global collection and the bounded recent enrichment
route on top of the existing phase 1 working tree. Read AGENTS.md, the plan,
verified contracts and this log. Preserved existing staged/unstaged work and
unrelated deleted test artifacts; no staging, reset or commit was performed.

### Working recent-data path

```powershell
.venv\Scripts\warera-marketguide --sync --resync-market --history-scope 7d --market-db data/warera_market.sqlite3
```

The command is implemented and exercised offline with fake clients and temporary
databases. It has **not** been run against production. It fixes one UTC anchor,
replays both global streams from their heads, enriches existing IDs, and imports
missing transactions until each crosses the seven-day boundary or returns no
cursor. It retains the complete crossing page, including its older rows. Equal
boundary timestamps continue to the next page. Report windows, chart horizons,
`--lookback-days`, and this explicit download scope remain independent.

Resync requires `--sync --history-scope 7d`; conflicting caps, legacy backfill,
item exclusions and offline actions are rejected before API construction.
`--history-scope all` remains deliberately unavailable. The existing
`--transaction-backfill --lookback-days N` route uses the same pager and retains
its independent scope; normal sync keeps the existing limits/defaults and now
collects both market types.

### Implemented behavior

- The API boundary accepts optional item context for compatibility, while normal
  collection requests only stream type, limit and an in-memory cursor. Both
  `trading` and `itemMarket` use the same endpoint path and parser. Mixed codes
  are valid globally; unexpected transaction types, constrained-code mismatches,
  malformed optional fields, missing page items and timestamp reversals are
  rejected. Full timestamp precision validates ordering within/across pages;
  equal timestamps are allowed without assuming an upstream ID tie order.
- Existing presence-aware domain models retain all observed source fields,
  independent buyer/seller user/MU/country/party references, equipment instance,
  code, optional type, every skill, condition, quantity and acquisition timestamp.
  Unknown scalar paths remain typed extension rows and now emit visible logging
  warnings for transactions and individual orders. Nested `__v` is excluded;
  envelope cursors never enter domain facts, persisted progress or logs. No raw
  page dictionaries pass into normal storage/report calls.
- One shared stream engine implements incremental, recent resync and legacy
  backfill pagination. Strict page ingestion commits all rows/children, progress
  counters and terminal coverage together; rejected rows roll back the page.
  Injected progress-write failures also roll back page rows. Earlier committed
  pages survive, errors do not establish exhaustion, and the other stream runs.
  Failure records store exception classes rather than request strings that could
  contain cursor values. Restart begins at the head, never a fabricated seek.
- Incremental stopping requires an entire previously normalized page inside
  verified coverage for the same stream, strictly reaching beyond its prior head.
  A lone duplicate, version-zero legacy row or per-item high-water mark cannot
  stop collection. Page caps record partial stream status and exclude the oldest
  timestamp from verified coverage. Trading scan coverage also updates legacy
  commodity chart provenance inside the same commit.
- Current prices/config/orders are collected separately from transaction pages.
  Their failures do not prevent transaction collection. Current quote exclusions
  never limit global ingestion. Unknown/delisted codes remain stored even when
  absent from the price map. Individual same-price orders retain their identities
  and positions while compatible aggregate depth remains available. No pending
  equipment listing endpoint was added.
- Retained explicit `transaction_type="trading"` item discovery in
  `load_market_rows` and trading-only legacy trade queries. Commodity price-only
  and order-only items remain discoverable. Added an explicit type selector to
  `transactions_for_window` for separate equipment reads; full normalized details
  remain available through `transaction_details`. Unknown display names fall
  back to the source code. No equipment-code exclusion list limits ingestion.
- Names remain IDs-first. Lookup endpoint candidates still lack verified
  response/access contracts, so no speculative lookups or account attribution
  were added. Any later name resolution must run only in sync, deduplicate
  entity-kind/ID requests, use best-effort cached results, and remain absent from
  `--from-db`. An offline CLI regression rejects any attempted API construction.

### Tests actually run

Used the existing `.venv`; no installation or environment recreation. All new
parser/sync tests use sanitized checked-in examples or synthetic responses through
fake clients. They never load credentials or call a network. All database tests
use temporary files.

| Command | Final result |
| --- | --- |
| `.venv\Scripts\pytest tests/test_warera_api.py tests/test_sync.py tests/test_market_store.py tests/test_market_data.py tests/test_cli.py` | **144 passed**, 2.10s |
| `.venv\Scripts\pytest` | **470 passed**, 8.09s |
| `git diff --check -- src tests docs README.md` | Passed |

Coverage includes observed transaction/equipment fields, missing item type,
multiple skill variants, institutional conflicts preserved without priority,
unknown scalar diagnostics/extensions, mixed/new/delisted codes, both global
streams, identity-preserving orders, API ordering/type/shape validation, exact
seven-day and equal-time boundaries, replay, normalized overlap, independent
failures, page caps, strict page rollback, progress rollback, catalog failures,
CLI flag conflicts, scope independence and network-free offline reports.

The mixed commodity/equipment database regression inserts equipment under both
its own code and a commodity's code. The complete shared market rows (including
VWAP, guidance and forecasts) and WE24 remain identical, commodity chart trades
remain unchanged, generated cards/tables exclude equipment, price/order-only
items remain, and equipment rows/stats are separately queryable. Existing
migration, store, report, architecture and chart tests remain passing.

Intermediate runs exposed obsolete per-item fake API signatures/stopping
assertions, which were replaced with global-stream contract tests, and a
keyword-only chart-loader test call, which was corrected. An initial full run
passed 467 tests; final catalog-failure isolation and missing-items guards were
then added and the relevant files and full suite rerun as shown above.

### Phase 3 handoff and limits

1. Build `--history-scope all` and the offline status command on this shared engine,
   not another pagination implementation. Add operational rate/page/row reporting,
   sanitized error detail, retry/cap policy and full-history coverage reconciliation.
   Existing HTTP pacing still applies; no new retry policy is claimed here.
2. Add tested catch-up behavior for arrivals during long scans and broader restart,
   same-timestamp and interrupted full-resync scenarios. Cursors remain memory-only;
   durable event markers do not imply supported historical seeking. Current
   overlap tests establish bounded observed replay behavior, not an upstream
   snapshot-consistency guarantee.
3. Implement independent transaction retention (`all` or positive days), keep
   observation retention separate, and update project configuration explicitly.
   Current housekeeping behavior is unchanged and can still prune older history.
4. Keep names optional until response contracts are verified. Preserve IDs and
   independent references; membership must never establish historical ownership.
5. Attribution, FIFO, equipment identity lineage, fee/gross/net semantics,
   participant rankings and dedicated equipment displays remain later work.
   This phase does not assert complete history or any realized P&L coverage.

Operational scope: no production database connection/migration, live API request,
production report, bulk history import, or pending equipment-listing collection.

## Phase 3 - 2026-09-23

Implemented full-history reconciliation, catch-up, offline status, transport
retry policy and independent transaction retention on the existing shared sync
engine. Read AGENTS.md, the plan, verified contracts and preceding implementation
log. Preserved the existing staged/unstaged phase work and unrelated deleted test
artifacts. No staging, reset or commit was performed.

### Runnable commands (implemented; operational import deferred)

```powershell
# Reconcile all API-available commodity and equipment history without truncation.
.venv\Scripts\warera-marketguide --sync --resync-market --history-scope all --market-db data/warera_market.sqlite3

# Reconcile seven days, independent of report/lookback settings.
.venv\Scripts\warera-marketguide --sync --resync-market --history-scope 7d --market-db data/warera_market.sqlite3

# Continue normal incremental collection of both market streams.
.venv\Scripts\warera-marketguide --sync --market-db data/warera_market.sqlite3

# Offline JSON status; no API client or credentials needed.
.venv\Scripts\warera-marketguide --market-sync-status --market-db data/warera_market.sqlite3

# Separate housekeeping respects the project's all-transaction policy.
.venv\Scripts\warera-marketguide --housekeeping --config marketguide.toml --market-db data/warera_market.sqlite3
```

These production-path commands are documentation only in this phase. All actual
command/database verification used temporary fixtures. Phase 6 owns the live
smoke test, backup/restore rehearsal and operational historical import.

### Implementation and evidence

- Both resync scopes use the existing global two-stream pager. All-history has
  no age/page cap and ignores duplicates/high-water marks, report horizons and
  lookback settings. Caps, exclusions, incompatible input modes and legacy
  backfill conflicts fail before API construction. Normal incremental sync and
  legacy backfill keep their existing scope contracts.
- Resync uses the verified maximum 100-row pages. Prices, game configuration and
  current orders are collected once, outside both historical and catch-up loops.
  Codes absent from current prices remain included in transaction collection.
- After both historical scans, successful streams replay their heads through
  the original fixed anchor, with a separately fixed catch-up anchor. Equal-time
  boundary pages continue until strictly older events or actual exhaustion.
  Committed event markers compare full microsecond timestamps plus opaque IDs;
  IDs are only deterministic local tie-breakers, never upstream seek tokens.
- Initial resync states remain running until catch-up finishes. Page rows,
  normalized children, counters, coverage and exhaustion observation commit
  together. An injected hard interruption during progress writing rolls back
  that page but preserves earlier pages. Failed pages do not create coverage;
  failures are independent per stream. Error records contain exception classes,
  not cursor-bearing transport URLs. Known rejected rows are counted separately.
- GET transport requests retain sequential pacing and have at most four attempts
  for timeout/connection errors and HTTP 429/500/502/503/504. Exponential backoff
  starts at one second; numeric Retry-After is bounded at 30 seconds. Permanent
  403 is not retried; mutations are not retried. Offline fake-clock tests verify
  bounded retries and pacing without sleeping or contacting the network.
- Offline status reports per-stream pages, inserted/enriched/unchanged/rejected,
  exact committed oldest/newest timestamps and IDs, normalization counts,
  coverage intervals, errors, measured elapsed scan time, and actual exhaustion.
  Exhaustion is independent of job success: a later catch-up failure remains
  failed/partial despite the earlier history scan exhausting. Page-capped normal
  sync now also correctly marks overall sync metadata partial.
- Exact replay no longer updates transaction/child rows or last_fetched_at when
  facts are unchanged. Progress records repeated observations. Equipment stats
  no longer issue an unnecessary parent INSERT OR IGNORE on exact replay.
- Added transaction_retention_days (all or positive integer days) independently
  of price/order retention. Missing settings preserve old retention behavior.
  The project explicitly sets all. Optional pruning cascades normalized child
  rows, clips enrichment and commodity coverage, marks trimmed coverage as
  retention-pruned, and marks affected progress partial. Transaction pruning
  uses microsecond precision where available. Observation pruning is unchanged.
- README, plan and schema notes now describe commands, operational limits and
  fetch-timestamp semantics. No API/domain contracts were strengthened beyond
  existing evidence. No participant costing/ranking/display work was added.

### Offline validation actually run

Used the existing .venv; no installation or environment recreation. Added
`tests/test_market_resync.py` with full exhaustion, both streams, no-price-list
codes, retained legacy enrichment, exact replay, opaque restart traversal,
atomic hard-crash recovery, same-time page boundaries, arrivals during the other
stream's scan, catch-up failure, rejected/failed pages, rate limits/retries,
partial offline status, conflict validation and retention coverage/cascades.
Expanded config validation and parameterized CLI wiring for both resync scopes.
Existing normal incremental and legacy CLI/backfill regression tests remain.

| Command | Final result |
| --- | --- |
| `.venv\Scripts\pytest tests/test_sync.py tests/test_market_resync.py tests/test_cli.py tests/test_config.py tests/test_market_store.py` | **127 passed**, 1.58s |
| `.venv\Scripts\pytest tests/test_warera_api.py` | **36 passed**, 0.04s |
| `.venv\Scripts\pytest` | **500 passed**, 8.37s |
| `git diff --check -- src tests docs README.md marketguide.toml` | Passed after removing an extra EOF blank line |

Intermediate validation found expected old page-count/failure-injection assumptions
and an API exception compatibility regression from rejected-row reporting. Updated
the former for catch-up/start markers and preserved WarEraApiError for parser
failures; the final focused/API/full suites above passed.

### Honest limitations and phase 6 handoff

Cursors remain memory-only. There is no verified date/ID seek and no directly
resumable deep opaque pagination. Restart must repeat requests from the head,
skip unchanged writes, and continue older traversal. Durable markers describe
commits; they cannot eliminate that repeated network cost. Full resync reruns
intentionally traverse all available history again.

Coverage is observed API pagination, not proof of complete game history or known
inventory basis. Snapshot stability, historical source retention, equal-time
upstream tie ordering, late backdated inserts/corrections, money/fee semantics and
equipment lineage remain unverified. Catch-up covers newly visible head activity
through a fixed anchor; future activity needs subsequent incremental collection,
and historical corrections need another reconciliation. Known malformed rows
count as rejected; transport/page-envelope errors do not invent rejected counts.

Elapsed timing covers completed transaction passes (including catch-up), not
catalog reads; a hard interruption may leave no finished timing. Remaining page
count/ETA are unknown. A running state after exit means interrupted work, never
successful exhaustion. Latest-invocation exhaustion remains a historical
observation if optional pruning later shrinks retained coverage. All-transaction
retention permits indefinite database growth.

No production database was opened, migrated, pruned or restored. No live API
request, production report, pending equipment-listing collection or full production
import was run. Operational completeness and profit/basis completeness are not
claimed.

## Phase 4 - 2026-09-23

Implemented offline participant source queries, read models, pure attribution,
FIFO/specific-item costing, independent rankings and trade explanations. Read
AGENTS.md, the full plan/metrics contract, API-contract findings and prior log.
No production database, API calls, ingestion, report displays or CLI formulas
were changed. Pre-existing deleted `.pytest-tmp` artifacts remain untouched.

### Entry points and architecture

- `MarketStore.iter_participant_history(start, end, batch_size=500)` selects raw
  references active in the window, retrieves their earlier buys AND sells, and
  includes every window transaction (including missing participants). A single
  ordered cursor plus four child queries per batch provides normalized parents,
  participants, equipment, stats and presence. No source history DataFrame,
  per-entity history query, or persisted leaderboard facts. Candidate raw actor
  references can conservatively over-select history; only resolved economic
  accounts with window activity become result rows.
- `load_participant_report(store, as_of=...)` shapes domain facts, supplies
  independent ingestion status, invokes metrics and joins dated cached names
  in batches of at most 250 keys. ID fallback needs no network. It returns common
  UTC boundaries, source status, global attribution coverage, entity aggregates,
  all category breakdowns and nine independent up-to-ten rankings.
- `calculate_participant_rankings` owns all economic resolution, costing,
  coverage arithmetic, category selection and deterministic ranking. No SQL,
  network, store imports or CLI calculations were added to this layer.
- `iter_equipment_sale_details` is a separate window-only streamed export read
  model: original sale condition, all stats, source references/actors, numeric
  precision, field presence and explicit unavailable P&L. It contains no commodity
  signals, executable asks, cards or new shared item-list entries.

### Calculation conventions and evidence gates

The common window is `[as_of - 7 days, as_of)` in aware UTC. Local history order
is full stored microseconds then opaque transaction ID. Naive timestamps and
unsorted/duplicate reducer input are rejected. Equal-time ID ordering is not
upstream causality: same-time purchases cannot cost a sale, and acquisition ties
are propagated to affected sales as ambiguous/partial. Prior sales consume lots
but do not contribute window P&L. A short sale cannot borrow future purchases.
Self-account transactions remain source facts and global diagnostic counts, but
neither side churns inventory or contributes ranked volume/P&L.

Each economic side is resolved independently once during the reducer pass.
Exactly one MU/country reference owns the side, with user retained as actor.
No citizenship/membership inference or actor double credit. Multiple institutions,
invalid/missing references and unsupported party accounts are excluded per side;
reason counts, source-money value and missing-money counts are explicit. An
unresolved counterparty does not suppress a resolved side. Actor IDs remain on
entity results; detailed equipment exports retain all raw side references.

FIFO uses exact decimal source inputs represented internally as rational numbers.
Proportional allocation and remaining lot costs are exact even for thirds; there
is no premature two-decimal or per-lot rounding. Public monetary/coverage fields
are Decimal projections at 50 significant digits, round-half-even. Rankings sort
exact internal values before projection. Unknown source values do not become zero
costs. Unknown-size purchases form FIFO barriers; an unknown-size prior sale
invalidates the remaining observed inventory for that owner/code. Unsold purchases
never create realized losses. The plan fixture produces **36 profit, 144 turnover**.

The pure metrics domain contract can accept explicit per-side `settlement`
evidence: `verified=True`, `money_role=gross|settled`, and an exact fee or None.
Gross money plus verified buyer fees establishes acquisition outlay; seller fees
reduce gross proceeds. Settled money already includes fees and is never charged
twice; a fee amount is needed to recover its gross value, not to subtract it again.
This is a tested evidence interface, **not a new verified API claim**. Neither the
DB read model nor unknown extension fields manufacture settlement evidence.
Historical money/fees remain unverified under phase 0, so current DB results use
**source-money turnover**, unavailable gross/net amounts and empty profit/loss
boards where no verified result exists. Report delivery still has volume.

Equipment costing additionally requires explicit `lineage_verified` evidence,
an identical instance and a previous acquisition by the resolved seller. Every
observed subsequent disposition invalidates the previous holding. Equal code,
condition/stats, or even an unverified equal instance ID never establishes a
match. The DB adapter never enables lineage. Verified equipment resale fixtures
exercise the future evidence interface only; no profitable live equipment match
was invented. Each export retains the sale's own stat/condition snapshot.

Known zero profit differs from unavailable profit; zero and unavailable are absent
from profit/loss boards. All entities participate in P&L selection regardless of
volume. P&L ties use descending turnover then entity ID; volume ties use entity ID.
No padding. One report-wide money basis avoids comparing gross and unidentified
source money. Buy/sell top-three categories use that same basis and expose units,
side-total shares, trade counts and monetary/count/share other totals. Equipment
signatures are versioned, include the full sorted stats and exact condition, and
preserve missing versus zero values. These signatures are descriptions only.

Basis-matched, net-matched, uncosted and unknown-fee quantities/source sale values
are separate; flags may overlap. Gross matched-sale coverage is only computed
when gross denominator/numerator are verified and denominator is nonzero;
source-money coverage is named separately. Missing source amounts label turnover
partial. Ingestion intervals, gaps, errors and API exhaustion are reported
independently from accounting coverage. Source gaps keep results partial even
when observed sales have 100% matched basis. Observed API coverage/exhaustion is
never a claim of full inventory provenance. Label results **observed market FIFO
realized P&L on matched sales**, not complete wealth or full inventory accounting.
Summing account-side turnover double-counts market trades; global diagnostic
source transaction value counts each source row once (self-trades separately
identified).

### Measured queries and bounds

The synthetic source test stores 2,420 normalized transactions: 1,200 relevant
old trades, 1,200 old inactive trades, and 20 current trades. With batch size 100,
1,220 rows are selected, inactive history is excluded, and exactly **53 source
statements** execute: one history cursor plus 4 x 13 child queries. Measured
stream iteration was **0.0280 seconds** on this Windows fixture, excluding fixture
insertion, name/status queries and costing. This is a small-fixture measurement,
not a production throughput or memory guarantee.

EXPLAIN shows `idx_transactions_type_time` for the bounded activity window,
all four covering `idx_participant_*` reference indexes, and parent primary-key
lookups for selected history IDs. Initial inspection exposed a planner choice
that scanned the market type for the final join; `CROSS JOIN` now fixes candidate
IDs as the outer loop, and a test guards the parent ID lookup. SQLite uses temporary
B-trees for reference distinctness, ID union and chronological order. Python holds
at most 500 source parents plus their children per batch; stats count per sale is
not capped. Costing retains open lots, ordering metadata and window aggregates.
Candidate deduplication/sorting and output categories are not constant-memory;
large historical activity still requires proportional work. No speculative index
or schema migration was added. Callers must finish streams before writing through
the same store connection.

### Tests actually run

Existing `.venv` used; no environment recreation, credential use or network tests.
Added `tests/test_participant_metrics.py` (30 cases) and
`tests/test_participant_market_data.py` (7 cases). They cover the hand calculation,
partial/multiple lots, prior dispositions, unknown basis and size, no future costs,
known-zero costs, explicit fees and included fees, mixed institutional ownership,
conflicts/self-trades/parties, no volume prefilter, all account kinds, ties,
UTC microsecond boundaries, unknown equipment lineage, verified synthetic resale,
intervening dispositions, per-sale stats/condition, unseen codes, category shares,
names, gaps, streaming query counts and query plans.

| Command | Result |
| --- | --- |
| `.venv\Scripts\pytest tests/test_participant_metrics.py tests/test_participant_market_data.py tests/test_market_data.py tests/test_inflation_market_data.py` | **88 passed**, 2.25s on final code |
| `.venv\Scripts\pytest` | **537 passed**, 9.42s; run before the final parent-lookup query-plan improvement |
| `.venv\Scripts\pytest -s tests/test_participant_market_data.py::test_batched_stream_query_plan_and_synthetic_volume` | **1 passed**, 0.84s; measured source iteration 0.0280s |

Initial focused tests found two fixture errors: a shared counterparty legitimately
realized profit in the ranking fixture, and a parser fixture omitted required
itemCode. Fixed those fixtures, then all focused tests passed. Subsequent review
added unknown-size FIFO barriers, independent fee/basis coverage and institutional
board tests. Full regression passed before the final query-plan adjustment; the
requested focused regressions and measured query test passed after that adjustment.
Final small refinements fix output rounding to half-even, omit meaningless mixed
unit sums from other totals, discard exhausted inventory keys, and retain ordering
metadata only for the current timestamp. The requested 88 tests passed again on
that final code. `git diff --check -- src tests docs` passed (only Windows line
ending notices).

### Phase 5 handoff and limits

Wire `load_participant_report` through orchestration to the existing report/output
pipeline with one reproducible `as_of`. Render its nine boards, top categories,
partial/result/source labels, and unavailable values without replacing None by
zero. Current DB-backed profit/loss boards may honestly be empty. Participant
source-money labels must persist until money/fee evidence changes. Add the planned
ranking/breakdown CSVs and flatten `iter_equipment_sale_details` into sale and
normalized skill CSVs; protect spreadsheet exports and escape names at rendering.
Decimal values require explicit export formatting; do not route them through float
for accounting. Report rows include raw names intentionally, not pre-escaped text.

Publish participant tables through table-element-only PNG capture, dedicated
participant footers, stable assets and geometry validation. Do not attach commodity
fee/quote footers. Equipment remains excluded from shared item discovery and its
dedicated visual design remains deferred. No phase 5 display/PNG/export integration
or phase 6 production migration/import/rehearsal is claimed here. Existing source
precision, ingestion gaps, nonmarket acquisition/disposition, historical fee
semantics and equipment continuity limits remain visible, rather than inferred
away from source totals or API pagination exhaustion.

## Phase 5 - 2026-09-23

Implemented participant publication in the existing report/export pipeline after
reading AGENTS.md, the report/read-model contracts and this log. No separate app,
production download, live API request, production migration or equipment display
was added. Existing commodity discovery scope, cards, charts and WE24 remain.

### Wiring and outputs

- CLI supplies one aware UTC `--as-of` boundary (or one invocation timestamp) to
  participant/equipment models and the existing commodity/WE24/chart read-model
  clocks. Naive or invalid timestamps fail argument parsing. Offline rendering
  needs no credentials/API client. Participant and equipment windows are exactly
  `[as_of - 7 days, as_of)`; earlier costing history remains in the read model.
- `load_participant_report` flows into `generate_html_report` / `write_outputs`.
  `iter_equipment_sale_details` feeds data exports only. Cached names are used
  with ID fallback. CSV-only input omits the new section and its inventory assets.
- Nine independent up-to-ten boards retain the model's ordering and matched P&L
  semantics. Coverage companions show purchases/sales, unknown basis/fees and
  source status; explanation companions show top buys/sells, full equipment stats
  and condition, quantities, money, shares, counts and other totals. Repeated IDs
  across entity kinds remain distinct. No padding or unavailable-as-zero P&L.
- Each table owns a method/window/units/coverage footer. Existing footers are not
  overwritten or supplemented by the generic commodity quote/fee note. Tables
  use intrinsic sizing, wrapped text, 16px cells and 14px footers. No scrolling,
  clipped columns, hover-only facts or equipment item tables were introduced.
- Stable identifiers and paths are
  `participant_rankings_7d/participants-{user|mu|country}-{losses|profits|volume}.png`,
  plus `{coverage|explanations}` companions where populated. Table screenshots
  include only the table and its footer; section composites remain separate.
- CSV paths: `participant_rankings_7d.csv`,
  `participant_trade_breakdown_7d.csv`, `participant_trade_stats_7d.csv`,
  `equipment_sales_7d.csv`, `equipment_sale_stats_7d.csv`. Normalized stat rows
  retain every skill, including stats too long for compact summaries. Breakdown
  joins use entity kind/ID, side and category index; equipment joins use sale ID.
  Ranking exports retain gross/matched net amounts, quantities and accounting
  coverage, plus source/attribution metadata and exact decimal projections.
- Current report metadata explicitly registers the five CSVs with the existing
  `asset_inventory.json` exporter; table assets include stable `table_id` values.
  There is no file glob that could accidentally publish stale participant files.
- HTML escapes external text; formula-leading CSV strings receive an apostrophe
  only in exported copies. Existing commodity CSV strings receive the same
  protection. Stored source facts and exact decimal accounting values are not
  rewritten. Empty datasets produce header-only files, not invented rows.

### Tests and visual verification actually performed

Used the existing `.venv`, temporary fixture databases and local headless Chrome.
Added `tests/test_participant_report.py` and extended both requested export tests
and CLI tests. Covered all nine populated boards, empty/partial results, fewer
than ten, independent loss/profit ordering and ties, repeated entity IDs across
kinds, missing names, malicious labels, 30-stat explanation vectors, 50-stat exact
exports, table-specific method footers, missing values and legacy CSV mode.
The real offline CLI test runs twice at the same offset-normalized cutoff,
compares participant CSV bytes, excludes transactions exactly at the boundary,
and rejects any attempted API construction. Equipment remains out of item cards.

Real browser tests export 15 participant tables (nine boards plus six companions),
check table scroll bounds and every cell rectangle, enforce minimum 14px text,
and compare each PNG's IHDR dimensions against 2x CSS bounds (outward-rounding
tolerance only). Published HTML contains images rather than table elements.

Visually opened user profit, coverage and explanation PNGs. Verified long names
wrap, full stats and exact condition remain visible, footers are readable and
complete, and no surrounding headings/whitespace enter the crop. Visual review
prompted removal of redundant matched-value columns from the coverage companion
(they remain in CSV), reducing its width while keeping displayed basis/fee values.
Final representative images in the temporary test output were:

| Image | PNG pixels | CSS minimum font / outside cells |
| --- | --- | --- |
| User profits | 2562 x 582 | 14px / 0 |
| User coverage | 2648 x 692 | 14px / 0 |
| User explanations (30 stats) | 2074 x 2014 | 14px / 0 |

| Command | Result |
| --- | --- |
| `.venv\Scripts\pytest tests/test_participant_report.py tests/test_report.py tests/test_report_exports.py tests/test_cli.py -q` | **60 passed**, 8.82s on final code |
| `.venv\Scripts\pytest -q` | **545 passed**, 14.91s before the final coverage-column visual refinement; requested 60 tests passed afterward |
| `git diff --check -- src tests docs README.md` | Passed before this log append; final check repeated below in the implementation session |

Initial checks caught an append-encoding issue, an outdated fake-store fixture,
and a containing section narrower than its intrinsically sized table. Fixed all
three; browser section/table captures now pass complete-geometry checks.

### Limits and phase 6 handoff

Historical money/fee semantics and equipment lineage remain unverified. Current
DB-backed profit/loss boards can be empty, while source-money volume remains
available. Synthetic verified settlement fixtures exercise populated P&L displays;
they are not live evidence. Partial results never claim total account net profit.
Unknown basis/fee flags overlap and source ingestion coverage is not inventory
provenance. Nonmarket acquisition/disposition remains outside this accounting.

`--as-of` fixes participant/export boundaries and report clocks; it does not
rewind current commodity order snapshots, cached names, or later database
corrections. This phase preserves current commodity execution context rather than
adding historical snapshot replay. Seven-day equipment details are materialized
for export after the DB context closes; large windows and long explanation tables
still use proportional memory/image height. No production-scale memory guarantee.

Phase 6 still owns the production backup/restore rehearsal, bounded live smoke
test, operational full-history import and measured throughput/coverage review.
Validate the new CSVs, stable PNG assets and honest empty profit/loss boards after
that import. Do not enable fee or lineage evidence without verification. Equipment
summary/cards/table design remains deferred pending the user's choice. No full
production download or claim of operational/history completeness was made here.

## Phase 6 rollout - 2026-09-23 (in progress)

User explicitly authorized production migration and all available history import.
Read AGENTS.md, the complete plan/contracts and every previous phase handoff.
Existing worktree changes and deleted test artifacts were preserved. Verified the
installed CLI options and the implementation before invoking the commands.

### Validation and production migration

- Existing `.venv\Scripts\pytest`: **545 passed in 14.16s**, full offline suite
  run once before production mutation. It includes real browser crop tests.
- Additional combined legacy-v4 rehearsal under
  `output/phase6-rollout/rehearsal`: installed migration CLI, exact original-table
  restore comparison, injected second-page interruption/rollback, head replay,
  observed exhaustion for both synthetic streams, idempotent replay, retained
  equipment stats, commodity isolation, offline report with nine board PNGs and
  complete geometry. Common fixture cutoff `2026-09-23T12:00:00Z`. Visually opened
  the user-volume table crop. Synthetic exhaustion is not production evidence.
- Process inspection required sandbox escalation. No Python/market application
  writer was present; no process was stopped. Disk free: 568,636,612,608 bytes.
  Project transaction retention is `all`, independent of 120-day observations.
  No housekeeping or aggregate-history cleanup was run.
- Production before: schema/user version **4/4**, integrity **ok**, **9,112,962**
  trading rows, source dates `2026-06-06T17:30:03.242000Z` through
  `2026-09-22T18:58:58.893000Z`. No equipment history in the legacy database.
- Executed implemented `--migrate-db --market-db data/warera_market.sqlite3`.
  Consistent backup:
  `data/warera_market.sqlite3.backup-20260923T175846714697Z` (2,008,023,040 bytes).
  Result **5/5**, integrity **ok**, same **9,112,962** transactions. All old table
  counts preserved: 109,550 order levels, 1,744 order observations, 1,855 price
  observations, 843 legacy coverage rows, 24 production configs, 24 item states,
  three schema metadata entries. Normalized child tables initially empty.
- Restored that production backup through `MarketStore.restore()` into separate
  `output/phase6-rollout/production-restore-check.sqlite3`. Original schema,
  integrity, table counts and stream dates match the pre-migration inventory.
  Production was not replaced or rolled back. Both backup and restore copy kept.
- Added read-only `MarketStore.database_inventory()` for reusable pre/post
  inspection without incidental migration. Focused migration tests after this
  addition: **7 passed in 1.37s**, including no creation/mutation during inspection.

### Bounded live smoke and import launch

Initial sandbox networking failed with four bounded ConnectionError attempts
(Windows socket permission denial); no HTTP response or database write followed.
Escalated live access succeeded. Two bounded probes each requested five commodity
transactions, five equipment transactions and three steel orders per side:
**six HTTP 200 responses**, no pending-listing calls. First helper invocation
stopped after commodity verification because of a list-call typo; second verified
source decimals, independent participant references, six equipment stats on five
sales, six individual orders and unchanged exact replay. It then hit a helper-only
missing timestamp field in the temporary legacy fixture. Fixed that fixture and
completed legacy enrichment/replay verification offline from the stored live
sample, with one existing ID enriched and zero duplicates. No extra network
request was needed. Production smoke inserted ten commodity rows and five
equipment sales in total; exact replay added none. Evidence:
`output/phase6-rollout/smoke-verified.json` plus prior-attempt summaries.

Started the exact implemented full-history command with escalated network access:

```powershell
.venv\Scripts\warera-marketguide --sync --resync-market --history-scope all --market-db data/warera_market.sqlite3
```

Progress is redirected to `output/phase6-rollout/full-import.log`. The printed
`lookback=7` startup default is a legacy report/backfill setting, not an all-history
cap; this invocation's history boundary and page cap are absent. Cursors remain
in process memory only. Launch is not completion; production exhaustion, final
catch-up, coverage reconciliation and final publication remain pending.

### Continuation - 2026-09-23 23:23 UTC (September 24 local)

The original process was absent on continuation. Its log ended at page 1,574
without a success/error footer; database progress still said running. Cause of
termination is not established. Committed trading progress: **118,224 inserted,
39,166 enriched, 10 unchanged, zero rejected**, oldest recovered timestamp
`2026-09-22T12:46:46.931Z`, newest `2026-09-23T18:02:45.762Z`.
Retained trading rows **9,231,196**, normalized **157,400**, legacy/unverified
**9,073,796**. Equipment still contains the five normalized smoke sales; its
full scan had not started. Neither stream exhausted; no contiguous enrichment
coverage interval was certified. Saved `output/phase6-rollout/interrupted-status.json`.

Original-run successful requests include 1,574 transaction pages plus the 26
current-state requests (prices, configuration, 24 order books); retries/failed
attempts were not instrumented and their total is unknown. No completed scan
duration was recorded. Restart must repeat pagination from the current head,
including new arrivals and the previously traversed history; the old 1,574 pages
are not a saved seek point or an exact future replay count.

With no conflicting writer present and 558,297,350,144 bytes free, restarted via
`Start-Process -WindowStyle Hidden` using the existing virtual environment. The
operational wrapper `output/phase6-rollout/run_import.py` delegates unchanged
full-resync arguments to `cli.main`, records HTTP attempt/status counts without
URLs or cursors, and permits normal catch-up only if both streams exhausted and
sync metadata reports success. Logs, transport counters and completion/failure
markers are under `output/phase6-rollout/restart-20260923/`. A running process
marker alone is not completion. No backup or history was deleted.

A separate consistent snapshot is being used for a partial interim report;
this does not replace the required final report or claim import acceptance.

The first production-snapshot report exposed a legacy precision bug: participant
SQL ordered unenriched rows by whole-second epoch plus opaque ID, while metrics
correctly required full source timestamp order. It failed closed with
`History must be unique and ordered by UTC timestamp and ID`; no incorrect report
was published. Store participant/equipment queries now use preserved timestamp
text when `created_at_us` is absent, through a deterministic connection-local
SQLite function. Indexed coarse window bounds remain unchanged; no production
row/schema rewrite is needed. Read-model iteration now explicitly closes its
source cursor on reducer failure before the database context exits.

Added mixed legacy/normalized same-second ordering and microsecond half-open
export regression. Focused participant read-model/metrics/store/migration tests:
**78 passed in 2.78s**. Retrying the same consistent snapshot and cutoff; import
continues independently. This fix neither changes ingestion nor assumes recovered
money/identity evidence for legacy rows.

### Verified partial publication - 2026-09-23 23:37 UTC

The fixed report succeeded from the consistent snapshot at common cutoff
**2026-09-23T23:24:19.555171+00:00**. Output:
`output/phase6-rollout/partial-preview/report/market_report.html`, five participant/
equipment CSVs, commodity CSVs/charts and `asset_inventory.json`. This snapshot
contains the original attempt's committed transaction state, before restarted
transaction collection; it is explicitly **partial**, not final rollout acceptance.

- **82** registered assets, **18** complete table crops including **15** participant
  tables (nine boards plus six coverage/explanation companions). Every table's
  cells/scroll bounds and PNG dimensions checked; participant text at least 14px.
  Visually inspected user-volume and country-coverage PNGs with complete footers.
- All nine boards exist. Each user/MU/country volume board has ten rows; all six
  profit/loss boards are honestly empty because settlement/basis evidence is
  unavailable. Unsupported P&L remains N/A, not zero.
- Equipment detail exports exactly match **5 sales / 6 skill rows** in the snapshot,
  including precise stat values. Shared CSV items and HTML item cards contain
  **24 commodity codes**; equipment assets are data CSVs only. No dedicated
  equipment presentation or pending listings were introduced.
- Common cutoff checked across ranking/breakdown/sale exports. Asset paths are
  unique, present and explicitly inventoried. Published HTML has static table
  images. Detailed results: `output/phase6-rollout/partial-preview/reconciliation.json`.
- Seven-day snapshot population: **862,463** stored transactions, **12,624**
  resolved active entities. Exact source money remains unavailable for **705,058**
  legacy transactions. Known global source value is **6,348,271.65000000002327971**,
  not complete seven-day market turnover.
- Unassigned sides: **1,410,133**, including **1,410,116** legacy missing-reference/
  missing-source-money sides and **17** unsupported-party sides. Known unassigned
  source value: **799.860999999999995**; the legacy missing-money value is unknown.
- Attributed source sale value **6,347,927.94800000002327971** is both uncosted and
  unknown-fee value; these flags overlap and must not be added. Matched source-sale
  value is zero under current evidence gates, not evidence of zero account profit.
  Both streams' history/window coverage remain partial/unverified.

Full import still running. This interim publication does not satisfy production
exhaustion, final catch-up or final publication acceptance.

An offline finalization helper was started hidden to wait for the import wrapper's
verified exhaustion/catch-up success. It then records offline status, creates a
consistent final snapshot, chooses one common UTC cutoff, renders and checks the
publication through the existing CLI/read models and verification helper. Its
status is `output/phase6-rollout/finalization.json`; logs are
`finalization-stdout.log` / `finalization-stderr.log`. It fails closed if import or
verification fails and leaves final visual review pending. Starting this waiting
helper is not evidence that any final publication exists or passed acceptance.

### Replay boundary crossed - 2026-09-23 23:59 UTC

Restart reached older, previously unenriched history on transaction request/page
**1,847**: 68 unchanged rows plus 32 enriched rows, oldest
`2026-09-22T12:46:25.001Z`. This required 1,847 transaction requests plus 26
current-state requests (**1,873 HTTP attempts, all 200**) before new older-history
enrichment resumed. The traversal included **27,268 newly visible transactions**
ahead of the old scan and replayed **157,400** prior normalized transactions.
The earlier 1,574-page position could not be used as a cursor/seek point.

At page **2,019**, restart totals were **27,268 inserted, 17,232 enriched,
157,400 unchanged, zero rejected**; oldest reached `2026-09-22T09:42:27.336Z`.
Saved `restart-20260923/replay-summary.json`. These are a progress checkpoint,
not final import totals; both-stream exhaustion remains pending.

### Available-history import completed - 2026-09-24

The hidden import wrapper completed at **00:52:22 UTC**, including normal catch-up.
Both full scans exhausted the actual available API history; none of the following
figures means all retained/game history or account inventory is known.

| Completed invocation | Trading | Equipment |
| --- | --- | --- |
| Full resync pages, including built-in catch-up | 3,857 | 706 |
| Inserted | 33,210 | 69,814 |
| Enriched | 194,942 | 0 |
| Unchanged observations | 157,458 | 759 |
| Rejected / errors | 0 / 0 | 0 / 0 |
| Oldest returned event | 2026-09-21T00:31:11.575Z | 2026-09-21T00:43:47.869Z |
| Normal catch-up pages | 5 | 2 |
| Normal catch-up inserted / unchanged | 391 / 109 | 12 / 188 |
| Latest catch-up event | 2026-09-24T00:48:44.093Z | 2026-09-24T00:48:48.672Z |
| Retained rows after catch-up | 9,264,797 | 69,831 |
| Normalization version 1 rows | 385,943 | 69,831 |
| Remaining legacy rows | 8,878,854 | 0 |

Metered HTTP totals: **4,589** for full resync and **33** for normal catch-up,
**4,622 total**, all HTTP 200; no transport retries needed in this completed run.
These include prices/configuration/orders. Full-resync transaction pages total
4,563; normal catch-up adds seven. Earlier interrupted requests and bounded smoke
probes are separately recorded above; do not add their replay rows as unique data.

Database total **9,334,628**, a net **221,666** new rows over pre-rollout. Across
attempts, **234,108** original commodity rows were enriched. Original commodity
history still reaches June 6. The API's September 21 floor prevented recovering
identities/source decimals on 8,878,854 retained legacy rows; no deletion or reset
was used to hide this. The observed floor is not an official retention-policy claim.
There are **69,831** equipment snapshots, **89,770** equipment skill rows,
**911,548** participant-side rows and **13,935** current-order entries. Original
aggregate history remains: order levels grew to 114,623, order observations to
1,817, price observations to 1,927. Integrity check is **ok**. Backup remains at
`data/warera_market.sqlite3.backup-20260923T175846714697Z`.

Full/catch-up status and counters are in `output/phase6-rollout/restart-20260923/`.
The first final publication completed at 00:59:43 UTC with common cutoff
**2026-09-24T00:52:32.648353+00:00**, at
`output/phase6-rollout/final-publication/report/`. Verification found 82 assets,
18 complete table crops (15 participant tables), all nine boards, 24 commodity
items/cards and exact equipment CSV matches (69,831 sales / 89,770 skill rows).
Six profit/loss boards remain empty; three volume boards have ten rows each.

At that cutoff: **959,457** stored window trades, **503,683** legacy rows with
unknown exact source money, **14,256** resolved entities. Known global source
value **18,825,748.6200000000862691** is partial, not complete window turnover.
Unassigned sides **1,007,433**: 1,007,366 missing-reference/missing-money sides and
67 unsupported-party sides. Known unassigned source value is
**4,418.4129999999999517**. Attributed source sales
**18,824,376.11800000008626910** are both uncosted and unknown-fee value; flags
overlap. Matched source-sale value is zero under the evidence gates, while net/
gross account P&L remains unavailable. Equipment lineage, historical settlement/
fees, nonmarket acquisitions/dispositions and older identities remain unknown.

### Authorized durable resume and unattended handoff - September 24

The user changed the earlier requirement: "make it so that you don't have to
actively monitor, and that the process can be resumed if killed" and requested
a next-agent monitoring prompt. This explicitly authorizes cursor persistence.
The previous import had already completed; it was not stopped or needlessly
replayed to install the new capability.

- Added opt-in `--resume-market` for all-history resync. Scalar metadata in the
  existing v5 `schema_meta` stores stream, fixed anchor, history phase, opaque next
  cursor, prior timestamp bound, page count, normalization version and page size.
  Page rows/children/progress/coverage and checkpoint share one transaction.
  Default scans retain original in-memory behavior. No raw response JSON is stored.
- Restart validates the checkpoint against progress, resumes the next page and
  skips completed historical streams. Catch-up replays its head safely. Cursor
  expiry/invalidity is an explicit failure retaining the checkpoint, never silent
  exhaustion or automatic head fallback. Status redacts token values. No long-term
  cursor-lifetime guarantee is made.
- `scripts/market_rollout.py` saves stage, snapshot, cutoff, PID and terminal
  result, with a per-database OS lock preventing duplicate runner writers. It runs
  full import, normal catch-up, snapshot, report and verification without active
  agent monitoring. Re-running the same job after process death resumes its stage;
  it is not a Windows service or automatic reboot restarter. Failed jobs stop for
  inspection; transport attempts/results are counted without credentials/URLs.
- `scripts/verify_market_publication.py` checks stable assets, nine boards, table
  dimensions/bounds/fonts, common cutoff, all equipment/stat rows and commodity-only
  cards against read models. No SQL, endpoints or accounting logic was copied into
  the runner. The offline fixture runner completed end to end; invoking its completed
  job again correctly performed no work.
- Four bounded live HTTP 200 requests, **two pages per stream**, tested immediate
  continuation across separate processes that hard-exited after each atomic page
  commit. Each temporary stream database retained **200 distinct rows** after page
  two. Evidence: `output/phase6-rollout/live-resume-rehearsal/`. Production was not
  mutated by this cursor probe; no pending equipment listings were requested.
- Added hard-process-exit, checkpoint/page rollback, completed-stream and catch-up
  restart, invalid-cursor/no-fallback, argument validation and OS-lock-release tests.
  Final relevant suite: **148 passed in 4.06s**. Initial runner-lock test killed the
  Windows venv launcher rather than its actual Python child; corrected the fixture
  to hard-exit the child and verify lock release. No application-lock defect remained.
- A later installed `.exe` migration-command retest was blocked by Windows
  application control (WinError 4551), including outside the sandbox. An escalated
  default temp directory also had access restrictions; a fresh workspace temp path
  confirmed the executable policy block. The initial full suite/production migration
  had already passed. This later launcher retest is **blocked, not passed**. No OS
  policy was disabled. A `.ps1` launcher was also blocked by host execution policy
  and removed; direct `Start-Process -WindowStyle Hidden` of the existing Python
  runner works and is the documented command.

### Coverage correction and current publication job

Production exhaustion exposed overbroad metadata: old code marked an exhausted
stream from 1970 even when the API returned only a September 21 floor. Fixed future
ingestion to start coverage at the oldest returned event; an empty exhausted stream
records exhaustion without inventing a historical interval. A tested store method
corrected the two production epoch-wide intervals and corresponding newly generated
commodity coverage metadata, preserving all source data/aggregates. Evidence:
`output/phase6-rollout/coverage-floor-repair.json` and `post-repair-status.json`.
The existing final snapshot preserves the pre-correction state as evidence.

Participant coverage now retains historical exhaustion observations after a later
incremental scan resets the latest-invocation flag. Neither that observation nor
the corrected interval can establish the missing part of the seven-day window.

Launched a **publication-only** unattended job with the completed-import evidence,
corrected metadata and the same common cutoff:
`output/phase6-rollout/resumable-publication/`. Its `job.json` is authoritative for
current stage/result, with `stdout.log`/`stderr.log`. No second full import was run.
Restarted only its identified worker after the last coverage-label fix; it resumed
the saved `report` stage, same snapshot and cutoff. Last observed worker PID 15684;
verify the current state/process rather than trusting an old PID.

Final refreshed export verification/visual review is delegated by the user's new
instruction, not claimed from launch. The ready-to-use monitoring/resume prompt is
[market-participants-next-agent-prompt.md](market-participants-next-agent-prompt.md).
Backups, original data, older reports/snapshots and unrelated worktree edits remain.

Final checkpoint consistency refinement also verifies the saved normalization
version and previous timestamp against committed progress. The focused resume/
runner tests passed again: **12 passed in 1.99s**. The Python module CLI help
confirms `--resume-market`; scoped `git diff --check` passed (line-ending notices
only). The refreshed production report rendered all **82** assets at 10:41 UTC
and advanced autonomously to stage **verify**; its terminal verification and
visual review remain for the handoff agent. No active polling is required to run
the remaining stages.

The unattended job subsequently finished at **10:42:57 UTC**:
`stage=done`, `status=complete_pending_visual_review`, no error. Refreshed output
is `output/phase6-rollout/resumable-publication/report/market_report.html`.
Final reconciliation confirms all nine boards, 82 assets, 18 complete table crops,
15 participant tables, 24 commodity items/cards and 69,831 equipment sale rows /
89,770 exact skill rows. Both streams now correctly show historical API exhaustion
while seven-day window coverage remains partial; coverage floors are the actual
September 21 timestamps. The next-agent prompt records this terminal result and
reserves final visual review/closeout, not another import or continuous monitoring.


### 2026-09-24: historical completeness claim withdrawn; documentation handoff

The user challenged the reported September 21 history floor and reported a prior
successful 90-day download. Comparing the former downloader with current code
identified the omitted per-commodity `itemCode` filter. A bounded read-only probe
using that filter reached September 20 on page 29; a second bounded probe reached
`2026-09-16T21:31:53.370Z` on page 71 with 100 rows and a nonempty next cursor.
The second probe had finished when the user requested stopping. No diagnostic
transaction was written to production. Logs are in
`output/phase6-rollout/filtered-history-evidence.log`.

This disproves the broad inference that the completed global scan retrieved all
available history. It does not establish a parser/mapping bug or a specific
server retention/default window. The user's protective-default hypothesis is
plausible and unverified; 90-day reach and equipment historical scopes still need
verification. The global job did finish, but historical rollout acceptance is
reopened. Prior visual-only/completion handoffs are superseded accordingly.

At the user's request, documented exact commands/API inputs, evidence, unresolved
questions, investigation prerequisites, scoped checkpoint/coverage requirements,
validation and corrected rollout acceptance in
[market-history-query-scope-spec.md](market-history-query-scope-spec.md). Updated
contracts, plan, schema/data-model notes, README and next-agent prompt. This turn
changed documentation only: no importer change, new network probe, database
mutation or job launch. No tests rerun for these documentation changes.
