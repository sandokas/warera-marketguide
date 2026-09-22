"""Real browser capture checks: complete element PNGs and explicit inventory."""
import json
import struct
from pathlib import Path
import pandas as pd
from warera_quant.report import write_outputs, export_report_assets
from warera_quant.charts import render_we24_chart


def test_current_assets_exclude_archives_and_tables_are_complete_pngs(tmp_path):
    archive = tmp_path/'old-inflation.png'
    archive.write_bytes(b'archived')
    (tmp_path/'market_inflation.csv').write_text('historical')
    frame = pd.DataFrame([
        {'item_code':'long', 'item_name':'Long item label with multiple descriptive words',
         'last_trade_price':92.5, 'stable_fair_price_7d':100},
        {'item_code':'missing', 'item_name':'Unavailable history and insufficient depth'},
    ])
    chart = render_we24_chart({}, tmp_path/'we24.png')
    _, html = write_outputs(frame,tmp_path,we24_chart_path=chart)
    inventory = export_report_assets(html,tmp_path)
    assert archive.read_bytes() == b'archived'
    assert (tmp_path/'market_inflation.csv').read_text() == 'historical'
    assert not any('inflation' in asset['path'] for asset in inventory)
    assert len([a for a in inventory if a['kind']=='item']) == 2
    assert {'table','composite','header','we24-summary','highlight','chart','footer'} <= {a['kind'] for a in inventory}
    for asset in inventory:
        if asset['kind'] == 'table':
            width,height = struct.unpack('>II',(tmp_path/asset['path']).read_bytes()[16:24])
            # Capture bounds round outward at both edges by up to one CSS
            # pixel each: allow four physical pixels at the export's 2x scale.
            assert abs(width-2*asset['css_size']['width']) <= 4
            assert abs(height-2*asset['css_size']['height']) <= 4
    published = html.read_text(encoding='utf-8')
    assert '<table ' not in published
    assert 'published-table' in published
    assert '-7.50' in published
    assert json.loads((tmp_path/'asset_inventory.json').read_text()) == inventory
