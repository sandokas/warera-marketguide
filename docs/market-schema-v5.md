# Market schema v5

Implemented in phase 1, 2026-09-23. The inspected `master` branch had v4 as its
latest schema. Only `market_store.py` imports SQLite or executes SQL.
`market_models.py` contains pure normalized models; `warera_api.py` maps source
fields and extracts unknown scalar leaves. No production database was migrated.

## Migration and compatibility

`initialize()` executes `BEGIN IMMEDIATE`, all pending migrations, the
`schema_meta.version` write and `PRAGMA user_version` write in one transaction.
Static v1-v3 DDL now uses individual `execute` statements rather than
`executescript` (which implicitly commits an existing transaction). Any exception
rolls back DDL, row changes, and both markers. Versions beyond supported v5 and
conflicting nonzero markers are rejected; legacy zero `user_version` is accepted.

Migration leaves every pre-existing column value unchanged. New first/last fetch
columns are initialized from `fetched_at`. Legacy normalization remains version
0/status `legacy`; exact decimals, participant identities, equipment snapshots,
individual orders, and enrichment coverage are **not** reconstructed. Missing
identities require a later source replay. Historical quotes, levels, sync state,
production points, and old `transaction_coverage` remain intact.

The executable offline command is:

```powershell
.venv\Scripts\warera-marketguide --migrate-db --market-db PATH_TO_DATABASE
```

It requires an existing database, creates a uniquely named sibling
`.backup-YYYYMMDDTHHMMSSffffffZ` using SQLite's online backup API, migrates, prints
the backup path and resulting schema, then exits before configuration/report/API
work. It rejects combinations with other input/database actions. On migration
failure the error identifies the retained backup. Only temporary fixtures were
used to test this command in phase 1.

`MarketStore.backup(new_path)` includes committed WAL data and refuses an existing
destination, the source path, or an uncommitted source transaction.
`MarketStore.restore(backup_path)` validates integrity/version markers, then uses
SQLite backup into the destination connection; it does not copy/delete WAL files
or automatically upgrade the restored schema. Stop other application writers
before restore. Call `initialize()` explicitly afterward if an upgrade is wanted.
There is no destructive down migration.

## Transaction additions

`transactions` retains all v4 columns and its upstream-ID primary key. Additions:

| Columns | Storage and constraints |
| --- | --- |
| `offer_created_at`, `updated_at` | Nullable source timestamp TEXT, unchanged spelling/precision |
| `created_at_us`, `updated_at_us` | Nullable INTEGER UTC microseconds for local ordering; original timestamp text remains authoritative for greater precision |
| `first_fetched_at`, `last_fetched_at` | Nullable TEXT; legacy initialized from original `fetched_at` |
| `money_decimal`, `quantity_decimal` | Nullable exact decimal TEXT; never derived by migration from REAL |
| `money_precision`, `quantity_precision` | Nullable TEXT, CHECK `decimal` or `decoded_float` |
| `normalization_version` | INTEGER NOT NULL DEFAULT 0, CHECK >= 0 |
| `normalization_status` | TEXT NOT NULL DEFAULT `legacy`, CHECK `legacy`, `normalized`, `extensions`, `error` |

`api_client.py` decodes JSON fractional numeric tokens as Decimal before float
conversion. The boundary keeps fixed decimal text without two-place rounding;
existing REAL projections and `unit_price` support current calculations. Inputs
already decoded as floats are explicitly marked `decoded_float`, not represented
as recovered original tokens. Null numeric facts remain null, not zero. Neither
source money nor normalization establishes gross/net meaning, fees, or cost basis.
`normalized` means the supplied fields were mapped by the parser; it does not
assert that optional identities were supplied or that history is complete.
`extensions` signals retained unknown scalar paths that need semantic review.

Index: `(transaction_type, created_at_epoch, id)`, in addition to the old
`(item_code, created_at_epoch DESC, id)` index.

## New relational tables

All fields are nullable unless marked required below. All child foreign keys use
`ON DELETE CASCADE`. Domain numeric TEXT values are validated as finite decimals
by the parser/store; arbitrary serialized objects are not accepted.

