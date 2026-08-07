# Highlighted Price-Action Charts and Header Export

Status: proposed.

## Purpose

The report highlights markets with the largest gaps between the latest completed trade and the
strict 7D blended fair estimate. Readers should be able to inspect the 30-day price action behind
those highlights before reaching the first decision table.

This feature adds:

- up to two highlighted-item candlestick charts immediately before **Fair Value & Buy / Sell
  Signals**;
- responsive placement using one PNG per selected item;
- a PNG export of the hero header plus all rendered highlight cards; and
- a later, independently deliverable support/resistance enhancement.

The feature must preserve the consolidated one-report contract: the report contains 1D, 7D, and
30D perspectives, while highlight ranking and guidance remain explicitly based on strict 7D fair
value.

## Existing behavior

The consolidated report already:

- loads `1D`, `7D`, and `30D` statistics together;
- renders a hero declaring the combined horizons;
- renders Largest Price Gaps cards using strict 7D blended fair value;
- displays strict `Fair 1D`, `Fair 7D`, and `Fair 30D` columns; and
- keeps guidance and Completed Market Activity explicitly 7D-based.

Highlight selection currently lives inside report rendering and retains no `item_code`. The
existing optional chart flow instead selects the first viable report row, generates one
`featured-trade.png`, and places it near the end of the report. Its history window is still coupled
to `--lookback-days`.

The existing chart volume is transaction quantity: the sum of item units traded in each candle.

## Definitions

### Strict 7D price gap

For an item with a latest completed-transaction price `P` and strict 7D blended fair estimate `F`:

```text
gap_pct = (P - F) / F * 100
```

`F` must be `stable_fair_price_7d`. Do not fall back to another horizon, the lagging price endpoint,
an order-book midpoint, bid, ask, VWAP, median, or average.

- `gap_pct < 0`: discount candidate.
- `gap_pct > 0`: premium candidate.
- `gap_pct == 0` within normal numeric comparison tolerance: neutral and not a highlight fallback.

No new minimum-magnitude threshold is introduced in this feature.

### Chart-capable item

An item is chart-capable when it has:

- a stable `item_code`;
- a latest completed-transaction price;
- a strict 7D blended fair estimate;
- valid completed transactions inside the trailing 30 calendar days; and
- enough populated candle intervals to pass the evidence rules below at one supported interval.

Current order-book observations do not create candles and do not make an otherwise unsupported
item chart-capable.

## Shared highlighted-item selection

Extract highlight selection from HTML construction into one reusable, deterministic function. It
must return structured domain values containing at least:

```text
item_code
item_name
role
rank_within_role
latest_completed_price
fair_7d
gap_pct
```

The cards and chart orchestration must consume the same selected values. Do not maintain one
ranking in `report.py` and another in `cli.py`.

Selection operates over chart-capable items so every rendered card has a corresponding chart:

1. If discounts and premiums both exist, select the largest discount and largest premium.
2. If only discounts exist, select the largest and second-largest discounts.
3. If only premiums exist, select the largest and second-largest premiums.
4. If only one chart-capable non-neutral item exists, select it.
5. If none exist, select nothing.

The second same-side card must be labelled `Second-largest discount` or `Second-largest premium`.
Never label a premium as a discount or manufacture an absent category.

Stable tie-breaking is required. After gap severity, use a deterministic secondary key such as
normalized item code. Document and test the chosen rule.

If the numerically largest gap lacks sufficient chart history, continue through that category's
ranking to the first chart-capable item. The card therefore represents the largest chart-capable
gap, not necessarily the largest gap among all database items. Report copy or accessible text must
make the chart-history requirement understandable without cluttering the visible card.

## Report layout

The relevant report order becomes:

```text
Hero header
Highlighted Price Gaps cards
Highlighted price-action charts
Fair Value & Buy / Sell Signals
Current Order Book
Completed Market Activity
Market Trends
Item Notes
```

Each highlighted card and its chart belong to the same responsive highlight column.

Wide layout:

```text
Card 1  | Card 2
Chart 1 | Chart 2
```

Narrow layout:

```text
Card 1
Chart 1
Card 2
Chart 2
```

Use the same chart PNG at every breakpoint. Do not generate separate desktop and mobile image
versions.

When one item is selected, its card and chart occupy the full available width. When no item is
selected, omit both the cards and chart area; do not render `N/A`, empty cards, or empty chart
containers. The hero remains visible.

The report must remain usable when chart generation fails after selection. Fail closed by omitting
the unmatched card/chart pair and preserving a valid report rather than showing a card for a
missing image.

## Chart data and candle construction

### History

Each chart uses exactly the trailing 30 calendar days of stored completed transactions, evaluated
against the report generation time. This is independent of `--lookback-days` and the sync/backfill
scope. If the database does not contain 30 days of history, use the available portion and apply the
same evidence thresholds.

