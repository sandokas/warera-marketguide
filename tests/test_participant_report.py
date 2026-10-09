"""Participant rendering uses supplied domain results, never network/storage."""
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import pytest

from warera_quant.metrics import calculate_participant_rankings
from warera_quant.report import format_player_summary, generate_html_report, write_outputs

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
    report = calculate_participant_rankings(sorted(trades, key=lambda t:(t['created_at'],t['id'])), as_of=NOW, accounting_mode="full-fifo")
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
    for entity_id, profit in (('0', Decimal('5')), ('1', Decimal('-3'))):
        participant = next(row for row in report['rankings'][kind]['volume']
                           if row['entity_id'] == entity_id)
        participant['top_items'][0]['profit_loss_btc'] = profit
    html = generate_html_report(frame(), participant_report=report, as_of=NOW)
    tables = re.findall(r'<table[^>]*data-table-id="participants-' + kind + r'-.*?</table>', html, re.S)
    assert len(tables) == 2
    turnover_table = next(table for table in tables if 'participants-' + kind + '-volume' in table)
    assert re.search(r'<td class="col-7d-turnover-btc number">[\d,]+\.\d{3}</td>', turnover_table)
    for table in tables:
        assert '<tfoot>' not in table
        assert all(text not in table for text in ('Coverage', 'P&L', 'Result', 'condition', 'Other categories', 'Observed:'))
    assert '<script>' not in html and '&lt;script&gt;' in html
    # Check that equipment stats use icon format instead of slash-separated
    assert '⚔️' in html or '•' in html  # Equipment stats now use icons
    assert 'stats:' not in html and 'Participant activity:' not in html
    # Check that new item-based breakdown columns are present
    assert 'Buy Qty' in html
    assert 'Buy Avg' in html
    assert 'Buy Total BTC' in html
    assert 'Sell Qty' in html
    assert 'Sell Avg' in html
    assert 'Sell Total BTC' in html
    assert 'Window Avg Difference/Unit' in html
    assert 'Window Comparison BTC' in html
    assert 'Window Net Buy Qty' in html
    assert '99.123456789' not in html
    assert '>99.123</td>' in html
    assert '>15.000</td>' in html
    assert re.search(r'<td class="[^"]*profit-positive">5\.000</td>', html)
    assert re.search(r'<td class="[^"]*profit-negative">-3\.000</td>', html)
    assert re.search(r'<td class="col-window-net-buy-qty number">', html)
    assert not re.search(r'<td class="[^"]*net-(?:positive|negative)', html)
    assert report['rankings'][kind]['profits']  # diagnostics preserved
    assert '2026-09-16T00:00:00+00:00' not in html


def test_empty_and_legacy_are_not_zero_results():
    report = calculate_participant_rankings([], as_of=NOW, accounting_mode="full-fifo")
    html = generate_html_report(frame(), participant_report=report)
    assert html.count('No qualifying observed activity.') == 6
    assert 'participants-' not in generate_html_report(frame())


def test_console_summary_combines_buy_and_sell_only_for_exact_equipment_signature():
    def equipment(attack):
        return {"equipment_code": "helmet4", "state": "100", "max_state": "100",
                "stats": {"attack": attack, "armor": "2"}}

    trades = [
        {"id": "buy-matched", "created_at": NOW - timedelta(days=3),
         "transaction_type": "itemMarket", "item_code": "helmet4", "money": "50", "quantity": "1",
         "participants": {"buy": {"user_id": "player"}, "sell": {}}, "equipment": equipment("3")},
        {"id": "sell-matched", "created_at": NOW - timedelta(days=2),
         "transaction_type": "itemMarket", "item_code": "helmet4", "money": "60", "quantity": "1",
         "participants": {"buy": {}, "sell": {"user_id": "player"}}, "equipment": equipment("3")},
        {"id": "buy-other-stats", "created_at": NOW - timedelta(days=1),
         "transaction_type": "itemMarket", "item_code": "helmet4", "money": "70", "quantity": "1",
         "participants": {"buy": {"user_id": "player"}, "sell": {}}, "equipment": equipment("4")},
    ]
    report = calculate_participant_rankings(trades, as_of=NOW, accounting_mode="full-fifo")
    player = next(row for row in report["entities"] if row["entity_id"] == "player")

    assert len(player["item_categories"]) == 2
    matched = next(item for item in player["item_categories"] if ("attack", "3") in item["category"][2])
    assert matched["buy_quantity"] == matched["sell_quantity"] == 1
    console = format_player_summary(report, player)
    assert "helmet4; armor=2, attack=3" in console
    assert "helmet4; armor=2, attack=4" in console
    assert "stats:" not in console
    assert "condition" not in console


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
    report = calculate_participant_rankings(sorted(trades, key=lambda t:(t['created_at'],t['id'])), as_of=NOW, accounting_mode="full-fifo")
    html = _participant_html(report)
    detail = html.split(f'data-table-id="participants-{kind}-explanations"')[1].split('</table>')[0]
    winner = next(r for r in report['entities'] if r['entity_id']=='winner')
    # Check that top items are shown in the new item-based breakdown table
    for item in winner.get('top_items', []):
        assert item['item_code'] in detail
    # Check that new columns exist
    assert 'Buy Qty' in detail
    assert 'Buy Avg' in detail
    assert 'Buy Total BTC' in detail
    assert 'Sell Qty' in detail
    assert 'Sell Avg' in detail
    assert 'Sell Total BTC' in detail
    assert 'Window Avg Difference/Unit' in detail
    assert 'Window Comparison BTC' in detail
    assert 'Window Net Buy' in detail
    assert 'commodity0' in detail
    # Missing data should still be handled properly
    assert 'condition' not in html
    assert len(report['rankings'][kind]['volume']) == 10
    # Compact leaders retain their own selected detail prefix.
    actor4 = next(r for r in report['entities'] if r['entity_id']=='actor4')
    assert len(actor4.get('top_items', [])) == 1
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
    # Check new item-based breakdown CSV
    item_breakdown = read('participant_item_breakdown_7d.csv')
    assert len(item_breakdown) > 0
    helmet_item = next(r for r in item_breakdown if r['item_code'] == 'helmet')
    assert helmet_item['state'] == '0' and helmet_item['max_state'] == '100'
    assert 'condition 0/100' in helmet_item['description']
    # Check that profit/loss fields exist
    assert 'window_average_difference_per_unit' in helmet_item
    assert 'window_comparison_value' in helmet_item
    assert 'buy_quantity' in helmet_item
    assert 'sell_quantity' in helmet_item
    # Check that net_unmatched replaced individual unmatched fields
    assert 'window_net_buy_quantity' in helmet_item
    assert 'unmatched_buy_quantity' not in helmet_item
    assert 'unmatched_sell_quantity' not in helmet_item
    # Legacy breakdown should still exist for compatibility
    category = next(r for r in read('participant_trade_breakdown_7d.csv') if r['item_code'] == 'helmet')
    assert category['state'] == '0' and category['max_state'] == '100'
    assert 'condition 0/100' in category['description']
    assert equipment['id'] == '=sale'
    assert report['rankings']['user']['volume'][0]['name'].startswith('=External')


