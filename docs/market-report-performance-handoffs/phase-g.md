# Phase G handoff

Status: complete. Date: 2026-10-06 (Europe/Lisbon).

G owns only the store, query/migration tests, affected version assertions and this note. F workspace edits were preserved; no shared files, F fixtures, staging, commits, agents, production DBs, API sync or publication were used. H still requires both F and G.

## Retained changes and contracts

Schema v7 -> v8: `migrate_to_v8` adds only `(entity_kind, name COLLATE NOCASE, entity_id)` on market_entities. Released migrations are unchanged. initialize() applies the index and synchronized version markers atomically. No transaction/child/observation index was added.

`find_users` performs separate selective ID and NOCASE name searches, deduplicates overlapping matches and sorts by entity ID. It preserves ambiguous-name candidates and exact-ID behavior. Latest observation methods replace full-history window ranking with distinct-item enumeration and correlated per-item newest-row seeks. Existing snapshot ordering remains **observed_at_epoch DESC, ID DESC**, including same-second source fractions; this phase does not redefine cached snapshot semantics. Exact transaction ordering continues to use legacy-aware microseconds then opaque ID.

Per-item window/period reads add a coarse indexed upper bound alongside their existing authoritative precise upper bound. Item discovery explicitly deduplicates transaction codes before union; equipment-only discovery previously returned one repeated code per parent and now returns the distinct codes requested by its discovery contract. Domain history/ownership/decimal evidence are unchanged.

`MarketStore.profile_read(operation) -> (result, evidence)` consumes an iterator inside the timed interval and records traced read statements, EXPLAIN plans, elapsed seconds, result counts, child-query counts and approximate VM instructions (100-instruction callback granularity). It temporarily owns trace/progress callbacks. Use an isolated disposable connection; it is not a production-opening API: normal `_connect` retains existing WAL configuration. No production profiling was performed. Result counts are materialized parent counts for parent iterators, not SQLite rows visited; VM instructions expose residual work without claiming row-visit instrumentation.

## Fixture and cache conditions

Unique G fixture: 30,200 transactions (30,000 old at 2026-06-07 plus 200 active); 40 items; every fifth parent itemMarket; 15,100 references to user U, all parents reference MU M/country C; legacy NULL created_at_us on every seventeenth parent. Each observation table has 20,000 rows/40 items; 10,000 cached users. Window [2026-09-15T00:00:00Z, 2026-09-22T00:00:00Z); explicit exclusive endpoint supplied to isolate query work from context resolution. This is the final default-window store query, not a full report benchmark. Entity U, commodity item1, name nAmE123; batch size 500.

SQLite default planner statistics, no ANALYZE/VACUUM. First samples are warm after fixture ingestion (OS cache and recently written SQLite pages); repeats use the same connection immediately. Separate baseline/final disposable DBs, identical fixture. Trace/progress overhead is included; timings are observational, not pass/fail thresholds. No cold disk claim or measured production speedup.

| Query | Parents/results before -> after | Child reads | First ms before -> after | Repeat ms before -> after | Approx VM instructions before -> after |
| --- | --- | --- | --- | --- | --- |
| window | 200 -> 200 | 4 | 1.988 -> 2.151 | 1.565 -> 1.913 | 20700 -> 20700 |
| entity-window | 100 -> 100 | 4 | 12.964 -> 12.361 | 12.149 -> 12.451 | 331600 -> 331600 |
| entity-fifo | 15100 -> 15100 | 124 | 137.440 -> 143.287 | 156.595 -> 185.956 | 1714100 -> 1714100 |
| global-fifo | 30200 -> 30200 | 244 | 376.390 -> 498.317 | 365.187 -> 503.061 | 3740900 -> 3740900 |
| item-history | 5 -> 5 | 0 | 0.204 -> 0.255 | 0.108 -> 0.129 | 900 -> 900 |
| item-long-history | 755 -> 755 | 0 | 7.587 -> 11.292 | 6.329 -> 7.947 | 146800 -> 146800 |
| daily | 1 -> 1 | 0 | 0.167 -> 0.154 | 0.054 -> 0.051 | 1000 -> 1000 |
| name | 1 -> 1 | 0 | 1.663 -> 0.151 | 1.083 -> 0.018 | 70000 -> 0 |
| latest-price | 40 -> 40 | 0 | 17.038 -> 1.083 | 15.517 -> 0.655 | 1021000 -> 62200 |
| latest-orders | 40 -> 40 | 0 | 16.385 -> 1.017 | 16.131 -> 0.700 | 1021400 -> 62600 |
| discovery | 40 -> 40 | 0 | 11.906 -> 9.297 | 11.855 -> 9.007 | 879900 -> 638700 |
| equipment-discovery | 6040 -> 8 | 0 | 5.664 -> 3.728 | 6.036 -> 4.356 | 60400 -> 78600 |

