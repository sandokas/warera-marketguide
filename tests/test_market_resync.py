"""Offline full-history integration: real parser/store with synthetic transport."""
import json
import sys
from datetime import timedelta

import pytest

from test_sync import FakeClient, NOW, trade, run
from warera_quant import cli, api_client
from warera_quant.market_store import MarketStore
from warera_quant.warera_api import TRANSACTIONS_ENDPOINT, normalize_transaction


def history():
    return {
        ("trading", None): {"items": [trade("legacy", code="delisted")], "nextCursor": "opaque-one"},
        ("trading", "opaque-one"): {"items": [trade("same-time", code="new-code")], "nextCursor": "opaque-two"},
        ("trading", "opaque-two"): {"items": [trade("ancient", "2020-01-01T00:00:00.000001Z", buyerId="buyer")]},
        ("itemMarket", None): {"items": [trade("equipment", "2020-01-01T00:00:00.000002Z", "retired-weapon", "itemMarket",
            item={"_id": "instance", "code": "retired-weapon", "skills": {"attack": 5}}, sellerId="seller")]},
    }


def full(store, client, **kwargs):
    return run(store, client, resync_market=True, history_scope="all", **kwargs)


def test_full_exhaustion_legacy_enrichment_exact_replay_and_no_repeated_catalog(tmp_path):
    path = tmp_path / "db"
    with MarketStore(path) as store:
        # Simulate a retained legacy row without normalized children.
        store.upsert_transactions("delisted", [trade("legacy", code="delisted")])
        with store._connect() as connection:
            connection.execute("update transactions set normalization_version=0 where id='legacy'")
        client = FakeClient(history())
        result = full(store, client, lookback_days=0.001)
        assert result.error_count == 0 and result.transactions_inserted == 3
        assert result.items[0].transactions_enriched == 1
        assert result.pages_fetched == 6  # four scan pages plus two catch-up pages
        status = store.market_sync_status()
        for stream in ("trading", "itemMarket"):
            assert status["streams"][stream]["latest_scan_exhausted"]
            assert status["streams"][stream]["unverified_retained"] == 0
        assert store.stream_status("trading")["progress"]["oldest_id"] == "ancient"
        assert store.stream_status("trading")["progress"]["oldest_at"].endswith("000001Z")
        calls = [payload for endpoint, payload in client.calls if endpoint == TRANSACTIONS_ENDPOINT]
        assert all(payload["limit"] == 100 and "itemCode" not in payload for payload in calls)
        assert len(client.calls) - len(calls) == 3
        before = {id: store.transaction_details(id) for id in ("legacy", "same-time", "ancient", "equipment")}
    # Restart has no cursor; replay must still traverse old pages and avoid row rewrites.
    with MarketStore(path) as store:
        statements = []
        store._connect().set_trace_callback(statements.append)
        client = FakeClient(history())
        result = full(store, client)
        assert result.transactions_inserted == 0
        assert result.items[0].transactions_enriched == 0
        assert all(store.transaction_details(id) == details for id, details in before.items())
        assert not any(sql.lower().startswith("update transactions ") for sql in statements)
        assert any(payload.get("cursor") == "opaque-two" for endpoint, payload in client.calls if endpoint == TRANSACTIONS_ENDPOINT)
        assert "opaque-" not in json.dumps(store.market_sync_status())
        assert not any("cursor" in col[1].lower() for table in store.table_names() for col in store._connect().execute(f'pragma table_info("{table}")'))


def test_same_timestamp_boundary_ids_are_not_seek_tokens(tmp_path):
    pages = history()
    pages[("trading", "opaque-two")]["items"][0]["createdAt"] = trade()["createdAt"]
    with MarketStore(tmp_path / "db") as store:
        full(store, FakeClient(pages))
        state = store.stream_status("trading")["progress"]
        assert state["oldest_id"] == "ancient" and state["newest_id"] == "same-time"
        assert store.market_sync_status()["streams"]["trading"]["retained"] == 3


