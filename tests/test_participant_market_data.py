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


def test_default_window_never_materializes_old_active_rows_or_csv(tmp_path, monkeypatch):
    import csv
    import warera_quant.market_data as data
    from warera_quant.report import _write_participant_exports
    with MarketStore(tmp_path / 'bounded.db') as store:
        ingest(store, [fact('start', -7), fact('sale', -1, buyer='V', seller='U')])
        original = data._participant_trade_input
        materialized = []
        def observe(row):
            materialized.append(row['id'])
            return original(row)
        monkeypatch.setattr(data, '_participant_trade_input', observe)
        def forbidden(*args, **kwargs):
            raise AssertionError('default report/CSV invoked history')
        monkeypatch.setattr(store, 'iter_participant_history', forbidden)
        def run():
            materialized.clear()
            sql = []
            store._connect().set_trace_callback(sql.append)
            try:
                report = data.load_participant_report(store, as_of=NOW, batch_size=1)
            finally:
                store._connect().set_trace_callback(None)
            children = [q for q in sql if q.startswith('select * from transaction_')]
            assert materialized == ['start', 'sale']
            assert len(children) == 8  # four child queries for each one-parent batch
            assert all("'old" not in q for q in children)
            return report, children
        before, child_before = run()
        ingest(store, [fact(f'old{i:04}', -30, buyer='U', seller='V') for i in range(2000)])
        after, child_after = run()
        for key in ("entities", "rankings", "coverage", "source_coverage"):
            assert before[key] == after[key]
        assert child_before == child_after
        assert after['accounting_mode'] == 'window'
        assert after['accounting_status'] == 'not_calculated'
        user = next(r for r in after['entities'] if r['entity_id'] == 'U')
        assert user['source_turnover'] == 20
        for field in ('matched_net_pnl', 'matched_quantity', 'uncosted_quantity',
                      'unknown_fee_quantity', 'matched_source_value_coverage'):
            assert user[field] is None
        _write_participant_exports(tmp_path, after, [])
        with (tmp_path / 'participant_rankings_7d.csv').open(newline='', encoding='utf-8') as f:
            rows = list(csv.DictReader(f))
        assert rows and all(r['accounting_mode'] == 'window' for r in rows)
        assert all(r['accounting_status'] == 'not_calculated' and r['matched_quantity'] == '' for r in rows)


def test_window_iterator_exact_microsecond_bounds(tmp_path):
    start = NOW - timedelta(days=7)
    end = NOW
    facts = [normalize_transaction({'_id': name, 'createdAt':stamp.isoformat(),
                                    'transactionType':'trading', 'itemCode':'steel',
                                    'buyerId':'U', 'sellerId':'V', 'money':10, 'quantity':1})
             for name, stamp in [('before', start-timedelta(microseconds=1)),
                                 ('start', start), ('inside', end-timedelta(microseconds=1)),
                                 ('end', end), ('after', end+timedelta(microseconds=1))]]
    with MarketStore(tmp_path / 'bounds.db') as store:
        ingest(store, facts)
        assert [r['id'] for r in store.iter_participant_window(start, end, batch_size=2)] == ['start', 'inside']
        default = load_participant_report(store)
        assert default['coverage']['market_transaction_count'] == 3
        historical = load_participant_report(store, as_of=NOW)
        assert historical['coverage']['market_transaction_count'] == 2


def test_explicit_fifo_read_model_consumes_older_dispositions(tmp_path, monkeypatch):
    import warera_quant.market_data as data
    from dataclasses import replace
    with MarketStore(tmp_path / 'fifo.db') as store:
        ingest(store, [fact('old-buy', -30, money='100', quantity='10'),
                       fact('old-sale', -20, buyer='V', seller='U', money='60', quantity='4'),
                       fact('sale', -1, buyer='V', seller='U', money='120', quantity='8')])
        original = data._participant_trade_input
        processed = []
        def observe(row):
            processed.append(row['id'])
            result = original(row)
            result['settlement'] = {side: {'verified': True, 'money_role':'gross', 'fee':'0'}
                                    for side in ('buy', 'sell')}
            return result
        monkeypatch.setattr(data, '_participant_trade_input', observe)
        report = load_participant_report(store, as_of=NOW, accounting_mode='full-fifo', batch_size=1)
        assert processed == ['old-buy', 'old-sale', 'sale']
        user = next(r for r in report['entities'] if r['entity_id'] == 'U')
        assert user['matched_quantity'] == 6 and user['uncosted_quantity'] == 2
        assert user['matched_net_pnl'] == 30
        assert user['source_turnover'] == 120
        assert report['coverage']['market_transaction_count'] == 1
        assert report['accounting_status'] == 'calculated'
        assert report['context']['accounting_mode'] == 'full-fifo'
        frozen = replace(store.resolve_report_context(NOW), accounting_mode='full-fifo')
        processed.clear()
        assert load_participant_report(store, context=frozen)['accounting_mode'] == 'full-fifo'
        assert processed == ['old-buy', 'old-sale', 'sale']
        processed.clear()
        assert load_participant_report(store, context=frozen, accounting_mode='window')['accounting_status'] == 'not_calculated'
        assert processed == ['sale']


