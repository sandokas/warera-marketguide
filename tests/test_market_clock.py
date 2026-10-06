"""Clock tests operate only on disposable databases and fake API transports."""
from datetime import datetime, timedelta, timezone
from time import perf_counter

import pytest

from warera_quant import market_store
from warera_quant.market_store import MarketStore
from warera_quant.market_models import RejectedTransactionPage
from warera_quant.warera_api import TopOrders, normalize_transaction


def stamp(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def fact(id="one", at="2026-09-23T10:00:00.123456Z", **extra):
    return normalize_transaction({"_id": id, "createdAt": at, "itemCode": "unfamiliar",
                                  "transactionType": "trading", "money": 3, "quantity": 1, **extra})


def v6_store(path, monkeypatch):
    store = MarketStore(path)
    with monkeypatch.context() as patch:
        patch.setattr(market_store, "LATEST_SCHEMA_VERSION", 6)
        store.initialize()
    return store


def test_empty_read_is_metadata_only_and_admin_does_not_advance(tmp_path):
    with MarketStore(tmp_path / "empty") as store:
        store.record_market_sync(stamp("2030-01-01T00:00:00Z"), status="partial")
        store.upsert_item_production_points({"bread": 1}, stamp("2030-01-01T00:00:00Z"))
        store.cache_entity_name("user", "u", "Name", "2030-01-01T00:00:00Z", "ok")
        queries = []
        store._connect().set_trace_callback(queries.append)
        assert store.data_as_of() is None
        assert store.data_as_of() is None
        assert len(queries) == 2
        assert all("select data_as_of_us from market_data_metadata" in q for q in queries)
        assert store.schema_version() == store.user_version() == 7


@pytest.mark.parametrize("observations", [False, True])
def test_v6_bootstrap_exact_legacy_and_both_streams(tmp_path, monkeypatch, observations):
    store = v6_store(tmp_path / "v6", monkeypatch)
    c = store._connect()
    # Simulate retained legacy rows, without normalization's microsecond column.
    with c:
        c.executemany("insert into transactions (id,item_code,transaction_type,created_at,created_at_epoch,fetched_at) values (?,?,?,?,?,?)", [
            ("a", "unknown", "trading", "2026-09-23T10:00:00.999999Z", 1758621600, "2030-01-01T00:00:00Z"),
            ("b", "boots99", "itemMarket", "2026-09-23T12:00:00.999998+02:00", 1758621600, "2030-01-01T00:00:00Z")])
        if observations:
            c.execute("insert into price_observations (item_code,observed_at,observed_at_epoch,current_price) values ('x','2026-09-23T10:00:01.000001Z',1758621601,1)")
            c.execute("insert into order_book_observations (item_code,observed_at,observed_at_epoch) values ('x','2026-09-23T10:00:01.000002Z',1758621601)")
    store.initialize()
    expected = stamp("2026-09-23T10:00:01.000002Z" if observations else "2026-09-23T10:00:00.999999Z")
    assert store.data_as_of() == expected
    assert store.schema_version() == store.user_version() == 7
    store.close()
    with MarketStore(store.path) as reopened:
        assert reopened.data_as_of() == expected


def test_replay_uses_retained_timestamp_and_revision_correction(tmp_path):
    with MarketStore(tmp_path / "replay") as store:
        store.ingest_transactions([fact()])
        original = store.data_as_of()
        assert store.ingest_transactions([fact()]).unchanged == 1
        store.ingest_transactions([fact("old", "2020-01-01T00:00:00Z")])
        store.ingest_transactions([fact(at="2029-01-01T00:00:00Z")])
        assert store.data_as_of() == original  # Rejected field conflict retained the old time.
        store.ingest_transactions([fact(at="2021-01-01T00:00:00Z", updatedAt="2030-01-01T00:00:00Z")])
        assert store.data_as_of() == stamp("2021-01-01T00:00:00Z")
        store.upsert_transactions("unfamiliar", [fact("legacy", "2027-01-01T00:00:00.000001Z")])
        assert store.data_as_of() == stamp("2027-01-01T00:00:00.000001Z")


def test_rejected_partial_and_page_rollback(tmp_path):
    with MarketStore(tmp_path / "partial") as store:
        bad = fact("bad", "2030-01-01T00:00:00Z")
        bad.values["money_decimal"] = "invalid"
        summary = store.ingest_transactions([fact(), bad])
        assert summary.inserted == summary.rejected == 1
        expected = store.data_as_of()
        with pytest.raises(RejectedTransactionPage):
            store.ingest_transactions([fact("future", "2031-01-01T00:00:00Z"), bad], strict=True)
        assert store.data_as_of() == expected
        assert store.transaction_details("future") is None


def test_page_progress_failure_rolls_back_clock_and_facts(tmp_path, monkeypatch):
    from warera_quant.market_models import StreamProgress
    with MarketStore(tmp_path / "progress") as store:
        store.ingest_transactions([fact()])
        expected = store.data_as_of()
        def fail(progress):
            assert store.data_as_of() > expected
            raise RuntimeError("progress failed after fact and clock")
        monkeypatch.setattr(store, "_write_progress", fail)
        # Existing sync fixtures construct the progress model; use its defaults here.
        progress = StreamProgress(stream="trading", scan_anchor="2026-09-23T12:00:00Z", status="partial")
        with pytest.raises(RuntimeError):
            store.ingest_transactions([fact("future", "2030-01-01T00:00:00Z")], progress=progress)
        assert store.data_as_of() == expected
        assert store.transaction_details("future") is None


def test_observation_rollback_empty_and_newer_facts(tmp_path, monkeypatch):
    with MarketStore(tmp_path / "observations") as store:
        t = stamp("2026-01-01T00:00:00.123456Z")
        store.insert_price_observations({}, t)
        store.insert_order_book_observations({}, t)
        assert store.data_as_of() is None
        store.insert_price_observations({"unknown": 1}, t)
        assert store.data_as_of() == t
        with monkeypatch.context() as patch:
            advance = store._advance_data_clock
            def broken(value):
                advance(value)
                raise RuntimeError("after clock write")
            patch.setattr(store, "_advance_data_clock", broken)
            for write, payload in [(store.insert_price_observations, {"x": 2}),
                                   (store.insert_order_book_observations, {"x": TopOrders([], [])})]:
                with pytest.raises(RuntimeError):
                    write(payload, t + timedelta(days=2))
                assert store.data_as_of() == t
        store.insert_order_book_observations({"equipment": TopOrders([], [])}, t + timedelta(microseconds=1))
        assert store.data_as_of() == t + timedelta(microseconds=1)
        store.insert_price_observations({"unknown": 1}, t)
        assert store.data_as_of() == t + timedelta(microseconds=1)


def test_backup_restore_and_pruning_reconcile_retained_facts(tmp_path):
    with MarketStore(tmp_path / "maintenance") as store:
        store.ingest_transactions([fact()])
        trade_time = store.data_as_of()
        newer = trade_time + timedelta(days=3)
        store.insert_price_observations({"x": 1}, newer)
        backup = store.backup(tmp_path / "backup")
        store.insert_order_book_observations({"x": TopOrders([], [])}, newer + timedelta(days=1))
        store.restore(backup)
        assert store.data_as_of() == newer
        store.run_housekeeping(retention_days=1, transaction_retention_days="all", now=newer + timedelta(days=2), vacuum_interval_days=0)
        assert store.data_as_of() == trade_time
        store.run_housekeeping(retention_days=1, now=newer + timedelta(days=2), vacuum_interval_days=0)
        assert store.data_as_of() is None


def test_v7_migration_failure_is_atomic(tmp_path, monkeypatch):
    store = v6_store(tmp_path / "failed", monkeypatch)
    migration = market_store.MIGRATIONS[7]
    def broken(c):
        migration(c)
        raise RuntimeError("bootstrap failed")
    with monkeypatch.context() as patch:
        patch.setitem(market_store.MIGRATIONS, 7, broken)
        with pytest.raises(RuntimeError):
            store.initialize()
    assert store.schema_version() == store.user_version() == 6
    assert "market_data_metadata" not in store.table_names()
    store.initialize()
    assert store.data_as_of() is None
    store.close()


def test_disposable_bootstrap_measurement(tmp_path, monkeypatch):
    store = v6_store(tmp_path / "benchmark", monkeypatch)
    c = store._connect()
    with c:
        c.executemany("insert into transactions (id,item_code,transaction_type,created_at,created_at_epoch,fetched_at) values (?,?,?,?,?,?)", (
            (str(i), "unknown", "trading" if i % 2 else "itemMarket", "2026-01-01T00:00:00.123456Z", 1767225600, "2030-01-01T00:00:00Z") for i in range(50000)))
    start = perf_counter()
    store.initialize()
    elapsed = perf_counter() - start
    print(f"v6->v7 bootstrap: 50000 transactions, warm fixture, {elapsed:.4f}s")
    assert store.data_as_of() == stamp("2026-01-01T00:00:00.123456Z")
    store.close()
