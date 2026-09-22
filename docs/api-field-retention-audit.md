# API field retention audit and recovery requirements

Audit date: 2026-09-22. This is the implementation inventory for preserving data
from every API used by normal market synchronization, followed by a full
re-download of available history. No storage migration or backfill has been run
as part of this audit. Preserve the existing database during implementation.

For the current phased implementation design, migration/resync command contracts,
realized P&L semantics, and tests, read the
[implementation plan](market-participants-implementation-plan.md) and
[copy-paste prompt library](market-participants-implementation-prompts.md).
Those documents supersede earlier implementation recommendations here; this
audit remains the source-field evidence inventory. The user chose realized
trading profit/loss where cost is known, not net cash flow.

## Scope and evidence

### Agreed implementation scope

Expand persistence for `tradingOrder.getTopOrders` and
`transaction.getPaginatedTransactions`, and add
equipment sales (`transactionType=itemMarket`) to commodity trades
(`transactionType=trading`). Both use `transaction.getPaginatedTransactions`.
Equipment collection must cover equipment independently of the commodity-price
item list. Preserve every returned order and transaction business field,
including nested equipment details and all participant fields, in normalized
storage. Exclude `__v` and the pagination envelope field `nextCursor`.

"Keep everything" now applies to order and transaction facts from those two
endpoints, for both market transaction types. Keep individual orders as well as
derived aggregates. Store nested equipment facts in columns and related tables,
not raw dictionaries/JSON or empty container records. Equivalent representations
of retained facts need not be duplicated. Full game-configuration/dictionary
archiving is outside this implementation scope; existing price and production
point persistence remains. Unrelated transaction types such as wages, donations,
or dismantling remain outside this market scope.

