# WarEra Market Guide

WarEra Market Guide is an aggressive decision-support tool for trading in the WarEra in-game market. Its goal is to turn real completed transactions and the current order book into clear buy, sell/exit, wait, profit-target, and stop-loss guidance. The report should make the proposed action, price levels, expected opportunity, and risk understandable at a glance.

This is not a neutral price dashboard. Market history and visualizations exist to support trading decisions. Signals may be aggressive because the market is inside a game, but they must remain traceable to the underlying data and must expose missing history, poor liquidity, and other limitations.

Live data is normalized at the API boundary and stored in SQLite. Reports and charts read from that database; raw WarEra responses are not persisted. See [Project goal and data authority](docs/project-goal.md) for the governing product and price-source rules.

## Quick start

Use the existing `.venv` when present. Otherwise create it, then install the dependencies:

```bash
python -m venv .venv
.venv\Scripts\pip install -e .   # Windows
# or
.venv/bin/pip install -e .      # Linux/macOS
```

Generate a report from the included sample data:

```bash
warera-marketguide --csv ./data/sample_market.csv --output output
```

Generated files:

```text
output/market_report.html
output/market_trends.csv
output/market_scores.csv
```

`market_scores.csv` is a compatibility copy of `market_trends.csv`.

## Live market data

Copy `.env.example` to `.env` and set your API key:

```text
WARERA_API_KEY=your_key_here
WARERA_API_BASE_URL=https://api2.warera.io/trpc
```

Do not commit or hardcode the key. Requests authenticate with the `X-Api-Key` header.

Sync fetched visible order books, completed transactions, and the official per-item
production-point configuration, then generate a report. The game-calculated price endpoint may
still be collected for compatibility or diagnostics, but it is lagging data and must not drive
market analysis or trading signals:

```bash
warera-marketguide --live --output output
```

The default database is `data/warera_market.sqlite3`, the report lookback is 7 days, and sync requests the API maximum of 100 order-book entries per side. Requests are spaced at least 1 second apart. Override those values when needed:

```bash
warera-marketguide \
  --live \
  --market-db data/warera_market.sqlite3 \
  --lookback-days 30 \
  --order-limit 100 \
  --min-interval 1 \
  --output output
```

On incremental runs, transaction pagination stops at stored transactions or the per-item high-water mark. On a new database it continues until the API returns no cursor unless `--history-pages` sets a page cap.

Use an explicit backfill to ignore high-water marks and stop at the lookback boundary:

```bash
warera-marketguide \
  --sync \
  --transaction-backfill \
  --lookback-days 120
```

A backfill imports authentic history still offered by the API and deduplicates existing
transactions. Download scope, display periods, and retention are independent. WE23 requires
history from before its fixed inception; see the index section below.

Useful sync options:

- `--sync` updates SQLite without generating report files.
- `--history-pages N` caps transaction pages per item; `0` means no page cap.
- `--exclude-item-code CODE` excludes an item and may be repeated.
- `--quiet` suppresses progress; `--verbose` shows page-level import details.
- `--min-tick` changes the price increment removed from the raw spread when calculating trading attractiveness. It defaults to `0.001`.

## Database housekeeping

Housekeeping is an independent operation: it never runs as part of a live sync. Run it explicitly
whenever desired, or schedule this command separately:

```bash
warera-marketguide --housekeeping
```

The routine prunes expired transactions, price observations, and order-book observations. The
default configuration in `marketguide.toml` retains 120 days, which leaves headroom for
90-day static chart history and delayed or missed synchronizations:

```toml
[housekeeping]
enabled = true
retention_days = 120
vacuum_interval_days = 30
```

SQLite reuses pages released by pruning, bounding normal database growth. When free pages exist,
the database is compacted no more often than `vacuum_interval_days`; set that value to `0` to
disable compaction. Set `enabled = false` to make the housekeeping command a no-op. A different
configuration file can be selected with `--config PATH`, and a different database with
`--market-db PATH`.

Increasing `retention_days` affects future pruning only. Data already removed by housekeeping
cannot be recovered unless the database was backed up separately.

## Reports from an existing database

Generate a report without making API calls:

```bash
warera-marketguide \
  --from-db \
  --market-db data/warera_market.sqlite3 \
  --lookback-days 7 \
  --output output
```

