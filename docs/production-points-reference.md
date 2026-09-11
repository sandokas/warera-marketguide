# Production Points by Factory Item

This table records the fixed production-chain ratios used by the report's `PP-equivalent Volume` calculation and the
`total_production_points` read-model field:

```text
PP-equivalent Volume = completed units × Total upstream PP per item
```

The values are a reference snapshot of the War Era Wiki's
[Company production and recipes table](https://warera.wiki/company) plus the WarEra game
configuration recorded when this reference was checked on 2026-07-15. This is a historical source
snapshot, not a fresh verification of the live game or wiki. At that check the wiki table did not
list Wood and Paper; their recorded game configuration was Wood `{productionPoints: 1}` and Paper
`{productionPoints: 1, productionNeeds: {wood: 1}}`. The application obtains direct
`productionPoints` from that configuration during sync. Total upstream PP is a fixed game-mechanics
mapping in `metrics.py`; it must be updated with this reference if WarEra changes a recipe.

| Factory item | Direct PP : Item | Other item consumed by direct recipe | Total PP : Item |
| --- | ---: | --- | ---: |
| Grain | 1 : 1 | — | 1 : 1 |
| Limestone | 1 : 1 | — | 1 : 1 |
| Lead | 1 : 1 | — | 1 : 1 |
| Petroleum | 1 : 1 | — | 1 : 1 |
| Coca (Mysterious Plant) | 1 : 1 | — | 1 : 1 |
| Iron | 1 : 1 | — | 1 : 1 |
| Wood | 1 : 1 | — | 1 : 1 |
| Livestock | 20 : 1 | — | 20 : 1 |
| Fish | 40 : 1 | — | 40 : 1 |
| Steel | 10 : 1 | 10 Iron | 20 : 1 |
| Concrete | 10 : 1 | 10 Limestone | 20 : 1 |
| Oil | 1 : 1 | 1 Petroleum | 2 : 1 |
| Bread | 10 : 1 | 10 Grain | 20 : 1 |
| Steak | 20 : 1 | 1 Livestock | 40 : 1 |
| Cooked Fish | 40 : 1 | 1 Fish | 80 : 1 |
| Paper | 1 : 1 | 1 Wood | 2 : 1 |
| Light Ammo | 1 : 1 | 1 Lead | 2 : 1 |
| Ammo | 4 : 1 | 4 Lead | 8 : 1 |
| Heavy Ammo | 16 : 1 | 16 Lead | 32 : 1 |
| Cocain (Pill) | 200 : 1 | 200 Mysterious Plant | 400 : 1 |

`Direct PP : Item` means Production Points consumed in the final item's own factory. `Total PP :
Item` adds the PP required to produce every ingredient. The report uses **Total PP : Item** because
its activity bar compares the full production effort embodied in each traded item.

The wiki labels the raw material as `Mysterious Plant` and the 400-total-PP processed item as `Pill`.
The report treats the market names `Coca` and `Cocain`/`Cocaine` as those respective factory items.

PP-equivalent Volume does not claim that the traded quantity was produced during the reporting
window. Item rows must not be summed into a market-wide PP total because a traded ingredient and a
traded processed item can represent overlapping production effort.