@pytest.mark.parametrize('mode', ['window', 'full-fifo'])
def test_publication_context_restores_accounting_mode(tmp_path, mode):
    from dataclasses import replace
    from warera_quant.market_data import resolve_publication_context
    with MarketStore(tmp_path / 'context.db') as store:
        ingest(store, [fact('recent', -1)])
        context = replace(store.resolve_report_context(), accounting_mode=mode)
        restored = resolve_publication_context(store, {'context':context.to_dict()})
        assert restored == context
        assert load_participant_report(store, context=restored)['accounting_mode'] == mode
        assert resolve_publication_context(store, {'accounting_mode':mode}).accounting_mode == mode


@pytest.mark.parametrize("kind,target,extra", [
    ("user", "U", {}), ("mu", "M", {"buyerMuId": "M", "sellerMuId": "M"}),
    ("country", "C", {"buyerCountryId": "C", "sellerCountryId": "C"})])
@pytest.mark.parametrize("mode", ["window", "full-fifo"])
def test_targeted_matches_complete_entity_and_batches(tmp_path, monkeypatch, kind, target, extra, mode):
    from warera_quant.market_data import load_entity_activity
    import warera_quant.market_data as data
    import warera_quant.metrics as metrics
    with MarketStore(tmp_path / "target.db") as store:
        buy_extra = {k: v for k, v in extra.items() if k.startswith("buyer")}
        sell_extra = {k: v for k, v in extra.items() if k.startswith("seller")}
        ingest(store, [fact("old-buy", -30, money="100", quantity="10", **buy_extra),
            fact("old-sale", -20, buyer="V", seller="U", money="40", quantity="4", **sell_extra),
            fact("start", -7, money="0.000000000000000003", quantity="0.000000000000000001", **buy_extra),
            fact("sale", -1, buyer="V", seller="U", money="120", quantity="8", **sell_extra),
            fact("unrelated", -1, buyer="X", seller="Y"), fact("end", 0, **buy_extra)])
        missing = normalize_transaction({"_id": "missing", "createdAt": (NOW-timedelta(days=2)).isoformat(),
            "transactionType": "trading", "itemCode": "missing", "buyerId": "U", "sellerId": "V", **buy_extra})
        ingest(store, [missing])
        context = store.resolve_report_context(NOW)
        complete = load_participant_report(store, context=context, accounting_mode=mode)
        expected = next(r for r in complete["entities"] if (r["entity_kind"], r["entity_id"]) == (kind,target))
        processed = []
        original = data._participant_trade_input
        def observe(row):
            processed.append(row["id"])
            return original(row)
        monkeypatch.setattr(data, "_participant_trade_input", observe)
        monkeypatch.setattr(metrics, "calculate_participant_rankings", lambda *a, **k: pytest.fail("global ranking"))
        monkeypatch.setattr(store, "iter_participant_history", lambda *a, **k: pytest.fail("global history"))
        monkeypatch.setattr(store, "iter_participant_window", lambda *a, **k: pytest.fail("global window"))
        queries = []
        store._connect().set_trace_callback(queries.append)
        result = load_entity_activity(store, kind, target, context=context, accounting_mode=mode, batch_size=2)
        assert result["entities"] == [expected]
        assert result["rankings"] == {}
        assert result["context"] == complete["context"]
        assert processed == (["start", "missing", "sale"] if mode == "window" else ["old-buy", "old-sale", "start", "missing", "sale"])
        child_queries = [q for q in queries if "where transaction_id in (" in q]
        assert len(child_queries) == (8 if mode == "window" else 12)
        assert all("unrelated" not in q and "end" not in q for q in child_queries)
        assert expected["missing_money_count"] == 1


def test_targeted_reference_ownership_diagnostics_and_resolution(tmp_path, monkeypatch):
    from warera_quant.market_data import load_entity_activity, resolve_player_identity
    with MarketStore(tmp_path / "owners.db") as store:
        ingest(store, [fact("mu", -6, buyer="actor", buyerMuId="M", sellerCountryId="C"),
            fact("conflict", -5, buyer="actor", buyerMuId="M", buyerCountryId="C"),
            fact("self", -4, buyer="actor", seller="other", buyerMuId="M", sellerMuId="M"),
            fact("inactive", -30, buyer="uncached-inactive")])
        store.cache_entity_name("user", "named", "actor", NOW.isoformat(), "found")
        store.cache_entity_name("user", "one", "Shared", NOW.isoformat(), "found")
        store.cache_entity_name("user", "two", "SHARED", NOW.isoformat(), "found")
        assert resolve_player_identity(store, "actor")[0] == "actor"
        assert resolve_player_identity(store, "uncached-inactive")[0] == "uncached-inactive"
        assert resolve_player_identity(store, "shared")[0] is None
        assert len(resolve_player_identity(store, "shared")[1]) == 2
        assert resolve_player_identity(store, "missing") == (None, [])
        actor = load_entity_activity(store, "user", "actor", as_of=NOW)
        assert actor["entities"] == []
        assert actor["coverage"]["unassigned_count"] == 1
        assert actor["coverage"]["self_trade_count"] == 1
        mu = load_entity_activity(store, "mu", "M", as_of=NOW)
        assert mu["entities"][0]["actor_ids"] == ["actor"]
        assert mu["entities"][0]["source_turnover"] == 10
        assert load_entity_activity(store, "user", "uncached-inactive", as_of=NOW)["entities"] == []