def test_arrivals_during_other_stream_scan_and_equal_anchor_pages(tmp_path):
    client = FakeClient(history())
    original = client.get_json
    def arriving(endpoint, *, params=None):
        payload = json.loads(params["input"]) if params else {}
        if endpoint == TRANSACTIONS_ENDPOINT and payload["transactionType"] == "itemMarket":
            client.pages[("trading", None)] = {"items": [trade("arrival", NOW.isoformat())], "nextCursor": "equal-anchor"}
            client.pages[("trading", "equal-anchor")] = {"items": [trade("arrival-equal", NOW.isoformat())], "nextCursor": "old-head"}
            client.pages[("trading", "old-head")] = history()[("trading", None)]
        return original(endpoint, params=params)
    client.get_json = arriving
    with MarketStore(tmp_path / "db") as store:
        result = full(store, client)
        assert result.error_count == 0
        assert store.transaction_details("arrival") and store.transaction_details("arrival-equal")
        assert store.stream_status("trading")["progress"]["scan_anchor"] == NOW.isoformat().replace("+00:00", "Z")
        assert store.stream_status("trading")["progress"]["oldest_id"] == "ancient"


@pytest.mark.parametrize("failure", ["crash", "transport", "rejected"])
def test_atomic_recovery_replays_head_then_continues_older(tmp_path, monkeypatch, failure):
    path = tmp_path / "db"
    pages = history()
    if failure == "transport":
        pages[("trading", "opaque-one")] = RuntimeError("secret opaque-one URL")
    if failure == "rejected":
        pages[("trading", "opaque-one")]["items"][0]["money"] = "bad"
    with MarketStore(path) as store:
        if failure == "crash":
            original = store._write_progress
            def crash(state):
                original(state)
                if state.stream == "trading" and state.pages == 2:
                    raise KeyboardInterrupt()
            monkeypatch.setattr(store, "_write_progress", crash)
            with pytest.raises(KeyboardInterrupt):
                full(store, FakeClient(pages))
        else:
            assert full(store, FakeClient(pages)).error_count == 1
        state = store.stream_status("trading")
        assert state["progress"]["pages"] == 1 and state["progress"]["inserted"] == 1
        assert not state["coverage"] and store.transaction_details("same-time") is None
        assert state["progress"]["rejected"] == (1 if failure == "rejected" else 0)
        assert "opaque-one" not in json.dumps(state)
    with MarketStore(path) as store:
        result = full(store, FakeClient(history()))
        assert result.error_count == 0 and result.items[0].transactions_inserted == 2
        assert store.transaction_details("ancient")


def test_retention_preserves_history_or_cascades_and_trims_coverage(tmp_path):
    with MarketStore(tmp_path / "db") as store:
        full(store, FakeClient(history()))
        store.insert_price_observations({"bread": 1}, NOW - timedelta(days=200))
        summary = store.run_housekeeping(retention_days=120, transaction_retention_days="all", vacuum_interval_days=0, now=NOW)
        assert summary.transactions_deleted == 0 and summary.price_observations_deleted == 1
        assert store.transaction_details("equipment")["stats"]
        summary = store.run_housekeeping(retention_days=120, transaction_retention_days=7, vacuum_interval_days=0, now=NOW)
        assert summary.transactions_deleted == 2
        assert store.transaction_details("equipment") is None
        assert not store._connect().execute("select * from transaction_equipment_stats").fetchall()
        assert not store._connect().execute("pragma foreign_key_check").fetchall()
        assert all(c["start_at"] >= "2026-09-16" for c in store.stream_status("trading")["coverage"])
        assert store.stream_status("trading")["progress"]["status"] == "partial"


def test_status_offline_partial_and_empty_stream(tmp_path, monkeypatch, capsys):
    path = tmp_path / "db"
    with MarketStore(path) as store:
        run(store, FakeClient(history()), history_pages=1)
        assert store.market_sync_metadata().status == "partial"
    monkeypatch.setattr(cli, "WarEraApiClient", lambda **kw: pytest.fail("status used API"))
    monkeypatch.setattr(sys, "argv", ["guide", "--market-sync-status", "--market-db", str(path)])
    cli.main()
    status = json.loads(capsys.readouterr().out)
    assert status["streams"]["trading"]["progress"]["status"] == "partial"
    assert not status["streams"]["trading"]["latest_scan_exhausted"]
    assert status["streams"]["itemMarket"]["latest_scan_exhausted"]
    assert "not complete game history" in status["limitations"]


