# Historical market acquisition: query scope correction

Status: investigation and implementation requirements, 2026-09-24. Historical
rollout acceptance is reopened. This specification supersedes prior claims that
the completed global scan exhausted all available market history. It does not
claim that the corrective importer is implemented.

## Observations and unresolved explanation

The former commodity downloader supplied `itemCode` on each paginated query. The
new two-stream importer omitted it, querying all commodities or all equipment.
The cursor field and request envelope are unchanged; a parser/mapping defect has
not been established.

| Query | Observed result |
| --- | --- |
| `transactionType=trading`, no item filter | 3,797 history pages; oldest `2026-09-21T00:31:11.575Z`; parser observed no usable continuation |
| `transactionType=itemMarket`, no item filter | 697 history pages; oldest `2026-09-21T00:43:47.869Z`; parser observed no usable continuation |
| `transactionType=trading`, `itemCode=heavyAmmo` | Diagnostic reached `2026-09-16T21:31:53.370Z` on page 71; 100 rows on that page and a nonempty string `nextCursor`; stopped at the diagnostic boundary, not exhaustion |

The user reports that the previous downloader obtained 90 days. This has not yet
been reproduced in the diagnostic. The filtered result proves older records are
reachable than the completed global scan retrieved. It does not prove a 90-day
retention policy or determine the behavior of every filter or equipment query.

A protective default time window for broad queries is a plausible explanation
suggested by the user. Its existence, duration, trigger and override are
**unverified**. Do not describe a three-day or seven-day server default as fact.
The requests were performed at different times; a controlled comparison remains
necessary. The documented request schema exposes no date-range parameter.

## Actual command and HTTP requests

The completed production invocation was:

```powershell
.venv\Scripts\warera-marketguide --sync --resync-market --history-scope all --market-db data/warera_market.sqlite3
```

`--history-scope all` only disables our local date boundary. It is not an API
parameter and cannot be assumed to disable server defaults. The full-resync
path uses `limit=100`, no local page cap, sequential requests, and a default
one-second minimum delay after the preceding request completes. This is observed
client configuration, not a verified statement of the server's maximum cadence.

All examples below are GET requests to
`https://api2.warera.io/trpc/transaction.getPaginatedTransactions`, authenticated
with `X-Api-Key`. The query parameter `input` contains URL-encoded JSON:

```json
{"limit":100,"transactionType":"trading"}
{"limit":100,"transactionType":"itemMarket"}
{"limit":100,"transactionType":"trading","itemCode":"heavyAmmo"}
```

Subsequent requests preserve the same filters and add `cursor` with the exact
returned `nextCursor`. Do not construct cursors from transaction IDs, decode them
as a supported seek interface, or transfer a cursor between filter scopes.

Current parser behavior treats missing, null or empty `nextCursor` as exhaustion.
Transport failures, rejected rows and pagination that makes no progress are
errors. The completed run did not preserve the terminal response shape, so its
logs cannot distinguish absent, null and empty continuation. Future diagnostics
must record that distinction without retaining raw responses or cursor values.

Evidence: `output/phase6-rollout/restart-20260923/stdout.log` and `transport.json`;
`output/phase6-rollout/filtered-history-evidence.log`; diagnostic scripts
`probe_filtered_floor.py` and `probe_filtered_week.py` in the same directory;
public request schema fetched into `output/phase6-rollout/swagger-current.js`.
These are local operational artifacts, not assumed version-controlled fixtures.
The diagnostic read the API without writing production transactions.

## Required investigation before choosing the acquisition strategy

1. Compare broad and item-filtered requests under the same credentials and close
   observation times. Use only documented filters and returned continuations.
   Record query scope, request count, event bounds, ordering, ID overlap, terminal
   envelope keys/types and whether continuation is absent/null/empty/nonempty.
2. Reproduce older commodity reach across representative items, including the
   reported 90-day history where available. Establish whether a documented
   override exists or per-item pagination is required. Do not invent a date input.
3. Investigate equipment separately: discover and verify the supported historical
   query scopes and an adequate scope inventory. Commodity item filters cannot
   be assumed to cover equipment. Pending equipment listings remain excluded.
4. Verify official request-rate guidance before changing cadence. Preserve bounded
   retries and surface failures rather than interpreting them as exhaustion.

## Implementation requirements after verification

- Keep endpoint/filter/response parsing in `warera_api.py`, orchestration in
  `sync.py`, persistence in `market_store.py`, and CLI orchestration in `cli.py`.
- Represent the exact query scope in progress, coverage and durable checkpoints:
  stream, all relevant filters, page size, normalization version, fixed anchor,
  phase and opaque continuation. Current stream-only checkpoint keys are not
  sufficient for independent filtered scans. Do not silently reinterpret them.
- Commit each page's normalized rows, children, progress and checkpoint atomically.
  Restart the same scope at its saved continuation. Reject mismatched scopes and
  surface expired tokens; any deliberate replay preserves all committed facts.
- Upsert by source transaction identity across overlapping scopes. Existing IDs
  enrich without duplication, and request/page counts remain distinct from unique
  inserted or enriched transaction counts.
- Track query-scoped exhaustion separately from market-wide coverage. A single
  exhausted item must not certify every commodity; do not union different items'
  time intervals into a claim of complete market coverage. Define the required
  scope inventory, including unavailable or unknown historical item coverage.
- Preserve prior global observations as scoped evidence. Review persisted coverage
  metadata and completion gates so a completed global job cannot authorize full
  historical acceptance. Preserve all facts, backups and aggregate history.
- Keep the unattended runner restartable across the complete scope inventory.
  Distinguish job completion from accepted historical coverage, with explicit
  partial or blocked status when required scopes cannot be collected.

## Validation and rollout acceptance

Add meaningful tests for different broad/filtered history floors, independent
scope checkpoints, kill/restart, atomic rollback, mismatched/expired cursors,
overlapping-ID enrichment, incomplete scope inventories and conservative coverage.
Rehearse on temporary data, then take a consistent backup before production
metadata/schema changes. Do not delete or replace the production database.

Once the acquisition contract is verified and implementation validated, collect
reachable history through the unattended runner, then perform normal catch-up,
offline reconciliation and a new common-as-of publication. Record counts,
requests, dates and exhaustion per scope. Verify all nine boards, equipment detail
stats, commodity-only shared presentation and complete table PNG crops.

The existing publication is a partial snapshot, not evidence of full historical
acceptance. Fees, missing inventory basis, nonmarket movements and equipment
lineage remain separate accounting limitations even after acquisition is fixed.
Publish supported partial volume/P&L without converting unknowns to zero.
