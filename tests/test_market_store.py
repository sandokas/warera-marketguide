from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from warera_quant.market_store import LATEST_SCHEMA_VERSION, MarketStore, SyncSummary, migrate_to_v1
from warera_quant.warera_api import OrderLevel, TopOrders, normalize_transaction, WarEraMarketApi
from warera_quant.market_models import ScalarField, StreamProgress, EnrichmentCoverage


def _store(tmp_path: Path) -> MarketStore:
    store = MarketStore(tmp_path / "market.sqlite3")
    store.initialize()
    return store


def test_initialize_creates_schema_tables(tmp_path):
    with _store(tmp_path) as store:
        assert store.table_names() == {
            "transactions",
            "transaction_coverage",
            "price_observations",
            "order_book_observations",
            "order_book_levels",
            "item_production_config",
            "item_sync_state",
            "schema_meta",
            "transaction_participants", "transaction_equipment", "transaction_equipment_stats",
            "transaction_field_state", "transaction_extra_fields", "order_book_entries",
            "order_entry_field_state", "order_entry_extra_fields", "market_entities",
            "market_ingestion_state", "market_enrichment_coverage",
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


# Phase 1 normalized storage acceptance tests.
def _sale(**updates):
    source = {"_id": "sale", "itemCode": "weapon", "transactionType": "itemMarket",
              "createdAt": "2026-09-22T10:00:00.123456789Z", "updatedAt": "2026-09-22T10:00:01.123456Z",
              "money": Decimal("36.4800000000000040001"), "quantity": 1,
              "buyerId": "actor-b", "buyerCountryId": "country", "sellerId": "actor-s", "sellerMuId": "mu",
              "item": {"_id": "instance", "code": "weapon", "quantity": 7,
                       "state": 0, "maxState": 100, "lastAcquisitionAt": "2026-09-20T12:00:00.123Z",
                       "skills": {"attack": Decimal("1.234567890123456789"), "dodge": 0}}}
    source.update(updates)
    return normalize_transaction(source)


def test_duplicate_enrichment_and_precision_preserve_equipment_and_skills(tmp_path):
    with _store(tmp_path) as store:
        minimal = normalize_transaction({"_id": "sale", "itemCode": "weapon", "transactionType": "itemMarket",
                                         "createdAt": "2026-09-22T10:00:00.123456789Z"})
        first = store.ingest_transactions([minimal])
        assert (first.inserted, first.enriched, first.unchanged, first.rejected) == (1, 0, 0, 0)
        enriched = store.ingest_transactions([_sale()])
        assert (enriched.inserted, enriched.enriched, enriched.unchanged) == (0, 1, 0)
        replay = store.ingest_transactions([_sale(), minimal])
        assert (replay.inserted, replay.enriched, replay.unchanged) == (0, 0, 2)
        row = store.transaction_details("sale")
        assert row["created_at"] == "2026-09-22T10:00:00.123456789Z"
        assert row["created_at_us"] % 1000000 == 123456
        assert row["money_decimal"] == "36.4800000000000040001"
        assert row["quantity_decimal"] == "1"
        assert row["equipment"][0]["item_quantity"] == "7"
        assert row["equipment"][0]["equipment_type"] is None
        assert {s["skill_code"]: s["value_decimal"] for s in row["stats"]} == {"attack": "1.234567890123456789", "dodge": "0"}
        assert row["participants"][0]["country_id"] == "country"
        assert row["participants"][1]["mu_id"] == "mu"
        assert row["extras"] == []  # Item code and side facts already have typed destinations.
        assert "weapon" not in store.item_codes()
        assert store.transactions_for_window("weapon", 0) == []


def test_newer_explicit_null_and_older_partial_responses(tmp_path):
    with _store(tmp_path) as store:
        store.ingest_transactions([_sale()])
        # Same revision null cannot erase a known value.
        store.ingest_transactions([_sale(buyerId=None, item={"skills": {"attack": None}})])
        assert store.transaction_details("sale")["participants"][0]["user_id"] == "actor-b"
        # A strictly newer source revision is an explicit correction, including null.
        store.ingest_transactions([_sale(updatedAt="2026-09-22T10:00:02Z", buyerId=None,
                                         item={"skills": {"attack": None, "newSkill": 9}})])
        store.ingest_transactions([_sale(updatedAt="2026-09-22T09:59:00Z", buyerId="obsolete")])
        row = store.transaction_details("sale")
        assert row["participants"][0]["user_id"] is None
        assert row["equipment"][0]["instance_id"] == "instance"
        skills = {s["skill_code"]: s["value_decimal"] for s in row["stats"]}
        assert skills == {"attack": None, "dodge": "0", "newSkill": "9"}
        assert next(p for p in row["presence"] if p["field_path"] == "buy.user_id")["is_null"] == 1


def test_missing_optional_and_explicit_null_are_distinct(tmp_path):
    with _store(tmp_path) as store:
        base = {"_id": "optional", "itemCode": "steel", "createdAt": "2026-09-22T10:00:00Z"}
        store.ingest_transactions([normalize_transaction(base)])
        assert not any(p["field_path"] == "buy.user_id" for p in store.transaction_details("optional")["presence"])
        store.ingest_transactions([normalize_transaction({**base, "buyerId": None, "item": None})])
        fields = {p["field_path"]: p["is_null"] for p in store.transaction_details("optional")["presence"]}
        assert fields["buy.user_id"] == fields["equipment"] == 1
        assert store.transaction_details("optional")["money_decimal"] is None


def test_unknown_scalar_paths_types_and_metadata_exclusions(tmp_path):
    fact = _sale(**{"__v": 99, "future": {"__v": 2, "a/b": [True, None, Decimal("0.000000000000000001"), "hi"],
                                         "a~1b": False, "nextCursor": "business-value"}})
    assert len(fact.diagnostics) == 6
    with _store(tmp_path) as store:
        store.ingest_transactions([fact])
        row = store.transaction_details("sale")
        extras = {e["field_path"]: (e["value_type"], e["scalar_value"]) for e in row["extras"]}
        assert extras["/future/a~1b/0"] == ("boolean", "true")
        assert extras["/future/a~1b/1"] == ("null", None)
        assert extras["/future/a~1b/2"] == ("number", "0.000000000000000001")
        assert extras["/future/a~01b"] == ("boolean", "false")
        assert extras["/future/nextCursor"] == ("string", "business-value")
        assert not any("__v" in p for p in extras)
        assert row["normalization_status"] == "extensions"
        assert store.ingest_transactions([fact]).unchanged == 1


def test_atomic_children_and_page_progress(tmp_path):
    with _store(tmp_path) as store:
        invalid = replace(_sale(), participants={"invalid-side": {"user_id": "bad"}})
        result = store.ingest_transactions([invalid])
        assert result.rejected == 1 and result.inserted == 0
        assert store.transaction_details("sale") is None
        invalid_extra = replace(_sale(), extras=(ScalarField("/bad", "number", "NaN"),))
        assert store.ingest_transactions([invalid_extra]).rejected == 1
        assert store.transaction_details("sale") is None
        progress = StreamProgress("itemMarket", pages=1, inserted=1)
        coverage = EnrichmentCoverage("itemMarket", "2026-09-21T00:00:00Z", "2026-09-23T00:00:00Z", "test scan", "bounded", "2026-09-23T00:00:00Z")
        store.ingest_transactions([_sale()], progress=progress, coverage=coverage)
        assert store.stream_status("itemMarket")["progress"]["pages"] == 1
        assert len(store.stream_status("itemMarket")["coverage"]) == 1
        assert store.stream_status("trading")["progress"] is None
        bad_coverage = replace(coverage, end_at=coverage.start_at)
        with pytest.raises(ValueError):
            store.ingest_transactions([_sale(_id="rolled-back")], progress=replace(progress, pages=2), coverage=bad_coverage)
        assert store.transaction_details("rolled-back") is None
        assert store.stream_status("itemMarket")["progress"]["pages"] == 1
        assert store.transaction_details("sale") is not None


def test_per_sale_equipment_snapshot_not_mutable_instance(tmp_path):
    with _store(tmp_path) as store:
        store.ingest_transactions([_sale(), _sale(_id="resale", item={"_id": "instance", "code": "weapon", "state": 33, "skills": {"attack": 4}})])
        assert store.transaction_details("sale")["equipment"][0]["state"] == "0"
        assert store.transaction_details("resale")["equipment"][0]["state"] == "33"
        assert len(store.transaction_details("sale")["stats"]) == 2


def test_individual_orders_and_zero_placeholders_derive_compatible_depth(tmp_path):
    class Client:
        def get_json(self, endpoint, **kwargs):
            return {"result": {"data": {"buyOrders": [
                {"_id": "a", "price": Decimal("2.00000000000000001"), "quantity": 3, "user": None, "offerAt": "2026-09-22T01:00:00.001Z", "__v": 9},
                {"_id": "b", "price": Decimal("2.00000000000000001"), "quantity": 4, "mu": "unit", "future": [True, None]},
                {"_id": "zero-price", "price": 0, "quantity": 5},
                {"_id": "zero-quantity", "price": 1, "quantity": 0}], "sellOrders": []}}}
    orders = WarEraMarketApi(Client()).get_top_orders("steel", 4)
    with _store(tmp_path) as store:
        store.insert_order_book_observations({"steel": orders}, datetime.now(timezone.utc))
        entries = store.order_entries(1)
        assert [e["order_id"] for e in entries] == ["a", "b", "zero-price", "zero-quantity"]
        assert entries[0]["price_decimal"] == "2.00000000000000001"
        assert entries[0]["offer_at"].endswith(".001Z")
        assert entries[0]["extras"] == []
        assert len(entries[1]["extras"]) == 2
        assert store.latest_order_book_with_levels("steel")["bids"] == [{"level_position": 0, "price": 2.0, "quantity": 7.0}]
        broken = replace(orders, entries=(replace(orders.entries[0], side="bad"),))
        with pytest.raises(Exception):
            store.insert_order_book_observations({"steel": broken}, datetime.now(timezone.utc))
        assert len(store.order_book_observations_for_window("steel", 0)) == 1


def test_entity_names_are_optional_dated_cache(tmp_path):
    with _store(tmp_path) as store:
        assert store.entity_name("user", "one") is None
        store.cache_entity_name("user", "one", "New", "2026-09-22T00:00:00Z", "ok")
        store.cache_entity_name("user", "one", "Old", "2026-09-21T00:00:00Z", "ok")
        assert store.entity_name("user", "one")["name"] == "New"
        store.cache_entity_name("user", "one", None, "2026-09-23T00:00:00Z", "failed")
        assert store.entity_name("user", "one")["name"] == "New"
        assert store.entity_name("user", "one")["lookup_status"] == "failed"


def test_transport_preserves_decimal_tokens_before_boundary(monkeypatch):
    from requests import Response
    from warera_quant.api_client import WarEraApiClient
    response = Response()
    response.status_code = 200
    response._content = b'{"money":36.4800000000000040001}'
    client = WarEraApiClient(api_key="test", min_interval_seconds=0)
    monkeypatch.setattr(client.session, "request", lambda *args, **kwargs: response)
    assert client.get_json("/test")["money"] == Decimal("36.4800000000000040001")


@pytest.mark.parametrize("fixture", ["trading.observed.json", "itemMarket.observed.json"])
def test_all_observed_fixture_fields_have_typed_destinations(tmp_path, fixture):
    path = Path(__file__).parent / "fixtures" / "market_contracts" / fixture
    payload = json.loads(path.read_text(), parse_float=Decimal)
    facts = [normalize_transaction(item) for item in payload["result"]["data"]["items"]]
    assert all(not fact.extras and not fact.diagnostics for fact in facts)
    with _store(tmp_path) as store:
        result = store.ingest_transactions(facts)
        assert result.inserted == len(facts) and result.rejected == 0
        assert store.ingest_transactions(facts).unchanged == len(facts)
        for source, fact in zip(payload["result"]["data"]["items"], facts):
            row = store.transaction_details(fact.values["id"])
            assert row["money_decimal"] == str(source["money"])
            assert row["money_precision"] == "decimal"
            assert row["offer_created_at"] == source["offerCreatedAt"]
            assert row["updated_at"] == source["updatedAt"]
            assert row["created_at"] == source["createdAt"]
            assert row["extras"] == []
            if "item" in source:
                assert len(row["stats"]) == len(source["item"]["skills"])


def test_equipment_is_filtered_at_all_commodity_query_boundaries(tmp_path):
    with _store(tmp_path) as store:
        store.ingest_transactions([_sale(itemCode="steel")])
        store.ingest_transactions([normalize_transaction({"_id": "unclassified", "itemCode": "unknown", "createdAt": "2026-09-22T10:00:00Z"})])
        store.upsert_transactions("steel", [{"_id": "commodity", "createdAt": "2026-09-22T10:00:00Z", "money": 6, "quantity": 3}])
        assert store.item_codes(transaction_type="trading") == ["steel"]
        assert store.item_codes(transaction_type="itemMarket") == ["steel"]
        assert store.item_codes(transaction_type=None) == ["steel", "unknown"]
        assert [r["id"] for r in store.transactions_for_window("steel", 0)] == ["commodity"]
        assert [r["id"] for r in store.transactions_for_period(["steel", "unknown"], 0, 9999999999)] == ["commodity"]
        assert store.completed_daily_facts(["steel", "unknown"], 0, 9999999999)[0]["turnover"] == 6
        assert store.transaction_details("sale")["transaction_type"] == "itemMarket"


def test_housekeeping_cascades_new_children_and_trims_enrichment_coverage(tmp_path):
    with _store(tmp_path) as store:
        store.ingest_transactions([_sale()], progress=StreamProgress("itemMarket", oldest_at="2026-09-22T10:00:00Z", oldest_id="sale", status="exhausted"))
        store.record_enrichment_coverage(EnrichmentCoverage("itemMarket", "2026-09-01T00:00:00Z", "2026-09-30T00:00:00Z", "test", "bounded", "2026-09-30T00:00:00Z"))
        summary = store.run_housekeeping(retention_days=1, vacuum_interval_days=0, now=datetime(2026, 9, 25, tzinfo=timezone.utc))
        assert summary.transactions_deleted == 1
        assert store.transaction_details("sale") is None
        for table in ("transaction_equipment", "transaction_equipment_stats", "transaction_participants", "transaction_field_state"):
            assert store._connect().execute(f"select count(*) from {table}").fetchone()[0] == 0
        assert store.stream_status("itemMarket")["coverage"][0]["start_at"] == "2026-09-24T00:00:00Z"
        assert store.stream_status("itemMarket")["progress"]["status"] == "partial"


def test_rejected_page_cannot_claim_coverage_or_advance_progress(tmp_path):
    with _store(tmp_path) as store:
        invalid = replace(_sale(), stats={"bad": "not-a-number"})
        with pytest.raises(ValueError, match="Rejected"):
            store.ingest_transactions([_sale(_id="good"), invalid], progress=StreamProgress("itemMarket", status="exhausted"))
        assert store.transaction_details("good") is None
        assert store.stream_status("itemMarket")["progress"] is None


def test_invalid_source_shapes_are_rejected_without_partial_records(tmp_path):
    with _store(tmp_path) as store:
        base = {"_id": "bad", "createdAt": "2026-09-22T10:00:00Z"}
        result = store.upsert_transactions("steel", [{**base, "item": [1, 2]}, {**base, "money": True}, {**base, "createdAt": "bad"}])
        assert result.rejected == 3
        assert store.transaction_details("bad") is None


def test_idless_legacy_hash_does_not_change_when_request_context_supplies_type(tmp_path):
    import hashlib
    expected = hashlib.sha256(b"bread2026-06-30T09:45:00Z12.55").hexdigest()
    with _store(tmp_path) as store:
        store.upsert_transactions("bread", [{"createdAt": "2026-06-30T09:45:00Z", "money": "12.5", "quantity": "5"}])
        row = store.transaction_details(expected)
        assert row is not None
        assert row["transaction_type"] == "trading"
