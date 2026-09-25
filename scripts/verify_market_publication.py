import csv
import json
import re
import struct
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from html import unescape
from warera_quant.market_store import MarketStore
from warera_quant.market_data import load_participant_report, iter_equipment_sale_details

folder = Path(sys.argv[1])
meta = json.loads((folder / 'publication.json').read_text())
as_of = datetime.fromisoformat(meta['as_of'])
out = folder / 'report'
assets = json.loads((out / 'asset_inventory.json').read_text())
assert len({a['path'] for a in assets}) == len(assets)
assert all((out / a['path']).is_file() for a in assets)
tables = [a for a in assets if a['kind'] == 'table']
participant = [a for a in tables if a.get('table_id', '').startswith('participants-')]
expected = {f'participants-{kind}-{board}' for kind in ('user','mu','country') for board in ('losses','profits','volume')}
assert expected <= {a['table_id'] for a in participant}
for a in tables:
    b = a['css_size']
    assert b['cellsOutside'] == 0
    assert b['scrollWidth'] <= b['width'] + 2 and b['scrollHeight'] <= b['height'] + 2
    w,h = struct.unpack('>II', (out / a['path']).read_bytes()[16:24])
    assert abs(w - 2*b['width']) <= 4 and abs(h - 2*b['height']) <= 4
for a in participant:
    assert a['css_size']['minCellFont'] >= 14
html = (out / 'market_report.html').read_text(encoding='utf-8')
assert '<table ' not in html

def rows(name):
    with (out / name).open(encoding='utf-8', newline='') as f:
        return list(csv.DictReader(f))

with MarketStore(meta['database']) as store:
    report = load_participant_report(store, as_of=as_of)
    details = list(iter_equipment_sale_details(store, as_of=as_of))
    commodity_codes = set(store.item_codes())
    sales = rows('equipment_sales_7d.csv')
    exported_stats = rows('equipment_sale_stats_7d.csv')
    assert {s['id'] for s in sales} == {s['id'] for s in details}
    expected_stats = {(s['id'],k,str(v) if v is not None else '') for s in details
        for k,v in ((s.get('equipment') or {}).get('stats') or {}).items()}
    assert {(s['transaction_id'],s['skill_code'],s['value']) for s in exported_stats} == expected_stats
    trends = rows('market_trends.csv')
    assert all(r['item_code'] in commodity_codes for r in trends)
    card_codes = {unescape(code) for code in re.findall(r'data-item-code="([^"]+)"', html)}
    assert card_codes == {r['item_code'] for r in trends}
    assert all(a['kind'] == 'data' for a in assets if 'equipment' in a['path'])
    for name in ('participant_rankings_7d.csv','participant_trade_breakdown_7d.csv','equipment_sales_7d.csv'):
        assert all(datetime.fromisoformat(r['as_of']) == as_of for r in rows(name))
    fields = ('source_buy_value','source_sell_value','matched_source_sale_value',
        'net_matched_source_sale_value','uncosted_source_sale_value','unknown_fee_source_sale_value')
    summary = {'as_of':as_of, 'status':meta['status'], 'turnover_basis':report['turnover_basis'],
        'sources':report['sources'], 'source_coverage':report['source_coverage'],
        'attribution':report['coverage'], 'entities':len(report['entities']),
        'account_side_totals':{k:sum((r[k] for r in report['entities']), Decimal(0)) for k in fields},
        'boards':{k:{b:len(v) for b,v in boards.items()} for k,boards in report['rankings'].items()},
        'equipment_sales':len(sales), 'equipment_stats':len(exported_stats),
        'commodity_items':len(trends), 'assets':len(assets), 'complete_table_crops':len(tables),
        'participant_table_crops':len(participant),
        'limits':report['limitations']}
(folder / 'reconciliation.json').write_text(json.dumps(summary, indent=2, default=str), encoding='utf-8')
print(json.dumps(summary, indent=2, default=str))
