from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from warera_quant.market_store import LATEST_SCHEMA_VERSION, MarketStore, SyncSummary, migrate_to_v1
from warera_quant.warera_api import OrderLevel, TopOrders


def _store(tmp_path: Path) -> MarketStore:
    store = MarketStore(tmp_path / "market.sqlite3")
    store.initialize()
    return store


def test_initialize_creates_schema_tables(tmp_path):
    with _store(tmp_path) as store:
        assert store.table_names() == {
            "transactions",
            "price_observations",
            "order_book_observations",
            "order_book_levels",
            "item_production_config",
            "item_sync_state",
            "schema_meta",
        }


def test_sync_write_commits_while_another_connection_holds_read_snapshot(tmp_path):
    with _store(tmp_path) as writer, MarketStore(writer.path) as reader:
        writer.mark_item_sync_attempt("bread")
        connection = reader._connect()
        connection.execute("begin")
        assert reader.get_item_state("bread").last_error is None
        try:
            writer.mark_item_sync_failure("bread", "test failure")
            assert reader.get_item_state("bread").last_error is None
        finally:
            connection.rollback()
        assert reader.get_item_state("bread").last_error == "test failure"


def test_upsert_item_production_points_preserves_undefined_items(tmp_path):
    observed_at = datetime(2026, 6, 30, 10, 0, tzinfo=timezone.utc)
    with _store(tmp_path) as store:
        store.upsert_item_production_points({"iron": 1, "steel": 10, "case1": None}, observed_at)

        assert store.item_production_points() == {"case1": None, "iron": 1.0, "steel": 10.0}


def test_initialize_sets_schema_and_user_versions(tmp_path):
    with _store(tmp_path) as store:
        assert store.schema_version() == LATEST_SCHEMA_VERSION
        assert store.user_version() == LATEST_SCHEMA_VERSION


def test_upsert_transactions_computes_unit_price_and_ignores_duplicate_ids(tmp_path):
    observed_at = datetime(2026, 6, 30, 10, 0, tzinfo=timezone.utc)
    with _store(tmp_path) as store:
        summary = store.upsert_transactions(
            "bread",
            [
                {
                    "id": "tx-1",
                    "createdAt": "2026-06-30T09:45:00Z",
                    "transactionType": "trading",
                    "money": "12.5",
                    "quantity": "5",
                },
                {
                    "id": "tx-1",
                    "createdAt": "2026-06-30T09:45:00Z",
                    "transactionType": "trading",
                    "money": "12.5",
                    "quantity": "5",
                },
            ],
            fetched_at=observed_at,
        )

        assert summary.inserted == 1
        assert summary.skipped == 1
        rows = store.transactions_for_window("bread", 0)
        assert rows == [
            {
                "id": "tx-1",
                "item_code": "bread",
                "transaction_type": "trading",
                "created_at": "2026-06-30T09:45:00Z",
                "created_at_epoch": 1782812700,
                "money": 12.5,
                "quantity": 5.0,
                "unit_price": 2.5,
                "fetched_at": "2026-06-30T10:00:00Z",
            }
        ]


def test_upsert_transactions_derives_stable_transaction_ids(tmp_path):
    transaction = {
        "createdAt": "2026-06-30T09:45:00Z",
        "transactionType": "trading",
        "money": "12.5",
        "quantity": "5",
    }
    with _store(tmp_path) as store:
        first = store.upsert_transactions("bread", [transaction])
        second = store.upsert_transactions("bread", [dict(transaction)])

        assert first.inserted == 1
        assert second.inserted == 0
        assert second.skipped == 1
        rows = store.transactions_for_window("bread", 0)
        assert len(rows) == 1
        assert len(rows[0]["id"]) == 64


def test_upsert_transactions_preserves_mongo_style_upstream_id(tmp_path):
    with _store(tmp_path) as store:
        store.upsert_transactions(
            "bread",
            [
                {
                    "_id": "6a404dfb4b1636383179ccfc",
                    "createdAt": "2026-06-30T09:45:00Z",
                    "transactionType": "trading",
                    "money": "12.5",
                    "quantity": "5",
                }
            ],
        )

        rows = store.transactions_for_window("bread", 0)
        assert rows[0]["id"] == "6a404dfb4b1636383179ccfc"


