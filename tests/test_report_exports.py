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


def test_participant_tables_complete_bounds_and_pngs(tmp_path):
    from test_participant_report import participant_fixture, frame, NOW
    _, html = write_outputs(frame(), tmp_path, participant_report=participant_fixture(), equipment_details=[], as_of=NOW)
    obsolete = tmp_path / 'participant_rankings_7d' / 'participants-user-losses.png'
    obsolete.parent.mkdir(exist_ok=True)
    obsolete.write_bytes(b'obsolete')
    unrelated = obsolete.with_name('keep-me.png')
    unrelated.write_bytes(b'keep')
    inventory = export_report_assets(html, tmp_path)
    assert not obsolete.exists()
    assert unrelated.read_bytes() == b'keep'
    tables = [a for a in inventory if a.get('table_id', '').startswith('participants-')]
    assert {a['table_id'] for a in tables} == {
        f'participants-{kind}-{board}' for kind in ('user','mu','country')
        for board in ('volume','explanations')}
    assert len(tables) == 6
    assert len({a['table_id'] for a in tables}) == 6
    for asset in tables:
        assert asset['path'].startswith('participant_rankings_7d/')
        bounds = asset['css_size']
        assert bounds['cellsOutside'] == 0
        assert bounds['minCellFont'] >= 14
        assert bounds['scrollWidth'] <= bounds['width'] + 2
        assert bounds['scrollHeight'] <= bounds['height'] + 2
        width, height = struct.unpack('>II', (tmp_path / asset['path']).read_bytes()[16:24])
        assert abs(width - 2 * bounds['width']) <= 4
        assert abs(height - 2 * bounds['height']) <= 4
    assert len([a for a in inventory if a['kind'] == 'data']) == 5
    assert '<table ' not in html.read_text(encoding='utf-8')
