"""Opted-in opaque continuations: crash safety, phase restart and no token output."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_market_resync import full, history
from test_sync import FakeClient
from warera_quant.market_store import MarketStore
from warera_quant.warera_api import TRANSACTIONS_ENDPOINT
from warera_quant import cli


def calls(client, stream):
    return [p for endpoint,p in client.calls if endpoint == TRANSACTIONS_ENDPOINT and p['transactionType'] == stream]


def test_checkpoint_rolls_back_with_page_and_resumes_next_committed_cursor(tmp_path, monkeypatch):
    path = tmp_path / 'atomic.db'
    with MarketStore(path) as store:
        original = store._write_checkpoint
        def crash(checkpoint):
            original(checkpoint)
            if checkpoint.stream == 'trading' and checkpoint.pages == 2:
                raise KeyboardInterrupt()
        monkeypatch.setattr(store, '_write_checkpoint', crash)
        with pytest.raises(KeyboardInterrupt):
            full(store, FakeClient(history()), resume_market=True)
        assert store.stream_checkpoint('trading').pages == 1
        assert store.stream_checkpoint('trading').next_cursor == 'opaque-one'
        assert store.transaction_details('same-time') is None
        assert store.stream_status('trading')['progress']['pages'] == 1
    with MarketStore(path) as store:
        client = FakeClient(history())
        messages = []
        result = full(store, client, resume_market=True, progress=messages.append)
        assert result.error_count == 0
        assert calls(client, 'trading')[0]['cursor'] == 'opaque-one'
        assert store.stream_checkpoint('trading').phase == 'history-complete'
        assert store.market_sync_status()['streams']['trading']['latest_scan_exhausted']
        assert 'opaque-' not in json.dumps(store.market_sync_status()) + str(messages)


def test_completed_stream_is_not_rewalked_after_other_stream_crash(tmp_path):
    path = tmp_path / 'streams.db'
    client = FakeClient(history())
    original = client.get_json
    def crash(endpoint, *, params=None):
        if endpoint == TRANSACTIONS_ENDPOINT and json.loads(params['input'])['transactionType'] == 'itemMarket':
            raise KeyboardInterrupt()
        return original(endpoint, params=params)
    client.get_json = crash
    with MarketStore(path) as store:
        with pytest.raises(KeyboardInterrupt):
            full(store, client, resume_market=True)
        assert store.stream_checkpoint('trading').phase == 'history-complete'
    with MarketStore(path) as store:
        client = FakeClient(history())
        assert full(store, client, resume_market=True).error_count == 0
        assert len(calls(client, 'trading')) == 1  # catch-up only, no historical replay
        assert all(s['latest_scan_exhausted'] for s in store.market_sync_status()['streams'].values())


def test_hard_process_exit_leaves_resumable_committed_page(tmp_path):
    path = tmp_path / 'killed.db'
    script = tmp_path / 'worker.py'
    script.write_text('''import json,os,sys
sys.path.insert(0, sys.argv[2])
from test_market_resync import full,history
from test_sync import FakeClient
from warera_quant.market_store import MarketStore
from warera_quant.warera_api import TRANSACTIONS_ENDPOINT
client=FakeClient(history())
original=client.get_json
def kill(endpoint, *, params=None):
    if endpoint == TRANSACTIONS_ENDPOINT and json.loads(params['input']).get('cursor') == 'opaque-one':
        os._exit(19)
    return original(endpoint, params=params)
client.get_json=kill
with MarketStore(sys.argv[1]) as store:
    full(store,client,resume_market=True)
''')
    result = subprocess.run([sys.executable,str(script),str(path),str(Path(__file__).parent)], timeout=30)
    assert result.returncode == 19
    with MarketStore(path) as store:
        assert store.stream_checkpoint('trading').pages == 1
        client = FakeClient(history())
        assert full(store, client, resume_market=True).error_count == 0
        assert calls(client, 'trading')[0]['cursor'] == 'opaque-one'
        assert store.market_sync_status()['streams']['trading']['retained'] == 3


def test_rejected_continuation_keeps_checkpoint_and_does_not_fallback(tmp_path):
    pages = history()
    pages[('trading','opaque-one')] = RuntimeError('expired secret continuation')
    with MarketStore(tmp_path / 'invalid.db') as store:
        assert full(store, FakeClient(pages), resume_market=True).error_count == 1
        point = store.stream_checkpoint('trading')
        client = FakeClient(pages)
        assert full(store, client, resume_market=True).error_count == 1
        assert len(calls(client, 'trading')) == 1
        assert calls(client, 'trading')[0]['cursor'] == point.next_cursor
        assert store.stream_checkpoint('trading') == point
        assert not store.market_sync_status()['streams']['trading']['latest_scan_exhausted']
        assert 'expired secret' not in json.dumps(store.market_sync_status())


def test_exhaustion_certifies_only_observed_dates_and_empty_has_no_interval(tmp_path):
    with MarketStore(tmp_path / 'floor.db') as store:
        full(store, FakeClient(history()), resume_market=True)
        coverage = store.stream_status('trading')['coverage']
        exhausted = next(c for c in coverage if c['completion_reason'] == 'api-exhausted')
        assert exhausted['start_at'] == '2020-01-01T00:00:00.000001Z'
        count = store.market_sync_status()['streams']['trading']['retained']
        # Rehearse the correction of metadata produced by the previous release.
        with store._connect() as connection:
            connection.execute("update market_enrichment_coverage set start_at='1970-01-01T00:00:00Z' where stream='trading' and completion_reason='api-exhausted'")
        assert store.repair_exhaustion_floor('trading', observed_at=exhausted['observed_at'], oldest_at=exhausted['start_at']) == 1
        assert store.repair_exhaustion_floor('trading', observed_at=exhausted['observed_at'], oldest_at=exhausted['start_at']) == 0
        assert store.market_sync_status()['streams']['trading']['retained'] == count
    with MarketStore(tmp_path / 'empty.db') as store:
        assert full(store, FakeClient(), resume_market=True).error_count == 0
        for stream in ('trading', 'itemMarket'):
            assert store.market_sync_status()['streams'][stream]['latest_scan_exhausted']
            assert store.stream_status(stream)['coverage'] == []


def test_resume_after_catchup_crash_skips_finished_history(tmp_path):
    client = FakeClient(history())
    original = client.get_json
    heads = 0
    def crash(endpoint, *, params=None):
        nonlocal heads
        p = json.loads(params['input']) if params else {}
        if endpoint == TRANSACTIONS_ENDPOINT and p['transactionType'] == 'trading' and not p.get('cursor'):
            heads += 1
            if heads == 2:
                raise KeyboardInterrupt()
        return original(endpoint, params=params)
    client.get_json = crash
    path = tmp_path / 'catchup.db'
    with MarketStore(path) as store:
        with pytest.raises(KeyboardInterrupt):
            full(store, client, resume_market=True)
    with MarketStore(path) as store:
        resumed = FakeClient(history())
        assert full(store, resumed, resume_market=True).error_count == 0
        assert len(calls(resumed, 'trading')) == len(calls(resumed, 'itemMarket')) == 1


@pytest.mark.parametrize('flags', [
    ['--resume-market'], ['--sync','--resume-market'],
    ['--sync','--resync-market','--history-scope','7d','--resume-market'],
    ['--market-sync-status','--resume-market'], ['--migrate-db','--resume-market'],
])
def test_resume_flags_fail_before_network(monkeypatch, flags):
    monkeypatch.setattr(cli, 'WarEraApiClient', lambda **kw: pytest.fail('network constructed'))
    monkeypatch.setattr(sys,'argv',['guide',*flags])
    with pytest.raises(SystemExit):
        cli.main()
