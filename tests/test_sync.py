"""Global stream tests use the real boundary parser and a network-free transport."""
import json
from datetime import datetime, timezone

import pytest

from warera_quant.market_store import MarketStore
from warera_quant.sync import sync_market_data
from warera_quant.warera_api import (WarEraMarketApi, PRICES_ENDPOINT, GAME_CONFIG_ENDPOINT,
                                    TOP_ORDERS_ENDPOINT, TRANSACTIONS_ENDPOINT, normalize_transaction)

NOW = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)


def trade(id="new", stamp="2026-09-23T10:00:00.001Z", code="bread", kind="trading", **extra):
    return {"_id": id, "createdAt": stamp, "itemCode": code, "transactionType": kind,
            "money": "12.001", "quantity": 4, **extra}


class FakeClient:
    def __init__(self, pages=None, order_error=False):
        self.pages = pages or {}
        self.calls = []
        self.order_error = order_error

    def get_json(self, endpoint, *, params=None):
        payload = json.loads(params["input"]) if params else None
        self.calls.append((endpoint, payload))
        if endpoint == PRICES_ENDPOINT:
            data = {"bread": 3}
        elif endpoint == GAME_CONFIG_ENDPOINT:
            data = {"items": {"bread": {"isTradable": True, "productionPoints": 10}}}
        elif endpoint == TOP_ORDERS_ENDPOINT:
            if self.order_error:
                raise RuntimeError("orders unavailable")
            data = {"buyOrders": [{"_id": "a", "price": 2, "quantity": 3},
                                  {"_id": "b", "price": 2, "quantity": 4}], "sellOrders": []}
        elif endpoint == TRANSACTIONS_ENDPOINT:
            assert "itemCode" not in payload
            data = self.pages.get((payload["transactionType"], payload.get("cursor")), {"items": []})
            if isinstance(data, Exception):
                raise data
        else:
            raise AssertionError(endpoint)
        return {"result": {"data": data}}


def run(store, client, **kwargs):
    return sync_market_data(WarEraMarketApi(client), store, observed_at=NOW, **kwargs)


def test_global_streams_new_codes_individual_orders_and_replay(tmp_path):
    client = FakeClient({("trading", None): {"items": [trade(code="retiredCode")]},
        ("itemMarket", None): {"items": [trade("equipment", code="weapon99", kind="itemMarket",
            buyerId="actor", buyerMuId="mu", buyerCountryId="country", sellerPartyId="party",
            item={"_id": "instance", "code": "weapon99", "skills": {"newSkill": 0, "attack": 12},
                  "state": 44, "maxState": 100, "quantity": 1, "lastAcquisitionAt": "2026-09-22T00:00:00Z"})]}})
    with MarketStore(tmp_path / "db") as store:
        result = run(store, client)
        assert result.error_count == 0 and result.transactions_inserted == 2
        assert result.pages_fetched == 2
        assert store.item_codes() == ["bread", "retiredCode"]
        row = store.transaction_details("equipment")
        assert row["participants"][0]["user_id"] == "actor"
        assert row["participants"][0]["mu_id"] == "mu"
        assert row["participants"][0]["country_id"] == "country"
        assert len(row["stats"]) == 2 and row["equipment"][0]["equipment_type"] is None
        book = store.order_book_observations_for_window("bread", 0)[0]
        entries = store.order_entries(book["id"])
        assert [e["order_id"] for e in entries] == ["a", "b"]
        replay = run(store, client)
        assert replay.transactions_inserted == 0 and replay.transactions_skipped == 2
        assert store.stream_status("trading")["progress"]["unchanged"] == 1
        assert store.transaction_coverage(["retiredCode"])["retiredCode"]


def test_recent_resync_ignores_duplicates_and_report_window(tmp_path):
    recent = trade()
    equal = trade("equal", "2026-09-16T12:00:00Z")
    client = FakeClient({("trading", None): {"items": [recent, equal], "nextCursor": "opaque"},
                        ("trading", "opaque"): {"items": [trade("older", "2026-09-16T11:59:59.999Z")], "nextCursor": "unused"}})
    with MarketStore(tmp_path / "db") as store:
        store.ingest_transactions([normalize_transaction(recent)])
        result = run(store, client, resync_market=True, history_scope="7d", lookback_days=1)
        assert result.error_count == 0 and result.pages_fetched == 5
        assert store.transaction_details("older")
        coverage = store.stream_status("trading")["coverage"][0]
        assert coverage["start_at"] == "2026-09-16T12:00:00Z"
        assert coverage["completion_reason"] == "history-boundary"
        assert "opaque" not in str(store.stream_status("trading"))


def test_duplicate_without_verified_coverage_and_equal_timestamps_continue(tmp_path):
    first = trade()
    client = FakeClient({("trading", None): {"items": [first], "nextCursor": "next"},
                        ("trading", "next"): {"items": [trade("same-time")]}})
    with MarketStore(tmp_path / "db") as store:
        store.ingest_transactions([normalize_transaction(first)])
        result = run(store, client)
        assert result.pages_fetched == 3 and result.transactions_inserted == 1
        assert not result.items[0].stopped_at_duplicate


