# Agent Instructions

## Environment and Tests

Use the existing project virtual environment at `.venv` when it is present. Do not recreate it just to run commands.

Preferred test command:

```bash
PYTHONPATH=src .venv/bin/python -m pytest
```

For a focused test file:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_market_store.py
```

If `.venv` is missing, follow the setup instructions in `README.md`.

## Architecture Boundaries

Keep the market database architecture layered. Do not duplicate API, database, parsing, query, or report-construction logic across modules.

Intended structure:

```text
api_client.py      low-level HTTP only
warera_api.py      only layer that knows WarEra endpoints and response shapes
sync.py            orchestrates API-to-database sync
market_store.py    only layer that imports sqlite3 or touches the database
market_data.py     query/read-model layer for reports and charts
metrics.py         calculations only
charts.py          chart rendering only
report.py          output rendering only
cli.py             argument parsing and orchestration only
```

Allowed dependency direction:

```text
cli.py
  -> sync.py
  -> market_data.py
  -> metrics.py
  -> charts.py
  -> report.py

sync.py
  -> warera_api.py
  -> market_store.py

market_data.py
  -> market_store.py

metrics.py, charts.py, report.py
  -> domain dictionaries/dataframes only
```

Forbidden dependencies:

- `metrics.py`, `charts.py`, and `report.py` must not import `sqlite3`, `requests`, `WarEraApiClient`, `MarketStore`, or `warera_api.py`.
- `market_data.py` must not call the WarEra API.
- `warera_api.py` must not write to SQLite.
- `market_store.py` must not call the WarEra API.
- `cli.py` should not contain SQL, endpoint parsing, pagination loops, or trend formulas.
- Only `market_store.py` should import `sqlite3`.
- Only `api_client.py` should import `requests`.
- Only `warera_api.py` should know WarEra market endpoint names.

Raw API responses are parsed at the API boundary. They should not be persisted as JSON or passed between normal application layers. SQLite is the source of truth for market history.

When adding features, prefer adding one reusable method to the correct layer instead of copying similar logic into a caller. If behavior seems needed in two places, extract it before wiring the second caller.