`__v` is intentionally excluded: it matches Mongoose's default internal document
version key (https://mongoosejs.com/docs/guide.html#versionKey); that convention
does not independently establish WarEra's backend implementation. `nextCursor`
is temporary pagination state, not a transaction fact, and is not persisted.
Use durable transaction progress markers for restart support instead.

Implement and validate expanded persistence before downloading history. Then
backfill all available commodity trades and equipment sales and refresh current
orders, with existing price/configuration collection unchanged. This is the agreed target, not a description
of what the current code already implements.

Normal sync calls exactly four endpoints. An optional CLI `--api-endpoint`
path accepts arbitrary endpoints, constructs reports, and does not persist their
responses; it has no fixed response schema to inventory.

Evidence: live responses (100 commodity transactions, 100 unfiltered transactions,
six steel orders, prices, and game configuration), application source, and the
public community client declarations. Samples do not prove exhaustive optional
field coverage. The official Swagger definitions for all four endpoints have
only `200: {description: ""}` and no response schema.

Sources:

- https://api2.warera.io/docs/
- https://api2.warera.io/docs/swagger-ui-init.js
- https://github.com/WarEraProjects/TRPC/blob/main/src/api/Responses.d.ts
- https://github.com/WarEraProjects/TRPC/blob/main/src/CustomEndpoints/TradingOrder.ts
- `src/warera_quant/warera_api.py`: endpoint calls and parsing
- `src/warera_quant/sync.py`: collection scope and pagination
- `src/warera_quant/market_store.py`: persistence and normalization

## transaction.getPaginatedTransactions

| Response field | Current disposition | Evidence |
| --- | --- | --- |
| `_id` | Stored as `id` | Live |
| `money` | Stored | Live |
| `quantity` | Stored | Live |
| `itemCode` | **Stored** as `item_code` | Live/code |
| `transactionType` | Stored as `transaction_type` | Live |
| `createdAt` | Stored as normalized UTC text and epoch seconds | Live |
| `sellerId` | **Discarded** | Live |
| `buyerId` | **Discarded** | Live |
| `sellerCountryId` | **Discarded** | Live commodity trades |
| `buyerCountryId` | **Discarded** | Live commodity trades |
| `sellerMuId` | **Discarded if received** | Live donation; community transaction schema |
| `buyerMuId` | **Discarded** | Live commodity trades |
| `offerCreatedAt` | **Discarded** | Live |
| `updatedAt` | **Discarded** | Live |
| `__v` | Intentionally excluded from persistence | Live/user decision |
| `nextCursor` | Temporary in-memory pagination state; intentionally not persisted | Live/code/user decision |

Nested equipment fields are a separate collection expansion, **not evidence of
quantity being discarded from downloaded commodity trades**. The diagnostic
unfiltered sample and community schema show `item._id`, `item.type`, `item.code`,
`item.skills.*`, `item.state`, `item.maxState`, `item.quantity`, and
`item.lastAcquisitionAt`. These must be retained when present in equipment sales.
Two actual `itemMarket` transactions were subsequently checked on 2026-09-22.
They confirm `_id`, `money`, `itemCode`, `quantity`, `sellerId`, `buyerId`,
`transactionType`, `item`, `offerCreatedAt`, `createdAt`, `updatedAt`, and `__v`.
Nested `item` contains `_id`, `code`, `skills`, `state`, `maxState`, `quantity`,
`lastAcquisitionAt`, and optional `type` (present for gloves, absent for a gun).
Observed skills were `precision`, `attack`, and `criticalChance`. One completed
sale was one `gloves3` for 14.17, with precision 15 and state/maxState 100/100.
This nested object describes equipment in a completed sale; it is not an active
listing. `offerCreatedAt` records the prior offer creation time, not an active
offer status. Samples remain non-exhaustive for optional fields.

The table covers verified fields, not every possible variant. Seller/buyer party
field names remain unverified; the official `partyId` request filter alone does
not establish response names. Keep all confirmed participant IDs separately:
user and country/MU IDs can coexist. Do not infer citizenship, membership, or
which account owns the funds solely from field coexistence.

Current collection always filters to `transactionType=trading` and a requested
item. Other types are never collected, rather than downloaded and discarded.
Official documented types: `applicationFee`, `trading`, `itemMarket`, `wage`,
`donation`, `articleTip`, `openCase`, `craftItem`, `dismantleItem`, `battleLoot`,
`countryMoneyTransfer`. Equipment sales (`itemMarket`) are now explicitly in the
implementation scope; other types remain outside it. Normalize equipment sales
at the API boundary and handle absent item/quantity/money fields according to
their actual response semantics.

## tradingOrder.getTopOrders

| Response field | Current disposition | Evidence |
| --- | --- | --- |
| `buyOrders[]`, `sellOrders[]` | Side retained as bid/ask; individual entries subsequently aggregated by price | Live/code |
| `_id` | **Discarded** | Live |
| `user` | **Discarded** | Live |
| `mu` | **Discarded if present** | Community top-orders buy-order schema |
| `country` | **Discarded if present**; not confirmed for this endpoint | Community public-orders-by-owner schema, a different endpoint |
| `itemCode` | **Stored** as `item_code` | Live/code |
| `quantity` | Stored as summed quantity per side/price | Live |
| `price` | Stored | Live |
| `offerAt` | **Discarded** | Live |
| `type` | **Stored** as bid/ask side | Live/code |
| `__v` | Intentionally excluded from persistence | Live/user decision |

Zero-price or zero-quantity entries are dropped. Same-price orders are merged,
losing order identity and individual quantities even within a single snapshot.
Observation time is our collection time, not a replacement for `offerAt`.
Only the requested top orders are fetched; deeper orders are not collected.
Other ownership fields must be verified rather than assumed absent.

## itemTrading.getPrices

The observed response is a flat map of 24 item codes to numeric prices. All
included item-price pairs are stored with collection time. No additional business
fields were present in this response. Explicitly excluded items are removed by
sync before persistence and are not synchronized for orders or transactions.
Historical price observations remain separate from executed transaction prices.

## gameConfig.getGameConfig

This section and its field-path appendix are audit reference only, not a request
to implement full configuration persistence. The user narrowed the expansion to
orders and transactions.

Only `items.<code>.productionPoints` for tracked tradable items is persisted,
alongside item code and collection time. The value is overwritten on subsequent
syncs: no configuration history is retained. `isTradable` is read to filter items
but is not stored. Items absent from the included prices map are not persisted.

Item `code` is **stored** as item identity for tracked items.
Verified item field names discarded: `type`, `rarity`, `productionNeeds`, `climates`, `usage`,
`isTradable`, `isConsumable`, `isDeposit`, `flatStats`, `dynamicStats`, `skinSlot`,
`iconImg`, `IconComponent`. Nested values are also discarded. For example,
`items.steel.productionNeeds.iron = 10` is returned but not persisted.

Every non-`items` section is discarded in full. The generated inventory below
records every observed nested field path and value type, including these sections,
without storing raw API JSON. Arrays use `[]` for element paths. This inventory
describes the response on the audit date, not a guaranteed future schema.

## Implementation and full recovery requirements

### Migration strategy

Keep the existing database and make a consistent backup before migration. Add
columns/tables without removing existing history. Commodity transaction backfill
enriches existing records by transaction ID; equipment sales are inserted as a
new stream. Re-downloading all available transaction history is required for
complete enrichment, but deleting the database neither reduces that network cost
nor restores old order/configuration snapshots. Upstream history may be shorter
than locally retained history. Do not delete the database to start fresh.

Pending equipment listings were subsequently removed from the requested scope.
Keep completed `itemMarket` transactions and commodity `getTopOrders` snapshots.
The following listing investigation is historical evidence only, not planned work.
The official Swagger checked on 2026-09-22 exposes
`itemOffer.getById(itemOfferId)` but no equipment-listing enumeration endpoint.
`inventory.fetchCurrentEquipment(userId)` returns equipped items, not market
listings.

The public equipment-market frontend was then inspected:
https://app.warera.io/_next/static/chunks/pages/market/equipments-2d19f68208077040.js
It calls `itemOffer.getItemOffers` with `itemCode`, `userId`, `limit`, and
`minSkills`, and paginates with `nextCursor`. A read-only request with
`itemCode=gun`, `limit=2`, using our existing API key returned HTTP 403:
`API tokens cannot access this endpoint`.

Equipment-listing enumeration is therefore identified but **blocked for the
current API-token authentication**. Do not bypass this restriction or claim the
listing importer can run with the current API key. It requires a supported,
authorized access method. Equipment sales remain accessible through the
transaction endpoint and can be implemented independently. No active-listing
response fields were obtained from the rejected request.

After migration, collect new activity while historical backfill proceeds. Track
backfill completion separately for commodity enrichment and equipment history;
save progress without persisting `nextCursor`. Replay must be idempotent. Label
incomplete historical coverage until every available page has been traversed.
No equipment-listing importer or listing-access investigation is required.

1. Preserve the existing database; migrate it in place or work from a verified
   backup. Do not delete useful history to force a download.
2. Parse and validate source fields in `warera_api.py`; keep HTTP in
   `api_client.py`, database access in `market_store.py`, and orchestration in
   `sync.py`. Store normalized facts, not raw response JSON.
3. Preserve all returned transaction and order business information, including
   participant fields, nested equipment facts, original order entries, source
   timestamps. Exclude `__v` and `nextCursor`. Persist equipment details in related
   tables; do not store raw dictionaries. Preserve returned zero-price/quantity
   order facts while excluding them from executable-depth calculations.
   Derived order-book aggregates can still be built from individual orders.
4. Add detection of unrecognized source fields and a visible diagnostic so
   newly returned fields cannot silently disappear. Define handling for optional
   and nested fields; do not treat a sample as the complete schema.
5. Replace transaction `INSERT OR IGNORE` with enrichment of existing IDs.
   Preserve existing non-null facts when a later response omits optional fields.
6. Track enrichment coverage separately from existing price-history coverage.
   Support resumable full-history backfill; do not stop at ordinary duplicate or
   high-water boundaries. Keep cursors temporary; durable restart progress must
   use other markers and tolerate replay without losing or duplicating facts.
7. After storage changes and validation, download **all available historical
   commodity trades and equipment sales**, not just seven days. Enrich existing
   commodity records and collect equipment sales independently of commodity item
   filters. Re-fetch current orders into expanded storage; retain existing price
   and production-point collection without adding configuration archiving.
   Record counts, errors, completion, and upstream limits. Keep equipment sales
   separate from commodity report calculations and track sync/backfill progress
   independently for each stream.
8. A full re-download cannot recover past order snapshots or old configuration
   values from endpoints that return current state only. Keep existing historical
   aggregates. Never present newly observed values as historical source facts.

The user explicitly wants this inventory retained for implementation and a full
re-download afterwards. This document records that direction; the audit itself
does not implement storage changes or execute the re-download.

## Observed game configuration field paths

`dict` and `list` entries identify containers; descendant paths identify their contents.
Item identity and production points are retained for tracked items as described above;
the remaining configuration fields are discarded. Container paths organize the inventory
and are not additional lost facts.

| Field path | Observed type(s) |
| --- | --- |
| `alliance` | dict |
| `alliance.leaveCooldownDays` | int |
| `alliance.leaveCooldownDisabledUntil` | str |
| `announcement` | dict |
| `announcement.durationHours` | int |
| `announcement.maxLength` | int |
| `announcement.maxPerHour` | int |
| `badge` | dict |
| `badge.alphaTester` | dict |
| `badge.alphaTester.availabilities` | list |
| `badge.alphaTester.availabilities[]` | str |
| `badge.alphaTester.preserveBetweenReset` | bool |
| `badge.alphaTester.reward` | int |
| `badge.babyBoomer` | dict |
| `badge.babyBoomer.availabilities` | list |
| `badge.babyBoomer.availabilities[]` | str |
| `badge.babyBoomer.preserveBetweenReset` | bool |
| `badge.babyBoomer.reward` | int |
| `badge.battleTerrainTop1` | dict |
| `badge.battleTerrainTop1.availabilities` | list |
| `badge.battleTerrainTop1.availabilities[]` | str |
| `badge.battleTerrainTop1.reward` | int |
| `badge.battleTop1` | dict |
| `badge.battleTop1.availabilities` | list |
| `badge.battleTop1.availabilities[]` | str |
| `badge.battleTop1.reward` | int |
| `badge.bugFinder` | dict |
| `badge.bugFinder.availabilities` | list |
| `badge.bugFinder.availabilities[]` | str |
| `badge.bugFinder.preserveBetweenReset` | bool |
| `badge.bugFinder.reward` | int |
| `badge.coffee` | dict |
| `badge.coffee.availabilities` | list |
| `badge.coffee.availabilities[]` | str |
| `badge.coffee.preserveBetweenReset` | bool |
| `badge.coffee.reward` | int |
| `badge.congressMember` | dict |
| `badge.congressMember.availabilities` | list |
| `badge.congressMember.availabilities[]` | str |
| `badge.congressMember.reward` | int |
| `badge.countryPresident` | dict |
| `badge.countryPresident.availabilities` | list |
| `badge.countryPresident.availabilities[]` | str |
| `badge.countryPresident.cooldownDays` | int |
| `badge.countryPresident.reward` | int |
| `badge.countryTournamentWinner` | dict |
| `badge.countryTournamentWinner.availabilities` | list |
| `badge.countryTournamentWinner.availabilities[]` | str |
| `badge.countryTournamentWinner.preserveBetweenReset` | bool |
| `badge.countryTournamentWinner.reward` | int |
| `badge.exploitFinder` | dict |
| `badge.exploitFinder.availabilities` | list |
| `badge.exploitFinder.availabilities[]` | str |
| `badge.exploitFinder.preserveBetweenReset` | bool |
| `badge.exploitFinder.reward` | int |
| `badge.foundingFather` | dict |
| `badge.foundingFather.availabilities` | list |
| `badge.foundingFather.availabilities[]` | str |
| `badge.foundingFather.preserveBetweenReset` | bool |
| `badge.foundingFather.reward` | int |
| `badge.giftPremium` | dict |
| `badge.giftPremium.availabilities` | list |
| `badge.giftPremium.availabilities[]` | str |
| `badge.giftPremium.preserveBetweenReset` | bool |
| `badge.giftPremium.reward` | int |
| `badge.giveawayWinner` | dict |
| `badge.giveawayWinner.availabilities` | list |
| `badge.giveawayWinner.availabilities[]` | str |
| `badge.giveawayWinner.reward` | int |
| `badge.govMember` | dict |
| `badge.govMember.availabilities` | list |
| `badge.govMember.availabilities[]` | str |
| `badge.govMember.cooldownDays` | int |
| `badge.govMember.reward` | int |
| `badge.hardWorker` | dict |
| `badge.hardWorker.availabilities` | list |
| `badge.hardWorker.availabilities[]` | str |
| `badge.hardWorker.reward` | int |
| `badge.muTournamentWinner` | dict |
| `badge.muTournamentWinner.availabilities` | list |
| `badge.muTournamentWinner.availabilities[]` | str |
| `badge.muTournamentWinner.preserveBetweenReset` | bool |
| `badge.muTournamentWinner.reward` | int |
| `badge.popularArticle` | dict |
| `badge.popularArticle.availabilities` | list |
| `badge.popularArticle.availabilities[]` | str |
| `badge.popularArticle.preserveBetweenReset` | bool |
| `badge.popularArticle.reward` | int |
| `badge.popularArticle.uniqueMetadataKey` | str |
| `badge.premium` | dict |
| `badge.premium.availabilities` | list |
| `badge.premium.availabilities[]` | str |
| `badge.premium.preserveBetweenReset` | bool |
| `badge.premium.reward` | int |
| `badge.prestige` | dict |
| `badge.prestige.availabilities` | list |
| `badge.prestige.availabilities[]` | str |
| `badge.prestige.preserveBetweenReset` | bool |
| `badge.prestige.reward` | int |
| `badge.referral` | dict |
| `badge.referral.availabilities` | list |
| `badge.referral.availabilities[]` | str |
| `badge.referral.reward` | int |
| `badge.resetSurvivor` | dict |
| `badge.resetSurvivor.availabilities` | list |
| `badge.resetSurvivor.availabilities[]` | str |
| `badge.resetSurvivor.preserveBetweenReset` | bool |
| `badge.resetSurvivor.reward` | int |
| `badge.roundTerrainTop1` | dict |
| `badge.roundTerrainTop1.availabilities` | list |
| `badge.roundTerrainTop1.availabilities[]` | str |
| `badge.roundTerrainTop1.reward` | int |
| `badge.roundTop1` | dict |
| `badge.roundTop1.availabilities` | list |
| `badge.roundTop1.availabilities[]` | str |
| `badge.roundTop1.reward` | int |
| `badge.staff` | dict |
| `badge.staff.availabilities` | list |
| `badge.staff.availabilities[]` | str |
| `badge.staff.preserveBetweenReset` | bool |
| `badge.staff.reward` | int |
| `badge.sugarDaddy` | dict |
| `badge.sugarDaddy.availabilities` | list |
| `badge.sugarDaddy.availabilities[]` | str |
| `badge.sugarDaddy.preserveBetweenReset` | bool |
| `badge.sugarDaddy.reward` | int |
| `badge.translator` | dict |
| `badge.translator.availabilities` | list |
| `badge.translator.availabilities[]` | str |
| `badge.translator.preserveBetweenReset` | bool |
| `badge.translator.reward` | int |
| `badge.vicePresident` | dict |
| `badge.vicePresident.availabilities` | list |
| `badge.vicePresident.availabilities[]` | str |
| `badge.vicePresident.cooldownDays` | int |
| `badge.vicePresident.reward` | int |
| `badge.voted` | dict |
| `badge.voted.availabilities` | list |
| `badge.voted.availabilities[]` | str |
| `badge.voted.reward` | int |
| `battle` | dict |
| `battle.allianceDamagesBonusPercent` | int |
| `battle.casesPer1kDamagesInPool` | int |
| `battle.countryOrderBonusPercent` | int |
| `battle.enemyDamagesBonusPercent` | int |
| `battle.govMemberBountyRewardPercent` | int |
| `battle.healthCost` | int |
| `battle.hitFor1CaseInPool` | int |
| `battle.lostAttackingRegionMalusPercent` | int |
| `battle.maxRounds` | int |
| `battle.muOrderBonusPercent` | int |
| `battle.occupyingYourRegionsMalusPercent` | int |
| `battle.patrioticBonusPercent` | int |
| `battle.pointsToWinRound` | int |
| `battle.rankingsLootPercentPer1kDmg` | float |
| `battle.regionNotLinkedToCapitalMalusPercent` | int |
| `battle.roundsToWin` | int |
| `battle.setCountryOrderMoneyCost` | int |
| `battle.setMuOrderMoneyCost` | int |
| `battle.setOrderMoneyCost` | int |
| `battle.tickPoints` | dict |
| `battle.tickPoints.1` | int |
| `battle.tickPoints.100` | int |
| `battle.tickPoints.200` | int |
| `battle.tickPoints.300` | int |
| `battle.tickPoints.400` | int |
| `battle.tickPoints.500` | int |
| `citizenshipApplication` | dict |
| `citizenshipApplication.autoApprovalEnabled` | bool |
| `citizenshipApplication.autoApprovalMaxPopulation` | int |
| `company` | dict |
| `company.changeItemCost` | int |
| `company.constructionCostIncreasePerCompany` | int |
| `company.depositResourceBonus` | int |
| `company.destructionValuePercent` | int |
| `company.moveCost` | int |
| `country` | dict |
| `country.maxTaxAmount` | int |
| `election` | dict |
| `election.candidateDurationHours` | int |
| `election.candidateMinLevel` | int |
| `election.electionVoteDurationHours` | int |
| `election.voteMinLevel` | int |
| `government` | dict |
| `government.memberWagePercent` | int |
| `government.nominationCooldownDays` | int |
| `government.presidentWagePercent` | int |
| `government.resistanceDecreasedCooldownInHours` | int |
| `government.resistanceIncreasedCooldownInHours` | int |
| `items` | dict |
| `items.ammo` | dict |
| `items.ammo.code` | str |
| `items.ammo.flatStats` | dict |
| `items.ammo.flatStats.percentAttack` | int |
| `items.ammo.isTradable` | bool |
| `items.ammo.productionNeeds` | dict |
| `items.ammo.productionNeeds.lead` | int |
| `items.ammo.productionPoints` | int |
| `items.ammo.rarity` | str |
| `items.ammo.skinSlot` | str |
| `items.ammo.type` | str |
| `items.ammo.usage` | str |
| `items.boots1` | dict |
| `items.boots1.code` | str |
| `items.boots1.dynamicStats` | dict |
| `items.boots1.dynamicStats.dodge` | list |
| `items.boots1.dynamicStats.dodge[]` | int |
| `items.boots1.iconImg` | str |
| `items.boots1.rarity` | str |
| `items.boots1.skinSlot` | str |
| `items.boots1.type` | str |
| `items.boots1.usage` | str |
| `items.boots2` | dict |
| `items.boots2.code` | str |
| `items.boots2.dynamicStats` | dict |
| `items.boots2.dynamicStats.dodge` | list |
| `items.boots2.dynamicStats.dodge[]` | int |
| `items.boots2.iconImg` | str |
| `items.boots2.rarity` | str |
| `items.boots2.skinSlot` | str |
| `items.boots2.type` | str |
| `items.boots2.usage` | str |
| `items.boots3` | dict |
| `items.boots3.code` | str |
| `items.boots3.dynamicStats` | dict |
| `items.boots3.dynamicStats.dodge` | list |
| `items.boots3.dynamicStats.dodge[]` | int |
| `items.boots3.iconImg` | str |
| `items.boots3.rarity` | str |
| `items.boots3.skinSlot` | str |
| `items.boots3.type` | str |
| `items.boots3.usage` | str |
| `items.boots4` | dict |
| `items.boots4.code` | str |
| `items.boots4.dynamicStats` | dict |
| `items.boots4.dynamicStats.dodge` | list |
| `items.boots4.dynamicStats.dodge[]` | int |
| `items.boots4.iconImg` | str |
| `items.boots4.rarity` | str |
| `items.boots4.skinSlot` | str |
| `items.boots4.type` | str |
| `items.boots4.usage` | str |
| `items.boots5` | dict |
| `items.boots5.code` | str |
| `items.boots5.dynamicStats` | dict |
| `items.boots5.dynamicStats.dodge` | list |
| `items.boots5.dynamicStats.dodge[]` | int |
| `items.boots5.iconImg` | str |
| `items.boots5.rarity` | str |
| `items.boots5.skinSlot` | str |
| `items.boots5.type` | str |
| `items.boots5.usage` | str |
| `items.boots6` | dict |
| `items.boots6.code` | str |
| `items.boots6.dynamicStats` | dict |
| `items.boots6.dynamicStats.dodge` | list |
| `items.boots6.dynamicStats.dodge[]` | int |
| `items.boots6.iconImg` | str |
| `items.boots6.rarity` | str |
| `items.boots6.skinSlot` | str |
| `items.boots6.type` | str |
| `items.boots6.usage` | str |
| `items.bread` | dict |
| `items.bread.IconComponent` | dict |
| `items.bread.IconComponent.compare` | null |
| `items.bread.code` | str |
| `items.bread.flatStats` | dict |
| `items.bread.flatStats.healthRegenPercent` | int |
| `items.bread.iconImg` | str |
| `items.bread.isConsumable` | bool |
| `items.bread.isTradable` | bool |
| `items.bread.productionNeeds` | dict |
| `items.bread.productionNeeds.grain` | int |
| `items.bread.productionPoints` | int |
| `items.bread.rarity` | str |
| `items.bread.type` | str |
| `items.case1` | dict |
| `items.case1.code` | str |
| `items.case1.isTradable` | bool |
| `items.case1.rarity` | str |
| `items.case1.type` | str |
| `items.case1.usage` | str |
| `items.case2` | dict |
| `items.case2.code` | str |
| `items.case2.isTradable` | bool |
| `items.case2.rarity` | str |
| `items.case2.type` | str |
| `items.case2.usage` | str |
| `items.chest1` | dict |
| `items.chest1.code` | str |
| `items.chest1.dynamicStats` | dict |
| `items.chest1.dynamicStats.armor` | list |
| `items.chest1.dynamicStats.armor[]` | int |
| `items.chest1.iconImg` | str |
| `items.chest1.rarity` | str |
| `items.chest1.skinSlot` | str |
| `items.chest1.type` | str |
| `items.chest1.usage` | str |
| `items.chest2` | dict |
| `items.chest2.code` | str |
| `items.chest2.dynamicStats` | dict |
| `items.chest2.dynamicStats.armor` | list |
| `items.chest2.dynamicStats.armor[]` | int |
| `items.chest2.iconImg` | str |
| `items.chest2.rarity` | str |
| `items.chest2.skinSlot` | str |
| `items.chest2.type` | str |
| `items.chest2.usage` | str |
| `items.chest3` | dict |
| `items.chest3.code` | str |
| `items.chest3.dynamicStats` | dict |
| `items.chest3.dynamicStats.armor` | list |
| `items.chest3.dynamicStats.armor[]` | int |
| `items.chest3.iconImg` | str |
| `items.chest3.rarity` | str |
| `items.chest3.skinSlot` | str |
| `items.chest3.type` | str |
| `items.chest3.usage` | str |
| `items.chest4` | dict |
| `items.chest4.code` | str |
| `items.chest4.dynamicStats` | dict |
| `items.chest4.dynamicStats.armor` | list |
| `items.chest4.dynamicStats.armor[]` | int |
| `items.chest4.iconImg` | str |
| `items.chest4.rarity` | str |
| `items.chest4.skinSlot` | str |
| `items.chest4.type` | str |
| `items.chest4.usage` | str |
| `items.chest5` | dict |
| `items.chest5.code` | str |
| `items.chest5.dynamicStats` | dict |
| `items.chest5.dynamicStats.armor` | list |
| `items.chest5.dynamicStats.armor[]` | int |
| `items.chest5.iconImg` | str |
| `items.chest5.rarity` | str |
| `items.chest5.skinSlot` | str |
| `items.chest5.type` | str |
| `items.chest5.usage` | str |
| `items.chest6` | dict |
| `items.chest6.code` | str |
| `items.chest6.dynamicStats` | dict |
| `items.chest6.dynamicStats.armor` | list |
| `items.chest6.dynamicStats.armor[]` | int |
| `items.chest6.iconImg` | str |
| `items.chest6.rarity` | str |
| `items.chest6.skinSlot` | str |
| `items.chest6.type` | str |
| `items.chest6.usage` | str |
| `items.coca` | dict |
| `items.coca.IconComponent` | dict |
| `items.coca.IconComponent.compare` | null |
| `items.coca.climates` | list |
| `items.coca.climates[]` | str |
| `items.coca.code` | str |
| `items.coca.isDeposit` | bool |
| `items.coca.isTradable` | bool |
| `items.coca.productionPoints` | int |
| `items.coca.rarity` | str |
| `items.coca.type` | str |
| `items.cocain` | dict |
| `items.cocain.IconComponent` | dict |
| `items.cocain.IconComponent.compare` | null |
| `items.cocain.code` | str |
| `items.cocain.flatStats` | dict |
| `items.cocain.flatStats.buffDurationHours` | int |
| `items.cocain.flatStats.debuffDurationHours` | float |
| `items.cocain.flatStats.percentAttack` | int |
| `items.cocain.isTradable` | bool |
| `items.cocain.productionNeeds` | dict |
| `items.cocain.productionNeeds.coca` | int |
| `items.cocain.productionPoints` | int |
| `items.cocain.rarity` | str |
| `items.cocain.type` | str |
| `items.concrete` | dict |
| `items.concrete.code` | str |
| `items.concrete.isTradable` | bool |
| `items.concrete.productionNeeds` | dict |
| `items.concrete.productionNeeds.limestone` | int |
| `items.concrete.productionPoints` | int |
| `items.concrete.rarity` | str |
| `items.concrete.type` | str |
| `items.cookedFish` | dict |
| `items.cookedFish.code` | str |
| `items.cookedFish.flatStats` | dict |
| `items.cookedFish.flatStats.healthRegenPercent` | int |
| `items.cookedFish.isConsumable` | bool |
| `items.cookedFish.isTradable` | bool |
| `items.cookedFish.productionNeeds` | dict |
| `items.cookedFish.productionNeeds.fish` | int |
| `items.cookedFish.productionPoints` | int |
| `items.cookedFish.rarity` | str |
| `items.cookedFish.type` | str |
| `items.fish` | dict |
| `items.fish.IconComponent` | dict |
| `items.fish.IconComponent.compare` | null |
| `items.fish.climates` | list |
| `items.fish.climates[]` | str |
| `items.fish.code` | str |
| `items.fish.isDeposit` | bool |
| `items.fish.isTradable` | bool |
| `items.fish.productionPoints` | int |
| `items.fish.rarity` | str |
| `items.fish.type` | str |
| `items.gloves1` | dict |
| `items.gloves1.code` | str |
| `items.gloves1.dynamicStats` | dict |
| `items.gloves1.dynamicStats.precision` | list |
| `items.gloves1.dynamicStats.precision[]` | int |
| `items.gloves1.iconImg` | str |
| `items.gloves1.rarity` | str |
| `items.gloves1.skinSlot` | str |
| `items.gloves1.type` | str |
| `items.gloves1.usage` | str |
| `items.gloves2` | dict |
| `items.gloves2.code` | str |
| `items.gloves2.dynamicStats` | dict |
| `items.gloves2.dynamicStats.precision` | list |
| `items.gloves2.dynamicStats.precision[]` | int |
| `items.gloves2.iconImg` | str |
| `items.gloves2.rarity` | str |
| `items.gloves2.skinSlot` | str |
| `items.gloves2.type` | str |
| `items.gloves2.usage` | str |
| `items.gloves3` | dict |
| `items.gloves3.code` | str |
| `items.gloves3.dynamicStats` | dict |
| `items.gloves3.dynamicStats.precision` | list |
| `items.gloves3.dynamicStats.precision[]` | int |
| `items.gloves3.iconImg` | str |
| `items.gloves3.rarity` | str |
| `items.gloves3.skinSlot` | str |
| `items.gloves3.type` | str |
| `items.gloves3.usage` | str |
| `items.gloves4` | dict |
| `items.gloves4.code` | str |
| `items.gloves4.dynamicStats` | dict |
| `items.gloves4.dynamicStats.precision` | list |
| `items.gloves4.dynamicStats.precision[]` | int |
| `items.gloves4.iconImg` | str |
| `items.gloves4.rarity` | str |
| `items.gloves4.skinSlot` | str |
| `items.gloves4.type` | str |
| `items.gloves4.usage` | str |
| `items.gloves5` | dict |
| `items.gloves5.code` | str |
| `items.gloves5.dynamicStats` | dict |
| `items.gloves5.dynamicStats.precision` | list |
| `items.gloves5.dynamicStats.precision[]` | int |
| `items.gloves5.iconImg` | str |
| `items.gloves5.rarity` | str |
| `items.gloves5.skinSlot` | str |
| `items.gloves5.type` | str |
| `items.gloves5.usage` | str |
| `items.gloves6` | dict |
| `items.gloves6.code` | str |
| `items.gloves6.dynamicStats` | dict |
| `items.gloves6.dynamicStats.precision` | list |
| `items.gloves6.dynamicStats.precision[]` | int |
| `items.gloves6.iconImg` | str |
| `items.gloves6.rarity` | str |
| `items.gloves6.skinSlot` | str |
| `items.gloves6.type` | str |
| `items.gloves6.usage` | str |
| `items.grain` | dict |
| `items.grain.IconComponent` | dict |
| `items.grain.IconComponent.compare` | null |
| `items.grain.climates` | list |
| `items.grain.climates[]` | str |
| `items.grain.code` | str |
| `items.grain.isDeposit` | bool |
| `items.grain.isTradable` | bool |
| `items.grain.productionPoints` | int |
| `items.grain.rarity` | str |
| `items.grain.type` | str |
| `items.gun` | dict |
| `items.gun.IconComponent` | dict |
| `items.gun.IconComponent.compare` | null |
| `items.gun.code` | str |
| `items.gun.dynamicStats` | dict |
| `items.gun.dynamicStats.attack` | list |
| `items.gun.dynamicStats.attack[]` | int |
| `items.gun.dynamicStats.criticalChance` | list |
| `items.gun.dynamicStats.criticalChance[]` | int |
| `items.gun.rarity` | str |
| `items.gun.skinSlot` | str |
| `items.gun.type` | str |
| `items.gun.usage` | str |
| `items.heavyAmmo` | dict |
| `items.heavyAmmo.IconComponent` | dict |
| `items.heavyAmmo.IconComponent.compare` | null |
| `items.heavyAmmo.code` | str |
| `items.heavyAmmo.flatStats` | dict |
| `items.heavyAmmo.flatStats.percentAttack` | int |
| `items.heavyAmmo.isTradable` | bool |
| `items.heavyAmmo.productionNeeds` | dict |
| `items.heavyAmmo.productionNeeds.lead` | int |
| `items.heavyAmmo.productionPoints` | int |
| `items.heavyAmmo.rarity` | str |
| `items.heavyAmmo.skinSlot` | str |
| `items.heavyAmmo.type` | str |
| `items.heavyAmmo.usage` | str |
| `items.helmet1` | dict |
| `items.helmet1.code` | str |
| `items.helmet1.dynamicStats` | dict |
| `items.helmet1.dynamicStats.criticalDamages` | list |
| `items.helmet1.dynamicStats.criticalDamages[]` | int |
| `items.helmet1.iconImg` | str |
| `items.helmet1.rarity` | str |
| `items.helmet1.skinSlot` | str |
| `items.helmet1.type` | str |
| `items.helmet1.usage` | str |
| `items.helmet2` | dict |
| `items.helmet2.code` | str |
| `items.helmet2.dynamicStats` | dict |
| `items.helmet2.dynamicStats.criticalDamages` | list |
| `items.helmet2.dynamicStats.criticalDamages[]` | int |
| `items.helmet2.iconImg` | str |
| `items.helmet2.rarity` | str |
| `items.helmet2.skinSlot` | str |
| `items.helmet2.type` | str |
| `items.helmet2.usage` | str |
| `items.helmet3` | dict |
| `items.helmet3.code` | str |
| `items.helmet3.dynamicStats` | dict |
| `items.helmet3.dynamicStats.criticalDamages` | list |
| `items.helmet3.dynamicStats.criticalDamages[]` | int |
| `items.helmet3.iconImg` | str |
| `items.helmet3.rarity` | str |
| `items.helmet3.skinSlot` | str |
| `items.helmet3.type` | str |
| `items.helmet3.usage` | str |
| `items.helmet4` | dict |
| `items.helmet4.code` | str |
| `items.helmet4.dynamicStats` | dict |
| `items.helmet4.dynamicStats.criticalDamages` | list |
| `items.helmet4.dynamicStats.criticalDamages[]` | int |
| `items.helmet4.iconImg` | str |
| `items.helmet4.rarity` | str |
| `items.helmet4.skinSlot` | str |
| `items.helmet4.type` | str |
| `items.helmet4.usage` | str |
| `items.helmet5` | dict |
| `items.helmet5.code` | str |
| `items.helmet5.dynamicStats` | dict |
| `items.helmet5.dynamicStats.criticalDamages` | list |
| `items.helmet5.dynamicStats.criticalDamages[]` | int |
| `items.helmet5.iconImg` | str |
| `items.helmet5.rarity` | str |
| `items.helmet5.skinSlot` | str |
| `items.helmet5.type` | str |
| `items.helmet5.usage` | str |
| `items.helmet6` | dict |
| `items.helmet6.code` | str |
| `items.helmet6.dynamicStats` | dict |
| `items.helmet6.dynamicStats.criticalDamages` | list |
| `items.helmet6.dynamicStats.criticalDamages[]` | int |
| `items.helmet6.iconImg` | str |
| `items.helmet6.rarity` | str |
| `items.helmet6.skinSlot` | str |
| `items.helmet6.type` | str |
| `items.helmet6.usage` | str |
| `items.iron` | dict |
| `items.iron.climates` | list |
| `items.iron.climates[]` | str |
| `items.iron.code` | str |
| `items.iron.isDeposit` | bool |
| `items.iron.isTradable` | bool |
| `items.iron.productionPoints` | int |
| `items.iron.rarity` | str |
| `items.iron.type` | str |
| `items.jet` | dict |
| `items.jet.code` | str |
| `items.jet.dynamicStats` | dict |
| `items.jet.dynamicStats.attack` | list |
| `items.jet.dynamicStats.attack[]` | int |
| `items.jet.dynamicStats.criticalChance` | list |
| `items.jet.dynamicStats.criticalChance[]` | int |
| `items.jet.rarity` | str |
| `items.jet.skinSlot` | str |
| `items.jet.type` | str |
| `items.jet.usage` | str |
| `items.knife` | dict |
| `items.knife.IconComponent` | dict |
| `items.knife.IconComponent.compare` | null |
| `items.knife.code` | str |
| `items.knife.dynamicStats` | dict |
| `items.knife.dynamicStats.attack` | list |
| `items.knife.dynamicStats.attack[]` | int |
| `items.knife.dynamicStats.criticalChance` | list |
| `items.knife.dynamicStats.criticalChance[]` | int |
| `items.knife.rarity` | str |
| `items.knife.skinSlot` | str |
| `items.knife.type` | str |
| `items.knife.usage` | str |
| `items.lead` | dict |
| `items.lead.climates` | list |
| `items.lead.climates[]` | str |
| `items.lead.code` | str |
| `items.lead.isDeposit` | bool |
| `items.lead.isTradable` | bool |
| `items.lead.productionPoints` | int |
| `items.lead.rarity` | str |
| `items.lead.type` | str |
| `items.lightAmmo` | dict |
| `items.lightAmmo.code` | str |
| `items.lightAmmo.flatStats` | dict |
| `items.lightAmmo.flatStats.percentAttack` | int |
| `items.lightAmmo.isTradable` | bool |
| `items.lightAmmo.productionNeeds` | dict |
| `items.lightAmmo.productionNeeds.lead` | int |
| `items.lightAmmo.productionPoints` | int |
| `items.lightAmmo.rarity` | str |
| `items.lightAmmo.skinSlot` | str |
| `items.lightAmmo.type` | str |
| `items.lightAmmo.usage` | str |
| `items.limestone` | dict |
| `items.limestone.climates` | list |
| `items.limestone.climates[]` | str |
| `items.limestone.code` | str |
| `items.limestone.isDeposit` | bool |
| `items.limestone.isTradable` | bool |
| `items.limestone.productionPoints` | int |
| `items.limestone.rarity` | str |
| `items.limestone.type` | str |
| `items.livestock` | dict |
| `items.livestock.IconComponent` | dict |
| `items.livestock.IconComponent.compare` | null |
| `items.livestock.climates` | list |
| `items.livestock.climates[]` | str |
| `items.livestock.code` | str |
| `items.livestock.isDeposit` | bool |
| `items.livestock.isTradable` | bool |
| `items.livestock.productionPoints` | int |
| `items.livestock.rarity` | str |
| `items.livestock.type` | str |
| `items.oil` | dict |
| `items.oil.IconComponent` | dict |
| `items.oil.IconComponent.compare` | null |
| `items.oil.code` | str |
| `items.oil.isTradable` | bool |
| `items.oil.productionNeeds` | dict |
| `items.oil.productionNeeds.petroleum` | int |
| `items.oil.productionPoints` | int |
| `items.oil.rarity` | str |
| `items.oil.type` | str |
| `items.pants1` | dict |
| `items.pants1.code` | str |
| `items.pants1.dynamicStats` | dict |
| `items.pants1.dynamicStats.armor` | list |
| `items.pants1.dynamicStats.armor[]` | int |
| `items.pants1.iconImg` | str |
| `items.pants1.rarity` | str |
| `items.pants1.skinSlot` | str |
| `items.pants1.type` | str |
| `items.pants1.usage` | str |
| `items.pants2` | dict |
| `items.pants2.code` | str |
| `items.pants2.dynamicStats` | dict |
| `items.pants2.dynamicStats.armor` | list |
| `items.pants2.dynamicStats.armor[]` | int |
| `items.pants2.iconImg` | str |
| `items.pants2.rarity` | str |
| `items.pants2.skinSlot` | str |
| `items.pants2.type` | str |
| `items.pants2.usage` | str |
| `items.pants3` | dict |
| `items.pants3.code` | str |
| `items.pants3.dynamicStats` | dict |
| `items.pants3.dynamicStats.armor` | list |
| `items.pants3.dynamicStats.armor[]` | int |
| `items.pants3.iconImg` | str |
| `items.pants3.rarity` | str |
| `items.pants3.skinSlot` | str |
| `items.pants3.type` | str |
| `items.pants3.usage` | str |
| `items.pants4` | dict |
| `items.pants4.code` | str |
| `items.pants4.dynamicStats` | dict |
| `items.pants4.dynamicStats.armor` | list |
| `items.pants4.dynamicStats.armor[]` | int |
| `items.pants4.iconImg` | str |
| `items.pants4.rarity` | str |
| `items.pants4.skinSlot` | str |
| `items.pants4.type` | str |
| `items.pants4.usage` | str |
| `items.pants5` | dict |
| `items.pants5.code` | str |
| `items.pants5.dynamicStats` | dict |
| `items.pants5.dynamicStats.armor` | list |
| `items.pants5.dynamicStats.armor[]` | int |
| `items.pants5.iconImg` | str |
| `items.pants5.rarity` | str |
| `items.pants5.skinSlot` | str |
| `items.pants5.type` | str |
| `items.pants5.usage` | str |
| `items.pants6` | dict |
| `items.pants6.code` | str |
| `items.pants6.dynamicStats` | dict |
| `items.pants6.dynamicStats.armor` | list |
| `items.pants6.dynamicStats.armor[]` | int |
| `items.pants6.iconImg` | str |
| `items.pants6.rarity` | str |
| `items.pants6.skinSlot` | str |
| `items.pants6.type` | str |
| `items.pants6.usage` | str |
| `items.paper` | dict |
| `items.paper.IconComponent` | dict |
| `items.paper.IconComponent.compare` | null |
| `items.paper.code` | str |
| `items.paper.isTradable` | bool |
| `items.paper.productionNeeds` | dict |
| `items.paper.productionNeeds.wood` | int |
| `items.paper.productionPoints` | int |
| `items.paper.rarity` | str |
| `items.paper.type` | str |
| `items.petroleum` | dict |
| `items.petroleum.IconComponent` | dict |
| `items.petroleum.IconComponent.compare` | null |
| `items.petroleum.climates` | list |
| `items.petroleum.climates[]` | str |
| `items.petroleum.code` | str |
| `items.petroleum.isDeposit` | bool |
| `items.petroleum.isTradable` | bool |
| `items.petroleum.productionPoints` | int |
| `items.petroleum.rarity` | str |
| `items.petroleum.type` | str |
| `items.rifle` | dict |
| `items.rifle.code` | str |
| `items.rifle.dynamicStats` | dict |
| `items.rifle.dynamicStats.attack` | list |
| `items.rifle.dynamicStats.attack[]` | int |
| `items.rifle.dynamicStats.criticalChance` | list |
| `items.rifle.dynamicStats.criticalChance[]` | int |
| `items.rifle.rarity` | str |
| `items.rifle.skinSlot` | str |
| `items.rifle.type` | str |
| `items.rifle.usage` | str |
| `items.scraps` | dict |
| `items.scraps.code` | str |
| `items.scraps.isTradable` | bool |
| `items.scraps.rarity` | str |
| `items.scraps.type` | str |
| `items.sniper` | dict |
| `items.sniper.code` | str |
| `items.sniper.dynamicStats` | dict |
| `items.sniper.dynamicStats.attack` | list |
| `items.sniper.dynamicStats.attack[]` | int |
| `items.sniper.dynamicStats.criticalChance` | list |
| `items.sniper.dynamicStats.criticalChance[]` | int |
| `items.sniper.rarity` | str |
| `items.sniper.skinSlot` | str |
| `items.sniper.type` | str |
| `items.sniper.usage` | str |
| `items.steak` | dict |
| `items.steak.IconComponent` | dict |
| `items.steak.IconComponent.compare` | null |
| `items.steak.code` | str |
| `items.steak.flatStats` | dict |
| `items.steak.flatStats.healthRegenPercent` | int |
| `items.steak.isConsumable` | bool |
| `items.steak.isTradable` | bool |
| `items.steak.productionNeeds` | dict |
| `items.steak.productionNeeds.livestock` | int |
| `items.steak.productionPoints` | int |
| `items.steak.rarity` | str |
| `items.steak.type` | str |
| `items.steel` | dict |
| `items.steel.code` | str |
| `items.steel.isTradable` | bool |
| `items.steel.productionNeeds` | dict |
| `items.steel.productionNeeds.iron` | int |
| `items.steel.productionPoints` | int |
| `items.steel.rarity` | str |
| `items.steel.type` | str |
| `items.tank` | dict |
| `items.tank.IconComponent` | dict |
| `items.tank.IconComponent.compare` | null |
| `items.tank.code` | str |
| `items.tank.dynamicStats` | dict |
| `items.tank.dynamicStats.attack` | list |
| `items.tank.dynamicStats.attack[]` | int |
| `items.tank.dynamicStats.criticalChance` | list |
| `items.tank.dynamicStats.criticalChance[]` | int |
| `items.tank.rarity` | str |
| `items.tank.skinSlot` | str |
| `items.tank.type` | str |
| `items.tank.usage` | str |
| `items.wood` | dict |
| `items.wood.IconComponent` | dict |
| `items.wood.IconComponent.compare` | null |
| `items.wood.climates` | list |
| `items.wood.climates[]` | str |
| `items.wood.code` | str |
| `items.wood.isDeposit` | bool |
| `items.wood.isTradable` | bool |
| `items.wood.productionPoints` | int |
| `items.wood.rarity` | str |
| `items.wood.type` | str |
| `items.woodenCase` | dict |
| `items.woodenCase.code` | str |
| `items.woodenCase.isTradable` | bool |
| `items.woodenCase.rarity` | str |
| `items.woodenCase.type` | str |
| `items.woodenCase.usage` | str |
| `law` | dict |
| `law.abusiveLawPossibleVotersNeeded` | int |
| `law.abusiveLawsCooldownInDays` | int |
| `law.accept_join_alliance` | dict |
| `law.accept_join_alliance.cost` | int |
| `law.create_alliance` | dict |
| `law.create_alliance.cost` | int |
| `law.create_alliance.maintenanceCost` | int |
| `law.define_enemy_country` | dict |
| `law.define_enemy_country.cost` | int |
| `law.define_enemy_country.maintenanceCost` | int |
| `law.lawVotesDurationHours` | int |
| `law.leave_alliance` | dict |
| `law.leave_alliance.cost` | int |
| `law.sendMoneyToCountry` | dict |
| `law.sendMoneyToCountry.allianceTaxRate` | float |
| `law.sendMoneyToCountry.externalTaxRate` | int |
| `law.set_color_scheme` | dict |
| `law.set_color_scheme.cost` | int |
| `law.votersRatioNeeded` | float |
| `loot` | dict |
| `loot.battleLootDamagePerLootItem` | int |
| `loot.damagePerLootItem` | int |
| `loot.weaponChancePercent` | int |
| `mercenaryContract` | dict |
| `mercenaryContract.acceptanceFeePercent` | int |
| `mercenaryContract.auction` | dict |
| `mercenaryContract.auction.bidStep` | float |
| `mercenaryContract.auction.maxDamagePerContract` | int |
| `mercenaryContract.auction.maxDuration` | int |
| `mercenaryContract.auction.maxPerKMultiplier` | int |
| `mercenaryContract.auction.minDuration` | int |
| `mercenaryContract.auction.minPerK` | float |
| `mercenaryContract.auction.timerExtensionAmount` | int |
| `mercenaryContract.auction.timerExtensionThreshold` | int |
| `mercenaryContract.bountyCooldownMinutes` | int |
| `mercenaryContract.bountyMinActiveCitizens` | int |
| `mercenaryContract.cancellationPenaltyPercent` | int |
| `mercenaryContract.enabled` | bool |
| `mercenaryContract.maxBountyWageMultiplier` | int |
| `mercenaryContract.nationalBountyEnabled` | bool |
| `mercenaryContract.nationalBountyMinActiveCitizens` | int |
| `mercenaryContract.reputation` | dict |
| `mercenaryContract.reputation.failurePerDamage` | int |
| `mercenaryContract.reputation.failurePerDollar` | int |
| `mercenaryContract.reputation.negativeBuyAmount` | int |
| `mercenaryContract.reputation.negativeBuyCooldownHours` | int |
| `mercenaryContract.reputation.negativeBuyCost` | int |
| `mercenaryContract.reputation.professionalsOnlyThreshold` | int |
| `mercenaryContract.reputation.successPerDollar` | int |
| `mercenaryContract.reputation.weeklyDecayPercent` | int |
| `mergingCost` | dict |
| `mergingCost.common` | int |
| `mergingCost.epic` | int |
| `mergingCost.legendary` | int |
| `mergingCost.mythic` | int |
| `mergingCost.rare` | int |
| `mergingCost.uncommon` | int |
| `mission` | dict |
| `mission.rerollMissionCost` | dict |
| `mission.rerollMissionCost.0` | int |
| `mission.rerollMissionCost.1` | int |
| `mission.rerollMissionCost.10` | int |
| `mission.rerollMissionCost.11` | int |
| `mission.rerollMissionCost.12` | int |
| `mission.rerollMissionCost.13` | int |
| `mission.rerollMissionCost.14` | int |
| `mission.rerollMissionCost.15` | int |
| `mission.rerollMissionCost.16` | int |
| `mission.rerollMissionCost.17` | int |
| `mission.rerollMissionCost.18` | int |
| `mission.rerollMissionCost.19` | int |
| `mission.rerollMissionCost.2` | int |
| `mission.rerollMissionCost.20` | int |
| `mission.rerollMissionCost.3` | int |
| `mission.rerollMissionCost.4` | int |
| `mission.rerollMissionCost.5` | int |
| `mission.rerollMissionCost.6` | int |
| `mission.rerollMissionCost.7` | int |
| `mission.rerollMissionCost.8` | int |
| `mission.rerollMissionCost.9` | int |
| `mission.reward` | dict |
| `mission.reward.daily` | dict |
| `mission.reward.daily.cases` | int |
| `mission.reward.daily.money` | int |
| `mission.reward.daily.xp` | int |
| `mission.reward.daily.xpWhenFinished` | int |
| `mission.reward.monthly` | dict |
| `mission.reward.monthly.cases` | int |
| `mission.reward.monthly.money` | int |
| `mission.reward.monthly.xp` | int |
| `mission.reward.monthly.xpWhenFinished` | int |
| `mission.reward.starting` | dict |
| `mission.reward.starting.cases` | int |
| `mission.reward.starting.money` | int |
| `mission.reward.starting.xp` | int |
| `mission.reward.starting.xpWhenFinished` | int |
| `mission.reward.weekly` | dict |
| `mission.reward.weekly.cases` | int |
| `mission.reward.weekly.money` | int |
| `mission.reward.weekly.xp` | int |
| `mission.reward.weekly.xpWhenFinished` | int |
| `mu` | dict |
| `mu.constructionCost` | int |
| `mu.destructionValuePercent` | int |
| `mu.healthPerHelp` | int |
| `mu.helpCooldownHours` | int |
| `mu.helpValue` | int |
| `mu.maxOwnedMus` | int |
| `mu.moveCost` | int |
| `mu.nationalityChangeCooldownDays` | int |
| `newspaper` | dict |
| `newspaper.commentMinLevel` | int |
| `newspaper.createArticleMinLevel` | int |
| `newspaper.gemTipValue` | int |
| `newspaper.publishCost` | int |
| `newspaper.tipMinLevel` | int |
| `newspaper.tipValue` | int |
| `org` | dict |
| `org.constructionCost` | int |
| `org.moveCost` | int |
| `party` | dict |
| `party.createCost` | int |
| `referral` | dict |
| `referral.canSetReferrerBeforeOrAtLevel` | int |
| `referral.levelNeededForBadge` | int |
| `referral.lifeTimeBadgeMoneySharePercent` | int |
| `referral.moneyForBeingReferred` | int |
| `region` | dict |
| `region.battleCooldownHours` | int |
| `region.citizenIncomeTaxPercent` | int |
| `region.decreaseBy` | int |
| `region.decreaseResistanceCost` | int |
| `region.depleteHourlyPercent` | float |
| `region.freeTravelHomeCooldownHours` | int |
| `region.homeMoveConcreteCost` | int |
| `region.increaseBy` | int |
| `region.increaseResistanceCost` | int |
| `region.liberationDaysCooldown` | int |
| `region.maxDailyResistance` | int |
| `region.maxResistance` | int |
| `region.minDailyResistance` | int |
| `region.nonAggressionHoursAfterLiberation` | int |
| `region.nonAggressionHoursAfterPeace` | int |
| `region.regionNotLinkedToCapitalMalusDevelopmentPercent` | int |
| `region.resistanceAllyBonusPercent` | int |
| `region.resistanceBarMultiplier` | int |
| `region.resistanceBattleCooldownHours` | int |
| `region.resistanceBattleStartCostMultiplier` | int |
| `region.resistanceCitizenBonus` | int |
| `region.resistanceCitizenBonusPercent` | int |
| `region.resistanceContributionCooldownAfterRevoltHours` | int |
| `region.resistanceContributionCost` | int |
| `region.resistanceContributionMinLevel` | int |
| `region.resistanceContributionValue` | int |
| `region.resistanceDecayPercent` | float |
| `region.resistanceForeignGovCostMultiplier` | int |
| `region.resistanceGrowthPercentMax` | float |
| `region.resistanceGrowthPercentMin` | float |
| `region.resistancePassiveGrowthPercent` | float |
| `region.resourcesBonus` | dict |
| `region.resourcesBonus.1` | int |
| `region.resourcesBonus.2` | float |
| `region.resourcesBonus.3` | float |
| `region.transferDaysCooldown` | int |
| `skills` | dict |
| `skills.armor` | dict |
| `skills.armor.levels` | dict |
| `skills.armor.levels.0` | dict |
| `skills.armor.levels.0.totalCost` | int |
| `skills.armor.levels.0.unlockAtLevel` | int |
| `skills.armor.levels.0.value` | int |
| `skills.armor.levels.1` | dict |
| `skills.armor.levels.1.cost` | int |
| `skills.armor.levels.1.totalCost` | int |
| `skills.armor.levels.1.unlockAtLevel` | int |
| `skills.armor.levels.1.value` | int |
| `skills.armor.levels.10` | dict |
| `skills.armor.levels.10.cost` | int |
| `skills.armor.levels.10.totalCost` | int |
| `skills.armor.levels.10.unlockAtLevel` | int |
| `skills.armor.levels.10.value` | int |
| `skills.armor.levels.2` | dict |
| `skills.armor.levels.2.cost` | int |
| `skills.armor.levels.2.totalCost` | int |
| `skills.armor.levels.2.unlockAtLevel` | int |
| `skills.armor.levels.2.value` | int |
| `skills.armor.levels.3` | dict |
| `skills.armor.levels.3.cost` | int |
| `skills.armor.levels.3.totalCost` | int |
| `skills.armor.levels.3.unlockAtLevel` | int |
| `skills.armor.levels.3.value` | int |
| `skills.armor.levels.4` | dict |
| `skills.armor.levels.4.cost` | int |
| `skills.armor.levels.4.totalCost` | int |
| `skills.armor.levels.4.unlockAtLevel` | int |
| `skills.armor.levels.4.value` | int |
| `skills.armor.levels.5` | dict |
| `skills.armor.levels.5.cost` | int |
| `skills.armor.levels.5.totalCost` | int |
| `skills.armor.levels.5.unlockAtLevel` | int |
| `skills.armor.levels.5.value` | int |
| `skills.armor.levels.6` | dict |
| `skills.armor.levels.6.cost` | int |
| `skills.armor.levels.6.totalCost` | int |
| `skills.armor.levels.6.unlockAtLevel` | int |
| `skills.armor.levels.6.value` | int |
| `skills.armor.levels.7` | dict |
| `skills.armor.levels.7.cost` | int |
| `skills.armor.levels.7.totalCost` | int |
| `skills.armor.levels.7.unlockAtLevel` | int |
| `skills.armor.levels.7.value` | int |
| `skills.armor.levels.8` | dict |
| `skills.armor.levels.8.cost` | int |
| `skills.armor.levels.8.totalCost` | int |
| `skills.armor.levels.8.unlockAtLevel` | int |
| `skills.armor.levels.8.value` | int |
| `skills.armor.levels.9` | dict |
| `skills.armor.levels.9.cost` | int |
| `skills.armor.levels.9.totalCost` | int |
| `skills.armor.levels.9.unlockAtLevel` | int |
| `skills.armor.levels.9.value` | int |
| `skills.armor.softCap` | int |
| `skills.attack` | dict |
| `skills.attack.levels` | dict |
| `skills.attack.levels.0` | dict |
| `skills.attack.levels.0.totalCost` | int |
| `skills.attack.levels.0.unlockAtLevel` | int |
| `skills.attack.levels.0.value` | int |
| `skills.attack.levels.1` | dict |
| `skills.attack.levels.1.cost` | int |
| `skills.attack.levels.1.totalCost` | int |
| `skills.attack.levels.1.unlockAtLevel` | int |
| `skills.attack.levels.1.value` | int |
| `skills.attack.levels.10` | dict |
| `skills.attack.levels.10.cost` | int |
| `skills.attack.levels.10.totalCost` | int |
| `skills.attack.levels.10.unlockAtLevel` | int |
| `skills.attack.levels.10.value` | int |
| `skills.attack.levels.2` | dict |
| `skills.attack.levels.2.cost` | int |
| `skills.attack.levels.2.totalCost` | int |
| `skills.attack.levels.2.unlockAtLevel` | int |
| `skills.attack.levels.2.value` | int |
| `skills.attack.levels.3` | dict |
| `skills.attack.levels.3.cost` | int |
| `skills.attack.levels.3.totalCost` | int |
| `skills.attack.levels.3.unlockAtLevel` | int |
| `skills.attack.levels.3.value` | int |
| `skills.attack.levels.4` | dict |
| `skills.attack.levels.4.cost` | int |
| `skills.attack.levels.4.totalCost` | int |
| `skills.attack.levels.4.unlockAtLevel` | int |
| `skills.attack.levels.4.value` | int |
| `skills.attack.levels.5` | dict |
| `skills.attack.levels.5.cost` | int |
| `skills.attack.levels.5.totalCost` | int |
| `skills.attack.levels.5.unlockAtLevel` | int |
| `skills.attack.levels.5.value` | int |
| `skills.attack.levels.6` | dict |
| `skills.attack.levels.6.cost` | int |
| `skills.attack.levels.6.totalCost` | int |
| `skills.attack.levels.6.unlockAtLevel` | int |
| `skills.attack.levels.6.value` | int |
| `skills.attack.levels.7` | dict |
| `skills.attack.levels.7.cost` | int |
| `skills.attack.levels.7.totalCost` | int |
| `skills.attack.levels.7.unlockAtLevel` | int |
| `skills.attack.levels.7.value` | int |
| `skills.attack.levels.8` | dict |
| `skills.attack.levels.8.cost` | int |
| `skills.attack.levels.8.totalCost` | int |
| `skills.attack.levels.8.unlockAtLevel` | int |
| `skills.attack.levels.8.value` | int |
| `skills.attack.levels.9` | dict |
| `skills.attack.levels.9.cost` | int |
| `skills.attack.levels.9.totalCost` | int |
| `skills.attack.levels.9.unlockAtLevel` | int |
| `skills.attack.levels.9.value` | int |
| `skills.companies` | dict |
| `skills.companies.levels` | dict |
| `skills.companies.levels.0` | dict |
| `skills.companies.levels.0.totalCost` | int |
| `skills.companies.levels.0.unlockAtLevel` | int |
| `skills.companies.levels.0.value` | int |
| `skills.companies.levels.1` | dict |
| `skills.companies.levels.1.cost` | int |
| `skills.companies.levels.1.totalCost` | int |
| `skills.companies.levels.1.unlockAtLevel` | int |
| `skills.companies.levels.1.value` | int |
| `skills.companies.levels.10` | dict |
| `skills.companies.levels.10.cost` | int |
| `skills.companies.levels.10.totalCost` | int |
| `skills.companies.levels.10.unlockAtLevel` | int |
| `skills.companies.levels.10.value` | int |
| `skills.companies.levels.2` | dict |
| `skills.companies.levels.2.cost` | int |
| `skills.companies.levels.2.totalCost` | int |
| `skills.companies.levels.2.unlockAtLevel` | int |
| `skills.companies.levels.2.value` | int |
| `skills.companies.levels.3` | dict |
| `skills.companies.levels.3.cost` | int |
| `skills.companies.levels.3.totalCost` | int |
| `skills.companies.levels.3.unlockAtLevel` | int |
| `skills.companies.levels.3.value` | int |
| `skills.companies.levels.4` | dict |
| `skills.companies.levels.4.cost` | int |
| `skills.companies.levels.4.totalCost` | int |
| `skills.companies.levels.4.unlockAtLevel` | int |
| `skills.companies.levels.4.value` | int |
| `skills.companies.levels.5` | dict |
| `skills.companies.levels.5.cost` | int |
| `skills.companies.levels.5.totalCost` | int |
| `skills.companies.levels.5.unlockAtLevel` | int |
| `skills.companies.levels.5.value` | int |
| `skills.companies.levels.6` | dict |
| `skills.companies.levels.6.cost` | int |
| `skills.companies.levels.6.totalCost` | int |
| `skills.companies.levels.6.unlockAtLevel` | int |
| `skills.companies.levels.6.value` | int |
| `skills.companies.levels.7` | dict |
| `skills.companies.levels.7.cost` | int |
| `skills.companies.levels.7.totalCost` | int |
| `skills.companies.levels.7.unlockAtLevel` | int |
| `skills.companies.levels.7.value` | int |
| `skills.companies.levels.8` | dict |
| `skills.companies.levels.8.cost` | int |
| `skills.companies.levels.8.totalCost` | int |
| `skills.companies.levels.8.unlockAtLevel` | int |
| `skills.companies.levels.8.value` | int |
| `skills.companies.levels.9` | dict |
| `skills.companies.levels.9.cost` | int |
| `skills.companies.levels.9.totalCost` | int |
| `skills.companies.levels.9.unlockAtLevel` | int |
| `skills.companies.levels.9.value` | int |
| `skills.criticalChance` | dict |
| `skills.criticalChance.levels` | dict |
| `skills.criticalChance.levels.0` | dict |
| `skills.criticalChance.levels.0.totalCost` | int |
| `skills.criticalChance.levels.0.unlockAtLevel` | int |
| `skills.criticalChance.levels.0.value` | int |
| `skills.criticalChance.levels.1` | dict |
| `skills.criticalChance.levels.1.cost` | int |
| `skills.criticalChance.levels.1.totalCost` | int |
| `skills.criticalChance.levels.1.unlockAtLevel` | int |
| `skills.criticalChance.levels.1.value` | int |
| `skills.criticalChance.levels.10` | dict |
| `skills.criticalChance.levels.10.cost` | int |
| `skills.criticalChance.levels.10.totalCost` | int |
| `skills.criticalChance.levels.10.unlockAtLevel` | int |
| `skills.criticalChance.levels.10.value` | int |
| `skills.criticalChance.levels.2` | dict |
| `skills.criticalChance.levels.2.cost` | int |
| `skills.criticalChance.levels.2.totalCost` | int |
| `skills.criticalChance.levels.2.unlockAtLevel` | int |
| `skills.criticalChance.levels.2.value` | int |
| `skills.criticalChance.levels.3` | dict |
| `skills.criticalChance.levels.3.cost` | int |
| `skills.criticalChance.levels.3.totalCost` | int |
| `skills.criticalChance.levels.3.unlockAtLevel` | int |
| `skills.criticalChance.levels.3.value` | int |
| `skills.criticalChance.levels.4` | dict |
| `skills.criticalChance.levels.4.cost` | int |
| `skills.criticalChance.levels.4.totalCost` | int |
| `skills.criticalChance.levels.4.unlockAtLevel` | int |
| `skills.criticalChance.levels.4.value` | int |
| `skills.criticalChance.levels.5` | dict |
| `skills.criticalChance.levels.5.cost` | int |
| `skills.criticalChance.levels.5.totalCost` | int |
| `skills.criticalChance.levels.5.unlockAtLevel` | int |
| `skills.criticalChance.levels.5.value` | int |
| `skills.criticalChance.levels.6` | dict |
| `skills.criticalChance.levels.6.cost` | int |
| `skills.criticalChance.levels.6.totalCost` | int |
| `skills.criticalChance.levels.6.unlockAtLevel` | int |
| `skills.criticalChance.levels.6.value` | int |
| `skills.criticalChance.levels.7` | dict |
| `skills.criticalChance.levels.7.cost` | int |
| `skills.criticalChance.levels.7.totalCost` | int |
| `skills.criticalChance.levels.7.unlockAtLevel` | int |
| `skills.criticalChance.levels.7.value` | int |
| `skills.criticalChance.levels.8` | dict |
| `skills.criticalChance.levels.8.cost` | int |
| `skills.criticalChance.levels.8.totalCost` | int |
| `skills.criticalChance.levels.8.unlockAtLevel` | int |
| `skills.criticalChance.levels.8.value` | int |
| `skills.criticalChance.levels.9` | dict |
| `skills.criticalChance.levels.9.cost` | int |
| `skills.criticalChance.levels.9.totalCost` | int |
| `skills.criticalChance.levels.9.unlockAtLevel` | int |
| `skills.criticalChance.levels.9.value` | int |
| `skills.criticalChance.skillOverflow` | str |
| `skills.criticalChance.skillOverflowValue` | int |
| `skills.criticalDamages` | dict |
| `skills.criticalDamages.levels` | dict |
| `skills.criticalDamages.levels.0` | dict |
| `skills.criticalDamages.levels.0.totalCost` | int |
| `skills.criticalDamages.levels.0.unlockAtLevel` | int |
| `skills.criticalDamages.levels.0.value` | int |
| `skills.criticalDamages.levels.1` | dict |
| `skills.criticalDamages.levels.1.cost` | int |
| `skills.criticalDamages.levels.1.totalCost` | int |
| `skills.criticalDamages.levels.1.unlockAtLevel` | int |
| `skills.criticalDamages.levels.1.value` | int |
| `skills.criticalDamages.levels.10` | dict |
| `skills.criticalDamages.levels.10.cost` | int |
| `skills.criticalDamages.levels.10.totalCost` | int |
| `skills.criticalDamages.levels.10.unlockAtLevel` | int |
| `skills.criticalDamages.levels.10.value` | int |
| `skills.criticalDamages.levels.2` | dict |
| `skills.criticalDamages.levels.2.cost` | int |
| `skills.criticalDamages.levels.2.totalCost` | int |
| `skills.criticalDamages.levels.2.unlockAtLevel` | int |
| `skills.criticalDamages.levels.2.value` | int |
| `skills.criticalDamages.levels.3` | dict |
| `skills.criticalDamages.levels.3.cost` | int |
| `skills.criticalDamages.levels.3.totalCost` | int |
| `skills.criticalDamages.levels.3.unlockAtLevel` | int |
| `skills.criticalDamages.levels.3.value` | int |
| `skills.criticalDamages.levels.4` | dict |
| `skills.criticalDamages.levels.4.cost` | int |
| `skills.criticalDamages.levels.4.totalCost` | int |
| `skills.criticalDamages.levels.4.unlockAtLevel` | int |
| `skills.criticalDamages.levels.4.value` | int |
| `skills.criticalDamages.levels.5` | dict |
| `skills.criticalDamages.levels.5.cost` | int |
| `skills.criticalDamages.levels.5.totalCost` | int |
| `skills.criticalDamages.levels.5.unlockAtLevel` | int |
| `skills.criticalDamages.levels.5.value` | int |
| `skills.criticalDamages.levels.6` | dict |
| `skills.criticalDamages.levels.6.cost` | int |
| `skills.criticalDamages.levels.6.totalCost` | int |
| `skills.criticalDamages.levels.6.unlockAtLevel` | int |
| `skills.criticalDamages.levels.6.value` | int |
| `skills.criticalDamages.levels.7` | dict |
| `skills.criticalDamages.levels.7.cost` | int |
| `skills.criticalDamages.levels.7.totalCost` | int |
| `skills.criticalDamages.levels.7.unlockAtLevel` | int |
| `skills.criticalDamages.levels.7.value` | int |
| `skills.criticalDamages.levels.8` | dict |
| `skills.criticalDamages.levels.8.cost` | int |
| `skills.criticalDamages.levels.8.totalCost` | int |
| `skills.criticalDamages.levels.8.unlockAtLevel` | int |
| `skills.criticalDamages.levels.8.value` | int |
| `skills.criticalDamages.levels.9` | dict |
| `skills.criticalDamages.levels.9.cost` | int |
| `skills.criticalDamages.levels.9.totalCost` | int |
| `skills.criticalDamages.levels.9.unlockAtLevel` | int |
| `skills.criticalDamages.levels.9.value` | int |
| `skills.dodge` | dict |
| `skills.dodge.levels` | dict |
| `skills.dodge.levels.0` | dict |
| `skills.dodge.levels.0.totalCost` | int |
| `skills.dodge.levels.0.unlockAtLevel` | int |
| `skills.dodge.levels.0.value` | int |
| `skills.dodge.levels.1` | dict |
| `skills.dodge.levels.1.cost` | int |
| `skills.dodge.levels.1.totalCost` | int |
| `skills.dodge.levels.1.unlockAtLevel` | int |
| `skills.dodge.levels.1.value` | int |
| `skills.dodge.levels.10` | dict |
| `skills.dodge.levels.10.cost` | int |
| `skills.dodge.levels.10.totalCost` | int |
| `skills.dodge.levels.10.unlockAtLevel` | int |
| `skills.dodge.levels.10.value` | int |
| `skills.dodge.levels.2` | dict |
| `skills.dodge.levels.2.cost` | int |
| `skills.dodge.levels.2.totalCost` | int |
| `skills.dodge.levels.2.unlockAtLevel` | int |
| `skills.dodge.levels.2.value` | int |
| `skills.dodge.levels.3` | dict |
| `skills.dodge.levels.3.cost` | int |
| `skills.dodge.levels.3.totalCost` | int |
| `skills.dodge.levels.3.unlockAtLevel` | int |
| `skills.dodge.levels.3.value` | int |
| `skills.dodge.levels.4` | dict |
| `skills.dodge.levels.4.cost` | int |
| `skills.dodge.levels.4.totalCost` | int |
| `skills.dodge.levels.4.unlockAtLevel` | int |
| `skills.dodge.levels.4.value` | int |
| `skills.dodge.levels.5` | dict |
| `skills.dodge.levels.5.cost` | int |
| `skills.dodge.levels.5.totalCost` | int |
| `skills.dodge.levels.5.unlockAtLevel` | int |
| `skills.dodge.levels.5.value` | int |
| `skills.dodge.levels.6` | dict |
| `skills.dodge.levels.6.cost` | int |
| `skills.dodge.levels.6.totalCost` | int |
| `skills.dodge.levels.6.unlockAtLevel` | int |
| `skills.dodge.levels.6.value` | int |
| `skills.dodge.levels.7` | dict |
| `skills.dodge.levels.7.cost` | int |
| `skills.dodge.levels.7.totalCost` | int |
| `skills.dodge.levels.7.unlockAtLevel` | int |
| `skills.dodge.levels.7.value` | int |
| `skills.dodge.levels.8` | dict |
| `skills.dodge.levels.8.cost` | int |
| `skills.dodge.levels.8.totalCost` | int |
| `skills.dodge.levels.8.unlockAtLevel` | int |
| `skills.dodge.levels.8.value` | int |
| `skills.dodge.levels.9` | dict |
| `skills.dodge.levels.9.cost` | int |
| `skills.dodge.levels.9.totalCost` | int |
| `skills.dodge.levels.9.unlockAtLevel` | int |
| `skills.dodge.levels.9.value` | int |
| `skills.dodge.softCap` | int |
| `skills.energy` | dict |
| `skills.energy.levels` | dict |
| `skills.energy.levels.0` | dict |
| `skills.energy.levels.0.isABar` | bool |
| `skills.energy.levels.0.totalCost` | int |
| `skills.energy.levels.0.unlockAtLevel` | int |
| `skills.energy.levels.0.value` | int |
| `skills.energy.levels.1` | dict |
| `skills.energy.levels.1.cost` | int |
| `skills.energy.levels.1.totalCost` | int |
| `skills.energy.levels.1.unlockAtLevel` | int |
| `skills.energy.levels.1.value` | int |
| `skills.energy.levels.10` | dict |
| `skills.energy.levels.10.cost` | int |
| `skills.energy.levels.10.totalCost` | int |
| `skills.energy.levels.10.unlockAtLevel` | int |
| `skills.energy.levels.10.value` | int |
| `skills.energy.levels.2` | dict |
| `skills.energy.levels.2.cost` | int |
| `skills.energy.levels.2.totalCost` | int |
| `skills.energy.levels.2.unlockAtLevel` | int |
| `skills.energy.levels.2.value` | int |
| `skills.energy.levels.3` | dict |
| `skills.energy.levels.3.cost` | int |
| `skills.energy.levels.3.totalCost` | int |
| `skills.energy.levels.3.unlockAtLevel` | int |
| `skills.energy.levels.3.value` | int |
| `skills.energy.levels.4` | dict |
| `skills.energy.levels.4.cost` | int |
| `skills.energy.levels.4.totalCost` | int |
| `skills.energy.levels.4.unlockAtLevel` | int |
| `skills.energy.levels.4.value` | int |
| `skills.energy.levels.5` | dict |
| `skills.energy.levels.5.cost` | int |
| `skills.energy.levels.5.totalCost` | int |
| `skills.energy.levels.5.unlockAtLevel` | int |
| `skills.energy.levels.5.value` | int |
| `skills.energy.levels.6` | dict |
| `skills.energy.levels.6.cost` | int |
| `skills.energy.levels.6.totalCost` | int |
| `skills.energy.levels.6.unlockAtLevel` | int |
| `skills.energy.levels.6.value` | int |
| `skills.energy.levels.7` | dict |
| `skills.energy.levels.7.cost` | int |
| `skills.energy.levels.7.totalCost` | int |
| `skills.energy.levels.7.unlockAtLevel` | int |
| `skills.energy.levels.7.value` | int |
| `skills.energy.levels.8` | dict |
| `skills.energy.levels.8.cost` | int |
| `skills.energy.levels.8.totalCost` | int |
| `skills.energy.levels.8.unlockAtLevel` | int |
| `skills.energy.levels.8.value` | int |
| `skills.energy.levels.9` | dict |
| `skills.energy.levels.9.cost` | int |
| `skills.energy.levels.9.totalCost` | int |
| `skills.energy.levels.9.unlockAtLevel` | int |
| `skills.energy.levels.9.value` | int |
| `skills.entrepreneurship` | dict |
| `skills.entrepreneurship.levels` | dict |
| `skills.entrepreneurship.levels.0` | dict |
| `skills.entrepreneurship.levels.0.isABar` | bool |
| `skills.entrepreneurship.levels.0.totalCost` | int |
| `skills.entrepreneurship.levels.0.unlockAtLevel` | int |
| `skills.entrepreneurship.levels.0.value` | int |
| `skills.entrepreneurship.levels.1` | dict |
| `skills.entrepreneurship.levels.1.cost` | int |
| `skills.entrepreneurship.levels.1.totalCost` | int |
| `skills.entrepreneurship.levels.1.unlockAtLevel` | int |
| `skills.entrepreneurship.levels.1.value` | int |
| `skills.entrepreneurship.levels.10` | dict |
| `skills.entrepreneurship.levels.10.cost` | int |
| `skills.entrepreneurship.levels.10.totalCost` | int |
| `skills.entrepreneurship.levels.10.unlockAtLevel` | int |
| `skills.entrepreneurship.levels.10.value` | int |
| `skills.entrepreneurship.levels.2` | dict |
| `skills.entrepreneurship.levels.2.cost` | int |
| `skills.entrepreneurship.levels.2.totalCost` | int |
| `skills.entrepreneurship.levels.2.unlockAtLevel` | int |
| `skills.entrepreneurship.levels.2.value` | int |
| `skills.entrepreneurship.levels.3` | dict |
| `skills.entrepreneurship.levels.3.cost` | int |
| `skills.entrepreneurship.levels.3.totalCost` | int |
| `skills.entrepreneurship.levels.3.unlockAtLevel` | int |
| `skills.entrepreneurship.levels.3.value` | int |
| `skills.entrepreneurship.levels.4` | dict |
| `skills.entrepreneurship.levels.4.cost` | int |
| `skills.entrepreneurship.levels.4.totalCost` | int |
| `skills.entrepreneurship.levels.4.unlockAtLevel` | int |
| `skills.entrepreneurship.levels.4.value` | int |
| `skills.entrepreneurship.levels.5` | dict |
| `skills.entrepreneurship.levels.5.cost` | int |
| `skills.entrepreneurship.levels.5.totalCost` | int |
| `skills.entrepreneurship.levels.5.unlockAtLevel` | int |
| `skills.entrepreneurship.levels.5.value` | int |
| `skills.entrepreneurship.levels.6` | dict |
| `skills.entrepreneurship.levels.6.cost` | int |
| `skills.entrepreneurship.levels.6.totalCost` | int |
| `skills.entrepreneurship.levels.6.unlockAtLevel` | int |
| `skills.entrepreneurship.levels.6.value` | int |
| `skills.entrepreneurship.levels.7` | dict |
| `skills.entrepreneurship.levels.7.cost` | int |
| `skills.entrepreneurship.levels.7.totalCost` | int |
| `skills.entrepreneurship.levels.7.unlockAtLevel` | int |
| `skills.entrepreneurship.levels.7.value` | int |
| `skills.entrepreneurship.levels.8` | dict |
| `skills.entrepreneurship.levels.8.cost` | int |
| `skills.entrepreneurship.levels.8.totalCost` | int |
| `skills.entrepreneurship.levels.8.unlockAtLevel` | int |
| `skills.entrepreneurship.levels.8.value` | int |
| `skills.entrepreneurship.levels.9` | dict |
| `skills.entrepreneurship.levels.9.cost` | int |
| `skills.entrepreneurship.levels.9.totalCost` | int |
| `skills.entrepreneurship.levels.9.unlockAtLevel` | int |
| `skills.entrepreneurship.levels.9.value` | int |
| `skills.health` | dict |
| `skills.health.levels` | dict |
| `skills.health.levels.0` | dict |
| `skills.health.levels.0.isABar` | bool |
| `skills.health.levels.0.totalCost` | int |
| `skills.health.levels.0.unlockAtLevel` | int |
| `skills.health.levels.0.value` | int |
| `skills.health.levels.1` | dict |
| `skills.health.levels.1.cost` | int |
| `skills.health.levels.1.totalCost` | int |
| `skills.health.levels.1.unlockAtLevel` | int |
| `skills.health.levels.1.value` | int |
| `skills.health.levels.10` | dict |
| `skills.health.levels.10.cost` | int |
| `skills.health.levels.10.totalCost` | int |
| `skills.health.levels.10.unlockAtLevel` | int |
| `skills.health.levels.10.value` | int |
| `skills.health.levels.2` | dict |
| `skills.health.levels.2.cost` | int |
| `skills.health.levels.2.totalCost` | int |
| `skills.health.levels.2.unlockAtLevel` | int |
| `skills.health.levels.2.value` | int |
| `skills.health.levels.3` | dict |
| `skills.health.levels.3.cost` | int |
| `skills.health.levels.3.totalCost` | int |
| `skills.health.levels.3.unlockAtLevel` | int |
| `skills.health.levels.3.value` | int |
| `skills.health.levels.4` | dict |
| `skills.health.levels.4.cost` | int |
| `skills.health.levels.4.totalCost` | int |
| `skills.health.levels.4.unlockAtLevel` | int |
| `skills.health.levels.4.value` | int |
| `skills.health.levels.5` | dict |
| `skills.health.levels.5.cost` | int |
| `skills.health.levels.5.totalCost` | int |
| `skills.health.levels.5.unlockAtLevel` | int |
| `skills.health.levels.5.value` | int |
| `skills.health.levels.6` | dict |
| `skills.health.levels.6.cost` | int |
| `skills.health.levels.6.totalCost` | int |
| `skills.health.levels.6.unlockAtLevel` | int |
| `skills.health.levels.6.value` | int |
| `skills.health.levels.7` | dict |
| `skills.health.levels.7.cost` | int |
| `skills.health.levels.7.totalCost` | int |
| `skills.health.levels.7.unlockAtLevel` | int |
| `skills.health.levels.7.value` | int |
| `skills.health.levels.8` | dict |
| `skills.health.levels.8.cost` | int |
| `skills.health.levels.8.totalCost` | int |
| `skills.health.levels.8.unlockAtLevel` | int |
| `skills.health.levels.8.value` | int |
| `skills.health.levels.9` | dict |
| `skills.health.levels.9.cost` | int |
| `skills.health.levels.9.totalCost` | int |
| `skills.health.levels.9.unlockAtLevel` | int |
| `skills.health.levels.9.value` | int |
| `skills.hunger` | dict |
| `skills.hunger.levels` | dict |
| `skills.hunger.levels.0` | dict |
| `skills.hunger.levels.0.isABar` | bool |
| `skills.hunger.levels.0.totalCost` | int |
| `skills.hunger.levels.0.unlockAtLevel` | int |
| `skills.hunger.levels.0.value` | int |
| `skills.hunger.levels.1` | dict |
| `skills.hunger.levels.1.cost` | int |
| `skills.hunger.levels.1.totalCost` | int |
| `skills.hunger.levels.1.unlockAtLevel` | int |
| `skills.hunger.levels.1.value` | int |
| `skills.hunger.levels.10` | dict |
| `skills.hunger.levels.10.cost` | int |
| `skills.hunger.levels.10.totalCost` | int |
| `skills.hunger.levels.10.unlockAtLevel` | int |
| `skills.hunger.levels.10.value` | int |
| `skills.hunger.levels.2` | dict |
| `skills.hunger.levels.2.cost` | int |
| `skills.hunger.levels.2.totalCost` | int |
| `skills.hunger.levels.2.unlockAtLevel` | int |
| `skills.hunger.levels.2.value` | int |
| `skills.hunger.levels.3` | dict |
| `skills.hunger.levels.3.cost` | int |
| `skills.hunger.levels.3.totalCost` | int |
| `skills.hunger.levels.3.unlockAtLevel` | int |
| `skills.hunger.levels.3.value` | int |
| `skills.hunger.levels.4` | dict |
| `skills.hunger.levels.4.cost` | int |
| `skills.hunger.levels.4.totalCost` | int |
| `skills.hunger.levels.4.unlockAtLevel` | int |
| `skills.hunger.levels.4.value` | int |
| `skills.hunger.levels.5` | dict |
| `skills.hunger.levels.5.cost` | int |
| `skills.hunger.levels.5.totalCost` | int |
| `skills.hunger.levels.5.unlockAtLevel` | int |
| `skills.hunger.levels.5.value` | int |
| `skills.hunger.levels.6` | dict |
| `skills.hunger.levels.6.cost` | int |
| `skills.hunger.levels.6.totalCost` | int |
| `skills.hunger.levels.6.unlockAtLevel` | int |
| `skills.hunger.levels.6.value` | int |
| `skills.hunger.levels.7` | dict |
| `skills.hunger.levels.7.cost` | int |
| `skills.hunger.levels.7.totalCost` | int |
| `skills.hunger.levels.7.unlockAtLevel` | int |
| `skills.hunger.levels.7.value` | int |
| `skills.hunger.levels.8` | dict |
| `skills.hunger.levels.8.cost` | int |
| `skills.hunger.levels.8.totalCost` | int |
| `skills.hunger.levels.8.unlockAtLevel` | int |
| `skills.hunger.levels.8.value` | int |
| `skills.hunger.levels.9` | dict |
| `skills.hunger.levels.9.cost` | int |
| `skills.hunger.levels.9.totalCost` | int |
| `skills.hunger.levels.9.unlockAtLevel` | int |
| `skills.hunger.levels.9.value` | int |
| `skills.lootChance` | dict |
| `skills.lootChance.levels` | dict |
| `skills.lootChance.levels.0` | dict |
| `skills.lootChance.levels.0.totalCost` | int |
| `skills.lootChance.levels.0.unlockAtLevel` | int |
| `skills.lootChance.levels.0.value` | int |
| `skills.lootChance.levels.1` | dict |
| `skills.lootChance.levels.1.cost` | int |
| `skills.lootChance.levels.1.totalCost` | int |
| `skills.lootChance.levels.1.unlockAtLevel` | int |
| `skills.lootChance.levels.1.value` | int |
| `skills.lootChance.levels.10` | dict |
| `skills.lootChance.levels.10.cost` | int |
| `skills.lootChance.levels.10.totalCost` | int |
| `skills.lootChance.levels.10.unlockAtLevel` | int |
| `skills.lootChance.levels.10.value` | int |
| `skills.lootChance.levels.2` | dict |
| `skills.lootChance.levels.2.cost` | int |
| `skills.lootChance.levels.2.totalCost` | int |
| `skills.lootChance.levels.2.unlockAtLevel` | int |
| `skills.lootChance.levels.2.value` | int |
| `skills.lootChance.levels.3` | dict |
| `skills.lootChance.levels.3.cost` | int |
| `skills.lootChance.levels.3.totalCost` | int |
| `skills.lootChance.levels.3.unlockAtLevel` | int |
| `skills.lootChance.levels.3.value` | int |
| `skills.lootChance.levels.4` | dict |
| `skills.lootChance.levels.4.cost` | int |
| `skills.lootChance.levels.4.totalCost` | int |
| `skills.lootChance.levels.4.unlockAtLevel` | int |
| `skills.lootChance.levels.4.value` | int |
| `skills.lootChance.levels.5` | dict |
| `skills.lootChance.levels.5.cost` | int |
| `skills.lootChance.levels.5.totalCost` | int |
| `skills.lootChance.levels.5.unlockAtLevel` | int |
| `skills.lootChance.levels.5.value` | int |
| `skills.lootChance.levels.6` | dict |
| `skills.lootChance.levels.6.cost` | int |
| `skills.lootChance.levels.6.totalCost` | int |
| `skills.lootChance.levels.6.unlockAtLevel` | int |
| `skills.lootChance.levels.6.value` | int |
| `skills.lootChance.levels.7` | dict |
| `skills.lootChance.levels.7.cost` | int |
| `skills.lootChance.levels.7.totalCost` | int |
| `skills.lootChance.levels.7.unlockAtLevel` | int |
| `skills.lootChance.levels.7.value` | int |
| `skills.lootChance.levels.8` | dict |
| `skills.lootChance.levels.8.cost` | int |
| `skills.lootChance.levels.8.totalCost` | int |
| `skills.lootChance.levels.8.unlockAtLevel` | int |
| `skills.lootChance.levels.8.value` | int |
| `skills.lootChance.levels.9` | dict |
| `skills.lootChance.levels.9.cost` | int |
| `skills.lootChance.levels.9.totalCost` | int |
| `skills.lootChance.levels.9.unlockAtLevel` | int |
| `skills.lootChance.levels.9.value` | int |
| `skills.management` | dict |
| `skills.management.levels` | dict |
| `skills.management.levels.0` | dict |
| `skills.management.levels.0.totalCost` | int |
| `skills.management.levels.0.unlockAtLevel` | int |
| `skills.management.levels.0.value` | int |
| `skills.management.levels.1` | dict |
| `skills.management.levels.1.cost` | int |
| `skills.management.levels.1.totalCost` | int |
| `skills.management.levels.1.unlockAtLevel` | int |
| `skills.management.levels.1.value` | int |
| `skills.management.levels.10` | dict |
| `skills.management.levels.10.cost` | int |
| `skills.management.levels.10.totalCost` | int |
| `skills.management.levels.10.unlockAtLevel` | int |
| `skills.management.levels.10.value` | int |
| `skills.management.levels.2` | dict |
| `skills.management.levels.2.cost` | int |
| `skills.management.levels.2.totalCost` | int |
| `skills.management.levels.2.unlockAtLevel` | int |
| `skills.management.levels.2.value` | int |
| `skills.management.levels.3` | dict |
| `skills.management.levels.3.cost` | int |
| `skills.management.levels.3.totalCost` | int |
| `skills.management.levels.3.unlockAtLevel` | int |
| `skills.management.levels.3.value` | int |
| `skills.management.levels.4` | dict |
| `skills.management.levels.4.cost` | int |
| `skills.management.levels.4.totalCost` | int |
| `skills.management.levels.4.unlockAtLevel` | int |
| `skills.management.levels.4.value` | int |
| `skills.management.levels.5` | dict |
| `skills.management.levels.5.cost` | int |
| `skills.management.levels.5.totalCost` | int |
| `skills.management.levels.5.unlockAtLevel` | int |
| `skills.management.levels.5.value` | int |
| `skills.management.levels.6` | dict |
| `skills.management.levels.6.cost` | int |
| `skills.management.levels.6.totalCost` | int |
| `skills.management.levels.6.unlockAtLevel` | int |
| `skills.management.levels.6.value` | int |
| `skills.management.levels.7` | dict |
| `skills.management.levels.7.cost` | int |
| `skills.management.levels.7.totalCost` | int |
| `skills.management.levels.7.unlockAtLevel` | int |
| `skills.management.levels.7.value` | int |
| `skills.management.levels.8` | dict |
| `skills.management.levels.8.cost` | int |
| `skills.management.levels.8.totalCost` | int |
| `skills.management.levels.8.unlockAtLevel` | int |
| `skills.management.levels.8.value` | int |
| `skills.management.levels.9` | dict |
| `skills.management.levels.9.cost` | int |
| `skills.management.levels.9.totalCost` | int |
| `skills.management.levels.9.unlockAtLevel` | int |
| `skills.management.levels.9.value` | int |
| `skills.precision` | dict |
| `skills.precision.levels` | dict |
| `skills.precision.levels.0` | dict |
| `skills.precision.levels.0.totalCost` | int |
| `skills.precision.levels.0.unlockAtLevel` | int |
| `skills.precision.levels.0.value` | int |
| `skills.precision.levels.1` | dict |
| `skills.precision.levels.1.cost` | int |
| `skills.precision.levels.1.totalCost` | int |
| `skills.precision.levels.1.unlockAtLevel` | int |
| `skills.precision.levels.1.value` | int |
| `skills.precision.levels.10` | dict |
| `skills.precision.levels.10.cost` | int |
| `skills.precision.levels.10.totalCost` | int |
| `skills.precision.levels.10.unlockAtLevel` | int |
| `skills.precision.levels.10.value` | int |
| `skills.precision.levels.2` | dict |
| `skills.precision.levels.2.cost` | int |
| `skills.precision.levels.2.totalCost` | int |
| `skills.precision.levels.2.unlockAtLevel` | int |
| `skills.precision.levels.2.value` | int |
| `skills.precision.levels.3` | dict |
| `skills.precision.levels.3.cost` | int |
| `skills.precision.levels.3.totalCost` | int |
| `skills.precision.levels.3.unlockAtLevel` | int |
| `skills.precision.levels.3.value` | int |
| `skills.precision.levels.4` | dict |
| `skills.precision.levels.4.cost` | int |
| `skills.precision.levels.4.totalCost` | int |
| `skills.precision.levels.4.unlockAtLevel` | int |
| `skills.precision.levels.4.value` | int |
| `skills.precision.levels.5` | dict |
| `skills.precision.levels.5.cost` | int |
| `skills.precision.levels.5.totalCost` | int |
| `skills.precision.levels.5.unlockAtLevel` | int |
| `skills.precision.levels.5.value` | int |
| `skills.precision.levels.6` | dict |
| `skills.precision.levels.6.cost` | int |
| `skills.precision.levels.6.totalCost` | int |
| `skills.precision.levels.6.unlockAtLevel` | int |
| `skills.precision.levels.6.value` | int |
| `skills.precision.levels.7` | dict |
| `skills.precision.levels.7.cost` | int |
| `skills.precision.levels.7.totalCost` | int |
| `skills.precision.levels.7.unlockAtLevel` | int |
| `skills.precision.levels.7.value` | int |
| `skills.precision.levels.8` | dict |
| `skills.precision.levels.8.cost` | int |
| `skills.precision.levels.8.totalCost` | int |
| `skills.precision.levels.8.unlockAtLevel` | int |
| `skills.precision.levels.8.value` | int |
| `skills.precision.levels.9` | dict |
| `skills.precision.levels.9.cost` | int |
| `skills.precision.levels.9.totalCost` | int |
| `skills.precision.levels.9.unlockAtLevel` | int |
| `skills.precision.levels.9.value` | int |
| `skills.precision.skillOverflow` | str |
| `skills.precision.skillOverflowValue` | int |
| `skills.production` | dict |
| `skills.production.levels` | dict |
| `skills.production.levels.0` | dict |
| `skills.production.levels.0.isABar` | bool |
| `skills.production.levels.0.totalCost` | int |
| `skills.production.levels.0.unlockAtLevel` | int |
| `skills.production.levels.0.value` | int |
| `skills.production.levels.1` | dict |
| `skills.production.levels.1.cost` | int |
| `skills.production.levels.1.totalCost` | int |
| `skills.production.levels.1.unlockAtLevel` | int |
| `skills.production.levels.1.value` | int |
| `skills.production.levels.10` | dict |
| `skills.production.levels.10.cost` | int |
| `skills.production.levels.10.totalCost` | int |
| `skills.production.levels.10.unlockAtLevel` | int |
| `skills.production.levels.10.value` | int |
| `skills.production.levels.2` | dict |
| `skills.production.levels.2.cost` | int |
| `skills.production.levels.2.totalCost` | int |
| `skills.production.levels.2.unlockAtLevel` | int |
| `skills.production.levels.2.value` | int |
| `skills.production.levels.3` | dict |
| `skills.production.levels.3.cost` | int |
| `skills.production.levels.3.totalCost` | int |
| `skills.production.levels.3.unlockAtLevel` | int |
| `skills.production.levels.3.value` | int |
| `skills.production.levels.4` | dict |
| `skills.production.levels.4.cost` | int |
| `skills.production.levels.4.totalCost` | int |
| `skills.production.levels.4.unlockAtLevel` | int |
| `skills.production.levels.4.value` | int |
| `skills.production.levels.5` | dict |
| `skills.production.levels.5.cost` | int |
| `skills.production.levels.5.totalCost` | int |
| `skills.production.levels.5.unlockAtLevel` | int |
| `skills.production.levels.5.value` | int |
| `skills.production.levels.6` | dict |
| `skills.production.levels.6.cost` | int |
| `skills.production.levels.6.totalCost` | int |
| `skills.production.levels.6.unlockAtLevel` | int |
| `skills.production.levels.6.value` | int |
| `skills.production.levels.7` | dict |
| `skills.production.levels.7.cost` | int |
| `skills.production.levels.7.totalCost` | int |
| `skills.production.levels.7.unlockAtLevel` | int |
| `skills.production.levels.7.value` | int |
| `skills.production.levels.8` | dict |
| `skills.production.levels.8.cost` | int |
| `skills.production.levels.8.totalCost` | int |
| `skills.production.levels.8.unlockAtLevel` | int |
| `skills.production.levels.8.value` | int |
| `skills.production.levels.9` | dict |
| `skills.production.levels.9.cost` | int |
| `skills.production.levels.9.totalCost` | int |
| `skills.production.levels.9.unlockAtLevel` | int |
| `skills.production.levels.9.value` | int |
| `skills.stamina` | dict |
| `skills.stamina.levels` | dict |
| `skills.stamina.levels.0` | dict |
| `skills.stamina.levels.0.isABar` | bool |
| `skills.stamina.levels.0.totalCost` | int |
| `skills.stamina.levels.0.unlockAtLevel` | int |
| `skills.stamina.levels.0.value` | int |
| `subSkinReward` | dict |
| `subSkinReward.deadline` | str |
| `subSkinReward.skinKey` | str |
| `unrest` | dict |
| `unrest.barMultiplier` | int |
| `unrest.battleCooldownHours` | int |
| `unrest.battleStartCost` | int |
| `unrest.bordersOpenDays` | int |
| `unrest.contributionCooldownAfterRevolutionHours` | int |
| `unrest.contributionCost` | int |
| `unrest.contributionMinLevel` | int |
| `unrest.contributionValue` | int |
| `unrest.nominationPeriodHours` | int |
| `upgrade` | dict |
| `upgrade.constructionContributionMinLevel` | int |
| `upgrade.constructionPointsPerContribution` | int |
| `upgrade.refundPercent` | int |
| `upgrade.regionDowngradeCooldownHours` | int |
| `upgrade.regionUpgradeCooldownHours` | int |
| `upgradesConfig` | dict |
| `upgradesConfig.automatedEngine` | dict |
| `upgradesConfig.automatedEngine.canDowngrade` | bool |
| `upgradesConfig.automatedEngine.levels` | dict |
| `upgradesConfig.automatedEngine.levels.1` | dict |
| `upgradesConfig.automatedEngine.levels.1.constructionPointsCost` | int |
| `upgradesConfig.automatedEngine.levels.1.level` | int |
| `upgradesConfig.automatedEngine.levels.1.stats` | dict |
| `upgradesConfig.automatedEngine.levels.1.stats.dailyProd` | int |
| `upgradesConfig.automatedEngine.levels.1.steelCost` | int |
| `upgradesConfig.automatedEngine.levels.2` | dict |
| `upgradesConfig.automatedEngine.levels.2.constructionPointsCost` | int |
| `upgradesConfig.automatedEngine.levels.2.level` | int |
| `upgradesConfig.automatedEngine.levels.2.stats` | dict |
| `upgradesConfig.automatedEngine.levels.2.stats.dailyProd` | int |
| `upgradesConfig.automatedEngine.levels.2.steelCost` | int |
| `upgradesConfig.automatedEngine.levels.3` | dict |
| `upgradesConfig.automatedEngine.levels.3.constructionPointsCost` | int |
| `upgradesConfig.automatedEngine.levels.3.level` | int |
| `upgradesConfig.automatedEngine.levels.3.stats` | dict |
| `upgradesConfig.automatedEngine.levels.3.stats.dailyProd` | int |
| `upgradesConfig.automatedEngine.levels.3.steelCost` | int |
| `upgradesConfig.automatedEngine.levels.4` | dict |
| `upgradesConfig.automatedEngine.levels.4.constructionPointsCost` | int |
| `upgradesConfig.automatedEngine.levels.4.level` | int |
| `upgradesConfig.automatedEngine.levels.4.stats` | dict |
| `upgradesConfig.automatedEngine.levels.4.stats.dailyProd` | int |
| `upgradesConfig.automatedEngine.levels.4.steelCost` | int |
| `upgradesConfig.automatedEngine.levels.5` | dict |
| `upgradesConfig.automatedEngine.levels.5.constructionPointsCost` | int |
| `upgradesConfig.automatedEngine.levels.5.level` | int |
| `upgradesConfig.automatedEngine.levels.5.stats` | dict |
| `upgradesConfig.automatedEngine.levels.5.stats.dailyProd` | int |
| `upgradesConfig.automatedEngine.levels.5.steelCost` | int |
| `upgradesConfig.automatedEngine.levels.6` | dict |
| `upgradesConfig.automatedEngine.levels.6.constructionPointsCost` | int |
| `upgradesConfig.automatedEngine.levels.6.level` | int |
| `upgradesConfig.automatedEngine.levels.6.stats` | dict |
| `upgradesConfig.automatedEngine.levels.6.stats.dailyProd` | int |
| `upgradesConfig.automatedEngine.levels.6.steelCost` | int |
| `upgradesConfig.automatedEngine.levels.7` | dict |
| `upgradesConfig.automatedEngine.levels.7.constructionPointsCost` | int |
| `upgradesConfig.automatedEngine.levels.7.level` | int |
| `upgradesConfig.automatedEngine.levels.7.stats` | dict |
| `upgradesConfig.automatedEngine.levels.7.stats.dailyProd` | int |
| `upgradesConfig.automatedEngine.levels.7.steelCost` | int |
| `upgradesConfig.automatedEngine.pendingDurationHours` | int |
| `upgradesConfig.base` | dict |
| `upgradesConfig.base.canBeDestroyed` | bool |
| `upgradesConfig.base.canBeDisabled` | bool |
| `upgradesConfig.base.canDowngrade` | bool |
| `upgradesConfig.base.levels` | dict |
| `upgradesConfig.base.levels.1` | dict |
| `upgradesConfig.base.levels.1.constructionPointsCost` | int |
| `upgradesConfig.base.levels.1.level` | int |
| `upgradesConfig.base.levels.1.maintenanceCostCountryDevScale` | float |
| `upgradesConfig.base.levels.1.minimumMaintenanceCost` | int |
| `upgradesConfig.base.levels.1.stats` | dict |
| `upgradesConfig.base.levels.1.stats.attackBonus` | int |
| `upgradesConfig.base.levels.1.steelCost` | int |
| `upgradesConfig.base.levels.2` | dict |
| `upgradesConfig.base.levels.2.constructionPointsCost` | int |
| `upgradesConfig.base.levels.2.level` | int |
| `upgradesConfig.base.levels.2.maintenanceCostCountryDevScale` | float |
| `upgradesConfig.base.levels.2.minimumMaintenanceCost` | int |
| `upgradesConfig.base.levels.2.stats` | dict |
| `upgradesConfig.base.levels.2.stats.attackBonus` | int |
| `upgradesConfig.base.levels.2.steelCost` | int |
| `upgradesConfig.base.levels.3` | dict |
| `upgradesConfig.base.levels.3.constructionPointsCost` | int |
| `upgradesConfig.base.levels.3.level` | int |
| `upgradesConfig.base.levels.3.maintenanceCostCountryDevScale` | float |
| `upgradesConfig.base.levels.3.minimumMaintenanceCost` | int |
| `upgradesConfig.base.levels.3.stats` | dict |
| `upgradesConfig.base.levels.3.stats.attackBonus` | int |
| `upgradesConfig.base.levels.3.steelCost` | int |
| `upgradesConfig.base.levels.4` | dict |
| `upgradesConfig.base.levels.4.constructionPointsCost` | int |
| `upgradesConfig.base.levels.4.level` | int |
| `upgradesConfig.base.levels.4.maintenanceCostCountryDevScale` | float |
| `upgradesConfig.base.levels.4.minimumMaintenanceCost` | int |
| `upgradesConfig.base.levels.4.stats` | dict |
| `upgradesConfig.base.levels.4.stats.attackBonus` | int |
| `upgradesConfig.base.levels.4.steelCost` | int |
| `upgradesConfig.base.levels.5` | dict |
| `upgradesConfig.base.levels.5.constructionPointsCost` | int |
| `upgradesConfig.base.levels.5.level` | int |
| `upgradesConfig.base.levels.5.maintenanceCostCountryDevScale` | float |
| `upgradesConfig.base.levels.5.minimumMaintenanceCost` | int |
| `upgradesConfig.base.levels.5.stats` | dict |
| `upgradesConfig.base.levels.5.stats.attackBonus` | int |
| `upgradesConfig.base.levels.5.steelCost` | int |
| `upgradesConfig.base.pendingDurationHours` | int |
| `upgradesConfig.breakRoom` | dict |
| `upgradesConfig.breakRoom.canDowngrade` | bool |
| `upgradesConfig.breakRoom.levels` | dict |
| `upgradesConfig.breakRoom.levels.1` | dict |
| `upgradesConfig.breakRoom.levels.1.level` | int |
| `upgradesConfig.breakRoom.levels.1.stats` | dict |
| `upgradesConfig.breakRoom.levels.1.stats.dailyHires` | int |
| `upgradesConfig.breakRoom.levels.1.stats.maxWorkers` | int |
| `upgradesConfig.breakRoom.levels.1.steelCost` | int |
| `upgradesConfig.breakRoom.levels.2` | dict |
| `upgradesConfig.breakRoom.levels.2.level` | int |
| `upgradesConfig.breakRoom.levels.2.stats` | dict |
| `upgradesConfig.breakRoom.levels.2.stats.dailyHires` | int |
| `upgradesConfig.breakRoom.levels.2.stats.maxWorkers` | int |
| `upgradesConfig.breakRoom.levels.2.steelCost` | int |
| `upgradesConfig.breakRoom.levels.3` | dict |
| `upgradesConfig.breakRoom.levels.3.level` | int |
| `upgradesConfig.breakRoom.levels.3.stats` | dict |
| `upgradesConfig.breakRoom.levels.3.stats.dailyHires` | int |
| `upgradesConfig.breakRoom.levels.3.stats.maxWorkers` | int |
| `upgradesConfig.breakRoom.levels.3.steelCost` | int |
| `upgradesConfig.breakRoom.levels.4` | dict |
| `upgradesConfig.breakRoom.levels.4.level` | int |
| `upgradesConfig.breakRoom.levels.4.stats` | dict |
| `upgradesConfig.breakRoom.levels.4.stats.dailyHires` | int |
| `upgradesConfig.breakRoom.levels.4.stats.maxWorkers` | int |
| `upgradesConfig.breakRoom.levels.4.steelCost` | int |
| `upgradesConfig.breakRoom.levels.5` | dict |
| `upgradesConfig.breakRoom.levels.5.level` | int |
| `upgradesConfig.breakRoom.levels.5.stats` | dict |
| `upgradesConfig.breakRoom.levels.5.stats.dailyHires` | int |
| `upgradesConfig.breakRoom.levels.5.stats.maxWorkers` | int |
| `upgradesConfig.breakRoom.levels.5.steelCost` | int |
| `upgradesConfig.bunker` | dict |
| `upgradesConfig.bunker.canBeDestroyed` | bool |
| `upgradesConfig.bunker.canBeDisabled` | bool |
| `upgradesConfig.bunker.canDowngrade` | bool |
| `upgradesConfig.bunker.levels` | dict |
| `upgradesConfig.bunker.levels.1` | dict |
| `upgradesConfig.bunker.levels.1.constructionPointsCost` | int |
| `upgradesConfig.bunker.levels.1.level` | int |
| `upgradesConfig.bunker.levels.1.maintenanceCostCountryDevScale` | float |
| `upgradesConfig.bunker.levels.1.minimumMaintenanceCost` | int |
| `upgradesConfig.bunker.levels.1.stats` | dict |
| `upgradesConfig.bunker.levels.1.stats.defenseBonus` | int |
| `upgradesConfig.bunker.levels.1.steelCost` | int |
| `upgradesConfig.bunker.levels.2` | dict |
| `upgradesConfig.bunker.levels.2.constructionPointsCost` | int |
| `upgradesConfig.bunker.levels.2.level` | int |
| `upgradesConfig.bunker.levels.2.maintenanceCostCountryDevScale` | float |
| `upgradesConfig.bunker.levels.2.minimumMaintenanceCost` | int |
| `upgradesConfig.bunker.levels.2.stats` | dict |
| `upgradesConfig.bunker.levels.2.stats.defenseBonus` | int |
| `upgradesConfig.bunker.levels.2.steelCost` | int |
| `upgradesConfig.bunker.levels.3` | dict |
| `upgradesConfig.bunker.levels.3.constructionPointsCost` | int |
| `upgradesConfig.bunker.levels.3.level` | int |
| `upgradesConfig.bunker.levels.3.maintenanceCostCountryDevScale` | float |
| `upgradesConfig.bunker.levels.3.minimumMaintenanceCost` | int |
| `upgradesConfig.bunker.levels.3.stats` | dict |
| `upgradesConfig.bunker.levels.3.stats.defenseBonus` | int |
| `upgradesConfig.bunker.levels.3.steelCost` | int |
| `upgradesConfig.bunker.levels.4` | dict |
| `upgradesConfig.bunker.levels.4.constructionPointsCost` | int |
| `upgradesConfig.bunker.levels.4.level` | int |
| `upgradesConfig.bunker.levels.4.maintenanceCostCountryDevScale` | float |
| `upgradesConfig.bunker.levels.4.minimumMaintenanceCost` | int |
| `upgradesConfig.bunker.levels.4.stats` | dict |
| `upgradesConfig.bunker.levels.4.stats.defenseBonus` | int |
| `upgradesConfig.bunker.levels.4.steelCost` | int |
| `upgradesConfig.bunker.levels.5` | dict |
| `upgradesConfig.bunker.levels.5.constructionPointsCost` | int |
| `upgradesConfig.bunker.levels.5.level` | int |
| `upgradesConfig.bunker.levels.5.maintenanceCostCountryDevScale` | float |
| `upgradesConfig.bunker.levels.5.minimumMaintenanceCost` | int |
| `upgradesConfig.bunker.levels.5.stats` | dict |
| `upgradesConfig.bunker.levels.5.stats.defenseBonus` | int |
| `upgradesConfig.bunker.levels.5.steelCost` | int |
| `upgradesConfig.bunker.pendingDurationHours` | int |
| `upgradesConfig.dormitories` | dict |
| `upgradesConfig.dormitories.canDowngrade` | bool |
| `upgradesConfig.dormitories.levels` | dict |
| `upgradesConfig.dormitories.levels.1` | dict |
| `upgradesConfig.dormitories.levels.1.level` | int |
| `upgradesConfig.dormitories.levels.1.stats` | dict |
| `upgradesConfig.dormitories.levels.1.stats.members` | int |
| `upgradesConfig.dormitories.levels.1.steelCost` | int |
| `upgradesConfig.dormitories.levels.2` | dict |
| `upgradesConfig.dormitories.levels.2.level` | int |
| `upgradesConfig.dormitories.levels.2.stats` | dict |
| `upgradesConfig.dormitories.levels.2.stats.members` | int |
| `upgradesConfig.dormitories.levels.2.steelCost` | int |
| `upgradesConfig.dormitories.levels.3` | dict |
| `upgradesConfig.dormitories.levels.3.level` | int |
| `upgradesConfig.dormitories.levels.3.stats` | dict |
| `upgradesConfig.dormitories.levels.3.stats.members` | int |
| `upgradesConfig.dormitories.levels.3.steelCost` | int |
| `upgradesConfig.dormitories.levels.4` | dict |
| `upgradesConfig.dormitories.levels.4.level` | int |
| `upgradesConfig.dormitories.levels.4.stats` | dict |
| `upgradesConfig.dormitories.levels.4.stats.members` | int |
| `upgradesConfig.dormitories.levels.4.steelCost` | int |
| `upgradesConfig.dormitories.levels.5` | dict |
| `upgradesConfig.dormitories.levels.5.level` | int |
| `upgradesConfig.dormitories.levels.5.stats` | dict |
| `upgradesConfig.dormitories.levels.5.stats.members` | int |
| `upgradesConfig.dormitories.levels.5.steelCost` | int |
| `upgradesConfig.headquarters` | dict |
| `upgradesConfig.headquarters.canBeDisabled` | bool |
| `upgradesConfig.headquarters.canDowngrade` | bool |
| `upgradesConfig.headquarters.levels` | dict |
| `upgradesConfig.headquarters.levels.1` | dict |
| `upgradesConfig.headquarters.levels.1.level` | int |
| `upgradesConfig.headquarters.levels.1.maintenanceCost` | int |
| `upgradesConfig.headquarters.levels.1.stats` | dict |
| `upgradesConfig.headquarters.levels.1.stats.attackBonus` | int |
| `upgradesConfig.headquarters.levels.1.steelCost` | int |
| `upgradesConfig.headquarters.levels.2` | dict |
| `upgradesConfig.headquarters.levels.2.level` | int |
| `upgradesConfig.headquarters.levels.2.maintenanceCost` | int |
| `upgradesConfig.headquarters.levels.2.stats` | dict |
| `upgradesConfig.headquarters.levels.2.stats.attackBonus` | int |
| `upgradesConfig.headquarters.levels.2.steelCost` | int |
| `upgradesConfig.headquarters.levels.3` | dict |
| `upgradesConfig.headquarters.levels.3.level` | int |
| `upgradesConfig.headquarters.levels.3.maintenanceCost` | int |
| `upgradesConfig.headquarters.levels.3.stats` | dict |
| `upgradesConfig.headquarters.levels.3.stats.attackBonus` | int |
| `upgradesConfig.headquarters.levels.3.steelCost` | int |
| `upgradesConfig.headquarters.levels.4` | dict |
| `upgradesConfig.headquarters.levels.4.level` | int |
| `upgradesConfig.headquarters.levels.4.maintenanceCost` | int |
| `upgradesConfig.headquarters.levels.4.stats` | dict |
| `upgradesConfig.headquarters.levels.4.stats.attackBonus` | int |
| `upgradesConfig.headquarters.levels.4.steelCost` | int |
| `upgradesConfig.headquarters.pendingDurationHours` | int |
| `upgradesConfig.pacificationCenter` | dict |
| `upgradesConfig.pacificationCenter.canBeDestroyed` | bool |
| `upgradesConfig.pacificationCenter.canBeDisabled` | bool |
| `upgradesConfig.pacificationCenter.canDowngrade` | bool |
| `upgradesConfig.pacificationCenter.levels` | dict |
| `upgradesConfig.pacificationCenter.levels.1` | dict |
| `upgradesConfig.pacificationCenter.levels.1.constructionPointsCost` | int |
| `upgradesConfig.pacificationCenter.levels.1.level` | int |
| `upgradesConfig.pacificationCenter.levels.1.maintenanceCostRegionDevScale` | float |
| `upgradesConfig.pacificationCenter.levels.1.minimumMaintenanceCost` | int |
| `upgradesConfig.pacificationCenter.levels.1.stats` | dict |
| `upgradesConfig.pacificationCenter.levels.1.stats.resistanceGrowthReduction` | float |
| `upgradesConfig.pacificationCenter.levels.1.steelCost` | int |
| `upgradesConfig.pacificationCenter.levels.2` | dict |
| `upgradesConfig.pacificationCenter.levels.2.constructionPointsCost` | int |
| `upgradesConfig.pacificationCenter.levels.2.level` | int |
| `upgradesConfig.pacificationCenter.levels.2.maintenanceCostRegionDevScale` | float |
| `upgradesConfig.pacificationCenter.levels.2.minimumMaintenanceCost` | int |
| `upgradesConfig.pacificationCenter.levels.2.stats` | dict |
| `upgradesConfig.pacificationCenter.levels.2.stats.resistanceGrowthReduction` | float |
| `upgradesConfig.pacificationCenter.levels.2.steelCost` | int |
| `upgradesConfig.pacificationCenter.levels.3` | dict |
| `upgradesConfig.pacificationCenter.levels.3.constructionPointsCost` | int |
| `upgradesConfig.pacificationCenter.levels.3.level` | int |
| `upgradesConfig.pacificationCenter.levels.3.maintenanceCostRegionDevScale` | float |
| `upgradesConfig.pacificationCenter.levels.3.minimumMaintenanceCost` | int |
| `upgradesConfig.pacificationCenter.levels.3.stats` | dict |
| `upgradesConfig.pacificationCenter.levels.3.stats.resistanceGrowthReduction` | float |
| `upgradesConfig.pacificationCenter.levels.3.steelCost` | int |
| `upgradesConfig.pacificationCenter.levels.4` | dict |
| `upgradesConfig.pacificationCenter.levels.4.constructionPointsCost` | int |
| `upgradesConfig.pacificationCenter.levels.4.level` | int |
| `upgradesConfig.pacificationCenter.levels.4.maintenanceCostRegionDevScale` | float |
| `upgradesConfig.pacificationCenter.levels.4.minimumMaintenanceCost` | int |
| `upgradesConfig.pacificationCenter.levels.4.stats` | dict |
| `upgradesConfig.pacificationCenter.levels.4.stats.resistanceGrowthReduction` | float |
| `upgradesConfig.pacificationCenter.levels.4.steelCost` | int |
| `upgradesConfig.pacificationCenter.levels.5` | dict |
| `upgradesConfig.pacificationCenter.levels.5.constructionPointsCost` | int |
| `upgradesConfig.pacificationCenter.levels.5.level` | int |
| `upgradesConfig.pacificationCenter.levels.5.maintenanceCostRegionDevScale` | float |
| `upgradesConfig.pacificationCenter.levels.5.minimumMaintenanceCost` | int |
| `upgradesConfig.pacificationCenter.levels.5.stats` | dict |
| `upgradesConfig.pacificationCenter.levels.5.stats.resistanceGrowthReduction` | float |
| `upgradesConfig.pacificationCenter.levels.5.steelCost` | int |
| `upgradesConfig.pacificationCenter.pendingDurationHours` | int |
| `upgradesConfig.storage` | dict |
| `upgradesConfig.storage.canDowngrade` | bool |
| `upgradesConfig.storage.levels` | dict |
| `upgradesConfig.storage.levels.1` | dict |
| `upgradesConfig.storage.levels.1.constructionPointsCost` | int |
| `upgradesConfig.storage.levels.1.level` | int |
| `upgradesConfig.storage.levels.1.stats` | dict |
| `upgradesConfig.storage.levels.1.stats.maxProduction` | int |
| `upgradesConfig.storage.levels.1.steelCost` | int |
| `upgradesConfig.storage.levels.2` | dict |
| `upgradesConfig.storage.levels.2.constructionPointsCost` | int |
| `upgradesConfig.storage.levels.2.level` | int |
| `upgradesConfig.storage.levels.2.stats` | dict |
| `upgradesConfig.storage.levels.2.stats.maxProduction` | int |
| `upgradesConfig.storage.levels.2.steelCost` | int |
| `upgradesConfig.storage.levels.3` | dict |
| `upgradesConfig.storage.levels.3.constructionPointsCost` | int |
| `upgradesConfig.storage.levels.3.level` | int |
| `upgradesConfig.storage.levels.3.stats` | dict |
| `upgradesConfig.storage.levels.3.stats.maxProduction` | int |
| `upgradesConfig.storage.levels.3.steelCost` | int |
| `upgradesConfig.storage.levels.4` | dict |
| `upgradesConfig.storage.levels.4.constructionPointsCost` | int |
| `upgradesConfig.storage.levels.4.level` | int |
| `upgradesConfig.storage.levels.4.stats` | dict |
| `upgradesConfig.storage.levels.4.stats.maxProduction` | int |
| `upgradesConfig.storage.levels.4.steelCost` | int |
| `upgradesConfig.storage.levels.5` | dict |
| `upgradesConfig.storage.levels.5.constructionPointsCost` | int |
| `upgradesConfig.storage.levels.5.level` | int |
| `upgradesConfig.storage.levels.5.stats` | dict |
| `upgradesConfig.storage.levels.5.stats.maxProduction` | int |
| `upgradesConfig.storage.levels.5.steelCost` | int |
| `upgradesConfig.storage.levels.6` | dict |
| `upgradesConfig.storage.levels.6.constructionPointsCost` | int |
| `upgradesConfig.storage.levels.6.level` | int |
| `upgradesConfig.storage.levels.6.stats` | dict |
| `upgradesConfig.storage.levels.6.stats.maxProduction` | int |
| `upgradesConfig.storage.levels.6.steelCost` | int |
| `upgradesConfig.storage.levels.7` | dict |
| `upgradesConfig.storage.levels.7.constructionPointsCost` | int |
| `upgradesConfig.storage.levels.7.level` | int |
| `upgradesConfig.storage.levels.7.stats` | dict |
| `upgradesConfig.storage.levels.7.stats.maxProduction` | int |
| `upgradesConfig.storage.levels.7.steelCost` | int |
| `upgradesConfig.storage.pendingDurationHours` | int |
| `user` | dict |
| `user.activeCitizenMinLevel` | int |
| `user.canTakeControlAtLevel` | int |
| `user.chatMinLevel` | int |
| `user.citizenshipDaysCooldown` | int |
| `user.constructionEnergyCost` | int |
| `user.dailyReward` | dict |
| `user.dailyReward.case1` | int |
| `user.dailyReward.money` | int |
| `user.dailyReward.xp` | int |
| `user.dailyXp` | int |
| `user.donationMinLevel` | int |
| `user.energyCostPerAction` | int |
| `user.equipmentSets` | dict |
| `user.equipmentSets.nonPremiumMax` | int |
| `user.equipmentSets.premiumMax` | int |
| `user.fieldsToPopulate` | str |
| `user.isInactiveAfterDays` | int |
| `user.kits` | dict |
| `user.kits.nonPremiumMax` | int |
| `user.kits.premiumMax` | int |
| `user.marketMinLevel` | int |
| `user.maxConstructionPoints` | int |
| `user.maxEnergy` | int |
| `user.maxHunger` | int |
| `user.prestige` | dict |
| `user.prestige.enabled` | bool |
| `user.prestige.enabledLevel` | int |
| `user.prestige.skillBonusLevels` | int |
| `user.prestige.xpMultiplierPerLevelPercent` | int |
| `user.prestige.xpMultiplierPercent` | int |
| `user.prestige.xpPenalty` | int |
| `user.regenDividedBy` | int |
| `user.resetSkillDaysCooldown` | int |
| `user.resetSkillsCostPerPoint` | float |
| `user.takeControlCooldownInDays` | int |
| `user.xpPerAction` | int |
| `worker` | dict |
| `worker.fidelityProductionBonusPercent` | int |
| `worker.maxFidelity` | int |
