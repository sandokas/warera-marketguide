# Phase 0 parser fixtures

Captured 2026-09-22, 20:35:42–20:35:48 UTC. See
[contracts](../../../docs/market-api-contracts.md) and the
[request ledger](../../../docs/market-api-evidence.json).

| File | Provenance and coverage |
| --- | --- |
| `trading.observed.json` | Five selected live rows: personal, seller MU, buyer MU, seller country, buyer country; all six participant reference names; long decimal money |
| `itemMarket.observed.json` | Five selected live sales: armor, dodge, attack + criticalChance, precision, criticalDamages; equipment type present/absent; source acquisition timestamps |
| `orders.observed.json` | Complete six-entry steel top-orders response at limit 3; user + MU bid, distinct same-price bids/asks |
| `edge-cases.synthetic.json` | Explicitly invented conflicts, nulls, unsupported party, unknown scalar/array/key shapes, absent skills/acquisition time, distinct-ID identical stats, unverified same-ID resale, zero order |

Observed transaction files are representative subsets, not complete pages.
Every 24-hex ID was consistently replaced across all three observed files with
an ordinal 24-hex pseudonym. No reverse mapping is saved. Timestamps, numeric
values, field presence, codes and skills are unchanged after JSON decoding.
`__v` is retained here solely as exclusion-test input. `nextCursor` is removed,
not replaced by null; omission does not establish exhaustion. No key, profile,
username, current membership, or live cursor is saved.

Synthetic cases alter observed shapes and explicitly declare expectations for
future parsers/calculators. They are not evidence of server behavior. In particular,
same-ID resale is not a verified equipment chain, and country/party/zero order
variants are not live observations in this capture. No fixture proves zero fees.
