from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from warera_quant.market_store import LATEST_SCHEMA_VERSION, MarketStore, SyncSummary


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
            "item_sync_state",
            "schema_meta",
        }


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
                "bread": {
                    "buyOrders": [
                        {"price": "3.10", "quantity": "4"},
                        {"price": "3.00", "quantity": "6"},
                    ],
                    "sellOrders": [
                        {"price": "3.40", "quantity": "2"},
                        {"price": "3.60", "quantity": "8"},
                    ],
                }
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
