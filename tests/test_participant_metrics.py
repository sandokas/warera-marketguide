from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from warera_quant.metrics import calculate_participant_rankings, resolve_market_account, equipment_category

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


def trade(id, day, money, quantity, buyer='U', seller='V', *, code='steel', verified=True,
          equipment=None, lineage=False, buy_fee='0', sell_fee='0', money_role='gross'):
    row = {'id': id, 'created_at': NOW + timedelta(days=day), 'item_code': code,
           'transaction_type': 'itemMarket' if equipment is not None else 'trading',
           'money': None if money is None else str(money),
           'quantity': None if quantity is None else str(quantity),
           'participants': {'buy': {'user_id': buyer} if isinstance(buyer, str) else buyer or {},
                            'sell': {'user_id': seller} if isinstance(seller, str) else seller or {}},
           'equipment': equipment, 'lineage_verified': lineage}
    if verified:
        row['settlement'] = {side: {'verified': True, 'money_role': money_role, 'fee': fee}
                             for side, fee in [('buy', buy_fee), ('sell', sell_fee)]}
    return row


def calculate(rows, **kwargs):
    return calculate_participant_rankings(sorted(rows, key=lambda t: (t['created_at'], t['id'])), as_of=NOW, **kwargs)


def entity(result, id='U', kind='user'):
    return next(row for row in result['entities'] if row['entity_id'] == id and row['entity_kind'] == kind)


def test_plan_fifo_prior_dispositions_and_partial_unknown_sale():
    rows = [trade('a', -10, 100, 10), trade('b', -9, 60, 4, 'V', 'U'),
            trade('c', -6, 24, 2), trade('d', -5, 120, 8, 'V', 'U')]
    report = calculate(rows)
    row = entity(report)
    assert row['matched_net_pnl'] == D(36)
    assert row['gross_turnover'] == D(144)
    assert row['matched_quantity'] == 8
    assert row['matched_sale_value_coverage'] == 1
    assert report['rankings']['user']['profits'][0]['entity_id'] == 'U'
    report = calculate(rows + [trade('e', -4, 30, 2, 'V', 'U')])
    row = entity(report)
    assert row['matched_net_pnl'] == 36
    assert row['uncosted_quantity'] == 2
    assert row['uncosted_source_sale_value'] == 30
    assert row['matched_sale_value_coverage'] == D('0.8')
    assert row['result_status'] == 'partial'


def test_unsold_purchases_are_not_losses_and_future_never_fills_old_sale():
    report = calculate([trade('a', -4, 100, 2, 'V', 'U'), trade('b', -3, 1000, 10)])
    row = entity(report)
    assert row['matched_net_pnl'] is None
    assert row['uncosted_quantity'] == 2
    assert row['source_turnover'] == 1100
    assert not report['rankings']['user']['losses']


def test_multiple_fractional_lots_exact_allocation_and_known_zero():
    report = calculate([trade('a', -10, '1', 3), trade('b', -9, '0', 2),
                        trade('c', -6, '1', 1, 'V', 'U'),
                        trade('d', -5, '2', 2, 'V', 'U'),
                        trade('e', -4, '4', 2, 'V', 'U')])
    row = entity(report)
    assert row['matched_net_pnl'] == 6  # (1+2+4) - (1+0), no per-lot rounding
    assert row['matched_quantity'] == 5
    report = calculate([trade('a', -9, None, 1), trade('b', -6, 10, 1, 'V', 'U')])
    assert entity(report)['matched_net_pnl'] is None
    assert entity(report)['uncosted_quantity'] == 1


@pytest.mark.parametrize('references,account,reason', [
    ({'user_id': 'u'}, ('user', 'u'), None),
    ({'user_id': 'actor', 'mu_id': 'm'}, ('mu', 'm'), None),
    ({'user_id': 'actor', 'country_id': 'c'}, ('country', 'c'), None),
    ({'user_id': 'actor', 'country_id': 'c', 'mu_id': 'm'}, None, 'conflicting_institutions'),
    ({'user_id': 'actor', 'mu_id': ''}, None, 'invalid_reference'),
    ({'party_id': 'p'}, None, 'unsupported_party'),
    ({}, None, 'missing_reference'),
])
def test_account_resolution(references, account, reason):
    resolved = resolve_market_account(references)
    assert resolved['account'] == account
    assert resolved['reason'] == reason
    assert resolved['actor_id'] == references.get('user_id')


