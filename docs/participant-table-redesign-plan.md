# Participant table redesign plan

Date: 2026-09-24. Status: proposed; awaiting user approval before implementation.

Confirmed revision: the user selected option 1, showing every item category
separately in the buy/sell breakdown. This replaces the proposed residual group.

Additional confirmed presentation requirements: omit equipment condition text
(including `condition 100/100`) because the user states only full-condition items
can be sold. Use official in-game equipment icons with the correct tier appearance.
User-supplied color sequence to verify against game assets: white, green, blue,
purple, red, gold for tiers 1–6; boots4 and gloves4 are expected to be purple.

This document covers the user's eleven requested changes. Only planning documents
are changed in this phase. Existing uncommitted work must be preserved. For the
published presentation, this request supersedes the earlier requirement to show
nine leaderboards and coverage tables. Historical collection work remains separate.

## Findings from the current implementation

- `report.py::_participant_html` renders losses, profits, turnover, coverage, and
  buy/sell explanations through one shared path for users, MUs, and countries.
- Its shared table helper inserts the long method/window/precision footer into
  every participant table. The generic report annotation separately inserts the
  `Observed:` footer into Activity Comparison.
- Turnover currently duplicates the raw entity ID in both Entity and ID when no
  name is cached. Existing PNGs confirm weak numeric hierarchy, long repeated IDs,
  decimal fractions for shares, and unusually tall explanation images.
- `metrics.py::calculate_participant_rankings` sorts categories by monetary value
  independently for each entity and each side. The first three become the named
  rows; all remaining categories become `Other categories`.
- `report.py` explicitly supplies `None` for that aggregate's quantity, producing
  `N/A`. Adding quantities of different items would not be a meaningful comparable
  unit total. This is not evidence that those trades have missing quantities.
- Equipment categories distinguish item code, full skill values, and condition.
  Two rows with the same item code can therefore be different categories.
- A separate issue needs care when removing Coverage: individual category totals
  currently add known quantities and track missing quantities separately. Without
  that column, a partial quantity could look complete. Keep the distinction in
  the quantity cell itself.
- `market_data.py` joins cached names without network calls. The current entity
  cache has name/status/timestamps, but no implemented avatar/emblem/flag pipeline.

## Requested changes and acceptance

| Request | Proposed implementation | Acceptance |
| --- | --- | --- |
| 1 | Exempt Activity Comparison from generic `Observed:` footer injection using an explicit table identity | Footer absent in HTML source and table PNG; unrelated table footers unaffected |
| 2, 3 | Stop rendering loss and profit sections | No user, MU, or country loss/profit sections or current-run PNG inventory entries |
| 4 | Remove long participant table footers, including turnover's method/precision paragraph | No repeated method footer in remaining participant tables |
| 5 | Remove Matched net P&L, Result, and Basis coverage (source) from turnover | Same concise column contract for all three entity kinds |
| 6 | Remove basis, fees and source coverage sections | No corresponding sections or current-run asset entries |
| 7 | Show every item category separately, with value, units, share and trade count | No Other categories, Remaining items or Mixed items aggregate rows; genuine missing data stays explicit |
| 8 | Remove Coverage from buy/sell detail tables | No Coverage column; incomplete values not presented as complete |
| 9 | Apply shared changes to users, MUs and countries | Parameterized verification of all three kinds |
| 10 | Retrieve official display identities and images through verified APIs/assets | Names and official avatars/emblems/flags appear wherever an entity is shown |
| 11 | After functional work, engage senior UI/UX engineer and senior illustrator agents | Coordinated visual design implemented and reviewed on actual exported PNGs |

Removing sections means removing them from the published report and current-run
asset inventory. Handle obsolete generated participant PNGs narrowly so old files
are not mistaken for new deliverables; preserve unrelated files. Underlying history,
costing, diagnostics and precise CSV exports remain available. This is a proposed
presentation change, not a request to delete stored facts or accounting capability.

## Proposed presentation

Keep one monetary-turnover leaderboard and one companion buy/sell breakdown per
entity kind, plus the existing Activity Comparison table. Preserve the existing
top-ten selection; "all entities" means faithful identity presentation for every
displayed user, MU and country, not an unrequested expansion to every game account.

Turnover columns: Rank, Entity, Turnover (BTC), Mostly bought, Mostly sold. Combine
the official image and name into Entity. Retain the full ID as subordinate text
when needed for disambiguation/fallback and in CSVs, avoiding two identical ID cells.
Use Users, Military Units and Countries as readable section labels.

Breakdown columns: Entity, Buy/Sell, Item / stats, Value (BTC), Units,
Share of buys/sales, Trades. Group rows by entity and side, avoid repeated identity
blocks where possible, and keep equipment stats readable. Use friendly
item labels from existing verified mappings; preserve distinct equipment variants.
Remove condition text from all displayed equipment labels, including compact
summaries and breakdown rows. Preserve source condition in storage and precise
exports; this display request does not change the existing category signature.

Show the official item icon wherever equipment is displayed, with the game's
verified tier colors, framing and artwork. For example, boots4 and gloves4 should
use the corresponding tier-4 boot/glove appearance, expected to be purple. Verify
whether the game uses tier-specific files or shared artwork with a styled frame,
then reproduce that treatment. Use readable item/tier labels alongside icons so
color is not the only way to identify a tier. Do not infer actual stats from color:
show the recorded stats of each category. Cache verified assets for offline export.

