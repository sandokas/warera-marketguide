"""Participant rendering uses supplied domain results, never network/storage."""
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import pytest

from warera_quant.metrics import calculate_participant_rankings
from warera_quant.report import generate_html_report, write_outputs

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


def participant_fixture():
    trades = []
    for kind in ('user', 'mu', 'country'):
        for i, proceeds in enumerate((15, 7, 15)):
            owner = {kind + '_id': str(i)}
            for suffix, day, money, buy, sell in [('buy', -9, 10, owner, {}), ('sell', -2, proceeds, {}, owner)]:
                trades.append({'id':kind + str(i) + suffix, 'created_at':NOW + timedelta(days=day),
                    'transaction_type':'trading', 'item_code':'steel', 'money':str(money), 'quantity':'1',
                    'participants':{'buy':buy, 'sell':sell},
                    'settlement':{side:{'verified':True, 'money_role':'gross', 'fee':'0'} for side in ('buy','sell')}})
    trades.append({'id':'equipment', 'created_at':NOW - timedelta(days=1), 'transaction_type':'itemMarket',
        'item_code':'helmet', 'money':'99.12345678901234567890123456789', 'quantity':'1',
        'participants':{'buy':{'user_id':'0'}, 'sell':{}},
        'equipment':{'equipment_code':'helmet', 'state':'0', 'max_state':'100',
            'stats':{f'skill-{i:02}':'1.123456789' for i in range(30)}}})
    report = calculate_participant_rankings(sorted(trades, key=lambda t:(t['created_at'],t['id'])), as_of=NOW)
    for row in report['entities'] + [r for b in report['rankings'].values() for rows in b.values() for r in rows]:
        if row['entity_id'] == '0':
            row['name'] = '=External <script> & extremely long participant name with several words'
    return report


def frame():
    return pd.DataFrame([{'item_code':'steel', 'item_name':'Steel', 'last_trade_price':10}])


@pytest.mark.parametrize('kind', ['user', 'mu', 'country'])
def test_shared_turnover_and_complete_breakdown(kind):
    import re
    report = participant_fixture()
    html = generate_html_report(frame(), participant_report=report, as_of=NOW)
    tables = re.findall(r'<table[^>]*data-table-id="participants-' + kind + r'-.*?</table>', html, re.S)
    assert len(tables) == 2
    for table in tables:
        assert '<tfoot>' not in table
        assert all(text not in table for text in ('Coverage', 'P&L', 'Result', 'condition', 'Other categories', 'Observed:'))
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert '/'.join(['1.123456789'] * 30) in html
    assert 'stats:' not in html and 'Participant activity:' not in html
    assert '100.00%' in html
    assert report['rankings'][kind]['profits']  # diagnostics preserved
    assert '2026-09-16T00:00:00+00:00' not in html


def test_empty_and_legacy_are_not_zero_results():
    report = calculate_participant_rankings([], as_of=NOW)
    html = generate_html_report(frame(), participant_report=report)
    assert html.count('No qualifying observed activity.') == 6
    assert 'participants-' not in generate_html_report(frame())


