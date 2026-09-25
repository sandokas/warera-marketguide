"""Migration tests use only temporary databases, including the installed offline CLI."""
from datetime import datetime, timezone
from pathlib import Path
import subprocess

import pytest

from warera_quant import market_store
from warera_quant.market_store import MarketStore, MIGRATIONS


def legacy_database(path, version):
    store = MarketStore(path)
    c = store._connect()
    c.execute("begin")
    c.execute("create table schema_meta (key text primary key,value text not null)")
    for number in range(1, version + 1):
        MIGRATIONS[number](c)
    c.execute("insert into schema_meta values ('version',?)", (str(version),))
    c.execute(f"pragma user_version={version}")
    c.execute("insert into transactions values ('old','steel','trading','2026-06-30T09:45:00.123Z',1782812700,36.480000000000004,3,12.160000000000002,'2026-06-30T10:00:00Z')")
    c.execute("insert into price_observations values (1,'steel','2026-06-30T10:00:00Z',1782813600,12)")
    c.execute("insert into order_book_observations values (1,'steel','2026-06-30T10:00:00Z',1782813600,11,13,5,6,2,16.7)")
    c.execute("insert into item_sync_state (item_code,newest_transaction_id,pages_fetched) values ('steel','old',7)")
    if version >= 2:
        c.execute("insert into order_book_levels values (1,1,'bid',0,11,5)")
    if version >= 3:
        c.execute("insert into item_production_config values ('steel',10,'2026-06-30T10:00:00Z')")
    if version >= 4:
        c.execute("insert into transaction_coverage values ('steel',1,1782813600,'legacy scan')")
    c.commit()
    return store


def contents(store):
    c = store._connect()
    return {table: [tuple(r) for r in c.execute(f"select * from {table} order by 1")]
            for table in sorted(store.table_names())}


def test_inventory_does_not_migrate_or_create_database(tmp_path):
    path = tmp_path / "inventory.sqlite3"
    store = legacy_database(path, 4)
    before = contents(store)
    store.close()
    inventory = store.database_inventory()
    assert inventory["schema_version"] == inventory["user_version"] == 4
    assert inventory["integrity"] == ["ok"]
    assert inventory["counts"]["transactions"] == 1
    assert inventory["transactions"][0]["oldest"] == "2026-06-30T09:45:00.123Z"
    assert contents(store) == before
    store.close()
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(market_store.sqlite3.OperationalError):
        MarketStore(missing).database_inventory()
    assert not missing.exists()


@pytest.mark.parametrize("version", [1, 4])
def test_upgrade_preserves_all_original_columns_and_reopen(tmp_path, version):
    path = tmp_path / "legacy.sqlite3"
    store = legacy_database(path, version)
    before = contents(store)
    store.initialize()
    assert store.schema_version() == store.user_version() == 6
    after = contents(store)
    for table, rows in before.items():
        if table == "schema_meta":
            continue
        assert [r[:len(rows[0])] for r in after[table]] == rows if rows else after[table] == []
    details = store.transaction_details("old")
    assert details["normalization_version"] == 0
    assert details["money_decimal"] is None
    assert details["participants"] == details["equipment"] == details["stats"] == []
    assert store.order_entries(1) == []  # Aggregate history is not individual-order evidence.
    assert store.stream_status("trading") == {"progress": None, "coverage": []}
    store.close()
    with MarketStore(path) as reopened:
        assert contents(reopened) == after
        reopened.initialize()
        assert contents(reopened) == after


@pytest.mark.parametrize("version", [1, 4])
def test_failed_upgrade_rolls_back_ddl_rows_and_both_markers(tmp_path, monkeypatch, version):
    path = tmp_path / "rollback.sqlite3"
    store = legacy_database(path, version)
    before = contents(store)
    real_migration = MIGRATIONS[5]

    def broken(c):
        real_migration(c)
        c.execute("update transactions set money=999")
        c.execute("update schema_meta set value='5' where key='version'")
        c.execute("pragma user_version=5")
        raise RuntimeError("injected after schema/data/version writes")

    monkeypatch.setitem(MIGRATIONS, 5, broken)
    with pytest.raises(RuntimeError, match="injected"):
        store.initialize()
    assert not store._connect().in_transaction
    assert contents(store) == before
    assert store.schema_version() == store.user_version() == version
    store.close()
    reopened = MarketStore(path)
    assert contents(reopened) == before
    assert reopened._connect().execute("pragma integrity_check").fetchone()[0] == "ok"
    monkeypatch.setitem(MIGRATIONS, 5, real_migration)
    reopened.initialize()
    assert reopened.schema_version() == reopened.user_version() == 6
    assert reopened.transaction_details("old")["money"] == before["transactions"][0][5]
    reopened.close()


def test_backup_includes_committed_wal_and_restore_preserves_backup_version(tmp_path):
    path = tmp_path / "wal.sqlite3"
    writer = legacy_database(path, 4)
    reader = MarketStore(path)
    c = reader._connect()
    c.execute("begin")
    c.execute("select * from transactions").fetchall()
    writer._connect().execute("update transactions set money=123.25 where id='old'")
    writer._connect().commit()
    assert Path(str(path) + "-wal").stat().st_size > 0
    backup = writer.backup(tmp_path / "backup.sqlite3")
    c.rollback()
    reader.close()
    writer.initialize()
    writer._connect().execute("delete from transactions")
    writer._connect().commit()
    writer.restore(backup)
    assert writer.schema_version() == writer.user_version() == 4
    assert writer._connect().execute("select money from transactions").fetchone()[0] == 123.25
    assert "transaction_participants" not in writer.table_names()
    writer.initialize()
    assert writer.transaction_details("old")["money"] == 123.25
    with pytest.raises(market_store.MarketStoreError):
        writer.backup(backup)
    writer.close()


def test_installed_offline_migrate_command(tmp_path):
    path = tmp_path / "cli.sqlite3"
    legacy_database(path, 4).close()
    executable = Path(__file__).parents[1] / ".venv" / "Scripts" / "warera-marketguide.exe"
    result = subprocess.run([str(executable), "--migrate-db", "--market-db", str(path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Backup:" in result.stdout and "schema v6" in result.stdout
    backups = list(tmp_path.glob("cli.sqlite3.backup-*"))
    assert len(backups) == 1
    backup = MarketStore(backups[0])  # Inspect without initializing/migrating the backup.
    assert backup.schema_version() == backup.user_version() == 4
    backup.close()
    with MarketStore(path) as store:
        assert store.transaction_details("old")["participants"] == []
    again = subprocess.run([str(executable), "--migrate-db", "--market-db", str(path)],
                           capture_output=True, text=True, timeout=30)
    assert again.returncode == 0, again.stderr
    assert len(list(tmp_path.glob("cli.sqlite3.backup-*"))) == 2
