# Agent Prompt: Implement Highlighted Price-Action Charts and Header Export

Implement `docs/highlighted-price-action-charts-spec.md` one phase at a time.

Before editing, read completely:

- `AGENTS.md`
- `docs/highlighted-price-action-charts-spec.md`
- `docs/report-window-consolidation-spec.md`
- `docs/market-data-model-spec.md`
- `docs/market-db-reporting-spec.md`
- `docs/market-trends-table-spec.md`
- the relevant source and test files, especially `market_data.py`, `metrics.py`, `charts.py`,
  `report.py`, `cli.py`, `test_market_data.py`, `test_charts.py`, `test_report.py`, and `test_cli.py`

Inspect the current worktree first and preserve unrelated user changes. The report-window
consolidation is already implemented; work from the current code rather than the older state
described in historical documentation.

## Objective

Add up to two independently exported 30D price-action chart PNGs for the exact items rendered in
the Largest Price Gaps cards. Place each chart under its card in a responsive layout immediately
before **Fair Value & Buy / Sell Signals**. Then add an independent PNG export containing the hero
plus all rendered highlight cards. Automated support/resistance is a separate optional phase.

Do not implement trading strategies or change report valuation/guidance semantics.

## Delivery protocol

Implement phases in order. At the end of each phase:

1. run that phase's focused tests;
2. run the complete suite;
3. inspect the generated HTML structure;
4. perform the specified PNG visual verification where browser tooling is available;
5. report the phase's files, behavior, tests, and remaining risks; and
6. leave the repository in a publishable state.

Do not start Phase 2 while Phase 1 criteria fail. Do not start Phase 3 unless the user explicitly
authorizes the optional phase after reviewing Phase 1 charts.

## Phase 1: Shared selection and responsive charts

Implement only Phase 1 of the spec:

- Extract one reusable deterministic highlighted-item selection path.
- Select only chart-capable items using strict 7D fair-value gaps.
- Support opposite-side, same-side, one-item, and zero-item layouts.
- Query at most trailing 30D completed transactions.
- Select candles from `4h`, `8h`, `12h`, and `1D` using centralized evidence rules.
- Render unmodified analytical OHLC, visually safe candles, units-traded volume, an evidence-gated
  time-based `7D SMA`, and the supplied strict 7D fair reference.
- Produce separate role-based PNG files.
- Pair each card and chart in responsive HTML before the first table.
- Retire the unrelated standard one-chart-at-the-end presentation without breaking explicitly
  supported compatibility behavior.

Do not put ranking, OHLC, or indicator formulas in `cli.py` or HTML construction. Do not calculate
fair value in `charts.py`.

Phase 1 is done only when every Phase 1 success criterion in the spec passes.

## Phase 2: Header PNG

After Phase 1 passes, implement only Phase 2:

- Wrap the hero and rendered cards in one explicit capture target.
- Export that target to `sections/report-header.png`.
- Export the hero alone when no cards exist.
- Preserve table PNG selectors and table-only image boundaries exactly.
- Keep charts outside the header image.

Prefer a reusable, explicitly targeted report-asset export API over broad selectors. Do not make
`render_report_table_pngs()` capture non-table content accidentally.

Phase 2 is done only when every Phase 2 success criterion in the spec passes.

## Phase 3: Support/resistance, only with explicit authorization

Do not implement Phase 3 in the same unreviewed change. If the user authorizes it after reviewing
the earlier phases, implement the evidence-scored support/resistance zones exactly as specified.

Keep the detector pure and deterministic. It must consume unmodified analytical candles and must
not depend on SQLite, the API, HTML, or chart pixel coordinates. Render no zone when evidence is
insufficient.

Phase 3 is done only when every Phase 3 success criterion in the spec passes.

## Mandatory architecture and data rules

- Follow `AGENTS.md` without exception.
- Only `market_store.py` may touch SQLite.
- Only `api_client.py` may import `requests`.
- `market_data.py` may read through `MarketStore` but may not call the API.
- `metrics.py` and selection/indicator calculations operate only on domain values/dataframes.
- `charts.py` renders supplied domain/chart data and does not query storage or APIs.
- `report.py` renders supplied highlights and paths; it does not discover chart history.
- `cli.py` orchestrates and contains no SQL, endpoint parsing, ranking formula, candle formula, or
  indicator formula.
- Completed transactions are the only price-history and candle authority.
- Never fill missing candle intervals with invented trades or fair values.
- Preserve the consolidated strict Fair 1D/7D/30D behavior and explicit 7D guidance.
- Preserve all static table PNG constraints.

## Verification commands

Use the existing `.venv`. Adapt the repository's preferred command to PowerShell and the Windows
virtual-environment executable while preserving `PYTHONPATH=src`.

Run focused files as applicable:

```text
tests/test_market_data.py
tests/test_charts.py
tests/test_report.py
tests/test_cli.py
```

Then run the complete test suite. Also run `git diff --check` and inspect `git diff` for unrelated
changes.

For PNG work, use the existing Chrome/Playwright path. Verify actual image dimensions and visually
inspect representative two-item, one-item, and zero-item reports when the environment permits.

## Final handoff for each phase

Report:

- which phase was completed;
- user-visible behavior delivered;
- selection and sparse-data rules implemented;
- exact output filenames;
- responsive and PNG visual checks performed;
- focused and full-suite test results;
- compatibility behavior retained;
- deviations from the spec, if any; and
- the next phase, which must remain unimplemented until its entry condition is satisfied.