FIFO entity includes all 15,100 older/recent parents, global FIFO 30,200. All earlier dispositions are retained; four batched child reads per nonempty batch remain unchanged. Large FIFO elapsed variation with identical VM counts is noise, not an optimization claim. Per-item histories return 5 active/755 full-period rows; daily output is one aggregate of 5 parents. Name returns one cache row; latest returns 40 observation parents per table, without level reads. Discovery reads indexes, not normalized parents or children.

## EXPLAIN QUERY PLAN evidence

Below are complete parent/aggregate plans for the measured baseline and final queries (child plans are summarized separately).

### window

before:
```text
SEARCH transactions USING INDEX idx_transactions_type_time (transaction_type=? AND created_at_epoch>? AND created_at_epoch<?)
USE TEMP B-TREE FOR ORDER BY
```

after:
```text
SEARCH transactions USING INDEX idx_transactions_type_time (transaction_type=? AND created_at_epoch>? AND created_at_epoch<?)
USE TEMP B-TREE FOR ORDER BY
```

### entity-window

before:
```text
CO-ROUTINE candidate_ids
SEARCH transaction_participants USING COVERING INDEX idx_participant_user (user_id=?)
SCAN c
SEARCH t USING INDEX sqlite_autoindex_transactions_1 (id=?)
USE TEMP B-TREE FOR ORDER BY
```

after:
```text
CO-ROUTINE candidate_ids
SEARCH transaction_participants USING COVERING INDEX idx_participant_user (user_id=?)
SCAN c
SEARCH t USING INDEX sqlite_autoindex_transactions_1 (id=?)
USE TEMP B-TREE FOR ORDER BY
```

### entity-fifo

before:
```text
CO-ROUTINE candidate_ids
SEARCH transaction_participants USING COVERING INDEX idx_participant_user (user_id=?)
SCAN c
SEARCH t USING INDEX sqlite_autoindex_transactions_1 (id=?)
USE TEMP B-TREE FOR ORDER BY
```

after:
```text
CO-ROUTINE candidate_ids
SEARCH transaction_participants USING COVERING INDEX idx_participant_user (user_id=?)
SCAN c
SEARCH t USING INDEX sqlite_autoindex_transactions_1 (id=?)
USE TEMP B-TREE FOR ORDER BY
```

### global-fifo

before:
```text
CO-ROUTINE history_ids
COMPOUND QUERY
LEFT-MOST SUBQUERY
CO-ROUTINE a
MATERIALIZE active
MATERIALIZE window_ids
SEARCH transactions USING INDEX idx_transactions_type_time (transaction_type=? AND created_at_epoch>? AND created_at_epoch<?)
SCAN w
SEARCH p USING INDEX sqlite_autoindex_transaction_participants_1 (transaction_id=?)
SCAN active
USE TEMP B-TREE FOR DISTINCT
SCAN a
SEARCH p USING COVERING INDEX idx_participant_user (user_id=?)
UNION USING TEMP B-TREE
CO-ROUTINE a
SCAN active
USE TEMP B-TREE FOR DISTINCT
SCAN a
SEARCH p USING COVERING INDEX idx_participant_mu (mu_id=?)
UNION USING TEMP B-TREE
CO-ROUTINE a
SCAN active
USE TEMP B-TREE FOR DISTINCT
SCAN a
SEARCH p USING COVERING INDEX idx_participant_country (country_id=?)
UNION USING TEMP B-TREE
CO-ROUTINE a
SCAN active
USE TEMP B-TREE FOR DISTINCT
SCAN a
SEARCH p USING COVERING INDEX idx_participant_party (party_id=?)
UNION USING TEMP B-TREE
SCAN window_ids
SCAN h
SEARCH t USING INDEX sqlite_autoindex_transactions_1 (id=?)
USE TEMP B-TREE FOR ORDER BY
```

