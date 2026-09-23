# Market API contracts — phase 0

Verified on 2026-09-22 against API documentation version `0.17.4-beta` and
public frontend build `d88vhlzaJYP486NJ15SZf`. This is a bounded observation,
not a backend specification or a historical completeness claim. No production
parser, database, importer, or report behavior is changed by this phase.

## Evidence and reproducibility

The checked-in [evidence ledger](market-api-evidence.json) records exact request
inputs (excluding ephemeral cursor values), request times, counts, page checks,
public asset URLs/hashes, and relevant official request schemas. The
[fixtures](../tests/fixtures/market_contracts/README.md) retain selected sanitized
responses. All live requests were GET, authenticated only on
`https://api2.warera.io/trpc`; public assets were fetched without credentials.

Evidence identifiers used below:

| ID | First-party source and locator | What it establishes |
| --- | --- | --- |
| D1 | [Swagger initialization](https://api2.warera.io/docs/swagger-ui-init.js), `swaggerDoc.paths` | Request schemas; no response schema for transactions/orders |
| L1 | `trading-1`, `trading-2` in ledger; 105 records | Global commodity stream, all six user/MU/country side references |
| L2 | `itemMarket-1`, `itemMarket-2`; 105 records | Equipment sales and per-sale item snapshots |
| L3 | `type-array`; 3 records, both types returned | Array type filter accepted |
| L4 | `steel-orders`; three bids and three asks | Individual order fields, MU reference and distinct same-price orders |
| F1 | [Shared frontend](https://app.warera.io/_next/static/chunks/pages/_app-a1535bfe9f68148d.js), transaction renderer: search `w.sellerCountryId`, `w.buyerMuId` | Institution labels plus separate user avatar; same rendering branch for both market types |
| F2 | Same asset, modules `57652`, `52982`, and `tradingOrder.createOrder` form | Selected inventory supplies owner references; accompanying user is rendered as avatar |
| F3 | Same asset, transaction renderer and modules `37732`, `96691` | Transaction display divides money by quantity; tax wrapper only transforms amounts when a tax type is supplied |
| F4 | [Equipment frontend](https://app.warera.io/_next/static/chunks/pages/market/equipments-2d19f68208077040.js), `Taxed price` and `taxType:"market"` | Current displayed equipment purchase price includes viewer-country market tax |
| F5 | [Market frontend](https://app.warera.io/_next/static/chunks/pages/market-9a4406c9208b8b85.js), `getAllOrdersByOwner` | Selected inventory user/MU/country used as order owner filters |

Assets are versioned; use the recorded build manifest to rediscover them if
removed. Hashes identify the exact inspected content. Frontend logic is evidence
of current presentation and request construction, not proof of backend settlement.

## Participants: actor and economic account

L1 has user-only trades, seller user + MU, buyer user + MU, seller user + country,
and buyer user + country. These are separate observed records, not fabricated
institutional examples. L2's sampled sides have user references only.

User clarification after phase 0 confirms the intended commodity attribution:
players execute trades, but a side's MU/country source ID identifies the economic
buyer/seller. This is an explicit product rule as well as being consistent with
the frontend evidence below; do not label ordinary single-institution commodity
sides unresolved merely because a user actor is also present.

Whether MUs/countries can buy or sell equipment remains unverified. The user
suspects they cannot; that is not an API capability guarantee. User-only equipment
samples do not prove a restriction. Do not hardcode equipment participants to
users, reject an institutional equipment record, or infer permission from whether
the entity can equip/use the item. If such a record arrives, retain all references
and apply the source-account rules below; flag genuinely conflicting references.

F1 displays each supplied institution reference independently, suppresses the
primary user label when any institutional reference is present, and retains a
separate user avatar. The predicate includes `sellerMuId`, `sellerCountryId`,
`sellerPartyId` (and corresponding buyer fields). Its special alternate branch
is for open/craft/dismantle operations, not `trading` or `itemMarket`.
F2 reads `marketInventoryId` and obtains that inventory; the commodity form sends
the inventory's `user`, `mu`, `country`, `party` as owner arguments. F5 requests
orders by those inventory references. Together these support the following
account-attribution contract; they do not prove the precise human who clicked
for a standing order fill.

| Side references | Attribution decision |
| --- | --- |
| Valid user alone, no institution reference | Personal user account |
| Exactly one valid MU or country reference, with or without user | That institutional account; retain user separately as source actor reference |
| Exactly one party reference | Unsupported ranking kind; retain it, exclude from the nine requested boards |
| Multiple institution kinds, malformed/contradictory reference, or no resolvable reference | Unresolved; exclude that side from ownership rankings and disclose count/value |

Resolve buyer and seller independently using only their own side's references.
A personal buyer purchasing from an MU seller contributes a personal buy and an
MU sale. A country buyer purchasing from an MU seller likewise has two resolved
institutional sides. Different account kinds across the two sides are normal,
not a conflict. For example, `buyerCountryId` plus `sellerMuId` is unambiguous;
`buyerCountryId` plus `buyerMuId` is a conflict on the buyer side only. An
unresolved side does not invalidate an otherwise resolved counterparty. Preserve
both sides; do not assign one owner kind to the entire transaction.

Do not assign country priority over MU (or vice versa). F1 would render both;
it supplies no economic precedence. Simultaneous MU/country references were not
observed in the selected examples; the conflict fixture is synthetic. Explicit
null, omission and invalid IDs must remain distinguishable; null is not an
institution ID, and omission during enrichment must not erase a previous fact.
Do not fall back to the user when an institutional reference is invalid.

`sellerPartyId`/`buyerPartyId` are now confirmed frontend field names, but were
not returned in these live market samples. Preserve if received; do not claim
live market occurrence. Generic frontend `processedByModAt` is also a known
potential scalar, not an observed market field; retain through extensions until
mapped. A source user reference is not evidence of current membership, and
current citizenship/MU/profile membership must never establish historical account
ownership. Do not credit institutional activity to the actor's personal ranking.

## Money, fees, precision and reporting

`money` is the source transaction amount; `quantity` is separate. F3 displays
`money / quantity` as a per-unit amount for both market types, without supplying
a tax type to its money wrapper. This supports treating money as a total source
amount, not a unit price. It does **not** establish gross buyer consideration,
seller net receipt, tax inclusion, or settlement balances.

F4 labels displayed equipment prices as taxed and supplies `taxType:"market"`
for the offer price. F3's tax helper computes `r*(1+l/100)` from the current
viewer-country tax rate. This is current purchase-display evidence consistent
with a buyer-side surcharge, not verified historical fee payer/amount. No market
mutation was executed and no balance reconciliation was attempted.

| Question | Result and required fallback |
| --- | --- |
| Commodity money gross versus net | Unresolved; keep source amount separately; no fee-free assertion |
| Equipment money gross versus net | Unresolved; UI taxed offer price cannot be equated to transaction money |
| Fee payer and exact amount | Unresolved for both streams; absence of a fee field is not zero fee |
| Fee rounding, minimums and historical rule | Unresolved; frontend display precision is not settlement rounding; current tax cannot be applied retrospectively |
| Known source precision | Observed commodity `0.804`, `1.907`, `36.480000000000004`; equipment `45.938`; millisecond UTC timestamps |

Use exact source decimal text in later normalization, with float projections only
for existing calculations. These fixtures preserve decoded numeric values, not
original HTTP number lexemes. Do not impose two-decimal rounding or silently
"correct" the observed long decimal. A future decimal-aware transport/parser must
preserve token precision before float conversion; this phase does not implement it.

Unknown fees mean **net P&L unavailable**, including otherwise matched commodity
lots. A gross P&L also requires verified gross monetary semantics; do not merely
rename a difference of unidentified source amounts as gross profit. Collection,
equipment descriptions and attributed source-money activity totals can proceed.
Label such totals **source-money turnover**, not verified gross spend/receipt;
keep gross/net fields unavailable until evidence supports them. Never rank a
guessed net profit/loss. Missing money/quantity is not zero; preserve the record
and flag the affected metric unavailable.

## Equipment identity and acquisition basis

L2 contains 105 distinct `item._id` values, no repeated instance across the two
pages. Thus this sample does not contain a verified acquisition/resale pair.
F2 looks up current inventory items by `_id`, which proves a current lookup key,
not stability through transfer. No first-party guarantee of transfer continuity
was found. IDs must remain opaque: their apparent timestamps, including proximity
to sale creation, do not establish physical lineage. Sanitized IDs deliberately
have no original timestamp structure.

`lastAcquisitionAt` is observed before sale time, but neither proves a market
purchase nor supplies cost or prior owner. The sampled item shapes include
single skills and a two-skill weapon; `item.type` is absent for that weapon.
Preserve every sale's own skills, condition and acquisition timestamp.

Acquisition identity/cost remains **unverifiable and uncosted** in this contract.
Even equal instance IDs cannot activate costing until continuity is independently
verified. Equal code, stats or condition never establish the same physical item.
Do not match equipment with commodity FIFO, infer cost from current prices, or
treat crafted/found equipment as a free acquisition. Future verification needs
an authoritative continuity contract or linked acquisition/resale evidence with
account lineage; collecting these sales now does not depend on finding it.

## Pagination and lookup contracts

D1 specifies integer limits greater than zero, maximum 100, default 10 for both
transactions and top orders. It documents optional `cursor`, `userId`, `muId`,
`countryId`, `partyId`, `itemCode`, and string-or-array `transactionType` for
transactions. No date, seek-by-ID, or sort argument appears; additional properties
are disallowed by the documented schema. Despite generated `post` entries, its
top-level description says calls use GET; all live checks used GET.

L1/L2 each returned 100 then five records using the returned cursor, with zero
ID overlap and descending `createdAt` both within and across pages. Timestamps
carry three fractional UTC digits. This verifies observed newest-first behavior,
not a guaranteed equal-timestamp tie order, snapshot consistency under concurrent
inserts, maximum retention, cursor lifetime or exhaustion behavior. Do not truncate
to seconds. An ID tie-break may make local replay deterministic but cannot be
claimed to be upstream causal order; ambiguous costing order needs explicit flags.

L3 verifies the documented type array, including both types in the response.
Retain two separate streams for coverage/status and failure isolation; no combined
stream change is needed. L4 confirms limit three returned three orders per side,
not three total; full market depth is not established. Do not probe unsupported
date or seek arguments, construct cursors from IDs, or treat errors as exhaustion.
Keep `nextCursor` only in memory. Restart replays from the head; durable event
markers do not enable historical API seek. These fixture subsets omit the cursor
envelope deliberately and must not be treated as exhausted live pages.

D1 documents `user.getUserLite(userId)`, `mu.getById(muId)`, and
`country.getCountryById(countryId)`. F3 also uses `country.getAllCountries`.
These are name-lookup candidates, not verified token-access/response contracts in
this phase. No profiles were fetched. Names remain optional, dated display data;
entity kind + ID is the fallback and reports must work offline.

## Scalar retention contract for phase 1

All scalar paths observed in L1/L2/L4 and the prior audit are accounted for here.
This is a mapping obligation, not a claim that the existing parser stores them.

| Source paths (relative to record) | Normalized destination/handling |
| --- | --- |
| Transaction `_id`, `transactionType`, `itemCode`, `money`, `quantity`, `createdAt`, `updatedAt`, `offerCreatedAt` | Transaction identity/type/item, exact source numeric values and source timestamps |
| `sellerId`, `buyerId`, `sellerMuId`, `buyerMuId`, `sellerCountryId`, `buyerCountryId` | Separate participant source columns; derived owner never replaces source references |
| `sellerPartyId`, `buyerPartyId` (frontend only) | Optional party source columns; unsupported ranking kind |
| `item._id`, `item.type`, `item.code`, `item.state`, `item.maxState`, `item.quantity`, `item.lastAcquisitionAt` | Per-transaction equipment snapshot with presence/null information |
| `item.skills.*` | Open skill-code/value rows; observed armor, attack, criticalChance, criticalDamages, dodge, precision; not a closed enumeration |
| Order `_id`, `user`, `mu`, `itemCode`, `quantity`, `price`, `offerAt`, `type` | Individual order identity/references, exact numeric values, source offer time, source type plus envelope side/position |
| Order `country` (prior audit candidate, not observed here) and future owner references | Preserve if present; no membership inference; unmapped fields use extensions |
| `processedByModAt` (frontend only), all other unknown scalar leaves | Normalized extra-field rows plus visible diagnostic, not raw JSON |
| Any record/nested `__v` | Intentionally excluded |
| Response pagination `nextCursor` | Temporary in-memory state only; no DB/log/cursor fixture value |

Order observation time does not replace `offerAt`. Preserve distinct same-price
orders and zero placeholders; only executable-depth calculations discard zero
price/quantity. No historical individual orders may be fabricated from aggregates.

Later parsers must account for every scalar leaf, including booleans, explicit
nulls and array elements, before acknowledging normalization coverage. Known
fields have typed destinations; unknown paths require lossless scalar extensions
and diagnostics. Use escaped path segments (for example JSON Pointer `~0`/`~1`)
and array indices so unusual keys do not collide. Do not serialize containers or
store raw response JSON in SQLite. Unknown structures that cannot be normalized
must produce an explicit error/quarantine status, never silent success. Exclude
only the envelope cursor, not an unrelated business leaf with the same name.

Fixture inventory tests guard the observed scalar inventory now. Runtime unknown
field preservation and presence-aware enrichment remain phase 1/2 acceptance
gates; they are not retroactively claimed for the current application.