When no input option is supplied, the CLI uses the default market database if it exists; otherwise it uses `data/sample_market.csv`. Prefer `--from-db` or an explicit CSV path in scripts so the input is clear.

DB-backed reports persist and display the latest completed market-sync timestamp separately from
the time the report itself was generated. A sync with item-level errors is marked as partial, and a
failed sync does not advance the stored freshness timestamp. Existing databases infer their initial
timestamp from the newest stored market observation.

Completed activity uses two bars. Completed Value sums transaction price × quantity
for every item and determines the row order. PP-equivalent Volume multiplies completed
units by the total upstream Production Points (PP) required for one item; for example,
1 Steel represents 20 total PP: 10 PP in its direct recipe plus 10 PP for its 10 Iron.
This is an embodied-effort comparison, not actual production during the report window.
Item rows must not be summed because ingredient and processed-item trades can overlap.
Items without a defined factory chain still rank by Completed Value and show `N/A` only
for PP fields.

## WE23 Market Index

DB-backed reports publish WE23 in place of the retired inflation overview. It uses a fixed
23-item universe, daily completed-trade VWAPs, and preceding 28-day turnover weights updated
on Mondays UTC. Holdings remain fixed between updates. Its fixed inception is
2026-08-01 at 100; changing display days does not rebase it.

The calculation requires all constituents and complete observed daily input windows. It excludes
the unfinished UTC day and does not substitute quotes, fabricate prices, or silently restart the
index when inception evidence is missing. Observed daily activity is not proof that the API supplied
every transaction. Missing evidence is shown as partial or unavailable. The headline 7D change
compares index levels seven calendar days apart, unlike the item chart's reference gap.

The weighting history starts 28 days before inception (2026-07-04), and the normal observed-coverage
rule excludes each item's first observed day. Collect earlier history where available. Housekeeping
can eventually remove evidence needed to reconstruct the chain; the current 120-day retention does
not permanently protect inception history. Increasing retention cannot restore deleted data.

Exports include `we23_series.csv`, `we23_weights.csv`, and `charts/we23.png`.
Legacy inflation calculations remain in the package, but the current CLI does not
publish `market_inflation.csv` or the old inflation overview. DB-backed reports still export
`market_action_costs.csv`: company relocation and MU HQ operating-cost benchmarks priced from
trailing representative completed trades, independently of the retired inflation calculation.
The archived settings in `docs/retired-inflation-config.toml` do not configure WE23.

## Charts and PNG publication

DB-backed highlight charts and report PNG exports are automatic. `--charts` and `--table-pngs`
remain compatibility flags; neither is required. Report publication requires a supported local
Chrome, Chromium, or Edge installation, including for CSV reports. Set `WARERA_CHROME_PATH`
in `.env` if automatic browser discovery cannot find it.

```bash
warera-marketguide --from-db --item-chart-days 30 --chart-interval 4h --we23-days 30 --output output
```

Item charts default to 30 days and 4-hour UTC candles. Supported primary intervals are `1h`, `2h`,
`4h`, and `1D`; they are not automatically coarsened. WE23 defaults to 30 display days.
Use `--chart-min-range-pct 5` to control the minimum visible price range. Sparse history remains
visible, and partial candles are marked. Database read models calculate 1D, 7D, and 30D statistics;
guidance, valuation dislocations, activity, and Item Price Context use 7D evidence.
`--lookback-days` controls download/backfill scope, not these report horizons.

```bash
warera-marketguide --from-db --all-price-action-charts --research-days 90 --output output
```

`--all-price-action-charts` exports eligible items under `charts/all/` without embedding them all.
`--research-days` adds standalone item and WE23 research charts. The main report includes eligible
historical watch charts; unavailable history is not manufactured.

Publication exports tables, the header, WE23 summary, highlight cards and pairs, item context
cards, section composites, and footer under `tables/`, `cards/`, and `sections/`. Filenames include
asset kind, position, and a slug; use `asset_inventory.json` for the authoritative current-run list.
Old files in a reused output directory are not automatically included in that inventory.

Each table PNG captures the complete table element without its section heading or surrounding
whitespace. The exporter checks for overflow and replaces the published HTML tables with those
static images, retaining table text as image alternative text. Composite section PNGs are separate
assets and may include headings. All item rows are retained; `--top` is a compatibility option.

