# Participant table redesign: execution prompts

Status: prepared, not started. Read the [approval plan](participant-table-redesign-plan.md)
and AGENTS.md first. The user must approve the plan before these prompts are run.
Preserve existing uncommitted changes. Use the existing `.venv`. Keep calculations,
API parsing, database access and rendering within their established layers.

## Prompt A — Shared table cleanup and data labels

Implement requests 1–9 through shared rendering for user, MU and country. Remove
Activity Comparison's `Observed:` footer; remove participant loss/profit and
basis/fees/source-coverage sections; remove participant method footers and the
requested P&L, Result and Coverage columns. Preserve underlying records and CSV
diagnostics. Keep turnover and its corresponding buy/sell breakdown only.

The user selected option 1: show every item category separately in the breakdown.
Render the complete existing `categories` collection per entity/side, ordered by
value, with units, monetary value, share and trade count. Aggregate trades of the
same commodity item; retain equipment variants by full stats and condition.
Keep condition in the source category signature and exports, but remove it from
all displayed equipment labels and column headers: no `condition 100/100` or other
condition text. The user states only full-condition equipment can be sold.
Remove Other categories entirely; do not add Remaining items or Mixed items rows.
Top-three summaries may remain in the compact leaderboard cells, but all categories
must appear in the companion breakdown. Preserve the top-ten entity selection.
Distinguish missing and partially known quantities
and money inside their cells after removing Coverage. Do not convert unknowns to
zero or compare unlike unit totals. Format fractions as readable percentages.

Inspect the stray participant coverage block inside `_flip_table_html` and repair
that affected path as necessary. Keep unrelated tables intact. Update focused
tests, README and current-run inventory expectations, handling obsolete generated
participant assets narrowly. Deliver changed files, verification results and
stable renderer/read-model contracts for the identity phase.

## Prompt B — Official user, MU and country identities

After A, verify the current first-party APIs and game rendering for official
names, user avatars, MU emblems and country flags. Begin with the candidate
endpoints documented in the plan, and record actual access/response/image findings.
Use bounded representative lookups; do not run unrelated historical collection.

Also verify official equipment icon mappings and tier appearance. The user's
expected tiers 1–6 are white, green, blue, purple, red and gold, with boots4 and
gloves4 purple. Confirm this against current official assets/frontend; do not
guess filenames or merely recolor unrelated artwork. Determine whether the game
uses tier-specific imagery or shared artwork with styled frames. Supply cached
icons and verified display metadata for equipment in summaries and breakdowns.

Implement reusable normalized identity records through warera_api -> sync ->
market_store -> market_data -> report. Only api_client handles low-level HTTP;
only market_store handles SQLite. Use additive migration if needed. Cache official
assets locally and normalize their metadata; do not persist raw API JSON. Support
bounded refresh, existing-name preservation on transient failures, and offline
report generation. Resolve every displayed identity for every entity kind. Supply
readable fallbacks when upstream profiles or images cannot be retrieved. Never
infer economic account ownership from current membership.

Add meaningful API/cache/migration/offline integration checks. Deliver verified
field contracts, game visual references, working local assets, rendering data
contracts and a clear list of any remaining upstream limitations.

## Prompt C — Senior UI/UX engineer

Start only after A and B are complete, as requested by the user. Work alongside
the senior illustrator with separate review deliverables. Review the actual
cleaned report and PNGs using verified entity assets. Design a hierarchy that lets
readers immediately see who leads, by how much, and what each account buys/sells.

Specify layout, type sizes, numeric alignment/precision, identity cell treatment,
buy/sell grouping, long equipment-stat handling, percentages, empty states and
missing-data labels. Favor clear entity names and turnover over raw identifiers.
Propose concrete changes against the existing renderer; avoid a second renderer.
Coordinate table-level colors/assets with the illustrator. Deliver implementation
specifications and a review checklist to the integration owner, without competing
edits to shared files unless ownership is explicitly assigned.

All tables must be complete, intrinsically sized static PNGs captured from table
elements. No hover information, clipping, horizontal scrolling or tiny text to
force a fixed width. Show every item category, including equipment variants with
full stats; accommodate longer breakdowns without hiding rows. Omit condition
text, use official equipment icons with verified tier treatment, and retain
readable item/tier labels so the meaning does not depend on color alone.

## Prompt D — Senior illustrator

Start after A and B, alongside C. Review verified in-game identity references and
the current table PNGs. Define a cohesive visual treatment for authentic user
avatars, MU emblems and country flags: sizing, framing, spacing and alignment.
Use the actual official assets so entities remain recognizable. Do not invent
replacement identities, badges or decorative art that competes with the data.

Recommend restrained rank emphasis, buy/sell cues and color. Equipment must use
the official in-game icon and verified tier treatment, including the expected
purple boots4/gloves4. Use the asset mappings from B; match game artwork and frames
without inventing replacements. Do not add condition text. Ensure labels communicate without color and
that imagery is sharp at publication size. Prefer existing assets and CSS/vector
treatment for table graphics. If bitmap generation becomes concretely useful,
explain its purpose and follow the imagegen skill; it must not replace official
entity imagery.

Deliver visual specifications/assets with source provenance and a before/after
review brief. Coordinate with C; leave shared renderer integration to one owner.

## Prompt E — Integrate, regenerate and review

Integrate the two specialist deliverables into the shared renderer after A–D.
Regenerate from the existing SQLite data with a fixed cutoff. Avoid unrelated
sync or history changes. Confirm cached images load and decode before screenshot
capture and that offline publication succeeds.

Check all three turnover tables and their retained breakdowns plus Activity
Comparison. Confirm removed sections/columns/footers are absent from HTML, PNGs
and the current-run inventory. Check full table bounds, font legibility, long
names, stats, percentages, correct identities and missingness labels. Verify cases
with equipment tier icons (including boots4/gloves4), offline image loading and
absence of condition text from displayed tables while preserving source data.
Verify cases
with more than three categories: every category appears exactly once per
entity/side, totals reconcile, and no residual aggregate or synthetic N/A quantity
row remains. Genuinely unavailable quantities must remain honestly labeled.
Keep precise CSVs and accounting data intact. Inspect actual final PNGs, not only
HTML assertions. Run affected tests using `.venv` and record results. Deliver
links to final report/assets and a concise summary of changes and real limitations.
