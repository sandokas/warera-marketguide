# Agent Instructions

## Git Operations

**NEVER attempt clever git operations.** Stick to basic, safe commands only:
- `git status` - check status
- `git branch` - list branches  
- `git checkout` - switch branches
- `git add` - stage changes
- `git commit` - commit changes with clear messages
- `git log` - view history
- `git diff` - view differences

**FORBIDDEN git operations:**
- `git cherry-pick` - NEVER use
- `git reset --hard` - ONLY when explicitly requested by user
- `git rebase` - NEVER use
- `git merge` - ONLY when explicitly requested by user
- Any git operations that manipulate history or attempt to be "clever"

**When user asks to add something to git:**
1. Simply edit the file
2. `git add` the file
3. `git commit` with clear message
4. STOP. Do not attempt to sync across branches or do anything fancy.

**If user complains about git state:**
1. Check current status with `git status`
2. Explain the situation clearly
3. Ask user what they want to do
4. Do exactly what they say, nothing more

## Environment and Tests

Use the existing project virtual environment at `.venv` when it is present. Do not recreate it just to run commands.

Preferred test command:

```bash
.venv\Scripts\pytest      # Windows
# or
.venv/bin/pytest          # Linux/macOS
```

For a focused test file:

```bash
.venv\Scripts\pytest tests/test_market_store.py      # Windows
# or
.venv/bin/pytest tests/test_market_store.py          # Linux/macOS
```

If `.venv` is missing, follow the setup instructions in `README.md`.

## Published Report Format

Report tables are published as static PNG images, not interactive HTML tables. PNG exports must capture only the table element, without section headings, descriptions, or surrounding whitespace. Every table must fit completely within its image canvas. Do not use horizontal scrolling, scroll containers, clipped overflow, or layouts that require touch or browser interaction. Improve readability with intrinsic table sizing and legible text rather than forced widths.

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

## Database Schema Migrations

**NEVER manually manipulate database schema or version markers.** Always use the migration system in `market_store.py`.

### When Schema Changes Are Needed

1. **Increment `LATEST_SCHEMA_VERSION`** in `market_store.py`
2. **Create a new migration function** (e.g., `migrate_to_v7`) that performs the schema changes
3. **Add the migration to the `MIGRATIONS` dictionary** with the version number as key
4. **Update tests** to expect the new schema version (update version assertions in test files)
5. **Let `MarketStore.initialize()` handle the migration** - never execute migrations manually

### Migration Function Requirements

- Must accept a single `sqlite3.Connection` parameter
- Should use `connection.execute()` for DDL statements
- All schema changes and version markers must be atomic in one transaction
- Include both schema changes and any data migrations needed

### Example Migration

```python
def migrate_to_v7(connection: sqlite3.Connection) -> None:
    """Add new_column to table_name for feature X."""
    connection.execute("alter table table_name add column new_column text")
    # Additional schema changes as needed
```

Then add to migrations dictionary:
```python
MIGRATIONS = {
    1: migrate_to_v1,
    # ... existing migrations
    6: migrate_to_v6,
    7: migrate_to_v7,  # New migration
}
```

### Version Markers

The database maintains two version markers that must always stay synchronized:
- `schema_meta.version` (application-controlled)
- `PRAGMA user_version` (SQLite-controlled)

The `initialize()` method automatically keeps these in sync. Never manually set either marker.

### Testing Schema Changes

- Update test assertions to expect the new version number
- Ensure migration tests cover the new schema changes
- Test both fresh installations and upgrades from previous versions

### Forbidden Practices

- **NEVER** execute DDL (CREATE/ALTER/DROP) or DML (INSERT/UPDATE/DELETE) commands directly on production databases
- **NEVER** manually modify `schema_meta` table
- **NEVER** manually set `PRAGMA user_version`
- **NEVER** bypass `MarketStore.initialize()` for schema changes
- **NEVER** have inconsistent version numbers between code and tests

### Allowed Read Operations

The following are **SAFE** for debugging and investigation:
- SELECT queries to inspect data state
- PRAGMA commands to check schema and database metadata
- Reading from existing tables for debugging purposes

These read operations are essential for troubleshooting and understanding database state, but should never be used to modify schema or data.
