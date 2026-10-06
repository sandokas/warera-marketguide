"""G-only disposable query fixture; timings are evidence, never thresholds."""
from datetime import datetime, timedelta, timezone
import json
from time import perf_counter

import pytest

from warera_quant import market_store
from warera_quant.market_store import MarketStore


def fixture(store):
    c = store._connect()
    start = datetime(2026, 9, 15, tzinfo=timezone.utc)
    old = start - timedelta(days=100)
    with c:
        c.executemany("insert into transactions (id,item_code,transaction_type,created_at,created_at_epoch,created_at_us,money,quantity,unit_price,fetched_at) values (?,?,?,?,?,?,?,?,?,?)", [
            (f"t{i:06}", f"item{i % 40}", "itemMarket" if i % 5 == 0 else "trading",
             (at := (start + timedelta(seconds=i % 100) if i >= 30000 else old + timedelta(seconds=i))).isoformat(),
             int(at.timestamp()), None if i % 17 == 0 else market_store._datetime_us(at), 3, 1, 3, at.isoformat())
            for i in range(30200)])
        c.executemany("insert into transaction_participants (transaction_id,side,user_id,mu_id,country_id) values (?, 'buy',?,?,?)",
                      [(f"t{i:06}", "U" if i % 2 == 0 else "other", "M", "C") for i in range(30200)])
        for table in ("price_observations", "order_book_observations"):
            columns = ",current_price" if table == "price_observations" else ""
            suffix = ",3" if columns else ""
            c.executemany(f"insert into {table} (item_code,observed_at,observed_at_epoch{columns}) values (?,?,?{suffix})",
                          [(f"item{i % 40}", (old + timedelta(seconds=i)).isoformat(), int(old.timestamp()) + i) for i in range(20000)])
        c.executemany("insert into market_entities (entity_kind,entity_id,name,lookup_status,lookup_attempted_at) values ('user',?,?, 'ok',?)",
                      [(f"u{i}", f"Name{i}", start.isoformat()) for i in range(10000)])
    return start, start + timedelta(days=7)


def operations(store, start, end):
    return {
        "window": lambda: store.iter_participant_window(start, end),
        "entity-window": lambda: store.iter_entity_activity("user", "U", start, end),
        "entity-fifo": lambda: store.iter_entity_activity("user", "U", start, end, accounting_mode="full-fifo"),
        "global-fifo": lambda: store.iter_participant_history(start, end),
        "item-history": lambda: store.transactions_for_window("item1", start.timestamp(), end=end),
        "item-long-history": lambda: store.transactions_for_window("item1", (start-timedelta(days=101)).timestamp(), end=end),
        "daily": lambda: store.completed_daily_facts(["item1"], int(start.timestamp()), int(end.timestamp())),
        "name": lambda: store.find_users("nAmE123"),
        "latest-price": store.latest_price_observations,
        "latest-orders": store.latest_order_book_observations,
        "discovery": store.item_codes,
        "equipment-discovery": lambda: store.item_codes(transaction_type="itemMarket"),
    }


def future_fixture(store, start):
    """A historical cutoff with substantial retained later data."""
    at = start + timedelta(days=30)
    c = store._connect()
    with c:
        c.executemany("insert into transactions (id,item_code,transaction_type,created_at,created_at_epoch,fetched_at) values (?, 'item1','trading',?,?,?)",
                      [(f"future{i}", at.isoformat(), int(at.timestamp()), at.isoformat()) for i in range(10000)])


def test_historical_upper_search_g(tmp_path):
    with MarketStore(tmp_path / "future.sqlite") as store:
        start, end = fixture(store)
        before = store.transactions_for_window("item1", start.timestamp(), end=end)
        future_fixture(store, start)
        after, profile = store.profile_read(lambda: store.transactions_for_window("item1", start.timestamp(), end=end))
        assert after == before
        assert any("created_at_epoch<?" in detail for p in profile["plans"] for detail in p["plan"])
        period = store.transactions_for_period(["item1"], start.timestamp(), end.timestamp(), end=end)
        assert period == before


def test_measure_g(tmp_path, capsys):
    store = MarketStore(tmp_path / "g.sqlite")
    store.initialize()
    start, end = fixture(store)
    evidence = {}
    for name, operation in operations(store, start, end).items():
        result, profile = store.profile_read(operation)
        # Repeat on the same connection: warm SQLite/OS caches, no ANALYZE.
        repeated, warm = store.profile_read(operation)
        assert result == repeated
        evidence[name] = profile | {"repeat_seconds": warm["elapsed_seconds"]}
    print(json.dumps(evidence))
    assert evidence["window"]["result_count"] == 200
    assert evidence["window"]["child_queries"] == 4
    assert evidence["entity-window"]["result_count"] == 100
    assert evidence["entity-window"]["child_queries"] == 4
    assert evidence["entity-fifo"]["result_count"] == 15100
    assert evidence["global-fifo"]["result_count"] == 30200
    store.close()