| Table | Actual columns, keys, constraints |
| --- | --- |
| `transaction_participants` | Required `transaction_id` FK transactions, required `side` CHECK buy/sell; `user_id`, `mu_id`, `country_id`, `party_id` TEXT; PK `(transaction_id, side)` |
| `transaction_equipment` | `transaction_id` TEXT PK/FK transactions; `instance_id`, `equipment_code`, `equipment_type`, `state`, `max_state`, `item_quantity`, `last_acquisition_at` TEXT |
| `transaction_equipment_stats` | Required `transaction_id` FK equipment, required `skill_code` TEXT; `value_decimal` TEXT; PK `(transaction_id, skill_code)` |
| `transaction_field_state` | Required `transaction_id` FK transactions, required `field_path` TEXT, required `is_null` INTEGER CHECK 0/1, nullable `source_updated_us` INTEGER; PK `(transaction_id, field_path)` |
| `transaction_extra_fields` | Required `transaction_id` FK transactions, required `field_path` TEXT, required `value_type` TEXT, `scalar_value` TEXT; PK `(transaction_id, field_path)`; scalar checks below |
| `order_book_entries` | Required `observation_id` INTEGER FK observations, required `side` CHECK bid/ask, required `entry_position` INTEGER CHECK >=0; `order_id`, required `item_code`, `source_type`, `user_id`, `mu_id`, `country_id`, `party_id`, required `price_decimal`, required `quantity_decimal`, `offer_at`, `price_precision`, `quantity_precision` TEXT; PK `(observation_id, side, entry_position)`; precision CHECK decimal/decoded_float |
| `order_entry_field_state` | Required observation ID/side/position, required `field_path` TEXT, required `is_null` INTEGER CHECK 0/1; PK includes all four key fields; composite FK order entry |
| `order_entry_extra_fields` | Required observation ID/side/position, required `field_path`, required `value_type`, nullable `scalar_value`; PK includes all four key fields; composite FK order entry; scalar checks below |
| `market_entities` | Required `entity_kind` TEXT CHECK user/mu/country/party, required `entity_id` TEXT, `name`, `name_observed_at`, required `lookup_status`, required `lookup_attempted_at` TEXT; PK `(entity_kind, entity_id)` |
| `market_ingestion_state` | Required `stream` TEXT PK CHECK trading/itemMarket, required `normalization_version` INTEGER >0, required `scan_mode` CHECK incremental/resync/backfill; `scan_anchor`, `oldest_at`, `oldest_id`, `newest_at`, `newest_id`, `attempted_at` TEXT; required `status` CHECK running/partial/complete/failed/exhausted; required nonnegative INTEGER `attempts`, `pages`, `inserted`, `enriched`, `unchanged`, `rejected`; nullable `last_error` TEXT |
| `market_enrichment_coverage` | Required `stream` CHECK trading/itemMarket, required `normalization_version` INTEGER >0, required TEXT `start_at`, `end_at`, `source`, `completion_reason`, `observed_at`; PK `(stream, normalization_version, start_at, end_at, source)`; store validates positive timestamp interval |

Source-reference indexes: each participant `{user,mu,country,party}_id` plus
transaction ID; equipment instance ID plus transaction ID; observation ID plus
upstream order ID. No speculative leaderboard indexes or precomputed P&L tables.

Scalar checks allow only `null`, `string`, `number`, `boolean`. A null type must
have SQL NULL; all other types require non-null scalar TEXT. Boolean values are
`true`/`false`; numbers use finite decimal text. Unknown field paths use JSON
Pointer escaping (`~0` for `~`, `~1` for `/`) and array indices. No container rows,
serialized dictionaries, raw API JSON, or nested `__v` are stored. The response
pagination envelope never enters normalization. A business leaf literally named
`nextCursor` under a record remains an unknown business fact, per the contract.

Known-field presence uses normalized column names, participant `buy.`/`sell.`
prefixes, `equipment.` prefixes, and `skill.` prefixes; unknown-field revision
state uses `extra:` plus the escaped source path. Structural equipment/skills
presence also distinguishes explicit null from omission. Source `item.quantity`
and transaction `quantity` remain independent values. Equipment is keyed by sale,
so repeated instance IDs do not overwrite another sale's stats or condition.