def test_institutional_ownership_mixed_sides_unassigned_and_self_trade():
    mu = {'mu_id': 'M', 'user_id': 'actor'}
    country = {'country_id': 'C', 'user_id': 'actor'}
    report = calculate([trade('a', -10, 10, 1, mu), trade('b', -6, 20, 1, country, mu),
        trade('c', -5, 999, 1, country, {'country_id': 'C', 'user_id': 'different_actor'}),
        trade('d', -4, 5, 1, {'country_id': 'C', 'mu_id': 'M', 'user_id': 'actor'}, 'U')])
    assert entity(report, 'M', 'mu')['matched_net_pnl'] == 10
    assert entity(report, 'M', 'mu')['actor_ids'] == ['actor']
    assert entity(report, 'C', 'country')['source_turnover'] == 20
    assert not any(row['entity_id'] == 'actor' for row in report['entities'])
    assert report['coverage']['unassigned_count'] == 1
    assert report['coverage']['unassigned_value'] == 5
    assert report['coverage']['self_trade_value'] == 999
    assert entity(report)['source_sell_value'] == 5


def test_self_trade_does_not_churn_inventory():
    report = calculate([trade('a', -10, 10, 1), trade('b', -8, 900, 1, 'U', 'U'),
                        trade('c', -6, 20, 1, 'V', 'U')])
    assert entity(report)['matched_net_pnl'] == 10


def test_verified_fees_and_already_included_money_are_not_double_charged():
    rows = [trade('a', -10, 100, 10, buy_fee='5'),
            trade('b', -6, 150, 10, 'V', 'U', sell_fee='3')]
    assert entity(calculate(rows))['matched_net_pnl'] == 42
    rows = [trade('a', -10, 105, 10, buy_fee='5', money_role='settled'),
            trade('b', -6, 147, 10, 'V', 'U', sell_fee='3', money_role='settled')]
    row = entity(calculate(rows))
    assert row['matched_net_pnl'] == 42
    assert row['matched_gross_pnl'] == 50
    assert row['top_sell'][0]['money'] == 150


def test_unknown_fee_is_not_zero_but_gross_result_can_be_known():
    report = calculate([trade('a', -10, 100, 10, buy_fee=None),
                        trade('b', -6, 150, 10, 'V', 'U')])
    row = entity(report)
    assert row['matched_gross_pnl'] == 50
    assert row['matched_net_pnl'] is None
    assert row['matched_quantity'] == 10
    assert row['unknown_fee_source_sale_value'] == 150
    assert not report['rankings']['user']['profits']


def test_unverified_source_money_cannot_be_renamed_gross_or_net():
    report = calculate([trade('a', -10, 100, 10, verified=False),
                        trade('b', -6, 150, 10, 'V', 'U', verified=False)])
    row = entity(report)
    assert report['turnover_basis'] == 'source-money'
    assert row['source_turnover'] == 150
    assert row['gross_turnover'] is None
    assert row['matched_gross_pnl'] is None
    assert row['matched_net_pnl'] is None
    assert row['matched_sale_value_coverage'] is None


def equipment(id='piece', state='10', stats=None):
    return {'instance_id': id, 'equipment_code': 'rare-new-code', 'state': state,
            'max_state': '20', 'stats': {'attack': '4', 'armor': '2'} if stats is None else stats}


@pytest.mark.parametrize('same_id,lineage,fees,expected', [
    (True, True, True, D(20)), (True, False, True, None),
    (False, True, True, None), (True, True, False, None)])