def test_incremental_stops_only_on_verified_normalized_overlap(tmp_path):
    rows = [trade(), trade("older", "2026-09-23T09:00:00Z")]
    with MarketStore(tmp_path / "db") as store:
        run(store, FakeClient({("trading", None): {"items": rows}}))
        client = FakeClient({("trading", None): {"items": rows, "nextCursor": "not-followed"}})
        result = run(store, client)
        assert result.items[0].stopped_at_high_water
        assert result.pages_fetched == 2


@pytest.mark.parametrize("failure", ["parse", "ordering", "commit", "reject", "transport"])
def test_failed_pages_do_not_commit_rows_or_coverage(tmp_path, monkeypatch, failure):
    first = trade()
    second = trade("second", "2026-09-23T09:00:00Z")
    pages = {("trading", None): {"items": [first], "nextCursor": "secret-cursor"},
             ("trading", "secret-cursor"): {"items": [second]}}
    if failure == "parse":
        second["money"] = "invalid"
    if failure == "ordering":
        second["createdAt"] = "2026-09-23T11:00:00Z"
    if failure == "transport":
        pages[("trading", "secret-cursor")] = RuntimeError("URL contains secret-cursor")
    with MarketStore(tmp_path / "db") as store:
        if failure == "commit":
            original = store._write_progress
            def fail(state):
                original(state)
                if state.stream == "trading" and state.pages == 2:
                    raise RuntimeError("commit failed")
            monkeypatch.setattr(store, "_write_progress", fail)
        if failure == "reject":
            original = store._merge_transaction
            def reject(fact, fetched):
                if fact.values["id"] == "second":
                    raise ValueError("bad child")
                return original(fact, fetched)
            monkeypatch.setattr(store, "_merge_transaction", reject)
        messages = []
        result = run(store, FakeClient(pages), progress=messages.append, verbose=True)
        assert result.error_count == 1
        assert store.transaction_details("new") and store.transaction_details("second") is None
        state = store.stream_status("trading")
        assert state["progress"]["pages"] == 1 and state["progress"]["inserted"] == 1
        assert state["progress"]["status"] == "failed" and not state["coverage"]
        assert "secret-cursor" not in str(state) + str(messages)
        assert store.stream_status("itemMarket")["progress"]["status"] == "exhausted"
        assert store.market_sync_metadata().status == "partial"


def test_failure_recording_preserves_original_exception(tmp_path, monkeypatch):
    original = RuntimeError("write failed")
    with MarketStore(tmp_path / "db") as store:
        def fail(*a, **k):
            raise original
        record_original = store.record_stream_progress
        def record(state):
            if state.status == "failed":
                raise ValueError("state failed")
            record_original(state)
        monkeypatch.setattr(store, "ingest_transactions", fail)
        monkeypatch.setattr(store, "record_stream_progress", record)
        with pytest.raises(RuntimeError) as caught:
            run(store, FakeClient())
        assert caught.value is original and original.__notes__


def test_order_failure_does_not_block_transactions(tmp_path):
    with MarketStore(tmp_path / "db") as store:
        result = run(store, FakeClient({("trading", None): {"items": [trade()]}}, order_error=True))
        assert result.error_count == 1 and result.transactions_inserted == 1
        assert result.order_books_observed == 0


def test_page_cap_and_legacy_backfill(tmp_path):
    client = FakeClient({("trading", None): {"items": [trade()], "nextCursor": "next"},
                        ("trading", "next"): {"items": [trade("old", "2026-09-01T00:00:00Z")], "nextCursor": "unused"}})
    with MarketStore(tmp_path / "db") as store:
        result = run(store, client, history_pages=1)
        assert result.pages_fetched == 2
        assert store.stream_status("trading")["progress"]["status"] == "partial"
        result = run(store, client, transaction_backfill=True, lookback_days=7)
        assert result.pages_fetched == 3 and store.transaction_details("old")


@pytest.mark.parametrize("options", [{"transaction_limit": 101}, {"history_scope": "all"},
    {"resync_market": True}, {"resync_market": True, "history_scope": "7d", "history_pages": 1}])
def test_invalid_scope_before_any_request(tmp_path, options):
    client = FakeClient()
    with MarketStore(tmp_path / "db") as store:
        with pytest.raises(ValueError):
            run(store, client, **options)
    assert not client.calls


@pytest.mark.parametrize("failed_endpoint", [PRICES_ENDPOINT, GAME_CONFIG_ENDPOINT])
def test_current_catalog_failure_does_not_block_global_collection(tmp_path, failed_endpoint):
    client = FakeClient({("trading", None): {"items": [trade(code="notInCatalog")]}})
    original = client.get_json
    def fail(endpoint, **kwargs):
        if endpoint == failed_endpoint:
            raise RuntimeError("unavailable")
        return original(endpoint, **kwargs)
    client.get_json = fail
    with MarketStore(tmp_path / "db") as store:
        result = run(store, client)
        assert result.error_count == 1 and result.transactions_inserted == 1
        assert store.transaction_details("new")["item_code"] == "notInCatalog"
