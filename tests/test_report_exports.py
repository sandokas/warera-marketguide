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
         'last_trade_price':92.5, 'stable_fair_price_7d':100,
         'order_book':{'best_bid':90, 'best_ask':95}},
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
    assert len([a for a in inventory if a['kind'] == 'data']) == 6
    assert '<table ' not in html.read_text(encoding='utf-8')


def test_h_raw_geometry_spans_sparse_and_large_participants(tmp_path):
    """Keep raw browser evidence alongside the actual full-element PNGs."""
    from playwright.sync_api import sync_playwright
    from warera_quant.charts import _chrome_executable
    from test_participant_report import participant_fixture, NOW
    participants = participant_fixture()
    for boards in participants['rankings'].values():
        for entity in boards['volume']:
            entity['top_items'] = [dict(entity['top_items'][-1], item_name=f'Long category {i} with descriptive words')
                                   for i in range(80)]
    frame = pd.DataFrame([
        {'item_code':'long', 'item_name':'Long item label with many descriptive words',
         'last_trade_price':92.5, 'stable_fair_price_7d':100,
         'order_book':{'best_bid':90, 'best_ask':95}},
        {'item_code':'sparse', 'item_name':'Sparse unavailable history', 'order_book':{}},
    ])
    _, html = write_outputs(frame, tmp_path, participant_report=participants, as_of=NOW)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_chrome_executable(None), headless=True)
        page = browser.new_page()
        page.goto(html.as_uri())
        page.evaluate("document.fonts.ready")
        page.locator('table.report-table').evaluate_all("""tables => {
            for(const table of tables){
                const count=[...table.tHead.rows[0].cells].reduce((n,c)=>n+c.colSpan,0);
                const header=table.tHead.insertRow(0);
                const cell=document.createElement('th');cell.colSpan=count;
                cell.textContent='Spanning multirow header';header.append(cell);
                const footer=table.tFoot || table.createTFoot();
                const note=footer.insertRow();const td=note.insertCell();td.colSpan=count;
                td.textContent='Footer contribution with a long readable note across all columns';
            }
        }""")
        html.write_text(page.content(), encoding='utf-8')
        browser.close()
    (tmp_path/'raw-pre-export.html').write_text(html.read_text(encoding='utf-8'), encoding='utf-8')
    inventory = export_report_assets(html, tmp_path)
    tables=[a for a in inventory if a['kind']=='table']
    assert len(tables) >= 9
    for asset in tables:
        geometry=asset['css_size']; table=geometry['table']
        assert table['columnCount'] > 0
        assert table['lastCellRight']['THEAD'] is not None
        assert table['lastCellRight']['TBODY'] is not None
        assert table['lastCellRight']['TFOOT'] is not None
        assert all(abs(gap) <= table['rightEdgeTolerance'] for gap in table['rightEdgeGaps'].values())
        assert geometry['cellsOutside']==0
        assert asset['png_size']['width'] > 0
        assert asset['png_size']['height'] > 0
    assert any(a['css_size']['height'] > 2000 for a in tables)


def test_h_rowspan_and_empty_tables(tmp_path):
    html=tmp_path/'report.html'
    html.write_text("""<style>table {border-collapse:collapse;width:max-content;font-size:16px}
        th,td {border:1px solid black;padding:10px}</style>
        <table class="report-table"><thead><tr><th rowspan="2">Name</th>
        <th colspan="2">Values</th></tr><tr><th>Buy</th><th>Sell</th></tr></thead>
        <tbody><tr><td>A</td><td>1</td><td rowspan="2">2</td></tr>
        <tr><td>B</td><td>3</td></tr></tbody></table>
        <table class="report-table"><thead><tr><th>A</th><th>B</th></tr></thead>
        <tbody><tr><td colspan="2">No qualifying observed activity.</td></tr></tbody></table>
        <table class="report-table"><thead><tr><th>Empty</th></tr></thead><tbody></tbody></table>""", encoding='utf-8')
    inventory=export_report_assets(html,tmp_path)
    tables=[a for a in inventory if a['kind']=='table']
    assert [a['css_size']['table']['columnCount'] for a in tables]==[3,2,1]
    assert tables[2]['css_size']['table']['lastCellRight']['TBODY'] is None


def test_h_rejects_canvas_outside_real_columns(tmp_path):
    import pytest
    html=tmp_path/'report.html'
    html.write_text('<table class="report-table" style="padding-right:100px;border-spacing:0">'
                    '<thead><tr><th>Name</th></tr></thead><tbody><tr><td>A</td></tr></tbody></table>')
    with pytest.raises(RuntimeError, match='Unused table edge'):
        export_report_assets(html,tmp_path)
    html.write_text('<table class="report-table" style="border-collapse:collapse">'
                    '<thead><tr><th>Name</th></tr></thead><tbody><tr><td>A</td></tr></tbody>'
                    '<tfoot><tr><td>Footer</td><td>Extra physical column</td></tr></tfoot></table>')
    with pytest.raises(RuntimeError, match='Unused table edge'):
        export_report_assets(html,tmp_path)