def test_equipment_requires_verified_specific_lineage_and_fees(same_id, lineage, fees, expected):
    report = calculate([trade('a', -10, 100, 1, equipment=equipment(), lineage=lineage, verified=fees),
        trade('b', -6, 120, 1, 'V', 'U', equipment=equipment('piece' if same_id else 'other', '5'),
              lineage=lineage, verified=fees)])
    row = entity(report)
    assert row['matched_net_pnl'] == expected
    assert row['source_turnover'] == 120
    assert row['top_sell'][0]['category'][-2:] == ('5', '20')


def test_equipment_disposition_invalidates_previous_owner_and_stats_not_identity():
    report = calculate([trade('a', -10, 100, 1, equipment=equipment(), lineage=True),
        trade('b', -9, 110, 1, 'V', 'U', equipment=equipment(), lineage=True),
        trade('c', -6, 120, 1, 'X', 'U', equipment=equipment(), lineage=True)])
    assert entity(report)['matched_net_pnl'] is None
    assert equipment_category({'equipment': equipment(stats={'z': '0', 'a': '1'})})[2] == (('a', '1'), ('z', '0'))
    assert equipment_category({'equipment': {'stats': None}}) != equipment_category({'equipment': {'stats': {'z': '0'}}})


def test_rankings_independent_no_volume_prefilter_ties_and_zero_distinct():
    rows = [trade('a', -10, 100, 1, 'loss'), trade('b', -6, 1, 1, 'V', 'loss')]
    for i in range(12):
        rows.append(trade(f'volume{i}', -5, 1000, 1, f'volume{i}', 'supplier'))
    for name in ['profitB', 'profitA']:
        rows += [trade(name + 'a', -9, 1, 1, name), trade(name + 'b', -4, 2, 1, 'V', name)]
    rows += [trade('zeroa', -9, 0, 1, 'zero'), trade('zerob', -4, 0, 1, 'V', 'zero')]
    report = calculate(rows)
    boards = report['rankings']['user']
    assert len(boards['volume']) == 10
    assert boards['losses'][0]['entity_id'] == 'loss'
    assert [r['entity_id'] for r in boards['profits']] == ['profitA', 'profitB']
    assert entity(report, 'zero')['matched_net_pnl'] == 0
    assert entity(report, 'volume0')['matched_net_pnl'] is None
    assert set(report['rankings']) == {'user', 'mu', 'country'}
    assert all(len(boards) == 3 for boards in report['rankings'].values())


def test_top_three_categories_percentages_other_and_unseen_codes():
    report = calculate([trade(str(i), -6 + i / 10, amount, i + 1, code='unseen' + str(i))
                        for i, amount in enumerate([40, 30, 20, 10])])
    row = entity(report)
    assert [c['money'] for c in row['top_buy']] == [40, 30, 20]
    assert [c['share'] for c in row['top_buy']] == [D('.4'), D('.3'), D('.2')]
    assert row['other_buy']['money'] == 10
    assert row['other_buy']['share'] == D('.1')
    assert row['top_buy'][0]['quantity'] == 1


def test_utc_boundaries_equal_time_and_input_order():
    first = trade('a', -7, 10, 1)
    first['created_at'] = first['created_at'].astimezone(timezone(timedelta(hours=3)))
    rows = [first, trade('b', -7, 20, 1, 'V', 'U'), trade('c', 0, 999, 1)]
    row = entity(calculate(rows))
    assert row['source_turnover'] == 30
    assert row['matched_net_pnl'] is None
    assert row['equal_timestamp_order'] is True
    with pytest.raises(ValueError, match='ordered'):
        calculate_participant_rankings(list(reversed(rows)), as_of=NOW)
    with pytest.raises(ValueError, match='timezone-aware'):
        calculate_participant_rankings([], as_of=NOW.replace(tzinfo=None))


def test_equal_time_acquisition_order_is_disclosed_on_later_sale():
    row = entity(calculate([trade('a', -10, 10, 1), trade('b', -10, 20, 1),
                            trade('c', -6, 30, 1, 'V', 'U')]))
    assert row['matched_net_pnl'] == 20
    assert row['equal_timestamp_order'] is True
    assert row['result_status'] == 'partial'