after:
```text
CO-ROUTINE history_ids
COMPOUND QUERY
LEFT-MOST SUBQUERY
CO-ROUTINE a
MATERIALIZE active
MATERIALIZE window_ids
SEARCH transactions USING INDEX idx_transactions_type_time (transaction_type=? AND created_at_epoch>? AND created_at_epoch<?)
SCAN w
SEARCH p USING INDEX sqlite_autoindex_transaction_participants_1 (transaction_id=?)
SCAN active
USE TEMP B-TREE FOR DISTINCT
SCAN a
SEARCH p USING COVERING INDEX idx_participant_user (user_id=?)
UNION USING TEMP B-TREE
CO-ROUTINE a
SCAN active
USE TEMP B-TREE FOR DISTINCT
SCAN a
SEARCH p USING COVERING INDEX idx_participant_mu (mu_id=?)
UNION USING TEMP B-TREE
CO-ROUTINE a
SCAN active
USE TEMP B-TREE FOR DISTINCT
SCAN a
SEARCH p USING COVERING INDEX idx_participant_country (country_id=?)
UNION USING TEMP B-TREE
CO-ROUTINE a
SCAN active
USE TEMP B-TREE FOR DISTINCT
SCAN a
SEARCH p USING COVERING INDEX idx_participant_party (party_id=?)
UNION USING TEMP B-TREE
SCAN window_ids
SCAN h
SEARCH t USING INDEX sqlite_autoindex_transactions_1 (id=?)
USE TEMP B-TREE FOR ORDER BY
```

### item-history

before:
```text
SEARCH transactions USING INDEX idx_transactions_type_time (transaction_type=? AND created_at_epoch>?)
USE TEMP B-TREE FOR ORDER BY
```

after:
```text
SEARCH transactions USING INDEX idx_transactions_type_time (transaction_type=? AND created_at_epoch>? AND created_at_epoch<?)
USE TEMP B-TREE FOR ORDER BY
```

### item-long-history

before:
```text
SEARCH transactions USING INDEX idx_transactions_type_time (transaction_type=? AND created_at_epoch>?)
USE TEMP B-TREE FOR ORDER BY
```

after:
```text
SEARCH transactions USING INDEX idx_transactions_type_time (transaction_type=? AND created_at_epoch>? AND created_at_epoch<?)
USE TEMP B-TREE FOR ORDER BY
```

### daily

before:
```text
SEARCH transactions USING INDEX idx_transactions_type_time (transaction_type=? AND created_at_epoch>? AND created_at_epoch<?)
USE TEMP B-TREE FOR GROUP BY
USE TEMP B-TREE FOR ORDER BY
```

after:
```text
SEARCH transactions USING INDEX idx_transactions_type_time (transaction_type=? AND created_at_epoch>? AND created_at_epoch<?)
USE TEMP B-TREE FOR GROUP BY
USE TEMP B-TREE FOR ORDER BY
```

### name

before:
```text
SEARCH market_entities USING INDEX sqlite_autoindex_market_entities_1 (entity_kind=?)
```

after:
```text
SEARCH market_entities USING INDEX sqlite_autoindex_market_entities_1 (entity_kind=? AND entity_id=?)
SEARCH market_entities USING INDEX idx_entities_kind_name (entity_kind=? AND name=?)
```

### latest-price

before:
```text
SEARCH price_observations USING INTEGER PRIMARY KEY (rowid=?)
LIST SUBQUERY 2
CO-ROUTINE (subquery-1)
CO-ROUTINE (subquery-4)
SCAN price_observations USING COVERING INDEX idx_price_observations_item_time
USE TEMP B-TREE FOR LAST TERM OF ORDER BY
SCAN (subquery-4)
SCAN (subquery-1)
CREATE BLOOM FILTER
```

