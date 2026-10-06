# Phase H handoff

Status: complete. Date: 2026-10-06 (Europe/Lisbon). H only; I integrates.

## Reproduction and implementation

The reported right strip was NOT reproduced in real local Chromium rendering of raw pre-export HTML or the viewed full-table PNGs. No cause or layout fix is claimed. Trading Guide, Current Order Book, Activity Comparison and participant tables share export_report_assets table.report-table element capture. All retain intrinsic max-content/auto table sizing. No cropping, fixed widths, font shrinking, hidden rows, scrolling or overflow clipping was added.

report.py now records table/row/group/cell bounds, computed sizing rules, logical column counts, colspans/rowspans, header/body/footer last-cell right edges, gaps and tolerances in current-run inventory. Grid occupancy handles rowspans (including zero through the row group), multirow headers and spanning empty-state cells. Empty groups have null edges; no cells means no edge claim. Each nonempty group uses the maximum actual cell right edge, including spans: footer-created extra columns cannot conceal a shorter header/body. Footer bounds/styles and cell geometry expose its width contribution rather than treating the last text position as a column boundary.

Right-edge tolerance is 1 CSS pixel for fractional rounding plus computed right border width, plus horizontal border spacing for separate borders. Collapsed report borders yielded 0.5 CSS-pixel gaps (tolerance 2). PNG IHDR width AND height are checked against full element bounds at device scale 2, allowing four physical pixels for outward rounding of both CSS edges. Existing cellsOutside (1 CSS pixel) and scrollWidth/scrollHeight (+2 CSS pixels) checks remain. Section composites have PNG/overflow checks but no table-edge assertion; their inventory kind remains distinct. Static HTML replacement, identity link overlays/assets, decoded images/fonts and current-run-only inventory remain intact.

## Browser evidence and viewed artifacts

Artifacts retained locally under C:/git/warera-marketguide/phase-h-artifacts-20261006 (temporary, uncommitted). Raw pre-export HTML and detailed inventory are in final/test_h_raw_geometry_spans_spar0 and last/test_h_raw_geometry_spans_spar0. Browser launch uses the supported local executable via _chrome_executable(None), existing .venv, isolated outputs; no browser checks skipped.

Viewed actual full PNGs, copied to the artifact root because view_image could not access pytest-created child directories:

- table-01-01.png: Trading Guide, long/sparse rows, multirow spanning header and spanning footer, 1960x406.
- table-02-02.png: Current Order Book, long label and sparse depth, spanning header/footer, 2050x348 (final fixture subsequently also includes an entirely sparse second book row).
- table-03-03.png: Activity Comparison, long/sparse rows, bar tracks and spanning header/footer, 1620x450.
- participants-country-volume.png: participant long name, sparse Bought/Sold, spanning header/footer, 1256x760.
- participants-user-explanations.png: ordinary participant fixture with long equipment stats and sparse numeric cells, 3198x1526.

Also viewed ordinary Trading Guide and Activity Comparison PNGs before their root copies were replaced by span variants. No outside-column strip appeared. Activity bar tracks and participant tall-row blank areas are intentional space inside valid columns, not excess canvas. Span fixture column counts: guide 8, book 8, activity 2, participant volume 5 and details 11; header/body/footer gaps all 0.5 CSS pixels. 80 selected rows per entity are exercised without invoking or altering selection logic; extremely tall detail canvases pass full-dimension checks. Those large canvases were mechanically validated, not claimed to have been inspected row by row at readable size.

## Tests and limits

Existing .venv commands, no cache provider, unique basetemp:

- pytest tests/test_report_exports.py tests/test_display_identity.py tests/test_participant_report.py tests/test_report.py -q: 83 passed in 39.95s (final directory).
- pytest tests/test_report_exports.py tests/test_display_identity.py -q: 36 passed in 39.12s (verified directory; after edge-check refinement).
- Final browser-only run after adding sparse second book and malformed footer regression: 5 passed in 36.63s (browser-final directory). Negative physical-footer-column case separately passed (1 passed in 2.40s). git diff --check passed.

Regression cases cover all three older tables plus participants, long/sparse rows, 80-row details, footer/span contributions, multirow headers, terminal rowspans, spanning no-activity state, empty tbody, full PNG dimensions and deliberate outside-column padding/footer-column mismatch rejection. Identity browser tests preserve offline image decode/link behavior. Initial runs exposed fixture issues (missing book input and expected table count) and an exaggerated repeated label causing existing section-composite overflow; corrected fixtures pass. The exaggerated label case remains a known composite-width limitation, not evidence for the reported table strip.

Unresolved visual evidence: adjacent long participant detail headings are crowded in the viewed existing fixture; the missing-identity country fallback wraps within its badge. Neither establishes the alleged right strip, and neither was changed. No user-provided failing PNG/raw HTML was available to identify a different failing input. A colspan-only extra logical column with zero physical width correctly does not fail; the malformed-footer rejection fixture uses a real extra cell with physical width. Empty tables with no rendered box cannot be screenshotted by Playwright; tested empty tbody retains a real header box. Tolerances follow computed browser border/spacing rather than text-fill heuristics.

Changed files: src/warera_quant/report.py, tests/test_report_exports.py, this handoff. No identity-test edits needed. No query, clock, FIFO, selection, database, migration, metrics, CLI, README/shared-plan edits; no agents, production sync/publication/writes, staging or Git commit.
