# Market participants, equipment, and realized trading results

Status: phases 1-3 normalized storage, migration, global ingestion, full/recent
resync, offline status, retries and independent retention implemented, 2026-09-23; see [schema v5](market-schema-v5.md),
[verified contracts](market-api-contracts.md) and the
[implementation log](market-participants-implementation-log.md).
Global two-stream collection and resync/status commands are implemented and tested
offline. Participant reports and the phase 6 operational import remain future work. The offline `--migrate-db` command is implemented and tested on
temporary fixtures only. Companion:
[implementation prompts](market-participants-implementation-prompts.md).

This plan supersedes implementation recommendations in the
[field-retention audit](api-field-retention-audit.md) where they differ. The audit
remains the record of observed response fields.

## 1. Agreed outcome

- Collect all available commodity trades (`trading`) and equipment sales
  (`itemMarket`), independent of the current price list or a hardcoded item list.
- Preserve order and transaction business fields, all buyer/seller source IDs,
  and equipment attributes at the time of each sale. Exclude `__v`, temporary
  `nextCursor`, and raw dictionaries/JSON. Equivalent stored facts need not be
  duplicated merely because they came from request context instead of the body.
- Preserve individual commodity orders alongside the existing price aggregates.
- Pending equipment listings are outside the current scope. Equipment work covers
  completed sales and their stats through `transaction.getPaginatedTransactions`.
- Migrate the existing database; enrich old rows and import missing rows instead
  of deleting history. Provide one full-resync command plus normal incremental
  sync, explicit status, deterministic replay, and operator-visible coverage.
- Add nine 7-day leaderboards: three rankings each for users, military units,
  and countries: realized losses, realized profits, and traded monetary volume.
- Show what each participant mostly bought and sold, including equipment stats.
- The user chose **realized trading profit/loss where acquisition cost is known**,
  not sales minus purchases. Incomplete basis must stay visibly incomplete.
- Deliver publication tables as complete, legible static PNGs and machine-readable
  exports. Keep existing commodity reports and signals working.
- Keep equipment out of the shared report item list, item cards, item tables,
  highlights, and commodity charts. Collect and store equipment now; defer its
  dedicated visual presentation until a display design is agreed. This does not
  remove equipment trades from the separately requested participant activity
  analysis or the underlying data exports.

"All" means the two market transaction types, including newly introduced and
historical/delisted item codes. It does not expand collection to wages, donations,
crafting, or dismantling. Full configuration archiving is outside this change.

## 2. Evidence and unresolved API contracts

Phase 0 evidence changes the following decisions: frontend account rendering
supports single-institution attribution while retaining the user actor, but no
priority for conflicting institutional references. Party side field names are
confirmed by frontend usage (not live market samples). Both stream limits are
documented as 100 and work live; the type-array filter also works, while separate
streams remain preferable. Equipment purchase UI includes current viewer-country
market tax. Historical money gross/net meaning, fee settlement/rounding and
equipment identity continuity remain unavailable. Until monetary semantics are
verified, publish **source-money turnover**, not verified gross turnover, and
leave gross/net P&L unavailable where unsupported. This qualifies the target
formulas and export labels below; it must not block collection. Full evidence
and fallback rules are in the phase 0 contracts.

Subsequent user clarification explicitly confirms commodity MU/country source
IDs as the economic account, with the player retained as actor. Institutional
equipment access is still unverified; do not impose a user-only equipment schema
or discard institutional references if returned.

Resolve the buyer and seller separately. Personal-to-MU, MU-to-country and other
mixed-account trades are valid; conflicting references mean multiple institution
kinds on the same side, never different kinds across buyer and seller. An
unresolved side does not exclude its otherwise resolved counterparty.

### Verified

| Capability | Finding |
| --- | --- |
| Completed commodity and equipment trades | Same `transaction.getPaginatedTransactions` endpoint; type filter distinguishes them |
| Global commodity page | Successfully requested with `transactionType=trading`, no `itemCode` |
| Equipment-sale page | Successfully requested with `transactionType=itemMarket`, no `itemCode` |
| Equipment sale details | `item` includes code, instance ID, skills, state/maxState, quantity, lastAcquisitionAt; `type` may be absent |
| Buyer/seller IDs | User IDs coexist with country or MU IDs in some transactions; retain all |
| Current commodity orders | `tradingOrder.getTopOrders`; limited visible orders, not guaranteed complete market depth |
| Response specification | Official Swagger gives no response schema for the four existing sync endpoints; community typings omit live fields |