## Trading guide percentage

Buy and Sell are quantity-aware executable order-book prices. Median 7D and 7D VWAP describe
completed transactions. The `% vs 7D reference` column and highlighted item chart use one helper:

```text
(latest completed trade / blended 7D reference - 1) * 100
blended reference = 50% VWAP + 30% median + 20% average of the last five trades in the window
```

Available reference components are reweighted when necessary. Invalid or missing prices leave
the percentage unavailable. This is a premium or discount to a historical reference, not the
change from seven days ago, an executable sell return, or a fee-adjusted profit.
For example, Wood at 0.106 against a reference of 0.09594023358 gives +10.49%.

## Other input modes

### CSV

The minimum useful CSV fields are:

```text
item_name,bid,ask,trades_7d,high_7d,low_7d
```

`last_trade_price`, `open_7d`, and `close_7d` provide completed-price context. Historical price fields must represent completed transactions supplied by the user; `bid` and `ask` represent current orders. Rich guide fields require additional evidence and may be unavailable in minimal CSV input. See `data/sample_market.csv` for a complete example.

### Custom JSON endpoint

`--api-endpoint` supports non-market JSON records through the generic API client:

```bash
warera-marketguide \
  --api-endpoint /your/custom/endpoint \
  --api-records-path result.data.items \
  --api-param limit=100 \
  --output output
```

`--api-param` may be repeated. This compatibility path does not sync data into the market database.

Run `warera-marketguide --help` for the complete option list.

## Product and market semantics

The data-source boundary is strict:

- completed transactions synced into SQLite are the source for latest traded price, price history, fair value, ranges, volume, momentum, and signal history;
- the newest order book is the source for current executable buy and sell prices, available depth, spread, pressure, and slippage;
- the WarEra price endpoint is a lagging, game-calculated value and is not an input to fair value, trends, signals, targets, or stop losses;
- missing transaction history remains missing and must not be filled with the price endpoint or the order-book midpoint.

`last_trade_price` means the newest real execution in the queried window. Best ask means the price a buyer can currently pay, and best bid means the price a seller can currently receive. These concepts must remain separate in calculations and presentation.

Transaction history drives open, high, low, close, VWAP, median, percentiles, volume, trend metrics, and historical signal inputs. Current order books are reported transparently as bid/ask quantity, monetary value, spread, pressure, cumulative levels, and fixed-budget slippage. The trading-attractiveness score remains a secondary CSV compatibility metric:

```text
Trading Attractiveness = (Effective Spread % x Window Trades) / Window Range %
```

Effective spread subtracts the minimum price tick from the raw bid/ask gap.

The product goal is to lead with clear actions rather than a generic market-quality score.
The current compact table publishes BUY, SELL, or HOLD; targets and risk plans described below
are product aspirations, not promises that every level is displayed or supported. Because
WarEra has no short selling and the report does not know the user's inventory, each item needs two
independent answers: whether a user without inventory should buy now or wait, and whether a user
holding inventory should sell now or hold. Sell guidance always means exiting owned inventory.
The decision layer should show the usable entry or exit price, how much can be traded, the profit
target, the stop-loss or invalidation level, the expected horizon, and why the signal exists.
Historical context, activity, order-book structure, and price state support that decision without
duplicating it.

More detail is available in:

- [Market database and architecture](docs/market-db-reporting-spec.md)
- [Market data semantics](docs/market-data-model-spec.md)
- [Report and liquidity semantics](docs/market-reporting-liquidity-spec.md)
- [Production Points by factory item](docs/production-points-reference.md)
- [Future web platform migration draft](docs/web-platform-project-definition.md)
- [Deferred support and resistance idea](docs/support-resistance-zones-future.md)
- [Project goal and data authority](docs/project-goal.md)
- [Repository architecture rules](AGENTS.md)

## Development

Run the test suite with the existing virtual environment:

```bash
.venv\Scripts\pytest      # Windows
# or
.venv/bin/pytest          # Linux/macOS
```

Only `market_store.py` accesses SQLite, only `api_client.py` performs HTTP requests, and only `warera_api.py` knows WarEra market endpoint names and payload shapes. Keep new work within those boundaries.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