Use UTC-aligned intervals because the market operates continuously. Do not apply exchange sessions,
weekends, opening hours, or overnight closures.

### Adaptive interval

Supported intervals, in preference order, are:

```text
4h -> 8h -> 12h -> 1D
```

Choose the finest interval that meets an explicit populated-candle evidence threshold. The exact
threshold must be centralized, documented in code, and covered by boundary tests. The initial
recommended rule is at least 12 populated candles and at least 3 distinct UTC days. If no interval
qualifies, the item is not chart-capable.

The chart title or subtitle must state the chosen interval and the available history span.

Do not fabricate OHLC values for empty intervals. Do not treat zero volume as a completed trade.
Elapsed-time gaps must not be described as active trading.

Visual padding currently applied to flat candles exists only to make a candle visible. Analytical
indicators and later support/resistance calculations must use unmodified OHLC data.

### Candles

For each populated UTC interval:

- Open: first valid completed unit price.
- High: highest valid completed unit price.
- Low: lowest valid completed unit price.
- Close: last valid completed unit price.
- Units traded: sum of positive transaction quantities.

All values come from normalized completed transactions stored in SQLite.

### Volume panel

Render quantity bars below the price candles. Label the panel `Units traded`, not merely `Volume`.
Do not substitute monetary value, transaction count, order-book depth, or PP-equivalent volume.

### Moving average

Render one evidence-gated 7-calendar-day simple moving average of candle closes.

- Define the rolling window by elapsed UTC time, not by a hard-coded number of non-empty rows.
- Require a centralized minimum number of valid closes inside the rolling period.
- Do not draw the line where the minimum evidence is unavailable.
- Recalculate after selecting the candle interval.
- Label it `7D SMA`.

This is an SMA, not a quantity-weighted average. VWMA is out of scope for the first phase.

### Reference line

Render the selected item's strict 7D blended fair estimate as a clearly labelled horizontal
reference line. Do not recalculate fair value in `charts.py` and do not substitute another horizon.

## Image outputs

Use stable, role-based filenames under the existing chart output directory:

```text
charts/largest-discount-price-action.png
charts/largest-premium-price-action.png
```

When both selected items come from the same side, use:

```text
charts/largest-discount-price-action.png
charts/second-largest-discount-price-action.png
```

or the corresponding premium names. With one selected item, produce only its role-based file.

Do not leave a stale second chart referenced in the report when the current run selects only one or
zero items. Handling deletion of obsolete output files must respect the repository's destructive
action rules; prefer generating a fresh output directory or excluding unselected paths from the
report over broad cleanup.

Charts must be readable independently of the HTML report: include item name, role, 30D scope,
chosen candle interval, price-axis label, units-volume label, and an indicator legend.

## Header PNG export

Add an explicit HTML wrapper around:

- the hero header; and
- all rendered highlight cards.

Do not include the candle charts, first table, or surrounding report whitespace in this wrapper.

Export the wrapper as:

```text
sections/report-header.png
```

If no items are selected, export the hero alone. The header PNG is still useful as the report's
publication header.

Keep existing table PNG behavior unchanged: each table PNG must still capture only its table.
Implement section-image capture separately or extend the exporter with explicit target types; do
not broaden table selectors so headings or cards enter table images.

The exported header must fit completely within its image canvas at the established desktop report
viewport. It must contain no clipped text, scrollbars, or unrelated whitespace.

## Architecture boundaries

All rules in `AGENTS.md` remain mandatory.

- `market_store.py` remains the only SQLite layer.
- `market_data.py` provides normalized 30D chart data and report-selection inputs.
- Selection and indicator calculations belong in a calculation/read-model layer, not HTML or CLI
  orchestration. Choose the narrowest existing layer consistent with `AGENTS.md`; do not introduce
  database dependencies into calculations.
- `charts.py` receives domain data/dataframes and renders PNGs only.
- `report.py` renders HTML from selected items and image paths only.
- `cli.py` orchestrates selection, data loading, chart rendering, report writing, and optional PNG
  export. It must not contain ranking formulas, SQL, OHLC formulas, or indicator calculations.
- Do not call the WarEra API from `market_data.py`, `metrics.py`, `charts.py`, or `report.py`.
- Do not duplicate highlight selection or candle construction.

The report must remain capable of rendering from supplied dataframe/compatibility input. If strict
item codes or DB-backed 30D trades are unavailable, omit chart-linked highlights safely rather than
inventing chart data.

## Phased delivery

Each phase is independently valuable and must leave the project in a passing, publishable state.
Do not begin a later phase until the prior phase's success criteria pass.

### Phase 1: Shared highlights and responsive price-action charts

Deliver:

- one reusable chart-capable highlight-selection path;
- up to two role-based 30D candlestick PNGs;
- adaptive `4h -> 8h -> 12h -> 1D` intervals;
- units-traded volume beneath candles;
- evidence-gated 7D SMA;
- strict 7D fair reference line;
- responsive card/chart pairing immediately before the first table; and
- removal or retirement of the unrelated one-chart-at-the-end presentation for standard DB-backed
  reports.

