# Phase B handoff

Status: complete. Date: 2026-10-06 (Europe/Lisbon).

## Schema and public contract

Inspected baseline: v6. New migration: `migrate_to_v7`, registered in
`MIGRATIONS`; `LATEST_SCHEMA_VERSION = 7`. `initialize()` applies schema,
bootstrap, and both version markers in its existing atomic transaction.
No released migration was changed, and no fact indexes were added.

`market_data_metadata` contains exactly one row, keyed by `id = 1` with a
singleton CHECK constraint. `data_as_of_us INTEGER` is nullable UTC microseconds
since the Unix epoch; NULL means no retained market facts.

Public accessor: `MarketStore.data_as_of(self) -> datetime | None` returns an
aware UTC datetime with exact microsecond precision. It performs one metadata
SELECT and no writes, history reads, lazy bootstrap, or wall-clock fallback.
The normal initialized-store contract applies. Restoring an older backup
preserves its schema version; call `initialize()` before using new-version
accessors, as with other migrated store features.

Transaction authority is retained `created_at_us`, falling back to parsed
legacy `created_at` ISO text. Observation authority is parsed `observed_at`.
Naive legacy ISO values are interpreted as UTC using the existing parser;
offsets and subsecond values are preserved. Integer arithmetic avoids float
timestamp rounding. There is no item, stream, commodity, or equipment filter.
`updated_at`, fetch timestamps, identity/assets, configuration, progress, and
sync-status timestamps do not independently contribute.
Existing `last_market_sync_at` and status remain separate and unchanged.

## Bootstrap and maintenance

The v7 migration bootstraps once by streaming only the timestamp columns from
transactions, prices, and order books, then writes the singleton maximum.
Existing indexes begin with item or transaction type rather than global exact
time. A narrow scan avoids relying on text sorting, legacy epoch precision, or
an assumed list of known items. Memory usage stays constant with history size.
Repeated initialization does not rerun the migration or clock bootstrap.

Disposable measurement: 50,000 legacy transactions, split between `trading`
and `itemMarket`, one unfamiliar item, identical fractional ISO timestamps,
empty observation tables, freshly inserted/warm fixture. Measured complete
v6-to-v7 `initialize()` at **0.0714s**, repeated at **0.0711s**. These are local
fixture measurements, not a production estimate. No production DB was opened,
initialized, migrated, or changed. The benchmark remains a test that prints
timing with `-s` and asserts the result without a speed threshold.

Eligible write paths:

- `ingest_transactions()` / `_merge_transaction()`: advance from the merged
  retained timestamp only for inserted/enriched records, within the existing
  record/page savepoints. Unchanged replay returns before metadata writes.
- `upsert_transactions()`: delegates to the same normalized ingestion path.
- `insert_price_observations()`: advances within its observation transaction,
  only when at least one row is inserted.
- `insert_order_book_observations()`: advances within the snapshot/entries/levels
  transaction, only when at least one snapshot is inserted.

Old replay cannot regress the clock. A newer observation can advance it even
when transaction IDs have not changed. Rejected records roll back their clock
writes; strict page rejection, progress/checkpoint/coverage failures, and failed
observation transactions roll back facts and metadata together. Partial sync
retains only the clock of committed facts.

An accepted source revision that moves a current maximum transaction timestamp
backward reconciles the retained maximum in the same transaction. This is an
actual correction to stored facts, distinct from old-history replay.
`run_housekeeping()` reconciles in its deletion transaction whenever facts were
removed, so deletion of the newest fact or all facts cannot leave a stale clock.
Backups include metadata; v7 restore copies the facts and their clock together.
Older backups obtain their clock through migration after restore.

All incremental, recent/all-history resync, backfill, and resume modes already
call these store methods. No `sync.py` integration change was necessary.

## Changed files and verification

- `src/warera_quant/market_store.py`: v7 migration, accessor, atomic advancement
  and retained-fact reconciliation.
- `tests/test_market_clock.py`: isolated clock, bootstrap, rollback, maintenance,
  read-only accessor, and measurement fixtures.
- `tests/test_sync.py`: clock assertions and sync-mode integration checks.
- `tests/test_market_store.py`: fresh-install table inventory.
- `tests/test_market_migration.py`, `tests/test_display_identity.py`: affected
  latest-version assertions and migration CLI output updated to v7.
- This handoff. Existing phase-A CLI/test changes were preserved.

Final focused command:

```powershell
.venv/Scripts/pytest tests/test_market_clock.py tests/test_market_store.py tests/test_market_migration.py tests/test_sync.py tests/test_market_resync.py tests/test_market_resume.py tests/test_display_identity.py -q -p no:cacheprovider --basetemp=.phase-b-verified
```

Result: **135 passed in 8.37s**. Includes fresh install, v6 upgrade, synchronized
markers, empty clock, legacy fractions/offsets, transaction-only bootstrap,
both streams/unfamiliar equipment, price/order maxima, replay, newer observations,
partial/rejected pages, rollback after clock/progress writes, migration failure,
reopen, backup/restore, pruning to an older fact and to empty, existing durable
resume checks, and identity/browser export regressions. `git diff --check` passed.
All DBs and output were disposable. Session-created test directories were removed.

## Limitations and phase boundary

Bootstrap and explicit pruning reconciliation are O(retained timestamp rows).
A rare accepted backward correction of a maximum transaction also requires
reconciliation; ordinary reads and forward writes do not scan history.
Malformed retained timestamp text fails migration atomically rather than silently
omitting facts. Out-of-band database edits are outside supported maintenance.
Restore retains the existing requirement that other writers be stopped.

This completes only B. No report cutoff wiring, phase C work, new fact indexes,
API collection, production writes, publication, agents, staging, or commits.