def test_empty_csvs_have_headers(tmp_path):
    write_outputs(frame(), tmp_path, participant_report=calculate_participant_rankings([], as_of=NOW), equipment_details=[])
    for name in ('participant_rankings_7d','participant_item_breakdown_7d','participant_trade_breakdown_7d','equipment_sales_7d','equipment_sale_stats_7d'):
        assert len((tmp_path / (name + '.csv')).read_text().splitlines()) == 1


@pytest.mark.parametrize("kind", ["user", "mu", "country"])
def test_selected_html_target_console_and_complete_csvs(tmp_path, kind):
    from warera_quant.metrics import calculate_entity_activity
    from warera_quant.report import _participant_html, _write_participant_exports
    trades = [{"id": f"item-{i:03}", "created_at": NOW-timedelta(days=1), "transaction_type": "trading",
        "item_code": f"category-{i:03}", "money": "0.000000000000000003", "quantity": "1",
        "participants": {"buy": {kind+"_id": "target"}}} for i in range(100)]
    report = calculate_participant_rankings(trades, as_of=NOW)
    row = report["entities"][0]
    assert len(row["top_items"]) == 80 and len(row["item_categories"]) == 100
    detail = _participant_html(report).split(f'data-table-id="participants-{kind}-explanations"')[1].split('</table>')[0]
    assert detail.count('<tr>') == 81  # header plus 80 details; no ten-row cap
    assert "category-079" in detail and "category-080" not in detail
    target = calculate_entity_activity(trades, entity_kind=kind, entity_id="target", as_of=NOW)
    console = format_player_summary(target, target["entities"][0])
    # CLI output always shows all items, not the filtered selection
    assert "100 categories" in console
    assert "category-079" in console and "category-080" in console
    _write_participant_exports(tmp_path, report, [])
    def read(name):
        with (tmp_path/name).open(encoding='utf-8', newline='') as file:
            return list(csv.DictReader(file))
    for name in ("participant_item_breakdown_7d.csv", "participant_trade_breakdown_7d.csv"):
        rows = read(name)
        assert len(rows) == 100
        assert {item["item_code"] for item in rows} == {f"category-{i:03}" for i in range(100)}
    assert all(Decimal(item["buy_total_value"]) == Decimal("0.000000000000000003")
               for item in read("participant_item_breakdown_7d.csv"))
    ranking = read("participant_rankings_7d.csv")[0]
    assert ranking["detail_selection_selected_count"] == "80"
    assert ranking["detail_selection_status"] == "complete"
    assert row["detail_selection"]["selected_count"] == 80


def test_partial_detail_console_context_and_csv_retention(tmp_path):
    from warera_quant.report import _participant_html, _write_participant_exports
    trades = [{"id": str(i), "created_at": NOW-timedelta(days=1), "transaction_type": "trading",
        "item_code": f"category-{i:02}", "money": (None if i == 12 else "100" if i == 0 else "1"),
        "quantity": "1", "participants": {"buy": {"user_id": "target"}}} for i in range(13)]
    trades.sort(key=lambda row: (row["created_at"],row["id"]))
    report = calculate_participant_rankings(trades, as_of=NOW)
    row = report["entities"][0]
    assert len(row["top_items"]) == 13
    console = format_player_summary(report, row)
    # CLI output always shows all items
    assert "13 categories" in console and "category-12" in console
    assert "covering 80" not in console
    html = _participant_html(report)
    assert "Missing money keeps all detail rows with unknown coverage" in html
    detail = html.split('data-table-id="participants-user-explanations"')[1].split('</table>')[0]
    assert "category-12" in detail and "coverage" not in detail.lower()
    _write_participant_exports(tmp_path, report, [])
    with (tmp_path/'participant_item_breakdown_7d.csv').open(encoding='utf-8', newline='') as file:
        assert len(list(csv.DictReader(file))) == 13


def test_selected_details_preserve_escaping_icons_and_identity():
    from warera_quant.report import _participant_html
    report = participant_fixture()
    html = _participant_html(report)
    assert '&lt;script&gt;' in html and '<script>' not in html
    assert '\u2694\ufe0f' in html or '\u2022' in html
    assert 'identity-' in html
    assert '<tfoot>' not in html