Phase 1 success criteria:

1. Cards and charts always refer to the same item codes and roles.
2. Both-side, same-side, one-item, and zero-item selection cases match this specification.
3. Two selected items render side by side when wide and stack card-then-chart when narrow.
4. One selected item uses full width; zero items produce no empty highlight area.
5. Each chart uses at most 30 calendar days of completed transactions and ignores
   `--lookback-days` for its history semantics.
6. Candle interval selection is deterministic and passes threshold boundary tests.
7. No empty interval receives fabricated OHLC data.
8. The lower panel is labelled `Units traded` and equals summed positive transaction quantity.
9. The `7D SMA` is time-based and absent where evidence is insufficient.
10. The strict 7D fair line uses the read-model value and is never recalculated or replaced in the
    renderer.
11. The existing report tables, consolidated fair columns, and their PNG behavior remain intact.
12. Focused tests and the full test suite pass.

### Phase 2: Hero and highlight-card PNG export

Deliver:

- an explicit hero-plus-highlight-cards capture wrapper;
- `sections/report-header.png` export;
- hero-only export when there are no valid highlights; and
- no change to table-only PNG capture boundaries.

Phase 2 success criteria:

1. Header PNG contains the complete hero and exactly the cards rendered in the report.
2. Header PNG excludes charts, tables, headings belonging to later sections, scrollbars, clipping,
   and excess whitespace.
3. Zero-highlight reports still export a complete hero-only PNG.
4. Existing table PNGs still contain only table elements.
5. Export works with the current Chrome/Playwright configuration and produces deterministic paths.
6. Browser-capture tests verify selectors, filenames, and capture boundaries.
7. A real rendered-image smoke test or documented manual visual check confirms legibility.
8. Focused tests and the full test suite pass.

### Phase 3: Evidence-scored support and resistance zones

This phase is optional and must be implemented only after Phase 1 charts are accepted.

Deliver:

- support/resistance detection from unmodified analytical candles;
- clustering of nearby swing lows and highs into zones;
- evidence scoring based on independent touches, recency, and units traded;
- at most one strongest support below the latest completed price and one strongest resistance above
  it;
- translucent, labelled zones; and
- omission when evidence is insufficient.

All thresholds must be centralized configuration or named constants and covered by tests. Do not
optimize thresholds against future data as part of this visualization task.

Phase 3 success criteria:

1. Detection never uses visually padded candle values or future data relative to the chart endpoint.
2. One anomalous transaction cannot qualify as a supported zone by itself.
3. Zones require the documented number of independent touches.
4. Support is below and resistance is above the latest completed price.
5. At most one zone of each type is rendered, and weak evidence renders none.
6. Calculations are deterministic and independent of chart pixel dimensions.
7. The chart remains legible with candles, 7D SMA, fair line, units volume, and zones.
8. Phase 1 and Phase 2 behavior remains unchanged.
9. Focused tests and the full test suite pass.

## Tests and verification

At minimum, add focused tests for:

- deterministic role selection and tie-breaking;
- chart-capability filtering;
- opposite-side, same-side, one-item, and zero-item results;
- 30D query boundaries and UTC candle alignment;
- adaptive interval boundaries;
- OHLC and units-volume aggregation;
- time-based SMA evidence gating;
- strict 7D fair-line input;
- responsive HTML structure and report ordering;
- missing/failed chart handling;
- stable role-based filenames;
- header wrapper and PNG selector isolation;
- unchanged table-only PNG capture; and
- support/resistance behavior if Phase 3 is implemented.

Use the existing `.venv` and the repository's preferred pytest invocation, translated to the
workspace's Windows executable path where necessary. Run focused tests before the full suite.

## Global success criteria

The complete feature is successful when:

- readers see price action for every rendered highlighted card before the first table;
- charts remain useful on wide and narrow screens without alternate image versions;
- selection, ranking, cards, filenames, and charts share one source of truth;
- all chart price history comes from completed transactions in the trailing 30 days;
- units volume and indicator semantics are explicit and accurate;
- sparse history produces coarser candles or no highlight rather than misleading output;
- the hero plus cards and every chart are independently publishable PNG assets;
- existing static table PNG design rules remain intact;
- architecture and price-authority boundaries remain intact; and
- every completed phase has its own passing acceptance tests and leaves a publishable report.

## Non-goals

- Changing the consolidated 1D/7D/30D report contract.
- Changing blended fair-value weights or primary 7D guidance.
- Trading-strategy signals, predictions, entries, take profit, stops, or position sizing.
- Interactive charts or separate mobile/desktop chart images.
- Treating bid, ask, midpoint, price-endpoint observations, or order-book depth as completed trades.
- Adding VWMA, RSI, MACD, or other indicators in Phase 1.
- Using automatically detected support/resistance as trading advice.
