from datetime import datetime, timedelta, timezone
from decimal import Decimal
from time import perf_counter

import pytest

from warera_quant.market_data import load_participant_report, iter_equipment_sale_details
from warera_quant.market_store import MarketStore
from warera_quant.warera_api import normalize_transaction
from warera_quant.market_models import StreamProgress, EnrichmentCoverage

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


def fact(id, day, *, buyer='U', seller='V', money='10', quantity='1', equipment=None, **extra):
    source = {'_id': id, 'createdAt': (NOW + timedelta(days=day)).isoformat(),
              'transactionType': 'itemMarket' if equipment is not None else 'trading',
              'itemCode': 'never-in-price-list', 'buyerId': buyer, 'sellerId': seller,
              'money': Decimal(money), 'quantity': Decimal(quantity), **extra}
    if equipment is not None:
        source['item'] = equipment
    return normalize_transaction(source)


def ingest(store, facts):
    summary = store.ingest_transactions(facts, fetched_at=NOW)
    assert summary.rejected == 0


def test_history_contains_earlier_buys_and_sells_but_not_inactive_or_future(tmp_path):
    with MarketStore(tmp_path / 'market.db') as store:
        ingest(store, [fact('a', -30), fact('b', -20, buyer='V', seller='U'),
            fact('inactive', -20, buyer='X', seller='Y'), fact('c', -7),
            fact('d', -1, buyer='V', seller='U'), fact('future', 0)])
        rows = list(store.iter_participant_history(NOW - timedelta(days=7), NOW, batch_size=1))
        assert [r['id'] for r in rows] == ['a', 'b', 'c', 'd']
        assert all(len(row['participants']) == 2 for row in rows)
        result = load_participant_report(store, as_of=NOW, batch_size=2)
        user = next(r for r in result['entities'] if r['entity_id'] == 'U')
        assert user['source_turnover'] == 20
        assert user['matched_net_pnl'] is None
        assert result['turnover_basis'] == 'source-money'
        assert not result['rankings']['user']['profits']
        assert user['name'] == 'U'
        assert user['matched_sale_value_coverage'] is None


def test_ownership_names_conflicts_self_trades_and_actor_retention(tmp_path):
    with MarketStore(tmp_path / 'market.db') as store:
        ingest(store, [fact('a', -6, buyer='actor', buyerMuId='M', sellerCountryId='C'),
            fact('b', -5, buyer='actor', buyerMuId='M', buyerCountryId='C'),
            fact('self', -4, buyer='actor', seller='other', buyerMuId='M', sellerMuId='M')])
        store.cache_entity_name('mu', 'M', '<name>', NOW.isoformat(), 'found')
        result = load_participant_report(store, as_of=NOW)
        assert not any(r['entity_id'] == 'actor' for r in result['entities'])
        mu = result['rankings']['mu']['volume'][0]
        assert mu['source_turnover'] == 10
        assert mu['name'] == '<name>'  # rendering escapes later, preserved source now
        assert mu['name_observed_at'] == NOW.isoformat()
        assert mu['actor_ids'] == ['actor']
        assert result['coverage']['unassigned_count'] == 1
        assert result['coverage']['self_trade_count'] == 1
        assert result['rankings']['country']['volume'][0]['entity_id'] == 'C'