def test_unknown_prior_sale_quantity_invalidates_remaining_lots():
    report = calculate([trade('a', -10, 100, 10), trade('b', -9, 20, None, 'V', 'U'),
                        trade('c', -6, 100, 10, 'V', 'U')])
    assert entity(report)['matched_net_pnl'] is None


def test_source_gaps_are_separate_from_matched_basis():
    sources = {'streams': {stream: {'progress': {'status': 'complete'}, 'latest_scan_exhausted': True,
        'coverage': [{'start_at': (NOW - timedelta(days=7)).isoformat(),
                      'end_at': (NOW - timedelta(days=4)).isoformat()},
                     {'start_at': (NOW - timedelta(days=3)).isoformat(), 'end_at': NOW.isoformat()}]}
        for stream in ['trading', 'itemMarket']}}
    report = calculate([trade('a', -10, 10, 1), trade('b', -6, 20, 1, 'V', 'U')], sources=sources)
    assert entity(report)['matched_net_pnl'] == 10
    assert entity(report)['matched_sale_value_coverage'] == 1
    assert entity(report)['result_status'] == 'partial'
    assert report['source_coverage']['streams']['trading']['window_covered'] is False
    assert report['source_coverage']['streams']['trading']['history_exhaustion_observed'] is True


def test_partial_lot_basis_and_fee_coverage_are_independent():
    rows = [trade('a', -10, 10, 1), trade('b', -9, 20, 1, buy_fee=None),
            trade('c', -8, None, 1), trade('d', -6, 120, 4, 'V', 'U')]
    row = entity(calculate(rows))
    assert row['matched_net_pnl'] == 20  # only first unit: 30 - 10
    assert row['matched_gross_pnl'] == 30  # first two: 60 - 30
    assert row['matched_quantity'] == 2
    assert row['net_matched_quantity'] == 1
    assert row['uncosted_quantity'] == 2
    assert row['uncosted_source_sale_value'] == 60
    # Second lot has unknown fees; fourth has no acquisition evidence. The
    # third has unknown money but verified fees, so isn't an unknown-fee lot.
    assert row['unknown_fee_source_sale_value'] == 60
    assert row['matched_sale_value_coverage'] == D('.5')
    assert row['result_status'] == 'partial'


def test_unknown_size_acquisition_cannot_be_skipped_in_fifo():
    rows = [trade('a', -12, 10, 1), trade('b', -11, 50, None), trade('c', -10, 100, 1),
            trade('d', -6, 90, 3, 'V', 'U'), trade('e', -5, 40, 1),
            trade('f', -4, 50, 1, 'V', 'U')]
    row = entity(calculate(rows))
    assert row['matched_net_pnl'] == 20
    assert row['uncosted_quantity'] == 3


def test_short_sale_does_not_consume_a_future_purchase():
    report = calculate([trade('a', -10, 90, 1, 'V', 'U'), trade('b', -9, 20, 1),
                        trade('c', -6, 30, 1, 'V', 'U')])
    assert entity(report)['matched_net_pnl'] == 10


def test_institutional_profit_loss_and_volume_boards_are_independent():
    rows = []
    for kind in ('mu', 'country'):
        winner = {kind + '_id': kind + 'winner', 'user_id': 'actor'}
        loser = {kind + '_id': kind + 'loser', 'user_id': 'actor'}
        rows += [trade(kind + 'a', -10, 10, 1, winner), trade(kind + 'b', -9, 30, 1, loser),
                 trade(kind + 'c', -6, 20, 1, 'customer', winner),
                 trade(kind + 'd', -5, 20, 1, 'customer', loser)]
    report = calculate(rows)
    for kind in ('mu', 'country'):
        assert report['rankings'][kind]['profits'][0]['matched_net_pnl'] == 10
        assert report['rankings'][kind]['losses'][0]['matched_net_pnl'] == -10
        assert [r['entity_id'] for r in report['rankings'][kind]['volume']] == [kind + 'loser', kind + 'winner']
