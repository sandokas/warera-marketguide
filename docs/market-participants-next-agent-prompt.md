# Next-agent prompt: correct historical import scope before phase 6 acceptance

## September 24 correction: investigate query scope before implementation

Read [the historical query-scope specification](market-history-query-scope-spec.md)
first. It supersedes the earlier full-history acceptance and visual-only handoff.
The filtered `heavyAmmo` diagnostic reached September 16 on page 71 with another
cursor; the completed global scan stopped around September 21. A protective
server default for broad queries is plausible but unverified. Neither a mapping
bug nor a specific default time window has been established.

First verify the query contract and supported scopes, separately for commodities
and equipment. Then implement scoped resumability/coverage, validate, acquire the
missing history additively and regenerate reports. Do not assume per-item queries
are the only supported solution before verification. The diagnostic made no
production data changes. Current work was documentation only; no corrected import
has started. The user's last request was to document findings and required work.

Continue the market-participants phase 6 rollout in `C:\git\warera-marketguide`.
Read `AGENTS.md`, the implementation plan, API/schema contracts, and **every phase
handoff** in `docs/market-participants-implementation-log.md`, especially the final
September 24 entries. Preserve unrelated worktree changes, backups and aggregate
history. Use the existing `.venv`. Do not rerun the full test suite without a new
reason: the initial full suite already passed 545 tests; subsequent relevant
regressions passed 148 tests after the resumability/coverage changes.

The user explicitly changed the original no-persisted-cursors requirement:
"make it so that you don't have to actively monitor, and that the process can be
resumed if killed." Durable opaque cursor checkpoints are now authorized and
implemented with `--resume-market`. This supersedes historical no-cursor wording.
Do not print cursor values, credentials, or raw API responses.

## Completed global job; historical acquisition remains incomplete

The production global-query job finished, followed by successful normal
catch-up. This does not establish full available-history completion. It completed before the new cursor feature was installed, so production
may have no resume checkpoint; that is expected, not a reason to replay history.

- Original consistent v4 backup:
  `data/warera_market.sqlite3.backup-20260923T175846714697Z`.
- Pre-rollout: 9,112,962 trading rows, schema v4, integrity OK.
- Migrated additively to v5. Production backup was restored separately and checked.
- Final retained rows: **9,264,797 trading + 69,831 equipment = 9,334,628**.
- Commodity normalized rows: **385,943**; **8,878,854 older legacy rows** remain
  unenriched. They were retained, not deleted or silently counted as recovered.
- Equipment stats: **89,770**, all 69,831 sales represented in the report window.
- Full-run dates reached: trading **2026-09-21T00:31:11.575Z**, equipment
  **2026-09-21T00:43:47.869Z**. Both APIs returned exhaustion. Do not infer an
  official retention duration or complete game history from these dates.
- Latest catch-up events: trading **2026-09-24T00:48:44.093Z**, equipment
  **2026-09-24T00:48:48.672Z**.
- Metered completed attempt: **4,589 full-resync HTTP attempts + 33 normal
  catch-up attempts = 4,622**, all 200. Full resync included 4,563 transaction pages
  and its built-in catch-up. Normal catch-up added seven pages / 403 new rows.
- Earlier interrupted attempt: 1,574 committed pages, 118,224 inserted,
  39,166 enriched, ten unchanged; at least 1,600 successful endpoint responses,
  with exact retry/in-flight totals unavailable. See log for measured head-replay
  cost. Do not mix invocation counters or count replay observations as unique rows.

Evidence of global-job completion (not historical acceptance):

```text
output/phase6-rollout/restart-20260923/process.json
output/phase6-rollout/restart-20260923/full-status.json
output/phase6-rollout/restart-20260923/catchup-status.json
output/phase6-rollout/restart-20260923/transport.json
output/phase6-rollout/restart-20260923/stdout.log
output/phase6-rollout/coverage-floor-repair.json
output/phase6-rollout/post-repair-status.json
```

Normal catch-up resets *latest-invocation* exhaustion; its false value does not
undo the full-run exhaustion evidence. Historical observed exhaustion remains
separately available. The former epoch-wide coverage intervals were corrected to
the oldest actually returned events, and future ingestion now uses that rule.
No source transactions or old aggregate observations were removed.

## Current unattended publication job

The maintained runner is `scripts/market_rollout.py`, using
`scripts/verify_market_publication.py` for export reconciliation. This job ran
**publication only**, from a consistent snapshot with corrected coverage and a
fixed cutoff. No further bulk API download is needed for this job.

```text
Job directory: output/phase6-rollout/resumable-publication
State:         output/phase6-rollout/resumable-publication/job.json
Logs:          stdout.log / stderr.log in that directory
Report:        report/market_report.html
Verification:  reconciliation.json and database-inventory.json
Common as_of:  2026-09-24T00:52:32.648353+00:00
```

Read `job.json` first. At **2026-09-24T10:42:57Z** the job reached `stage: done`,
`status: complete_pending_visual_review`, with no error. Automated publication and
reconciliation are finished; final visual review/closeout remains. Its last worker
PID was **15684**.
PIDs can be reused, so verify the actual current process against the state and
command line before any action. Rendering this database takes several minutes;
unchanged stage timestamps alone do not prove a hang. Success is
`stage: done`, `status: complete_pending_visual_review`, not merely a launched PID.
Failure preserves its stage with an exception class. A dead process can leave
`status: running`; inspect process existence and logs. Never start a competing
writer. The runner also holds an OS lock per database, released on process death.