Evidence links: [official API docs](https://api2.warera.io/docs/),
[community response types](https://github.com/WarEraProjects/TRPC/blob/main/src/api/Responses.d.ts),
[public equipment-market frontend inspected](https://app.warera.io/_next/static/chunks/pages/market/equipments-2d19f68208077040.js).
Public chunk URLs are versioned and may change; discover the current manifest if
the cited URL is unavailable. Never use an API key on unrelated documentation hosts.

### Phase 0 must resolve or explicitly mark unavailable

1. Whether country/MU side IDs identify the economic account, while user IDs
   identify the person acting for it. Verify from first-party UI behavior or
   authoritative documentation, with live examples; do not equate them with a
   user's membership. Determine how any simultaneous country and MU references
   are interpreted. Do not choose an arbitrary priority.
2. Monetary semantics of `money` for both types: gross consideration versus net
   receipt; whether fees are included; fee payer, amount, rounding, and whether
   a rule is historically applicable. Current report fee assumptions are not
   evidence of historical equipment fees.
3. Whether equipment `item._id` is stable through transfers and resale. Two items
   with equal code/stats are not necessarily the same item; IDs near transaction
   creation time deserve investigation. Never match by similar appearance alone.
4. Exact timestamp precision and pagination ordering; page maximum; supported
   type-array filter. Prefer two known-working type streams unless a combined
   stream is verified. No claim of a date filter or direct historical seek until
   one is confirmed.
5. Optional source fields, including any party references, and supported name
   lookup methods. Source IDs suffice if names cannot be retrieved.

Use bounded reads and sanitized representative fixtures; not bulk history for
contract discovery. Pending equipment listings need no investigation or importer
in this implementation.

## 3. Meaning of the nine rankings

### Window and population

Use one timezone-aware UTC `as_of` for all calculations. Window is the half-open
interval `[as_of - 7 days, as_of)`. Include sales completed in that window for
realized P&L and buys/sells completed in that window for volume. Cost matching
must consume chronological history **before** the window as well.

Compute rankings over all eligible entities, not a volume-prefiltered top ten.
The three lists per entity type are independent; every row still shows volume.
This resolves the wording "top 10 volume with most net loss/win" as three
separate top-ten lists. Do not suppress a valid large loss merely because its
participant is outside the ten largest traders by volume.

### Attribution

An economic side contributes once: to a user, MU, or country according to the
verified source-account semantics. A government official executing a country
trade must not also receive that country's P&L in their personal ranking.
Retain the actor's user ID separately. These are institutional account rankings,
not aggregates of all citizens or MU members; current membership never rewrites
historical transactions. Keep unresolved/unsupported owner kinds out of rankings
and report their value/count separately. Parties, if encountered, retain their
source data but have no requested leaderboard.

Keep both sides of a same-account trade as source facts, but exclude it from
ranked turnover and P&L to avoid artificial volume and inventory churn. Do not
infer common ownership between distinct IDs. Transfer between different entity
kinds is a trade if the source says so, not an implicit inventory transfer.

### Profit and basis

For matched sold quantities:

```text
net_realized_pnl = sale_proceeds_after_verified_sell_fees
                 - allocated_purchase_cost_including_verified_buy_fees
turnover_7d = gross_buy_consideration_7d + gross_sell_consideration_7d
```

Use decimal arithmetic with documented source precision and rounding. Do not
round each lot prematurely or assume two decimal places. Distinguish the gross
consideration, known fees, and net proceeds; do not charge a fee twice if `money`
already includes it. Store observed values separately from calculations. An
unknown fee is not a zero fee. If only gross P&L is supportable, expose it as
gross and mark net P&L unavailable for the affected lots. Do not rank a guessed
net amount. Verify fee-free commodity semantics during the contract phase.

Commodity units use FIFO purchase lots per economic owner and item code. Apply
every earlier sale to those lots, including sales before the report window, so
the same cost is not reused. Retain unconsumed lots without recognizing any P&L.
A purchase, however large, is not itself a realized loss. Partial lot fills
allocate cost proportionally. Unmatched sold quantities have unknown basis,
not zero basis, and cannot borrow costs from future purchases.

FIFO is an accounting convention for observed market acquisitions. Nonmarket
production, gifts, consumption, or off-market transfers are not tracked here.
Therefore label results **observed market FIFO realized P&L on matched sales**;
do not claim complete personal/account profit or actual complete inventory.
If evidence invalidates a lot's availability or account lineage, mark that
sale uncosted. Full API pagination does not prove full inventory provenance.

Equipment sales use specific-item matching only when stable instance lineage
has been verified, the prior purchase belongs to the same economic account,
and no known intervening disposition invalidates it. Store each sale's own
stats and condition even for the same instance. Never FIFO-match two equipment
pieces merely by code or matching stats. If lineage is unavailable, equipment
still contributes to volume and trade explanations; its basis/P&L is unavailable.
Likewise do not fabricate acquisition costs for found or crafted equipment.

Per entity expose matched quantities/value, unknown-basis sales value, unknown-fee
value, unresolved-attribution value, and source-history coverage. Different flags
can overlap; do not blindly add them together. Define matched-sale-value coverage
as matched gross sale consideration / eligible attributed gross sale consideration
in the window; zero denominator is N/A, not 100%. A participant with some known
and some unknown costs has a partial matched-sale result, not a total net result.
Label partial rows individually. Use titles saying results are on matched sales.

### Ordering and explanations

| List, repeated for users/MUs/countries | Selection and deterministic sorting |
| --- | --- |
| Largest realized losses | Known matched net P&L < 0, ascending P&L; tie: turnover descending, entity ID ascending |
| Largest realized profits | Known matched net P&L > 0, descending P&L; tie: turnover descending, entity ID ascending |
| Largest traded volume | Turnover descending; tie: entity ID ascending; P&L may be unavailable |

Return up to ten entities; do not pad or rank no-sales/no-basis accounts as zero
profit. Profits/losses use absolute money, not return percentage. Show gross money
spent, gross money sold, turnover, matched net P&L, coverage, and top purchases /
sales. Unit volume can be a per-item detail but is not comparable across items.
Sum entity turnover across all sides counts a market trade twice; global market
volume must count each transaction once, and cross-kind totals need that warning.

For each ranked entity, select its top three buy categories and top three sell
categories by gross money during the window, using all attributable trades even
when basis is unknown. Include money, quantity, share of that side's total value,
and an optional final "other" amount. No unsupported causal claims about motives.
An equipment category consists of code plus the full sorted skill/value vector
and exact condition state/maxState (version the signature). This grouping is for
descriptions/comparisons, never proof of instance identity. Do not merge missing
stats/condition with explicit zero values. Long stat lists wrap and remain fully
available in the detailed export; small categories retain their trade count.

## 4. Database migration design

The phase 1 branch inspection found `LATEST_SCHEMA_VERSION` 4. The additive
migration is now v5; actual columns and constraints are in [schema v5](market-schema-v5.md). Only
`market_store.py` imports SQLite or executes schema/data SQL.

### Additive schema contract

Existing `transactions` stays keyed by upstream transaction ID. Preserve its
current columns for compatibility. Add source `offer_created_at`, `updated_at`,
millisecond/microsecond ordering precision derived without losing source text,
`first_fetched_at`, `last_fetched_at`, and `normalization_version` (legacy rows 0).
Add canonical decimal text values for source money/quantity where needed for
costing; legacy REAL values alone must not be labeled newly verified. Keep
existing float projections for commodity calculations. Missing versus explicit
null fields must be distinguishable in parsed updates; omission is not deletion.

| New table | Key / important columns | Purpose |
| --- | --- | --- |
| `transaction_participants` | PK `(transaction_id, side)`; side buy/sell; `user_id`, `mu_id`, `country_id`, optional verified `party_id`; FK transaction cascade | Preserve all source references; no inferred account priority in storage |
| `transaction_equipment` | PK/FK transaction ID; instance ID, equipment code, optional type, state, max_state, item_quantity, last_acquisition_at | Per-sale equipment snapshot, not a mutable latest-state row |
| `transaction_equipment_stats` | PK `(transaction_id, skill_code)`; numeric value as decimal text; FK equipment snapshot cascade | Open-ended skills without fixed columns |
| `order_book_entries` | PK `(observation_id, side, entry_position)`; upstream order ID when supplied, item code, type, user/MU/country/other verified references, exact price/quantity, offer_at; FK observation cascade | Individual fetched orders including zero placeholders; existing aggregate levels remain |
| `market_entities` | PK `(entity_kind, entity_id)`; name, name_observed_at, lookup_status | Optional cached names; source identity works without lookups |
| `transaction_extra_fields` | PK `(transaction_id, field_path)`; value_type and scalar text/null value; FK transaction cascade | Preserve newly encountered scalar facts until semantic mapping is added |
| `order_entry_extra_fields` | Composite FK to order entry; field_path, value_type, scalar value | Same for orders |
| `market_ingestion_state` | stream/type, normalization_version, scan mode, scan anchor, committed oldest/newest timestamp+ID, attempts, status, counts, last error | Progress by stream; no persisted cursor |
| `market_enrichment_coverage` | stream, normalization_version, interval start/end, source, completion reason, observed_at | Distinguish full enrichment from legacy price coverage |

The extra-field tables store normalized scalar attributes with a documented
escaped path and array indices, not JSON or serialized dictionaries. Known
fields have typed domain columns/tables; do not duplicate them in extensions.
Do not store object/list container rows solely to reconstruct raw JSON. Preserve
unknown scalar fields with a diagnostic, but do not silently use them as economic
facts or mark a required unresolved semantic field as fully understood.

Nested `__v` metadata is excluded too; exclude `nextCursor` as the response
pagination envelope, not arbitrary unrelated business fields with a similar name.
Source parsing and extension extraction belong at the API boundary. Add a small
pure domain-types module if shared models are needed, with no HTTP/DB dependency.

Add indexes for `(transaction_type, created_at_epoch, id)`, participant source
IDs plus transaction ID, equipment instance plus transaction ID, and observation
plus order ID. Check query plans on representative history before adding more
indexes; do not store precomputed leaderboards as source facts.

### Safety, enrichment, and compatibility

1. Backup via `MarketStore` using SQLite's consistent backup facility; don't
   copy only the main file while a WAL writer is active. Print the backup path.
2. Apply schema changes and version markers atomically. Avoid `executescript`
   implicit-commit behavior defeating migration rollback. Verify a failed v5
   leaves a usable v4 database and a retry succeeds. No destructive down migration.
3. Legacy participant/equipment rows remain absent/unknown, not guessed. Preserve
   prices, transactions, historical aggregate snapshots, sync metadata, and their
   original timestamps. Do not manufacture individual orders from old aggregates.
4. One page's normalized rows, children, and progress commit together. Enrich
   existing IDs; distinguish inserted/enriched/unchanged/rejected counts. Do not
   erase optional facts because a later response omits them. A newer authoritative
   explicit correction needs tested semantics; older payloads must not overwrite
   newer facts. Idempotent replay creates no duplicate equipment stats/orders.
5. Explicitly filter legacy commodity APIs/read models to `trading`. Currently
   `item_codes()` unions all stored transactions and history methods lack a type
   filter: equipment would otherwise leak into commodity fair values and WE24.
   Deal explicitly with legacy null transaction types; classify only where their
   provenance proves commodity ingestion, otherwise keep them unclassified.
   Give `MarketStore.item_codes` an explicit market-type selector (or a dedicated
   commodity discovery method) and have `load_market_rows` request commodities.
   The transaction branch uses `transaction_type = 'trading'`; preserve existing
   commodity price/order-only rows. Apply the same selection to its history
   queries. SQL stays in MarketStore, with market_data choosing the report scope.
   Do not filter only the final HTML or remove equipment from global sync
   discovery/storage. Use transaction type, not an equipment-code denylist or
   optional nested `item.type`. Equipment data remains available through explicit
   separate queries. This is a small query-boundary change, not a database split.
6. Keep catalog/discovery independent from price-map keys. Unknown historical
   codes can still be stored/reported by their source code. No need for a full
   configuration archive just to retain market facts.
7. Add independently configurable transaction retention (`"all"` or positive
   days) and keep order/price retention independently configurable. The proposed
   project setting is `transaction_retention_days = "all"` so a 120-day
   housekeeping run cannot undo this requested full-history import. Preserve old
   configuration behavior when the new setting is absent; change the project
   TOML explicitly. Pruning child rows/coverage must follow any actual deletion.

## 5. Sync strategy and proposed commands

The offline `--migrate-db`, normal two-stream sync, both resync scopes and
`--market-sync-status` commands below are implemented and tested offline.
Ranking cutoff options remain **proposed CLI contracts**.

```powershell
# Offline additive migration and consistent backup (new command).
.venv\Scripts\warera-marketguide --migrate-db --market-db data/warera_market.sqlite3

# Early value: enrich/import seven days for both market types (new options).
# Volume and trade explanations become useful; P&L coverage may still be low.
.venv\Scripts\warera-marketguide --sync --resync-market --history-scope 7d --market-db data/warera_market.sqlite3

# All available history for both market types; preserve the database.
.venv\Scripts\warera-marketguide --sync --resync-market --history-scope all --market-db data/warera_market.sqlite3

# Normal ongoing sync, both types by default after implementation.
.venv\Scripts\warera-marketguide --sync --market-db data/warera_market.sqlite3

# Offline transaction coverage/progress (new command).
.venv\Scripts\warera-marketguide --market-sync-status --market-db data/warera_market.sqlite3

# Offline report with a reproducible ranking cutoff (new --as-of option).
.venv\Scripts\warera-marketguide --from-db --as-of 2026-09-22T20:00:00Z --market-db data/warera_market.sqlite3 --output output
```

`--resync-market` means enrichment/reconciliation, not truncation. `--history-scope
all` traverses until exhaustion and is not limited by `--lookback-days`, report
windows, old high-water marks, duplicates, or default history caps. Validate
conflicting scope/cap flags before any API request. Explicit item exclusions with
an all-market command must be rejected or clearly mark the job scoped/partial;
do not silently claim all coverage. Existing backfill options retain documented
compatibility or issue a clear deprecation path.

Prefer two global paginated streams (trading and itemMarket), maximum documented
page size, rather than a separate historical walk for every item. This includes
codes missing from current prices and avoids unnecessary empty first pages. It
does not eliminate the roughly one response per page of existing history needed
to recover fields never stored. Benchmark requests/pages/rows, not a promise of
"instant" enrichment. Do not download unrelated transaction types.

Refactor one shared pagination engine for incremental, scoped enrichment, and
full reconciliation. Apply transport pacing and bounded retries for transient
errors; permanent 403 is not retried as a transient error. No unbounded parallel
requests. Capture orders separately from each transaction page so backfills do
not repeatedly re-fetch unchanged current-state endpoints.

Normal sync stops only after a verified contiguous overlap with a fully enriched
version of the same stream; one arbitrary duplicate is insufficient. Test page
boundaries with equal timestamps; retain full precision and IDs. Fix one anchor
per scan. New trades arriving during a long scan are handled by a catch-up pass
and do not move its history boundary. Never treat an API error as exhaustion.

### Restart limits when cursors are not saved

Keep `nextCursor` only in memory as requested. Durable timestamp/ID markers
record what is committed; **they do not prove the API supports seeking there**.
With only opaque cursors, an interrupted process must replay pagination from
the beginning to reach old history. Avoid rewriting unchanged rows, but acknowledge
the repeated requests. Never forge/decode/reconstruct cursors from IDs. A future
supported date/ID filter can improve this after verification. True efficient
cross-process deep resume would otherwise require revisiting the no-cursor-storage
decision; it is not promised by this plan.

The status command reports elapsed time, pages/rows, insertion/enrichment counts,
oldest/newest committed event, per-stream errors and gaps, normalization coverage,
and whether exhaustion was observed. Elapsed timing is the measured transaction
scan plus catch-up duration (excluding current-state reads); interrupted running
scans may have no completed timing. Counts include repeated observations during
head replay/catch-up. Rejected counts cover known malformed rows, not unreadable
transport pages. Exact replay does not update transaction fetch timestamps.
The initial scan remains running until catch-up finishes, and exhaustion can
coexist with a later catch-up error. Status keeps latest-invocation exhaustion
separate from retained coverage, which optional pruning can shrink.
"API history exhausted" is distinct from
"all game history known". Rate and ETA estimates use measured progress and remain
unknown if remaining page count is unavailable. Rerunning a completed resync
command intentionally rechecks history; normal daily collection uses `--sync`.

## 6. Read models, costing, and report integration

Responsibilities remain those in AGENTS.md. `market_store.py` provides bounded,
ordered source queries and backup/migration operations; `market_data.py` shapes
domain inputs and joins cached display names; `metrics.py` owns attribution
rules, inventory matching, P&L, turnover, ranking, and description selection.
`cli.py` wires those calls; `report.py` only renders/export supplied results.
No network calls during `--from-db` report generation, including name lookups.

Read candidate entities active in the window, then retrieve their earlier
market histories in batches/streams and stable chronological order. Do not run
one full-history query per row, load all source rows into a giant DataFrame, or
discard historical dispositions needed for basis. A pure incremental FIFO
reducer maintains only open lots and window contributions. Add query-plan and
synthetic-volume checks where they establish useful bounds; avoid speculative
performance targets without a baseline. Source amount precision, ordering, and
entity keys must be identical between stream and batch implementations.

Return one structured result with common as_of, coverage, sources, per-entity
aggregates, nine rankings, and detailed buy/sell categories. Distinguish source
ingestion completeness from cost/fee/attribution completeness. A current-state
commodity-order failure never makes completed-sale volume unavailable.

Add the report section through `generate_html_report` / `write_outputs`; retain
commodity and WE24 outputs. Keep equipment detail exports keyed by transaction
and full stats/condition. Do not add equipment item cards, tables, galleries,
highlights, or a dedicated equipment summary to the published report yet: their
display design is deferred. Participant boards remain separate from the shared
item list and can include equipment activity as already specified. Do not derive
executable equipment asks from old completed sales.

For each leaderboard use a main ten-row table with rank, entity, P&L, volume,
basis coverage, and wrapped mostly-bought/mostly-sold descriptions. Put scope,
method, dates, units, and partial-data wording in a table-specific footer so they
remain in its PNG. Larger explanations may be in a companion table per entity
kind, and full details in CSV; never require hover/scroll to read essential facts.

The current `annotate_table` adds a commodity quote/fee footer to generic tables;
give participant/equipment tables dedicated semantics so that footer is not
attached to them. Extend `export_report_assets` using actual table-element
captures, stable asset identifiers, and its geometry checks. Published HTML
contains PNGs, not interactive tables. No forced narrow widths, clipping,
horizontal scrolling, or reduced font sizes to fit long equipment descriptions.

Proposed additional exports:

- `participant_rankings_7d.csv`: entity kind/ID/name, ranking kind/rank, totals,
  matched P&L, gross P&L if separately available, basis/fee/source coverage, as_of.
- `participant_trade_breakdown_7d.csv`: entity, buy/sell, item, market type, stat
  signature/condition, money/quantity, share, trade count; named stat columns or
  a companion normalized stats CSV, no hidden raw JSON blobs.
- `equipment_sales_7d.csv` and `equipment_sale_stats_7d.csv`: transaction facts
  and one row per stat respectively, enabling exact equipment comparisons.
- `participant_rankings_7d/` table PNGs, added to `asset_inventory.json` through
  the existing explicit asset/export pipeline.

Cached names are best effort and dated; ID fallback remains readable. Escape
all externally sourced names in HTML and protect spreadsheet exports from
formula execution without changing preserved source data in the database.

## 7. Phased value delivery and acceptance

| Phase | Deliverable and value | Acceptance gate |
| --- | --- | --- |
| 0 | Verified API/domain contracts and small representative fixtures | Attribution, money/fees, item lineage, and pagination either verified or explicitly unavailable |
| 1 | Additive migration, normalized models, enrichment, consistent backup | v1/v4 upgrade and v5 reopen preserve old history; failure rollback; source fields retained and excluded metadata absent |
| 2 | Complete-field commodity/equipment ingestion and current commodity order snapshots | First bounded 7-day import yields usable volume/stats; equipment never enters commodity calculations; names optional |
| 3 | Full-resync/status commands, pacing, honest restart behavior, transaction retention | All-history traversal, same-second boundaries, overlap, replay, errors, and retention tested offline |
| 4 | Realized matched-sale P&L, attribution, rankings, and explanations | Hand-calculated FIFO/equipment/fee cases pass; unresolved basis excluded rather than fabricated |
| 5 | Nine PNG participant leaderboards and detail CSVs; existing item displays remain commodity-only | Deterministic output, complete table crops, readable long names/stats, truthful partial coverage; existing outputs regressions pass |
| 6 | End-to-end rehearsal and operational rollout | Backup/restore demonstrated, bounded live smoke followed by requested full import, coverage reconciled, final report generated |
| Deferred display | Dedicated equipment cards/tables/summary | Agree a display design before adding equipment to any item presentation |

Phases can deliver recent volume and equipment descriptions before old history
finishes. Do not defer all value until full-history completion. P&L improves only
as actual acquisition evidence arrives; do not backfill invented cost values.
The separate [prompt library](market-participants-implementation-prompts.md)
contains complete instructions for each phase, tests, and handoff requirements.

## 8. Required test matrix

Use the existing `.venv`; fake API fixtures in tests must not use `.env` or make
network calls. New test filenames below are implementation targets.

| Area | Required cases |
| --- | --- |
| API parsing | Both market types; all six confirmed participant references; absent optional fields vs explicit null; equipment missing type; arbitrary skills; source precision; unknown scalar preservation; excluded metadata; no silent field loss |
| Migration/store | Legacy v1/v4 fixtures; all previous rows unchanged; version atomicity and rollback; WAL-consistent backup/restore; child FK constraints; idempotent enrichment; omitted fields don't erase data; older updates can't replace newer facts |
| Orders | Same-price distinct orders retained but aggregates unchanged; zero placeholder retained outside executable depth; repeated snapshots distinct; old aggregate snapshots not fabricated into entries |
| Sync | Global types include non-price-list codes; empty stream; duplicates; same-timestamp multi-page boundary; interrupted page transaction; retry/cap/error; no equipment-listing requests; new arrivals; all-history ignores 7-day report default; two independent stream states |
| Restart | No cursor anywhere in persisted schema/state/log artifacts; restart replays safely; no unsupported historical seek; markers do not imply coverage beyond committed pages |
| Retention | Full-history transaction setting survives housekeeping; observation pruning still works; optional transaction pruning cascades and shrinks coverage accurately |
| Attribution | Personal user; user acting for MU/country; conflict/unknown IDs; no double credit; no membership inference; account self-trade excluded; distinct accounts still separate |
| FIFO | Buys before window, partial/multiple lots, intervening prior sales, unsold purchases, unmatched sales, no future matching, deterministic equal timestamps, known-zero vs unknown cost, stable decimal allocations |
| Equipment | Same code/different stats; same stats/different instances; verified resale; changed condition; unstable/unverified ID -> unknown basis; absent stats vs zero; equipment with no fees evidence -> unknown net |
| Ranking | Three lists x three entity kinds, negative/positive/zero filtering, no cost basis not treated as zero, deterministic ties, fewer than ten, partially costed rows labelled, volume-only equipment valid |
| Reporting | Exact UTC boundaries, missing names, HTML escaping/CSV formula protection, top-three buy/sell shares, long skill vectors, PNG element dimensions/overflow, table-specific footers, offline-only rendering |
| Regression | Existing commodity guide, charts, WE24, CSV mode, CLI and architecture constraints; mixed trading/itemMarket DB yields commodity-only shared item list/cards/tables while equipment rows/stats remain stored and queryable |

Hand-calculation fixture: U buys 10 steel for 100 before the window, sells 4 for
60 before the window, buys 2 for 24 in the window, and sells 8 for 120 in the
window. Fee-free FIFO consumes cost 60 + 24 = 84; realized window profit = 36,
turnover = 24 + 120 = 144. The earlier sale must consume four units but its profit
must not appear in the window. A subsequent sale with no remaining acquisition
lot has unknown basis and must not add fictitious profit. Another entity buying
unsold inventory for 1,000 has turnover 1,000 and no realized-loss entry.

After focused phase tests pass, run the full suite once for integration; rerun
broader tests only after changes/failures justify it. PNG work requires one real
browser export inspection as well as geometry assertions. Do not run a bulk
network download merely to test parser or P&L correctness.

## 9. Rollout and completion criteria

Implementation is complete when the new command can migrate safely, preserve
facts, enrich historical rows, import equipment trades, calculate only supported
realized outcomes, and publish the nine boards plus explanations/exports with
tests passing. Operational history import is a separate measured completion:
both market streams reach reported available-history exhaustion, a catch-up
sync succeeds, and the report shows actual basis/fee/attribution coverage.

Do not declare historical profit coverage complete merely because the API has
no more pages. Publish remaining unmatched value and oldest recovered dates.
Do not delete the old database or erase its existing snapshots as cleanup.
Completed equipment sales and stats are in scope; pending equipment listings
are excluded from implementation and completion criteria.