Remove `Other categories` entirely. Render every category from the existing
`categories` read-model collection for each displayed entity and buy/sell side,
ordered by monetary value. Aggregate trades of the same commodity item into one
row; keep equipment variants distinct by full stats and condition. Every row
shows its own value, units, side share and trade count. Do not introduce Remaining
items or Mixed items rows or omit categories after the first three. The compact
Mostly bought/Mostly sold leaderboard cells can retain their top-three summary;
the companion breakdown must contain every category. The top-ten entity selection
is unchanged. Longer breakdown tables are accepted; readability must not depend
on truncation, hidden rows or smaller text.
For genuinely unavailable quantities, use `Unknown`; for partial known totals,
use `X known` with a concise indication of missing quantities in the same cell.
Apply equivalent honest presentation when monetary values are incomplete.

Display shares as percentages, monetary values with grouping and consistent
precision, and numerical columns right-aligned with tabular digits. Give turnover
the strongest numerical emphasis. Buy and sell labels must be understandable
without color. Show the 7-day UTC period and money basis once in report context;
retain relevant BTC/time-window labels in table headers so standalone PNGs remain
understandable. Do not recreate the removed explanatory footer elsewhere as a
large repeated block.

## Implementation phases

1. **Shared rendering cleanup and semantics.** Implement removals, complete per-item
   breakdowns, meaningful empty states, and quantity/money missingness presentation.
   Select breakdown entities from turnover rankings only. Update rendering tests,
   export expectations and README. Inspect the duplicate coverage-rendering block
   currently inside `_flip_table_html`: it references participant-local variables
   and must not survive as another path that emits removed content or breaks
   generation. Repair only the directly affected report path, with a regression
   check if that path is exercised.
2. **Official identity verification and integration.** Verify current official
   endpoint access, response fields, image URLs and in-game rendering, including
   equipment icon mappings and tier styling from official game assets. Implement
   normalized profile retrieval, cache storage, bounded refresh and offline joins.
   Resolve the displayed population, including every identity retained in detail
   tables. Fetch assets before rendering; use cached local assets during export.
   Preserve last successful profiles on transient failures. A missing/deleted or
   inaccessible profile must get an honest readable fallback, never a fabricated
   official image or name. Record any actual fidelity limitation in the handoff.
3. **Senior specialist design pass, after phases 1–2.** Delegate distinct briefs to
   a senior UI/UX engineer and senior illustrator. They may review independently
   with separate deliverables; integrate their recommendations through one renderer
   owner to avoid conflicting edits. Match verified game identity treatment while
   emphasizing rank, entity, turnover and buy/sell patterns. Use official identity
   assets; decorative generated images are not required for this table redesign.
4. **Publication and review.** Regenerate against the existing database at a fixed
   UTC cutoff, check the asset inventory and visually inspect all remaining table
   PNGs. Record tests, representative images and any unavailable identity assets.

## API and architecture plan

The repository's September 22 evidence lists `user.getUserLite(userId)`,
`mu.getById(muId)`, and `country.getCountryById(countryId)`; frontend evidence also
mentions `country.getAllCountries`. These are candidates, not verified image
contracts. The [official documentation](https://api2.warera.io/docs/) was opened
during planning but exposed no response schema through the text browser. Verify
the current official schema/frontend and bounded samples during phase 2 before
choosing fields; an API supplies data/assets, while the frontend defines styling.

- `api_client.py`: low-level HTTP, including any reusable binary asset retrieval.
- `warera_api.py`: sole owner of endpoint names, response parsing and normalized
  user/MU/country display records and any API-supplied item asset mappings; no raw
  response JSON passed downstream.
- `sync.py`: bounded profile/asset refresh orchestration, retries and cache policy;
  use existing configuration/authentication without exposing credentials.
- `market_store.py`: normalized identity metadata, cache queries and any additive
  migration; the only SQLite access layer. No database reset or history rewrite.
- `market_data.py`: offline joins supplying complete display records to reports.
- `metrics.py`: reusable category aggregation and missingness calculations only;
  use the existing complete category collection without adding residual counts.
- `report.py`: shared identity rendering and table styling from supplied data;
  no API calls or database imports. Await local image decoding before capture.
- `cli.py`: orchestration of an explicit refresh/report flow; `--from-db` remains
  offline. Record asset/name observation dates without changing trade attribution.

Cache image files locally with normalized metadata references, bounded downloads,
and graceful missing-image handling. Verify URLs from actual official responses;
do not synthesize guessed avatar paths. Current profile membership must never
reassign historical economic ownership.

## Verification and completion criteria

Use the existing `.venv`. Run focused participant/report/export tests, plus API,
sync, storage/migration and read-model tests affected by identity integration.
Cover all three entity kinds, missing profiles/assets, stale cache, long names,
equipment variants, more than three categories per side, missing quantities and
offline use. Verify every category appears exactly once per entity/side, totals
reconcile to the read model, and no synthetic residual aggregate is rendered.
Check that displayed equipment labels contain no condition text, while source
condition remains intact. Verify official tier icons in summaries and breakdowns,
including boots4/gloves4 and representative other tiers, with offline decoding.
Preserve escaping, source precision, CSV formula protection and deterministic ranks.

Every published table remains a static PNG of the complete table element only:
no section headings, surrounding whitespace, clipping, scrollbars or interactive
dependencies. Check intrinsic sizing, legible fonts, full stats, decoded images
and color-independent meaning. Compare before/after images at realistic reading
size. Broader tests follow if the shared renderer or cache migration affects them.

No implementation, API profile download, database migration, report regeneration
or specialist delegation starts until the user approves this plan. This checkpoint
comes directly from the user's request to plan/document/confirm before moving on.

Execution briefs: [separate work prompts](participant-table-redesign-prompts.md).