after:
```text
SEARCH price_observations USING INTEGER PRIMARY KEY (rowid=?)
LIST SUBQUERY 3
CO-ROUTINE items
SCAN price_observations USING COVERING INDEX idx_price_observations_item_time
SCAN items
CORRELATED SCALAR SUBQUERY 1
SEARCH p USING COVERING INDEX idx_price_observations_item_time (item_code=?)
USE TEMP B-TREE FOR LAST TERM OF ORDER BY
CREATE BLOOM FILTER
```

### latest-orders

before:
```text
SEARCH order_book_observations USING INTEGER PRIMARY KEY (rowid=?)
LIST SUBQUERY 2
CO-ROUTINE (subquery-1)
CO-ROUTINE (subquery-4)
SCAN order_book_observations USING COVERING INDEX idx_order_book_observations_item_time
USE TEMP B-TREE FOR LAST TERM OF ORDER BY
SCAN (subquery-4)
SCAN (subquery-1)
CREATE BLOOM FILTER
```

after:
```text
SEARCH order_book_observations USING INTEGER PRIMARY KEY (rowid=?)
LIST SUBQUERY 3
CO-ROUTINE items
SCAN order_book_observations USING COVERING INDEX idx_order_book_observations_item_time
SCAN items
CORRELATED SCALAR SUBQUERY 1
SEARCH p USING COVERING INDEX idx_order_book_observations_item_time (item_code=?)
USE TEMP B-TREE FOR LAST TERM OF ORDER BY
CREATE BLOOM FILTER
```

### discovery

before:
```text
MERGE (UNION)
LEFT
MERGE (UNION)
LEFT
SEARCH transactions USING INDEX idx_transactions_type_time (transaction_type=?)
USE TEMP B-TREE FOR ORDER BY
RIGHT
SCAN price_observations USING COVERING INDEX idx_price_observations_item_time
RIGHT
SCAN order_book_observations USING COVERING INDEX idx_order_book_observations_item_time
```

after:
```text
MERGE (UNION)
LEFT
MERGE (UNION)
LEFT
SEARCH transactions USING INDEX idx_transactions_type_time (transaction_type=?)
USE TEMP B-TREE FOR DISTINCT
USE TEMP B-TREE FOR ORDER BY
RIGHT
SCAN price_observations USING COVERING INDEX idx_price_observations_item_time
RIGHT
SCAN order_book_observations USING COVERING INDEX idx_order_book_observations_item_time
```

### equipment-discovery

before:
```text
SEARCH transactions USING INDEX idx_transactions_type_time (transaction_type=?)
USE TEMP B-TREE FOR ORDER BY
```

after:
```text
SEARCH transactions USING INDEX idx_transactions_type_time (transaction_type=?)
USE TEMP B-TREE FOR DISTINCT
```

Child batches search their existing transaction_id-leading primary indexes in participants, equipment, equipment_stats and field_state; no duplicated child indexes. Window/equipment coarse type/time searches are selective in time, but exact COALESCE predicates remain residual and require temporary ORDER BY. Targeted queries selectively search references by entity ID, then seek each candidate parent by ID: time/type remain residual, so old reference growth still increases DB work despite bounded domain/child processing. FIFO active-reference DISTINCT/UNION B-trees are required deduplication; final exact-time ORDER BY is also temporary. No ownership denormalization.

Latest queries still SCAN the narrow item/time index to enumerate distinct items; they do not magically become O(items). Each item then SEARCHes its leading item key and reads newest epoch/ID rows, with a last-term temporary sort for equal-epoch IDs. Large all-equal-epoch groups remain a limitation. Discovery still scans retained narrow keys, and type-scoped discovery scans/searches the type range plus temporary DISTINCT/ORDER operations; deduplication reduces repeated domain results, not every history visit. Daily aggregates retain temporary GROUP BY and ORDER BY because day buckets differ from the source index ordering. A type/time index appearing in a plan does not make item filtering selective.

## Candidates accepted/rejected and costs

Accepted: separate ID/name seeks plus NOCASE index; latest-row seek rewrite; distinct discovery; coarse historical upper bounds. No forced INDEXED BY or test requirement for a particular retained index name. Plan tests assert selective name/item seek behavior and indexed upper bounds.