@pytest.mark.parametrize("legacy", [False, True])
def test_targeted_precision_and_frozen_cutoffs(tmp_path, legacy):
    from warera_quant.market_data import load_entity_activity
    with MarketStore(tmp_path / "precision.db") as store:
        start = NOW - timedelta(days=7)
        points = [("before", start-timedelta(microseconds=1)), ("start", start),
                  ("inside", NOW-timedelta(microseconds=1)), ("end", NOW)]
        ingest(store, [normalize_transaction({"_id": id, "transactionType": "trading",
            "createdAt": at.isoformat(), "itemCode": "exact", "buyerId": "U", "sellerId": "V",
            "money": Decimal("0.000000000000000003"), "quantity": Decimal("0.000000000000000001")})
            for id, at in points])
        if legacy:
            # Isolated legacy fixture only; retain source timestamp precision.
            store._connect().execute("update transactions set created_at_us=null")
        for request, count in [(None, 3), (NOW, 2), (NOW+timedelta(days=10), 3)]:
            context = store.resolve_report_context(request)
            targeted = load_entity_activity(store, "user", "U", context=context)
            complete = load_participant_report(store, context=context)
            assert targeted["context"] == complete["context"]
            assert targeted["entities"] == [next(r for r in complete["entities"] if r["entity_id"] == "U")]
            assert targeted["entities"][0]["source_turnover"] == Decimal("0.000000000000000003")*count
            assert targeted["entities"][0]["item_categories"][0]["buy_avg_price"] == 3


def test_targeted_growth_avoids_unrelated_materialization(tmp_path, monkeypatch):
    from warera_quant.market_data import load_entity_activity
    import warera_quant.market_data as data
    with MarketStore(tmp_path / "growth.db") as store:
        ingest(store, [fact("target", -1)])
        context = store.resolve_report_context()
        before = load_entity_activity(store, "user", "U", context=context)
        ingest(store, [fact(f"old-{i}", -30) for i in range(1000)] +
                     [fact(f"other-{i}", -1, buyer="X", seller="Y") for i in range(1000)])
        rows, queries = [], []
        original = data._participant_trade_input
        def observe(row):
            rows.append(row["id"])
            return original(row)
        monkeypatch.setattr(data, "_participant_trade_input", observe)
        store._connect().set_trace_callback(queries.append)
        after = load_entity_activity(store, "user", "U", context=context)
        assert after["entities"] == before["entities"]
        assert rows == ["target"]
        assert len([q for q in queries if "where transaction_id in (" in q]) == 4
        query, params = store.entity_activity_query("user", "U", context.window_start(), context.window_end_exclusive)
        plan = [r[3] for r in store._connect().execute("explain query plan " + query, params)]
        assert any("SEARCH" in step and "transactions" in step for step in plan)


@pytest.mark.parametrize("kwargs", [{"entity_kind": "party"}, {"entity_id": " "},
    {"accounting_mode": "invalid"}, {"batch_size": 0}, {"batch_size": 501}])
def test_targeted_invalid_contracts_on_empty_database(tmp_path, kwargs):
    from warera_quant.market_data import load_entity_activity
    with MarketStore(tmp_path / "empty.db") as store:
        options = {"entity_kind": "user", "entity_id": "U", **kwargs}
        with pytest.raises(ValueError):
            load_entity_activity(store, **options)


def test_targeted_full_fifo_replays_only_target_acquisitions_and_dispositions(tmp_path, monkeypatch):
    from warera_quant.market_data import load_entity_activity
    with MarketStore(tmp_path / "fifo.db") as store:
        ingest(store, [fact("buy", -30, quantity="10", money="100"),
            fact("sale-old", -20, buyer="V", seller="U", quantity="4", money="40"),
            fact("sale", -1, buyer="V", seller="U", quantity="8", money="120"),
            fact("other", -1, buyer="X", seller="Y")])
        import warera_quant.market_data as data
        original = data._participant_trade_input
        def verified_fixture(row):
            result = original(row)
            result["settlement"] = {side: {"verified": True, "money_role": "gross", "fee": "0"}
                                    for side in ("buy", "sell")}
            return result
        monkeypatch.setattr(data, "_participant_trade_input", verified_fixture)
        report = load_entity_activity(store, "user", "U", as_of=NOW, accounting_mode="full-fifo")
        row = report["entities"][0]
        assert row["matched_quantity"] == 6
        assert row["uncosted_quantity"] == 2
        assert row["matched_net_pnl"] == 30
        assert row["source_turnover"] == 120
        assert report["accounting_status"] == "calculated"