def test_equipment_export_preserves_each_snapshot_no_matching_or_signals(tmp_path):
    with MarketStore(tmp_path / 'market.db') as store:
        item = {'_id': 'same-id', 'code': 'new-equipment', 'skills': {'z': 2, 'a': 0},
                'state': 10, 'maxState': 20}
        ingest(store, [fact('old', -10, equipment=item),
            fact('a', -7, buyer='V', seller='U', equipment={**item, 'state': 8}),
            fact('b', -1, equipment={**item, 'state': 3, 'skills': {'z': 4, 'a': 0}}),
            fact('at-end', 0, equipment=item), fact('commodity', -1)])
        sales = list(iter_equipment_sale_details(store, as_of=NOW, batch_size=1))
        assert [r['id'] for r in sales] == ['a', 'b']
        assert [r['equipment']['state'] for r in sales] == ['8', '3']
        assert sales[0]['equipment']['stats'] == {'a': '0', 'z': '2'}
        assert sales[1]['equipment']['stats']['z'] == '4'
        assert all(r['net_realized_pnl'] is None for r in sales)
        assert all('signal' not in r and 'ask' not in r for r in sales)
        assert sales[0]['participants']['sell']['user_id'] == 'U'
        report = load_participant_report(store, as_of=NOW)
        assert report['rankings']['user']['profits'] == []
        assert store.item_codes(transaction_type='trading') == ['never-in-price-list']
        assert report['rankings']['user']['volume']


def test_missing_stats_and_zero_stats_remain_distinct(tmp_path):
    with MarketStore(tmp_path / 'market.db') as store:
        ingest(store, [fact('a', -6, equipment={'_id': 'a', 'code': 'gear'}),
            fact('b', -5, equipment={'_id': 'b', 'code': 'gear', 'skills': {'attack': 0}, 'state': 0})])
        sales = list(iter_equipment_sale_details(store, as_of=NOW))
        assert sales[0]['equipment']['stats'] is None
        assert sales[1]['equipment']['stats'] == {'attack': '0'}
        assert sales[0]['equipment']['state'] is None
        assert sales[1]['equipment']['state'] == '0'
        user = next(r for r in load_participant_report(store, as_of=NOW)['entities'] if r['entity_id'] == 'U')
        assert len(user['categories']['buy']) == 2


def test_empty_and_utc_microsecond_boundaries(tmp_path):
    with MarketStore(tmp_path / 'market.db') as store:
        empty = load_participant_report(store, as_of=NOW)
        assert empty['entities'] == []
        assert all(not board for group in empty['rankings'].values() for board in group.values())
        start = NOW - timedelta(days=7)
        sources = []
        for id, at in [('before', start - timedelta(microseconds=1)), ('start', start),
                       ('inside', NOW - timedelta(microseconds=1)), ('end', NOW)]:
            sources.append(normalize_transaction({'_id': id, 'transactionType': 'itemMarket',
                'itemCode': 'unknown',
                'createdAt': at.astimezone(timezone(timedelta(hours=3))).isoformat(),
                'money': 1, 'quantity': 1, 'buyerId': 'U', 'item': {'code': 'unknown'}}))
        ingest(store, sources)
        assert [r['id'] for r in iter_equipment_sale_details(store, as_of=NOW)] == ['start', 'inside']
        result = load_participant_report(store, as_of=NOW)
        assert result['coverage']['market_transaction_count'] == 2
        assert result['coverage']['unassigned_count'] == 2
        assert result['as_of'] == NOW


def test_legacy_source_precision_orders_mixed_history_and_half_open_exports(tmp_path):
    """Opaque IDs and truncated legacy epochs cannot order same-second trades."""
    cutoff = NOW + timedelta(microseconds=500001)
    start = cutoff - timedelta(days=7)
    stamps = [('before', start - timedelta(microseconds=1)), ('start', start),
              ('z-early-legacy', NOW - timedelta(seconds=1, microseconds=900000)),
              ('m-normalized', NOW - timedelta(seconds=1, microseconds=500000)),
              ('a-late-legacy', NOW - timedelta(seconds=1, microseconds=100000)),
              ('inside', cutoff - timedelta(microseconds=1)), ('end', cutoff)]
    with MarketStore(tmp_path / 'legacy-order.db') as store:
        ingest(store, [normalize_transaction({'_id':id, 'createdAt':at.isoformat(),
            'transactionType':'itemMarket', 'itemCode':'gear', 'money':1, 'quantity':1,
            'buyerId':'U', 'sellerId':'V', 'item':{'code':'gear'}}) for id,at in stamps])
        with store._connect() as connection:
            connection.execute("update transactions set created_at_us=null, normalization_version=0 where id != 'm-normalized'")
        history = list(store.iter_participant_history(start, cutoff, batch_size=1))
        assert [row['id'] for row in history] == [id for id,_ in stamps[:-1]]
        sales = list(store.iter_equipment_sales(start, cutoff, batch_size=1))
        assert [row['id'] for row in sales] == [id for id,_ in stamps[1:-1]]
        report = load_participant_report(store, as_of=cutoff, batch_size=1)
        assert report['coverage']['market_transaction_count'] == 5