def test_insert_price_and_order_book_observations(tmp_path):
    observed_at = datetime(2026, 6, 30, 10, 0, tzinfo=timezone.utc)
    with _store(tmp_path) as store:
        store.insert_price_observations({"bread": "3.25"}, observed_at)
        store.insert_order_book_observations(
            {
                "bread": TopOrders(
                    buy_orders=[OrderLevel(3.1, 4), OrderLevel(3.0, 6)],
                    sell_orders=[OrderLevel(3.4, 2), OrderLevel(3.6, 8)],
                )
            },
            observed_at,
        )

        price_rows = store.price_observations_for_window("bread", 0)
        assert price_rows == [
            {
                "id": 1,
                "item_code": "bread",
                "observed_at": "2026-06-30T10:00:00Z",
                "observed_at_epoch": 1782813600,
                "current_price": 3.25,
            }
        ]
        order_rows = store.order_book_observations_for_window("bread", 0)
        assert order_rows[0]["best_bid"] == 3.1
        assert order_rows[0]["best_ask"] == 3.4
        assert order_rows[0]["bid_depth"] == 10.0
        assert order_rows[0]["ask_depth"] == 10.0
        assert order_rows[0]["spread_abs"] == pytest.approx(0.3)
        assert order_rows[0]["spread_pct"] == pytest.approx(9.230769)
        latest = store.latest_order_book_with_levels("bread")
        assert latest is not None
        assert latest["levels_available"] is True
        assert latest["bids"] == [
            {"level_position": 0, "price": 3.1, "quantity": 4.0},
            {"level_position": 1, "price": 3.0, "quantity": 6.0},
        ]


def test_sync_state_success_and_failure_updates(tmp_path):
    synced_at = datetime(2026, 6, 30, 10, 0, tzinfo=timezone.utc)
    failed_at = datetime(2026, 6, 30, 11, 0, tzinfo=timezone.utc)
    with _store(tmp_path) as store:
        store.mark_item_sync_success(
            "bread",
            SyncSummary(
                pages_fetched=2,
                transactions_inserted=7,
                newest_created_at="2026-06-30T09:45:00Z",
                newest_created_at_epoch=1782812700,
                newest_transaction_id="tx-7",
                synced_at=synced_at,
            ),
        )
        state = store.get_item_state("bread")

        assert state is not None
        assert state.newest_transaction_id == "tx-7"
        assert state.last_successful_sync_at == "2026-06-30T10:00:00Z"
        assert state.last_attempted_sync_at == "2026-06-30T10:00:00Z"
        assert state.last_error is None
        assert state.pages_fetched == 2
        assert state.transactions_inserted == 7

        store.mark_item_sync_failure("bread", RuntimeError("timeout"), attempted_at=failed_at)
        failed_state = store.get_item_state("bread")

        assert failed_state is not None
        assert failed_state.newest_transaction_id == "tx-7"
        assert failed_state.last_successful_sync_at == "2026-06-30T10:00:00Z"
        assert failed_state.last_attempted_sync_at == "2026-06-30T11:00:00Z"
        assert failed_state.last_error == "timeout"


