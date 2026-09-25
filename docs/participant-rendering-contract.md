# Participant rendering contract for identity integration

Requests 1-9 implemented on 2026-09-24. Identity retrieval and visual design are
subsequent phases; this change does not fetch profiles or modify storage.

## Read-model boundary

`load_participant_report` supplies the existing domain dictionary to
`report._participant_html`. The renderer performs no database or network access.
`rankings[kind]["volume"]` is the authoritative ordered top-ten population for
both tables, for `user`, `mu`, and `country`. Loss/profit rankings and `entities`
remain available to diagnostic exports; they must not expand the displayed set.

Entity rows retain `entity_kind`, `entity_id`, optional `name`, report-basis
turnover fields, `missing_money_count`, `top_buy`, `top_sell`, and `categories`.
`categories[side]` is the complete collection already ordered by descending value
with deterministic signature tie breaks. Each category supplies `item_code`,
`category`, `money`, `quantity`, `share`, `trade_count`, `missing_money_count`, and
`missing_quantity_count`. Preserve these keys and source values during identity
joins. Commodity categories aggregate by item; equipment signatures include the
full stat vector and source condition. Identical displayed labels can intentionally
represent different condition signatures; never merge them in presentation.

Identity enrichment should join by `(entity_kind, entity_id)` in the read-model
layer and supply cached display metadata. Current fallback is `name or entity_id`;
full IDs remain separate columns and CSV keys. Current profile membership must
never alter historical attribution or ranks. Future identity markup belongs in a
shared renderer helper used by both tables, not in calculations or API calls from
report rendering.

## Presentation and exports

Stable table IDs: `participants-{kind}-volume` and
`participants-{kind}-explanations`. Six tables are emitted, including empty states.
The latter ID is retained for compatibility but now means the complete breakdown.
All categories appear once per selected entity/side. Compact summaries may use
`top_buy`/`top_sell`; residual groups are never rendered.

`_participant_amount` formats complete values, `Unknown` for wholly missing data,
and `X known (N missing)` for partial subtotals. Missingness comes from counts,
not whether the known sum equals zero. Shares are percentages, or `Unknown` when
the read model cannot establish a denominator. No cross-category unit total is
computed. `_category_description` omits condition for display; exports explicitly
request `include_condition=True` and retain state/max_state and normalized stats.
Money basis and UTC window appear once in report context. Tables contain no method
footer. Activity Comparison has the explicit `data-report-table` identity
`activity-comparison` to exempt it from generic annotation.

CSV schemas, precision, accounting diagnostics, and underlying records remain
unchanged. Static PNG capture uses the complete table element at intrinsic size.
The current-run inventory contains only current targets. After successful capture,
cleanup deletes only exact losses/profits/coverage PNG filenames for the three
entity kinds within `participant_rankings_7d`; unrelated archives are retained.

## Verification

Focused suites cover shared rendering for all entity kinds and sides, more than
three categories, commodity aggregation, condition-distinct equipment, partial
and missing cells, top-ten selection, escaping, CSV condition/precision retention,
the repaired flip-board path, and real-browser PNG bounds/current inventory.