Rejected composite `(transaction_type,item_code,created_at_epoch,id)`: on the same un-analyzed fixture SQLite still chose type/time for per-item histories; long history used 146,800 VM instructions before and after, recent history 900 before/after. Daily instructions fell 1,000 -> 300, but existing item/time index achieved the same 300 without the composite. Discovery benefit did not justify another wide fact index. Candidate timings were 0.204 -> 0.200 ms recent history; daily 0.167 -> 0.157 ms. Removed before final validation.

Rejected SQL UNION ID/name rewrite: planner chose entity-kind-only scan for name branch, 50,000 VM instructions and 1.42 ms; separate searches achieved both full-key seeks. Rejected native `(transaction_type,created_at_us,id)` candidate via a disposable experimental next migration: legacy-aware COALESCE could not use native-time range/order, same 20,700 VM instructions; 1.906 -> 3.095 ms. No custom timestamp function expression index or precision backfill was attempted. Retained exact/legacy temporary sorts rather than changing facts. Additional newest-row ID/covering indexes were not retained: seek rewrite alone removes ranking work, and additional storage/write cost lacks demonstrated need; pathological equal-second groups were not benchmarked.

Measured v7 -> v8 initialize(): 0.011610 s; allocated DB pages 14,024,704 -> 14,299,136 bytes, **274,432 bytes added** for 10,000 names (page_count * page_size, including allocation; WAL/file-size timing excluded). Injected failure after index creation rolled back DDL and kept both markers at v7; subsequent upgrade/fresh install/reopen pass.

Separate ingestion experiment adds 10,000 legacy future item1 trades at 2026-10-15 to the same base fixture: historical upper-bound read preserved five rows, 21.169 -> 0.166 ms, 150,900 -> 900 approximate VM instructions. Both precise and coarse bounds exclude future parents; this supplies measured evidence for keeping the upper-bound rewrite.

Supported normalized ingest, five consecutive batches of 500 new parents, same-connection warm state: median 0.067663 s v7 vs 0.073585 s v8. Supported cache-name writes, five batches of 500 individually committed upserts: median 0.145981 s vs 0.169352 s. These noisy samples show possible write overhead, not throughput guarantees; transaction tables gained no index. Added index directly affects identity cache writes. Baseline single 500-parent sample 0.079361 s vs 0.109759 s reinforces why small timing changes are not claimed as precise penalties.

## Changed files and verification

- src/warera_quant/market_store.py
- tests/test_market_query_performance_g.py (unique fixtures, plans, equivalence, cost, rollback, legacy ties, bounds)
- tests/test_market_clock.py, tests/test_market_migration.py, tests/test_display_identity.py (all affected latest-version assertions/CLI schema output)
- docs/market-report-performance-handoffs/phase-g.md

Focused command (existing .venv, isolated outputs):

```powershell
.venv/Scripts/pytest tests/test_market_query_performance_g.py tests/test_market_store.py tests/test_market_migration.py tests/test_market_clock.py tests/test_display_identity.py tests/test_sync.py tests/test_market_resync.py tests/test_market_resume.py tests/test_participant_market_data.py tests/test_report_context.py -q -p no:cacheprovider --basetemp=.phase-g-verified
```

Result: **185 passed in 16.40s**. `git diff --check` passed. G temporary DB/output paths were removed after evidence was recorded in this note; no F paths were touched.

Tests cover fresh installs, v7 upgrade, older upgrades, rollback, precise legacy/normal ordering and opaque ID ties, historical upper bounds, decimal source text, ambiguity and ID/name overlap, latest epoch/ID ties, distinct equipment discovery, bounded child batches, all-FIFO parents and existing read-model ownership/full-FIFO equivalence. No hard-coded timing thresholds.

Limitations: synthetic timings only; no production profile/upgrade estimate. Existing snapshot latest ordering intentionally remains epoch/ID, while bounded histories retain source microsecond precision. Exact-time/index and reference-growth costs remain; schema v8 does not accelerate explicit FIFO. Benchmark traces consume memory and include instrumentation overhead. H/I integration remains outside G.
