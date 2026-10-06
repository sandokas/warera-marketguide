# Participant rendering contract for identity integration

Updated 2026-10-06 for the performance plan's integration contract. Identity
enrichment remains outside rendering; renderers do not fetch profiles or modify storage.

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
The latter ID is retained for compatibility and displays `top_items`: combined
buy/sell category rows selected by the shared calculation helper. This is the
shortest descending-turnover prefix reaching at least 80% of the entity's
same-basis turnover, including the crossing row. There is no row cap or equal-tie
expansion; category signatures break ties deterministically. Exact arithmetic,
not rounded display amounts, controls selection. `detail_selection` records
threshold, selected turnover/share/count, total row count and completeness/status.
Missing money keeps all rows and marks coverage partial/unknown. Known zero
turnover selects no positive prefix. The coverage-policy note sits outside tables.
Standalone targeted summaries consume the same selection. Compact summaries may
use `top_buy`/`top_sell`; residual groups are never rendered.

`item_categories` and `categories[side]` remain complete window collections for
every analyzed entity, including those outside the displayed top ten. Do not
replace these arrays with the selected prefix during identity joins or export.
Complete category turnover must reconcile to the entity's same-basis denominator.

`_participant_amount` formats complete values, `Unknown` for wholly missing data,
and `X known (N missing)` for partial subtotals. Missingness comes from counts,
not whether the known sum equals zero. Shares are percentages, or `Unknown` when
the read model cannot establish a denominator. No cross-category unit total is
computed. `_category_description` omits condition for display; exports explicitly
request `include_condition=True` and retain state/max_state and normalized stats.
Money basis and UTC window appear once in report context. Tables contain no method
footer. Activity Comparison has the explicit `data-report-table` identity
`activity-comparison` to exempt it from generic annotation.

Item and per-side breakdown CSVs retain every window category, source condition
and normalized stat, including categories omitted from PNGs. Ranking CSVs include
selection/context/accounting metadata. Default window accounting skips inventory
replay; matched realized P&L and historical costing diagnostics remain unavailable
with `accounting_status=not_calculated` and blank CSV cells. Explicit `full-fifo`
replays earlier buys and dispositions, but adds no earlier turnover to the window.
`calculated` does not certify complete fees, basis or source coverage. Item
comparison CSV columns use `window_*` names and `comparison_basis`; domain legacy
aliases describe window comparisons, never realized P&L or total holdings.

The shared frozen context uses database C inclusively by default, historical
explicit T <= C exclusively, and clamps future requests to inclusive C. The query
epsilon never shifts the logical start or chart axis; generation time is separate.
Static PNG capture uses the complete table element at intrinsic size.
Geometry validates header/body/footer column edges, spans, overflow and PNG
dimensions with computed border/rounding tolerances; it does not require text to
fill each cell. No clipping, forced widths, scrolling or surrounding headings.
The current-run inventory contains only current targets. After successful capture,
cleanup deletes only exact losses/profits/coverage PNG filenames for the three
entity kinds within `participant_rankings_7d`; unrelated archives are retained.

## Verification

Focused suites cover shared rendering for all entity kinds and sides, more than
three categories, 80-row exact prefixes, complete CSV retention, commodity
aggregation, condition-distinct equipment, partial
and missing cells, top-ten selection, escaping, CSV condition/precision retention,
the repaired flip-board path, and real-browser PNG bounds/current inventory.