def test_upgrade_equivalence_cost_and_rollback_g(tmp_path, monkeypatch):
    store = MarketStore(tmp_path / "upgrade.sqlite")
    with monkeypatch.context() as patch:
        patch.setattr(market_store, "LATEST_SCHEMA_VERSION", 7)
        store.initialize()
    start, end = fixture(store)
    before = {name: store.profile_read(op) for name, op in operations(store, start, end).items()}
    c = store._connect()
    size_before = c.execute("pragma page_count").fetchone()[0] * c.execute("pragma page_size").fetchone()[0]
    real = market_store.MIGRATIONS[8]

    def broken(connection):
        real(connection)
        raise RuntimeError("G migration injected failure")

    with monkeypatch.context() as patch:
        patch.setitem(market_store.MIGRATIONS, 8, broken)
        with pytest.raises(RuntimeError, match="injected"):
            store.initialize()
    assert store.schema_version() == store.user_version() == 7
    assert not c.execute("select 1 from sqlite_master where name='idx_entities_kind_name'").fetchone()
    started = perf_counter()
    store.initialize()
    upgrade = perf_counter() - started
    assert store.schema_version() == store.user_version() == 8
    size_after = c.execute("pragma page_count").fetchone()[0] * c.execute("pragma page_size").fetchone()[0]
    for name, op in operations(store, start, end).items():
        result, profile = store.profile_read(op)
        assert result == before[name][0]
        if name == "name":
            assert all("SEARCH" in p["plan"][0] for p in profile["plans"])
            assert any("name=?" in detail for p in profile["plans"] for detail in p["plan"])
        if name.startswith("latest-"):
            assert any("SEARCH p" in detail and "item_code=?" in detail
                       for p in profile["plans"] for detail in p["plan"])
    print(json.dumps({"upgrade_seconds": upgrade, "bytes_before": size_before,
                      "bytes_after": size_after, "added_bytes": size_after-size_before}))
    store.close()


def test_legacy_precision_ties_evidence_g(tmp_path):
    with MarketStore(tmp_path / "ties.sqlite") as store:
        start = datetime(2026, 9, 15, tzinfo=timezone.utc)
        c = store._connect()
        with c:
            for id, micro, legacy in [("z", 1, True), ("a", 2, False), ("b", 2, True), ("end", 3, False)]:
                at = start + timedelta(microseconds=micro)
                c.execute("insert into transactions (id,item_code,transaction_type,created_at,created_at_epoch,created_at_us,money_decimal,quantity_decimal,fetched_at) values (?,?,?,?,?,?,?,?,?)",
                          (id, "x", "trading", at.isoformat(), int(at.timestamp()), None if legacy else market_store._datetime_us(at), "0.1234567890123456789", "1", at.isoformat()))
                c.execute("insert into transaction_participants (transaction_id,side,user_id,mu_id) values (?, 'buy','U','M')", (id,))
        end = start + timedelta(microseconds=3)
        for read in [lambda: store.iter_participant_window(start, end),
                     lambda: store.iter_entity_activity("user", "U", start, end),
                     lambda: store.iter_entity_activity("mu", "M", start, end, accounting_mode="full-fifo"),
                     lambda: store.iter_participant_history(start, end)]:
            rows = list(read())
            assert [r["id"] for r in rows] == ["z", "a", "b"]
            assert all(r["money_decimal"] == "0.1234567890123456789" for r in rows)
        assert [r["id"] for r in store.transactions_for_window("x", start.timestamp(), end=end)] == ["z", "a", "b"]
        assert store.item_codes(transaction_type="itemMarket") == []
        store.cache_entity_name("user", "U", "Same", start.isoformat(), "ok")
        store.cache_entity_name("user", "V", "same", start.isoformat(), "ok")
        assert [r["entity_id"] for r in store.find_users("sAME")] == ["U", "V"]
        store.cache_entity_name("user", "U", "U", start.isoformat(), "ok")
        assert [r["entity_id"] for r in store.find_users("U")] == ["U"]
        # Latest cache semantics remain epoch then ID, not source-text precision.
        with c:
            for at in [start + timedelta(microseconds=9), start + timedelta(microseconds=1)]:
                c.execute("insert into price_observations (item_code,observed_at,observed_at_epoch,current_price) values ('x',?,?,1)", (at.isoformat(), int(at.timestamp())))
                c.execute("insert into order_book_observations (item_code,observed_at,observed_at_epoch) values ('x',?,?)", (at.isoformat(), int(at.timestamp())))
        assert store.latest_price_observations()["x"]["id"] == 2
        assert store.latest_order_book_observations()["x"]["id"] == 2


def test_rejected_native_precision_index_g(tmp_path, monkeypatch):
    with MarketStore(tmp_path / "candidate.sqlite") as store:
        start, end = fixture(store)
        _, before = store.profile_read(lambda: store.iter_participant_window(start, end))

        def candidate(connection):
            connection.execute("create index g_candidate_native_time on transactions(transaction_type,created_at_us,id)")

        with monkeypatch.context() as patch:
            patch.setitem(market_store.MIGRATIONS, 9, candidate)
            patch.setattr(market_store, "LATEST_SCHEMA_VERSION", 9)
            store.initialize()
        _, after = store.profile_read(lambda: store.iter_participant_window(start, end))
        # A native-only key cannot order/filter the legacy-aware COALESCE.
        assert not any("created_at_us>?" in detail for p in after["plans"] for detail in p["plan"])
        print(json.dumps({"native_precision_candidate": {"before": before, "after": after}}))
