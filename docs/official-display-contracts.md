# Official identity and equipment display contracts

Verified and implemented 2026-09-24, following phase A. Existing uncommitted work
was preserved. No market transaction API, historical pagination or collection ran.
This completes prompt B; the specialist design phase is separate.

## Evidence and actual access

[Machine-readable findings](identity-evidence/verified-contracts.json) contain
observed display scalars, all 30 displayed IDs and resolved names, image URLs,
normalized content hashes/dimensions, observation times and capture dimensions.
These are selected field observations, not persisted raw API responses.

| Official source | Actual finding |
| --- | --- |
| [Swagger initialization](https://api2.warera.io/docs/swagger-ui-init.js) | HTTP 200 with browser User-Agent; initial Python default User-Agent received 403. Documents input schemas and POST operations, but provides no profile response schema. |
| [Public app](https://app.warera.io) | HTTP 200; build `d88vhlzaJYP486NJ15SZf`. |
| [Shared frontend](https://app.warera.io/_next/static/chunks/pages/_app-a1535bfe9f68148d.js) | HTTP 200; exact content SHA-256 in ledger and equipment manifest. |
| `user.getUserLite` | Authenticated GET with `input={"userId":ID}` succeeded. `result.data` object, `_id`, `username`, `avatarUrl`, optional `animatedAvatarUrl`. |
| `mu.getById` | Authenticated GET with `input={"muId":ID}` succeeded. `result.data` object, `_id`, `name`, `avatarUrl`, optional `animatedAvatarUrl`. |
| `country.getCountryById` | Authenticated GET with `input={"countryId":ID}` succeeded. `result.data` object, `_id`, `name`, `code`; no flag URL supplied. |
| `country.getAllCountries` | One authenticated GET succeeded; array of 180 countries. Confirms the name/code contract; refresh uses individual requested IDs, not repeated full catalogs. |
| `gameConfig.getGameConfig` | Authenticated GET succeeded; `items[code]` supplies `type`, `code`, `rarity`, `iconImg`, `skinSlot` and stat ranges. Only normalized equipment display fields are retained. |

Representative calls used Ramoncin, Tercio de la Dama and Bolivia, starting with
an already stored user ID. That user's current MU provided one representative
profile ID for contract investigation only. Membership never enters economic
attribution, history, rankings or display-population selection.

Subsequently refreshed the existing publication's complete 30-entity volume
population: ten users, ten MUs, ten countries. Cutoff:
`2026-09-24T00:52:32.648353+00:00`. All 30 names and all 30 images succeeded.
The actual recomputed read model at that cutoff also resolves all 30 images.

The population refresh made 30 profile attempts, one equipment-config request,
and 35 image attempts (30 profile images and five shared equipment images).
Eleven images initially failed *local validation*, not upstream retrieval: ten
flags use SVG `mask`, and one API-provided avatar uses `cdn.discordapp.com/avatars/`.
After supporting those observed contracts, an image-only retry of those eleven
succeeded. Its eleven deferred profile entries reflect `max_profiles=0`, not
failed profile requests. Two additional flag downloads diagnosed the SVG masks.
The five shipped equipment images were also downloaded independently for the
checked-in asset bundle. All successful image downloads enforce HTTP 200.

## Identity and game rendering

`warera_api.get_identity(kind, id)` validates response ID and a nonempty official
name, returning only `DisplayIdentity(entity_kind, entity_id, name, image_url,
country_code)`. Unknown shapes fail without replacing a good profile. No raw
response dictionary, membership, account balance or unrelated profile data crosses
the boundary. The parser accepts the project's existing `result.data.json` wrapper
as well as the directly observed `result.data` form.

Frontend module 25562 defines `https://media.warera.io/images`. Module 2683 reads
the country's **code** and constructs `/flags/{code}.svg?v=16`; these paths were
verified by actual downloads, not inferred from country names. Flags are official
32×24 SVGs. The frontend uses the user's `username` and avatar, and the MU's `name`
and avatar. Premium animation is optional in the game; reports use `avatarUrl`,
decode its first frame, and cache a PNG. Names remain text, escaped on output.

The download client never sends the API key to image hosts and rejects redirects.
The observed asset origins are `media.warera.io` and the specific avatar path on
Discord's CDN. Other origins get a readable fallback pending verification.

Actual table references: [users](identity-evidence/user-turnover.png),
[military units](identity-evidence/mu-turnover.png),
[countries](identity-evidence/country-turnover.png). These are report captures with
actual official assets, not screenshots of a logged-in game session.

## Equipment: verified mapping differs from the expected sequence

The official frontend uses the same artwork for all six tiers of each slot:
`helmet.png`, `chest.png`, `pants.png`, `boots.png`, `gloves.png` under
`https://media.warera.io/images/itemsv2/`, with `?v=1`. All five downloaded and
decoded at 256×256. There are **30 verified codes, five images, six styled frames**.
No tier-specific filename was guessed; no artwork was recolored.

| Tier | Official rarity | Default frontend color scheme |
| --- | --- | --- |
| 1 | common | gray (neutral, not a literal white frame) |
| 2 | uncommon | emerald / green |
| 3 | rare | blue |
| 4 | epic | purple |
| 5 | legendary | yellow / gold |
| 6 | mythic | red |

Thus **boots4 and gloves4 are purple**, but the proposed red-5/gold-6 order is
reversed relative to the verified game. Module 78492 defines rarity numbers,
16125 maps rarity to color (colorblind mode changes epic to pink), 77622 defines
item mappings, 55860 selects artwork, 68923 selects rarity/skins, and 66656 defines
the frame. The frame uses the scheme's 825-color border and a 45-degree gradient
from 825 to 950, with a heavier bottom border. Exact resolved hexadecimal colors
are in the bundled manifest and evidence ledger.

[Equipment reference PNG](identity-evidence/equipment-reference.png) and
[offline HTML](identity-evidence/equipment-reference.html) reconstruct these
verified default frames and artwork. They are explicitly reference reconstructions,
not game screenshots or evidence of any item's rolled stats. The report always
uses recorded category stats and keeps condition out of displayed labels.

## Layered implementation and schema 6

- `api_client`: authenticated JSON transport and separate bounded public-byte GET.
- `warera_api`: endpoint names, shape validation, normalized profile records and
  equipment mappings checked against the verified frontend manifest.
- `sync`: explicit population, TTL, request budgets, deduplicated asset downloads,
  normalization and content-addressed atomic file replacement.
- `market_store`: all SQLite access and additive v5→v6 migration. Adds profile
  `image_url`, `country_code`, `image_cache_url`; normalized `display_assets` and
  `equipment_display` tables. No history rewrite or raw JSON storage.
- `market_data`: offline joins, complete readable identity records, category
  display metadata for both full categories and compact top-three summaries.
- `report`: shared escaped identity/item helpers, local asset output, explicit
  image decoding before PNG capture. No network or database access.

`display_assets` retains source URL, local path, SHA-256, MIME, dimensions, byte
count, successful observation time, latest attempt time and status. Images are
limited to 5 MB per response and 4096×4096 decoded dimensions. SVG validation
allows only passive drawing elements and local references. Raster files are
decoded/re-encoded to PNG. One file is reused for identical content.

Refresh defaults: at most 30 profiles, 35 images and one equipment-config request;
24-hour attempt TTL, hard profile/image limit 100 each. HTTP read retries retain
the existing client's maximum four attempts. Profile lookup failures preserve
the successful name and observation time. Failed image refreshes preserve the
successful image, including when the upstream URL changes. Failed lookups back
off; missing/corrupt local images are detected and can be re-fetched. Out-of-order
profile/asset observations cannot overwrite newer records. Limits are explicit
in the returned attempted/deferred/error summary.

The production database was upgraded with an online backup:
`data/warera_market.sqlite3.backup-20260924T162001758538Z`. Only the display cache
was refreshed; historical facts and attribution were not changed.

## Rendering data contract and offline use

`rankings[kind]['volume']` remains the sole displayed population for both tables.
`displayed_identity_keys()` deduplicates exactly this set. Every aggregate/ranking
row receives `identity`, with `display_name`, kind/id, dated profile status,
`image_src`, image provenance/hash/dimensions/date/status and country code.
Unavailable names use `User ID`, `Military unit ID`, or `Country ID`; unavailable
images use readable kind labels. A retained older image is marked `stale` in the
read model. Display enrichment never changes rank, totals, actors or ownership.

Every category/top summary receives `display` metadata where verified: code,
rarity, tier, color scheme, frame colors, source URL, observation date and local
image source. Unverified future codes remain readable text, without guessed art.
Bundled equipment files allow offline icons even before a live cache refresh.

Read-model image sources are validated data URIs. `write_outputs` materializes each
distinct image once in the report's `display-assets/` directory, using relative
references so the report folder is portable. Rendering-only metadata and image
bytes are excluded from accounting CSVs. Local database cache paths are absolute;
moving only the database to another machine requires restoring assets or a refresh.

```powershell
.venv\Scripts\warera-marketguide --refresh-identities --market-db data/warera_market.sqlite3 --as-of 2026-09-24T00:52:32.648353+00:00
.venv\Scripts\warera-marketguide --from-db --market-db data/warera_market.sqlite3 --as-of 2026-09-24T00:52:32.648353+00:00 --output output/market
```

Refresh is standalone and never calls market-history endpoints. `--from-db` stays
offline and cannot be combined with refresh. Optional bounds:
`--identity-limit`, `--asset-limit`, `--identity-max-age-hours` (zero forces age
refresh). A lower budget leaves remaining identities as cached/readable fallbacks;
the result explicitly counts deferred work.

## Validation and remaining limitations

The full test directory passed **591 tests** before the final metadata/sanitizer
refinements; final verification is recorded in the implementation log. Checks
cover all three profile kinds, malformed/mismatched responses, request limits,
TTL/backoff, last-good names/images, changed image URLs, corruption, forbidden
origins, credential-free downloads, migration rollback and idempotence, offline
joins, preserved attribution, all 30 equipment mappings and real-browser decoding
with network requests blocked. Existing complete-table PNG tests also pass.

At the actual published cutoff, the offline HTML decoded **1,956 image elements**
from 35 local files. Six complete table-only PNGs were captured at 1× CSS scale
under `output/identity-verification/`; the ledger records their dimensions.
The user breakdown contains all 1,255 categories and is 89,619 pixels tall.
MU/country breakdowns contain 88/96 categories. No rows were truncated or hidden.
The full source is `participants.source.html`; this is a functional phase-B
reference, not the subsequent specialist design/publication pass.

Remaining upstream/fidelity limits:

1. Swagger has no response schemas; these are dated observed contracts, not a
   guarantee against future changes. Authenticated access was verified; anonymous
   access, deleted/private profile behavior and alternative image hosts were not.
2. Profile names/images describe current profiles, not the identity's appearance
   at trade time. Current membership never supplies economic ownership.
3. Static reports deliberately omit animation, premium name effects, viewer-specific
   relationship outlines, equipped cosmetic skins, colorblind mode and animated
   legendary/mythic sheen. Base equipment artwork and default rarity frames are verified.
4. Unknown future equipment mappings fail closed during refresh and preserve the
   last verified cache. Missing profiles/images get readable fallbacks. No current
   upstream image failure remains in the displayed 30-entity population.
5. The game currently contradicts the expected tier-5/tier-6 colors; the renderer
   follows the verified official mapping rather than silently swapping them.


## Item icon expansion (2026-09-25)

The portable `items.json` bundle now contains all 60 official catalog codes using
35 decoded PNGs, including knife, gun (pistol), rifle, sniper, tank and jet (plane).
The API boundary normalizes icon filenames using the verified frontend rule:
`iconImg` when supplied, otherwise the item code plus `.png`. Refresh the bundle
with `.venv/Scripts/python scripts/download_item_icons.py`.

User, MU and country summaries and breakdowns now display item icons with the
existing rarity frames. Equipment stat values follow the image separated by `/`;
names, stat keys, the `stats:` prefix and tier/rarity captions are omitted.
Unknown codes retain a readable text fallback. Accounting exports retain their
full descriptions. The participant window paragraph and Current Order Book
footer are omitted from the published report.