@pytest.mark.parametrize("flags", [
    ["--market-sync-status", "--sync"], ["--market-sync-status", "--migrate-db"],
    ["--sync", "--resync-market", "--history-scope", "all", "--transaction-backfill"],
    ["--sync", "--resync-market", "--history-scope", "all", "--exclude-item-code", "bread"],
    ["--sync", "--resync-market", "--history-scope", "all", "--history-pages", "1"],
    ["--sync", "--history-pages", "-1"], ["--sync", "--order-limit", "101"],
])
def test_conflicts_before_api(monkeypatch, flags):
    monkeypatch.setattr(cli, "WarEraApiClient", lambda **kw: pytest.fail("constructed API"))
    monkeypatch.setattr(sys, "argv", ["guide", *flags])
    with pytest.raises(SystemExit):
        cli.main()


@pytest.mark.parametrize("statuses,expected", [([429, 503, 200], 3), ([403], 1), ([500]*4, 4)])
def test_paced_bounded_read_retries(monkeypatch, statuses, expected):
    # Patch the existing transport; no credentials or network are consulted.
    monkeypatch.setattr(api_client, "load_dotenv", lambda: None)
    ticks = [100.0]
    sleeps = []
    def sleep(seconds):
        sleeps.append(seconds)
        ticks[0] += seconds
    monkeypatch.setattr(api_client.time, "monotonic", lambda: ticks[0])
    monkeypatch.setattr(api_client.time, "sleep", sleep)
    client = api_client.WarEraApiClient(api_key="offline", min_interval_seconds=1)
    calls = []
    def request(*a, **kw):
        status = statuses[len(calls)]
        calls.append(status)
        response = api_client.requests.Response()
        response.status_code = status
        response.headers["Retry-After"] = "2"
        response._content = b'{"value": 1.001}'
        return response
    monkeypatch.setattr(client.session, "request", request)
    if statuses[-1] == 200:
        assert str(client.get_json("fake")["value"]) == "1.001"
    else:
        with pytest.raises(api_client.requests.HTTPError):
            client.get_json("fake")
    assert len(calls) == expected
    if expected > 1:
        assert sleeps and all(1 <= value <= 30 for value in sleeps)


def test_catchup_failure_keeps_exhaustion_separate_from_job_success(tmp_path):
    client = FakeClient(history())
    original = client.get_json
    heads = 0
    def fail_catchup(endpoint, *, params=None):
        nonlocal heads
        payload = json.loads(params["input"]) if params else {}
        if endpoint == TRANSACTIONS_ENDPOINT and payload["transactionType"] == "trading" and "cursor" not in payload:
            heads += 1
            if heads == 2:
                raise RuntimeError("catch-up unavailable")
        return original(endpoint, params=params)
    client.get_json = fail_catchup
    with MarketStore(tmp_path / "db") as store:
        assert full(store, client).error_count == 1
        status = store.market_sync_status()["streams"]["trading"]
        assert status["latest_scan_exhausted"]
        assert status["progress"]["status"] == "failed"
        assert status["progress"]["pages"] == 3
        assert status["progress"]["inserted"] == 3
        assert store.market_sync_metadata().status == "partial"


def test_transport_timeouts_retry_but_mutations_do_not(monkeypatch):
    monkeypatch.setattr(api_client, "load_dotenv", lambda: None)
    monkeypatch.setattr(api_client.time, "sleep", lambda _: None)
    client = api_client.WarEraApiClient(api_key="offline", min_interval_seconds=0)
    calls = []
    def timeout(*a, **kw):
        calls.append(a)
        raise api_client.requests.Timeout()
    monkeypatch.setattr(client.session, "request", timeout)
    with pytest.raises(api_client.requests.Timeout):
        client.get_json("fake")
    assert len(calls) == 4
    calls.clear()
    with pytest.raises(api_client.requests.Timeout):
        client.request_json("POST", "fake")
    assert len(calls) == 1