@pytest.mark.parametrize('kind', ['user', 'mu', 'country'])
@pytest.mark.parametrize('side', ['buy', 'sell'])
def test_all_categories_missingness_and_top_ten(kind, side):
    from warera_quant.report import _participant_html
    trades = []
    def add(item, money, quantity, owner='winner', equipment=None):
        trades.append(dict(id=str(len(trades)), created_at=NOW-timedelta(days=1),
            transaction_type='itemMarket' if equipment else 'trading', item_code=item,
            money=money, quantity=quantity, participants={side:{kind+'_id':owner}}, equipment=equipment))
    for i in range(6):
        add('commodity'+str(i), str(100+i), '2')
    add('commodity0', '5', '3')
    add('commodity1', None, None)
    add('unknown', None, None)
    for state in ('90','100'):
        add('boots4', '10', '1', equipment={'equipment_code':'boots4','state':state,'max_state':'100','stats':{'attack':'3'}})
    for i in range(12):
        add('outsider'+str(i), str(i+1), '1', owner='actor'+str(i))
    report = calculate_participant_rankings(sorted(trades, key=lambda t:(t['created_at'],t['id'])), as_of=NOW)
    html = _participant_html(report)
    detail = html.split(f'data-table-id="participants-{kind}-explanations"')[1].split('</table>')[0]
    winner = next(r for r in report['entities'] if r['entity_id']=='winner')
    for c in winner['categories'][side]:
        assert c['item_code'] in detail
    assert detail.count('<td>commodity0</td>') == 1
    assert detail.count('<td>boots4 3</td>') == 2
    assert '<td>5</td>' in detail
    assert '101 known (1 missing)' in detail and '2 known (1 missing)' in detail
    assert '<td>Unknown</td>' in detail
    assert 'condition' not in html
    assert len(report['rankings'][kind]['volume']) == 10
    assert '<td>outsider0</td>' not in html and '<td>outsider1</td>' not in html
    assert all(x not in html for x in ('Other categories', 'Remaining items', 'Mixed items'))


def test_activity_footer_and_flip_board_regression():
    from warera_quant.report import _flip_board_html
    html = generate_html_report(frame())
    activity = html.split('data-report-table="activity-comparison"')[1].split('</table>')[0]
    assert 'Observed:' not in activity and '<tfoot>' not in activity
    flip = _flip_board_html([{'Item':'Steel','Verdict':'watch','Evidence':'limited'}], show_entry=True,
        show_forecast=True, show_net=True, suppress_execution_reason=False)
    assert 'Steel' in flip and 'participants-' not in flip


def test_precise_exports_full_stats_formula_protection_and_source_unchanged(tmp_path):
    report = participant_fixture()
    equipment = {'id':'=sale', 'as_of':NOW, 'money':'0.12345678901234567890123456789',
        'participants':{'buy':{'user_id':'@actor'}}, 'presence':{'equipment.skills':False},
        'equipment':{'state':'0','max_state':'100', 'stats':{f'=stat{i}':str(i) for i in range(50)}}}
    write_outputs(frame(), tmp_path, participant_report=report, equipment_details=iter([equipment]), as_of=NOW)
    def read(name):
        with (tmp_path / name).open(encoding='utf-8', newline='') as f:
            return list(csv.DictReader(f))
    rankings = read('participant_rankings_7d.csv')
    assert {r['entity_kind'] for r in rankings} == {'user','mu','country'}
    assert any(r['name'].startswith("'=External") for r in rankings)
    assert all(r['as_of'] == NOW.isoformat() for r in rankings)
    stats = read('equipment_sale_stats_7d.csv')
    assert len(stats) == 50 and stats[-1]['skill_code'] == "'=stat49"
    sale = read('equipment_sales_7d.csv')[0]
    assert sale['money'] == equipment['money']
    assert sale['participants_buy_user_id'] == "'@actor"
    assert len(read('participant_trade_stats_7d.csv')) == 30
    category = next(r for r in read('participant_trade_breakdown_7d.csv') if r['item_code'] == 'helmet')
    assert category['state'] == '0' and category['max_state'] == '100'
    assert 'condition 0/100' in category['description']
    assert equipment['id'] == '=sale'
    assert report['rankings']['user']['volume'][0]['name'].startswith('=External')


def test_empty_csvs_have_headers(tmp_path):
    write_outputs(frame(), tmp_path, participant_report=calculate_participant_rankings([], as_of=NOW), equipment_details=[])
    for name in ('participant_rankings_7d','participant_trade_breakdown_7d','equipment_sales_7d','equipment_sale_stats_7d'):
        assert len((tmp_path / (name + '.csv')).read_text().splitlines()) == 1