def test_market_store_is_only_source_module_importing_sqlite3():
    source_root = Path(__file__).parents[1] / "src" / "warera_quant"
    needle = "import " + "sqlite3"
    offenders = [
        path.relative_to(source_root).as_posix()
        for path in source_root.glob("*.py")
        if path.name != "market_store.py" and needle in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_v1_database_migrates_without_losing_compact_observation(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    store = MarketStore(path)
    connection = store._connect()
    migrate_to_v1(connection)
    connection.execute("create table schema_meta (key text primary key, value text not null)")
    connection.execute("insert into schema_meta values ('version', '1')")
    connection.execute("pragma user_version = 1")
    connection.execute(
        """
        insert into order_book_observations (
            item_code, observed_at, observed_at_epoch, best_bid, best_ask,
            bid_depth, ask_depth, spread_abs, spread_pct
        ) values ('bread', '2026-06-30T10:00:00Z', 1782813600, 3.1, 3.4, 10, 8, .3, 9.2)
        """
    )
    connection.commit()
    store.close()

    with MarketStore(path) as migrated:
        assert migrated.schema_version() == LATEST_SCHEMA_VERSION
        snapshot = migrated.latest_order_book_with_levels("bread")
        assert snapshot is not None
        assert snapshot["best_bid"] == 3.1
        assert snapshot["bids"] == []
        assert snapshot["asks"] == []
        assert snapshot["levels_available"] is False


def test_order_book_insert_aggregates_duplicate_prices_and_is_atomic(tmp_path):
    observed_at = datetime(2026, 6, 30, 10, 0, tzinfo=timezone.utc)
    with _store(tmp_path) as store:
        store.insert_order_book_observations(
            {"bread": TopOrders(
                buy_orders=[OrderLevel(3, 2), OrderLevel(3, 4)],
                sell_orders=[OrderLevel(4, 1)],
            )},
            observed_at,
        )
        snapshot = store.latest_order_book_with_levels("bread")
        assert snapshot is not None
        assert snapshot["bids"] == [{"level_position": 0, "price": 3.0, "quantity": 6.0}]

        with pytest.raises(Exception):
            store.insert_order_book_observations(
                {"steel": TopOrders(
                    buy_orders=[OrderLevel(2, 1)],
                    sell_orders=[OrderLevel(3, -1)],
                )},
                observed_at,
            )
        assert store.latest_order_book_with_levels("steel") is None


def test_order_book_history_with_levels_is_chronological_and_keeps_legacy_rows(tmp_path):
    with _store(tmp_path) as store:
        later = datetime(2026, 6, 30, 12, tzinfo=timezone.utc)
        earlier = datetime(2026, 6, 30, 10, tzinfo=timezone.utc)
        for observed_at, bid in ((later, 4), (earlier, 3)):
            store.insert_order_book_observations(
                {"bread": TopOrders(
                    buy_orders=[OrderLevel(bid, 2)],
                    sell_orders=[OrderLevel(bid + 1, 3)],
                )},
                observed_at,
            )
        rows = store.order_book_history_with_levels("bread")

    assert [row["observed_at"] for row in rows] == [
        "2026-06-30T10:00:00Z",
        "2026-06-30T12:00:00Z",
    ]
    assert rows[0]["bids"] == [{"level_position": 0, "price": 3.0, "quantity": 2.0}]


def test_housekeeping_prunes_expired_market_history_and_cascades_levels(tmp_path):
    now = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    expired_at = now - timedelta(days=46)
    retained_at = now - timedelta(days=45)
    with _store(tmp_path) as store:
        for suffix, observed_at in (("old", expired_at), ("keep", retained_at)):
            store.upsert_transactions(
                "bread",
                [{
                    "id": f"tx-{suffix}",
                    "createdAt": observed_at.isoformat(),
                    "transactionType": "trading",
                    "money": 10,
                    "quantity": 2,
                }],
                fetched_at=now,
            )
            store.insert_price_observations({"bread": 5}, observed_at)
            store.insert_order_book_observations(
                {"bread": TopOrders(
                    buy_orders=[OrderLevel(4, 2)],
                    sell_orders=[OrderLevel(5, 3)],
                )},
                observed_at,
            )

        summary = store.run_housekeeping(
            retention_days=45,
            vacuum_interval_days=0,
            now=now,
        )

        assert summary.transactions_deleted == 1
        assert summary.price_observations_deleted == 1
        assert summary.order_book_observations_deleted == 1
        assert summary.rows_deleted == 3
        assert summary.vacuumed is False
        assert [row["id"] for row in store.transactions_for_window("bread", 0)] == ["tx-keep"]
        assert len(store.price_observations_for_window("bread", 0)) == 1
        assert len(store.order_book_observations_for_window("bread", 0)) == 1
        level_count = store._connect().execute("select count(*) from order_book_levels").fetchone()[0]
        assert level_count == 2


def test_housekeeping_vacuums_only_when_free_pages_exist_and_interval_is_due(tmp_path):
    now = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    expired_at = now - timedelta(days=10)
    with _store(tmp_path) as store:
        store.insert_price_observations(
            {f"item-{index}": float(index) for index in range(1, 500)},
            expired_at,
        )

        first = store.run_housekeeping(retention_days=1, vacuum_interval_days=30, now=now)
        second = store.run_housekeeping(
            retention_days=1,
            vacuum_interval_days=30,
            now=now + timedelta(days=1),
        )

        assert first.price_observations_deleted == 499
        assert first.vacuumed is True
        assert second.vacuumed is False


def test_market_sync_metadata_persists_across_store_instances(tmp_path):
    path = tmp_path / "market.sqlite3"
    synced_at = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    with MarketStore(path) as store:
        store.record_market_sync(synced_at, status="partial")

    with MarketStore(path) as reopened:
        metadata = reopened.market_sync_metadata()

    assert metadata is not None
    assert metadata.synced_at == "2026-07-20T12:00:00Z"
    assert metadata.status == "partial"


def test_existing_database_infers_initial_sync_metadata_from_latest_observation(tmp_path):
    path = tmp_path / "market.sqlite3"
    observed_at = datetime(2026, 7, 20, 12, tzinfo=timezone.utc)
    with MarketStore(path) as store:
        store.insert_price_observations({"bread": 5}, observed_at)

    with MarketStore(path) as reopened:
        metadata = reopened.market_sync_metadata()

    assert metadata is not None
    assert metadata.synced_at == "2026-07-20T12:00:00Z"
    assert metadata.status == "inferred"