def test_historical_exhaustion_survives_later_incremental_status(tmp_path):
    with MarketStore(tmp_path / 'historical-exhaustion.db') as store:
        ingest(store, [fact('a', -1)])
        store.record_enrichment_coverage(EnrichmentCoverage('trading', (NOW-timedelta(days=3)).isoformat(),
            NOW.isoformat(), 'global-contiguous-pagination', 'api-exhausted', NOW.isoformat()))
        store.record_stream_progress(StreamProgress('trading', scan_mode='incremental', status='complete'))
        report = load_participant_report(store, as_of=NOW)
        stream = report['source_coverage']['streams']['trading']
        assert stream['history_exhaustion_observed']
        assert not stream['window_covered']  # Exhausted three days does not cover seven days.


def test_source_gaps_retained_separately_and_order_failure_does_not_block_volume(tmp_path):
    with MarketStore(tmp_path / 'market.db') as store:
        ingest(store, [fact('a', -6)])
        store.record_stream_progress(StreamProgress('trading', status='failed', last_error='PageError'))
        store.record_enrichment_coverage(EnrichmentCoverage('trading', (NOW - timedelta(days=7)).isoformat(),
            (NOW - timedelta(days=5)).isoformat(), 'fixture', 'page', NOW.isoformat()))
        result = load_participant_report(store, as_of=NOW)
        assert result['sources']['streams']['trading']['progress']['last_error'] == 'PageError'
        assert result['source_coverage']['status'] == 'partial_or_unverified'
        assert result['rankings']['user']['volume'][0]['source_turnover'] == 10


def test_batched_stream_query_plan_and_synthetic_volume(tmp_path):
    with MarketStore(tmp_path / 'market.db') as store:
        # Distinct inactive references test actual candidate reduction; active
        # entities retain all 1,200 old dispositions, plus a 20-event window.
        facts = [fact(f'old{i:05}', -30 + i / 1000,
                      buyer='U' if i % 2 else 'V', seller='V' if i % 2 else 'U') for i in range(1200)]
        facts += [fact(f'inactive{i:05}', -30, buyer=f'x{i}', seller=f'y{i}') for i in range(1200)]
        facts += [fact(f'recent{i:05}', -6 + i / 100, buyer='U', seller='V') for i in range(20)]
        ingest(store, facts)
        plans = store.participant_query_plan(NOW - timedelta(days=7), NOW)
        assert any('idx_participant_user' in plan for plan in plans)
        assert any('SEARCH t USING INDEX sqlite_autoindex_transactions_1 (id=?)' in plan for plan in plans)
        statements = []
        store._connect().set_trace_callback(statements.append)
        started = perf_counter()
        ids = [row['id'] for row in store.iter_participant_history(NOW - timedelta(days=7), NOW, batch_size=100)]
        elapsed = perf_counter() - started
        store._connect().set_trace_callback(None)
        assert len(ids) == 1220
        assert not any(id.startswith('inactive') for id in ids)
        # 1 main query + four batched child queries per batch, not N+1.
        assert len(statements) == 1 + 4 * 13
        print(f'participant synthetic: stored=2420 selected={len(ids)} batch=100 statements={len(statements)} elapsed={elapsed:.4f}s')
        print('participant query plan: ' + ' | '.join(plans))
        with pytest.raises(ValueError, match='batch_size'):
            list(store.iter_participant_history(NOW - timedelta(days=7), NOW, batch_size=501))