## Store behavior

- `ingest_transactions(facts, fetched_at=..., progress=..., coverage=...)` commits
  accepted rows, children, progress, and coverage as one page. Each record uses a
  savepoint: invalid children reject that record without leaving partial rows.
  A progress/coverage failure rolls back the whole page. Rejected records prohibit
  a page from claiming exhaustion or enrichment coverage.
- Return counts are `inserted`, `enriched`, `unchanged`, `rejected`. Compatibility
  `skipped` counts noninserted inputs; newest summary ordering uses microseconds
  plus ID. `last_fetched_at` advances only for an inserted/enriched row; exact replay
  avoids transaction/child rewrites. Scan progress counts unchanged observations.
- Conflicting facts require a strictly newer source `updated_at` revision for that
  field. Missing fields never delete values. Newer explicit scalar null can clear
  a known value; equal/older null cannot. Old responses may fill previously absent
  fields. Partial equipment skill maps never replace/delete the whole skill set.
  Structural null records presence without erasing existing child facts.
- `upsert_transactions(item_code, rows)` remains the legacy commodity adapter,
  delegating source parsing to `warera_api.normalize_transaction`; normalized
  models pass straight through. Its item-specific context supplies trading type
  when omitted. Normal API pages now return normalized models. Upstream IDs and
  the historical ID-less fallback hash algorithm remain compatible.
- Individual order entries preserve upstream identity and source envelope
  position. Same-price entries remain distinct. Aggregate compatible levels are
  derived from entries; zero price/quantity placeholders remain stored but are
  excluded from executable depth. Existing level-only callers remain supported.
- `transaction_details`, `order_entries`, `entity_name`, and `stream_status` expose
  normalized facts offline. Name cache updates reject older attempts, preserve a
  cached name after failed lookup, and keep its original observation time.
- Progress is separate for trading/itemMarket. It contains no cursor and implies
  no historical seek capability. Enrichment coverage is separate from old price
  coverage; migration creates neither. An exhaustion marker is only an observed
  upstream condition, not proof of complete game or inventory history.
- `item_codes(transaction_type="trading")` and legacy commodity history queries
  exclude equipment and unclassified null types. `market_data` explicitly selects
  trading. Price/order-only commodity codes remain discoverable. Use
  `item_codes(transaction_type=None)` for all stored types or `itemMarket` for
  equipment discovery. Legacy null rows remain unchanged/unclassified until
  source provenance establishes a type; they are not guessed from code names.
- Existing housekeeping cascades through the new children and trims enrichment
  coverage when history is pruned. Separate all-history transaction retention
  remains phase 3; phase 1 does not authorize or initiate bulk collection.

## Evidence and remaining work

The focused migration tests exercise v1/v4 upgrades, all old column contents,
reopen/idempotency, failure after DDL/data/both-marker writes, retry, WAL-backed
backup/restore, and the installed offline command. Store tests exercise exact
precision, fixture mapping, null/presence/revision merging, per-sale stats,
unknown scalars, zero orders, page/child atomicity, cache timestamps, commodity
query isolation and retention cascades. Actual run totals are recorded in the
[implementation log](market-participants-implementation-log.md).

Phase 2 must wire separate global trading/itemMarket streams into the existing
sync orchestration, replace legacy duplicate/high-water stopping with proven
normalized overlap, handle rejection diagnostics visibly, and populate progress
and coverage only from committed scans. It must preserve full source timestamps,
use normalized domain models, and retain the new parser's Decimal transport.
Name lookup remains optional; no current membership attribution. No P&L, fee
assumptions, equipment lineage claims, full-resync/status CLI, or bulk history
operation is implemented by this migration.


Phase 3 also uses scalar `schema_meta` keys `market_elapsed_<stream>` and
`market_exhaustion_<stream>`. Elapsed values measure the completed transaction
scan/catch-up work, excluding catalog reads. Exhaustion timestamps are committed
with the exhausting page and reset at the start of a new invocation. They record
an observation independently of retained coverage; pruning can shrink coverage
without undoing that historical observation. No cursor or raw response is stored.