If this publication worker has died or failed for a fixed/retryable reason, resume
the **same job**, snapshot and cutoff with this hidden launch (network escalation
may be needed by the execution environment even for process inspection):

```powershell
$stamp = Get-Date -Format 'yyyyMMddTHHmmssfff'
Start-Process -FilePath 'C:\git\warera-marketguide\.venv\Scripts\python.exe' `
  -ArgumentList '-u','C:\git\warera-marketguide\scripts\market_rollout.py',`
    '--job-dir','output/phase6-rollout/resumable-publication',`
    '--publish-only','--exhaustion-evidence','output/phase6-rollout/restart-20260923/full-status.json',`
    '--as-of','2026-09-24T00:52:32.648353+00:00' `
  -WorkingDirectory 'C:\git\warera-marketguide' -WindowStyle Hidden `
  -RedirectStandardOutput "C:\git\warera-marketguide\output\phase6-rollout\publication-$stamp.stdout.log" `
  -RedirectStandardError "C:\git\warera-marketguide\output\phase6-rollout\publication-$stamp.stderr.log"
```

Do not change Windows execution/application-control policy. This host blocked a
new `.ps1` launcher and later blocked the installed `.exe` migration-test launcher
with WinError 4551. Direct hidden launch of the existing Python runner works;
it calls the implemented CLI in-process. The original installed-command test and
production migration passed earlier; the later launcher-policy failure remains
an environmental validation limitation, not a passing retest.

The preceding report at `output/phase6-rollout/final-publication/report/` already
rendered and verified successfully, but predates the coverage-floor/historical
exhaustion-label corrections. Prefer the refreshed `resumable-publication` report.
The earlier `partial-preview` is historical evidence, not final output.

## Acceptance and closeout

1. Observe the unattended job's terminal result. If it fails, diagnose/fix the
   actual problem and resume its saved stage. Do not infer completion from files
   that may belong to an earlier/incomplete export.
2. On success, review `reconciliation.json`, inventory and report. Confirm all nine
   boards (three entity kinds x losses/profits/volume), 15 participant tables,
   18 complete table crops, 82 current assets, common cutoff, unique/present stable
   inventory paths, and static table images. The verification script checks cell
   bounds, scroll dimensions, PNG dimensions, fonts, export source matches and
   commodity-only cards. Visually open representative final volume, coverage and
   explanation PNGs as well. No dedicated equipment visual or pending listings.
3. Expected: 24 shared commodity items/cards; equipment CSVs match **69,831 sale
   rows / 89,770 stat rows**. Volume boards have ten rows per entity kind; six
   profit/loss boards are honestly empty under unverified settlement/basis evidence.
4. Reconcile the cutoff's coverage: 959,457 stored window trades; 503,683 legacy
   rows lack exact source money; 14,256 resolved entities. Known global source
   value **18,825,748.6200000000862691** is partial window volume. Unassigned sides
   **1,007,433**, including 1,007,366 missing-reference/missing-money sides and
   67 unsupported-party sides; known unassigned value **4,418.4129999999999517**.
   Attributed source sales **18,824,376.11800000008626910** are both unknown-basis
   and unknown-fee value; those flags overlap, so do not add them. Unsupported
   P&L is unavailable, not zero. API exhaustion does not establish nonmarket
   inventory provenance, historical fees, gross/net meaning, or equipment lineage.
5. Update the implementation log/plan with actual final publication status and
   paths, without rewriting prior phase history. Report backup path, import and
   request counts, dates reached, output links, tests and residual coverage limits.
   Do not delete backups, snapshots or old aggregates as cleanup. Do not claim
   that every retained legacy row was enriched.

## Durable resume for future interrupted imports

This is available and tested; do not invoke it just to repeat the completed run:

```powershell
.venv\Scripts\python -m warera_quant.cli --sync --resync-market --history-scope all --resume-market --market-db data/warera_market.sqlite3
```

For an unattended future full run, launch `.venv\Scripts\python.exe -u
scripts/market_rollout.py --job-dir output/<new-job>` hidden with logs. It executes
full resync with durable checkpoints, normal catch-up, a consistent snapshot,
report and verification; re-run the same job after process death. A completed job
does not start another import. This is not an installed Windows service or an
automatic reboot restart. The user requested resumability and a next-agent handoff,
not continuous interactive agent monitoring.

The checkpoint stores the opaque continuation, fixed anchor, previous timestamp
boundary, stream/version/page-size and committed page count as scalar SQLite
metadata. It commits with the page. A rejected/expired cursor fails visibly and
remains saved; long-term cursor validity is unknown. Do not forge/decode cursors,
silently fall back to the head, clear checkpoints via ad hoc SQL, or reset the DB.
An explicitly chosen full resync without `--resume-market` starts fresh pagination
and retains existing facts. Four bounded live requests on temporary databases
proved two pages per stream can resume across hard exits without duplicate rows.
Fixtures additionally cover mid-page rollback and restart during catch-up.
