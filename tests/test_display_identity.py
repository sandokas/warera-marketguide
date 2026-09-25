import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image
import pytest

from warera_quant.api_client import WarEraApiClient
from warera_quant.display_assets import normalize_image, bundled_equipment_display, asset_data_uri
from warera_quant.market_data import load_participant_report, displayed_identity_keys
from warera_quant.market_models import DisplayIdentity
from warera_quant.market_store import MarketStore, MIGRATIONS
from warera_quant.report import _participant_html
from warera_quant.sync import refresh_display_cache
from warera_quant.warera_api import WarEraMarketApi, WarEraApiError, normalize_transaction

NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


class Client:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.image_fail = False
        self.url = 'https://media.warera.io/example.png'

    def get_json(self, endpoint, *, params=None):
        self.calls.append((endpoint, params))
        if self.fail:
            raise TimeoutError('transient')
        entity_id = next(iter(json.loads(params['input']).values()))
        return {'result': {'data': {'_id': entity_id, 'username': '<User & name>',
            'name': 'Official name', 'code': 'bo', 'avatarUrl': self.url,
            'mu': 'current-membership-must-not-be-attributed'}}}

    def get_public_bytes(self, url):
        self.calls.append(('image', url))
        if self.image_fail:
            raise TimeoutError('image failure')
        output = io.BytesIO()
        Image.new('RGB', (4, 3), 'purple').save(output, format='PNG')
        return output.getvalue(), 'image/png'


@pytest.mark.parametrize('kind,endpoint,key,name', [
    ('user', '/user.getUserLite', 'userId', '<User & name>'),
    ('mu', '/mu.getById', 'muId', 'Official name'),
    ('country', '/country.getCountryById', 'countryId', 'Official name')])
def test_verified_profile_boundary(kind, endpoint, key, name):
    client = Client()
    result = WarEraMarketApi(client).get_identity(kind, 'id')
    assert result.name == name
    assert client.calls == [(endpoint, {'input': json.dumps({key: 'id'})})]
    assert not hasattr(result, 'mu')
    if kind == 'country':
        assert result.image_url == 'https://media.warera.io/images/flags/bo.svg?v=16'


@pytest.mark.parametrize('payload', [None, {}, {'_id': 'wrong', 'username': 'Name'},
                                    {'_id': 'id', 'username': None}])
def test_bad_profiles_cannot_poison_cache(payload):
    class Bad:
        def get_json(self, *args, **kwargs):
            return {'result': {'data': payload}}
    with pytest.raises(WarEraApiError):
        WarEraMarketApi(Bad()).get_identity('user', 'id')


def test_bounded_refresh_backoff_and_last_success_survive_failures(tmp_path):
    client = Client()
    api = WarEraMarketApi(client)
    with MarketStore(tmp_path/'cache.db') as store:
        keys = [('user', 'u'), ('mu', 'm')]
        kwargs = dict(asset_dir=tmp_path/'assets', equipment=False)
        summary = refresh_display_cache(api, store, keys, now=NOW, max_profiles=1, **kwargs)
        assert summary['profiles_attempted'] == summary['assets_attempted'] == 1
        assert summary['deferred'] == 1
        original = store.entity_name('user', 'u')
        asset = store.cached_asset(original['image_url'])
        assert asset_data_uri(asset)
        summary = refresh_display_cache(api, store, [('user', 'u')], now=NOW+timedelta(hours=1), **kwargs)
        assert summary['profiles_attempted'] == summary['assets_attempted'] == 0
        client.fail = client.image_fail = True
        summary = refresh_display_cache(api, store, [('user', 'u')], now=NOW+timedelta(days=2), **kwargs)
        assert len(summary['errors']) == 2
        cached = store.entity_name('user', 'u')
        assert cached['name'] == original['name'] and cached['name_observed_at'] == original['name_observed_at']
        assert cached['lookup_status'] == 'unavailable'
        assert asset_data_uri(store.cached_asset(original['image_url'])) == asset_data_uri(asset)
        client.fail = False
        client.url = 'https://media.warera.io/replacement.png'
        refresh_display_cache(api, store, [('user', 'u')], now=NOW+timedelta(days=4), **kwargs)
        assert store.entity_name('user', 'u')['image_cache_url'] == original['image_url']


def test_v5_additive_migration_preserves_names_and_rolls_back(tmp_path, monkeypatch):
    store = MarketStore(tmp_path/'v5.db')
    c = store._connect()
    c.execute('create table schema_meta (key text primary key,value text not null)')
    for v in range(1, 6):
        MIGRATIONS[v](c)
    c.execute("insert into schema_meta values ('version','5')")
    c.execute('pragma user_version=5')
    c.commit()
    store.cache_entity_name('user', 'u', 'Retained', NOW.isoformat(), 'ok')
    original = MIGRATIONS[6]
    def broken(connection):
        original(connection)
        raise RuntimeError('rollback')
    monkeypatch.setitem(MIGRATIONS, 6, broken)
    with pytest.raises(RuntimeError):
        store.initialize()
    assert store.schema_version() == store.user_version() == 5
    assert 'display_assets' not in store.table_names()
    monkeypatch.setitem(MIGRATIONS, 6, original)
    store.initialize()
    store.initialize()
    assert store.entity_name('user', 'u')['name'] == 'Retained'
    assert store.schema_version() == store.user_version() == 6
    store.close()


def test_offline_all_kinds_and_equipment_without_membership_attribution(tmp_path, monkeypatch):
    client = Client()
    with MarketStore(tmp_path/'report.db') as store:
        for i, kind in enumerate(('user', 'mu', 'country')):
            refs = {'buyerId': 'u'} if kind == 'user' else {'buyerId': 'actor', 'buyer'+kind.title()+'Id': kind}
            store.ingest_transactions([normalize_transaction({'_id':str(i), 'itemCode':'boots4',
                'transactionType':'itemMarket', 'createdAt':(NOW-timedelta(hours=1)).isoformat(),
                'money':10, 'quantity':1, 'item':{'code':'boots4','skills':{'dodge':23},'state':100,'maxState':100}, **refs})], fetched_at=NOW)
        refresh_display_cache(WarEraMarketApi(client), store, [('user','u'),('mu','mu'),('country','country')],
                              asset_dir=tmp_path/'assets', equipment=False, now=NOW)
    def forbidden(*args, **kwargs):
        raise AssertionError('offline report attempted HTTP')
    monkeypatch.setattr(WarEraApiClient, 'get_json', forbidden)
    monkeypatch.setattr(WarEraApiClient, 'get_public_bytes', forbidden)
    with MarketStore(tmp_path/'report.db') as store:
        report = load_participant_report(store, as_of=NOW)
        assert set(displayed_identity_keys(report)) == {('user','u'),('mu','mu'),('country','country')}
        html = _participant_html(report)
        assert 'current-membership' not in html and 'actor' not in html
        assert '&lt;User &amp; name&gt;' in html
        assert html.count('class="identity-image"') == 6
        assert html.count('class="equipment-image"') == 6
        assert 'Tier 4 / epic' not in html and 'condition' not in html
        # Equipment stats now use icon format (e.g., 💨23) instead of "stats:"
        assert 'stats:' not in html and ('💨23' in html or '•23' in html)
        for row in report['entities']:
            assert row['identity']['image_src'].startswith('data:image/')


def test_official_equipment_assets_are_complete_and_decodable():
    items = bundled_equipment_display()
    assert len(items) == 30
    assert items['boots4']['color_scheme'] == items['gloves4']['color_scheme'] == 'purple'
    assert items['boots5']['color_scheme'] == 'yellow'
    assert items['boots6']['color_scheme'] == 'red'
    assert len({v['image_url'] for v in items.values()}) == 5
    assert all(v.get('image_src', '').startswith('data:image/png;base64,') for v in items.values())


def test_equipment_mapping_changes_fail_closed():
    class Changed:
        def get_json(self, *args, **kwargs):
            return {'result': {'data': {'items': {'boots4': {'type':'equipment', 'rarity':'mythic','iconImg':'boots.png'}}}}}
    with pytest.raises(WarEraApiError, match='Unverified'):
        WarEraMarketApi(Changed()).get_equipment_display()


@pytest.mark.parametrize('url', ['http://media.warera.io/a', 'https://example.com/a',
                               'https://media.warera.io@localhost/a', 'https://media.warera.io:444/a'])
def test_public_asset_origin_checked_before_http(url):
    with pytest.raises(ValueError):
        WarEraApiClient.get_public_bytes(url)


@pytest.mark.parametrize('content,mime', [(b'<html>oops</html>', 'text/html'),
    (b'<svg xmlns="http://www.w3.org/2000/svg"><script>bad()</script></svg>', 'image/svg+xml'),
    (b'<svg width="10" height="10"><use href="https://example.com/a"/></svg>', 'image/svg+xml')])
def test_invalid_images_are_rejected(content, mime):
    with pytest.raises(ValueError):
        normalize_image(content, mime)


def test_binary_http_is_bounded_and_never_uses_authenticated_session(monkeypatch):
    from warera_quant import api_client
    class Response:
        status_code = 200
        headers = {'Content-Type': 'image/png; charset=binary'}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, size): yield b'123'; yield b'456'
    calls = []
    def get(url, **kwargs):
        calls.append(kwargs)
        return Response()
    monkeypatch.setattr(api_client.requests, 'get', get)
    with pytest.raises(ValueError, match='byte limit'):
        WarEraApiClient.get_public_bytes('https://media.warera.io/test', max_bytes=5)
    assert 'X-Api-Key' not in calls[0]['headers']
    assert calls[0]['allow_redirects'] is False and calls[0]['stream'] is True
    Response.status_code = 302
    with pytest.raises(ValueError, match='HTTP 200'):
        WarEraApiClient.get_public_bytes('https://media.warera.io/test')


def test_corrupt_asset_falls_back_and_retries_within_profile_ttl(tmp_path):
    client = Client()
    with MarketStore(tmp_path/'cache.db') as store:
        kwargs = dict(asset_dir=tmp_path/'assets', equipment=False)
        refresh_display_cache(WarEraMarketApi(client), store, [('user', 'u')], now=NOW, **kwargs)
        cached = store.cached_asset(client.url)
        Path(cached['local_path']).write_bytes(b'corrupted')
        assert asset_data_uri(cached) is None
        result = refresh_display_cache(WarEraMarketApi(client), store, [('user','u')],
                                       now=NOW+timedelta(hours=1), **kwargs)
        assert result['profiles_attempted'] == 0 and result['assets_attempted'] == 1
        assert asset_data_uri(store.cached_asset(client.url))


def test_offline_browser_decodes_images_and_full_table_pngs(tmp_path):
    from playwright.sync_api import sync_playwright
    from warera_quant.report import export_report_assets
    from warera_quant.charts import _chrome_executable
    from test_participant_report import participant_fixture
    from warera_quant.market_data import enrich_participant_display
    report = participant_fixture()
    with MarketStore(tmp_path/'cache.db') as store:
        refresh_display_cache(WarEraMarketApi(Client()), store, displayed_identity_keys(report),
                              asset_dir=tmp_path/'assets', equipment=False, now=NOW)
        enrich_participant_display(store, report)
    # Include every verified tier, both problematic tier-four items and full stats.
    equipment = bundled_equipment_display()
    for row in report['rankings']['user']['volume']:
        for i, category in enumerate(row['categories']['buy']):
            code = 'boots4' if i == 0 else 'gloves4'
            category['item_code'] = code
            category['display'] = equipment[code]
    source = tmp_path/'reference.html'
    source.write_text('<html><body>'+_participant_html(report)+'</body></html>', encoding='utf-8')
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_chrome_executable(None), headless=True)
        page = browser.new_page()
        network = []
        page.route('http://**', lambda route: (network.append(route.request.url), route.abort()))
        page.route('https://**', lambda route: (network.append(route.request.url), route.abort()))
        page.goto(source.as_uri())
        page.evaluate('async () => await Promise.all([...document.images].map(i => i.decode()))')
        assert page.locator('img.identity-image').count() > 0
        assert page.locator('img.equipment-image').count() > 0
        assert page.locator('img').evaluate_all('images => images.every(i => i.complete && i.naturalWidth > 0)')
        assert not network
        browser.close()
    inventory = export_report_assets(source, tmp_path)
    tables = [a for a in inventory if a.get('table_id', '').startswith('participants-')]
    assert len(tables) == 6
    assert all(a['css_size']['cellsOutside'] == 0 for a in tables)


def test_bundled_weapons_and_items_render_as_icons_with_compact_stats():
    from warera_quant.display_assets import bundled_item_display
    from warera_quant.report import _category_html
    items = bundled_item_display()
    for code in ("knife", "gun", "rifle", "sniper", "tank", "jet", "iron", "bread"):
        assert items[code]["image_src"].startswith("data:image/png;base64,")
        html = _category_html({"item_code": code, "display": items[code],
            "category": ("equipment-v1", code, (("attack", "141"), ("criticalChance", "31")), None, None)})
        # Equipment stats now use icon format (e.g., ⚔️141 🎯31) instead of slash-separated
        assert ("⚔️141" in html or "•141" in html) and ("🎯31" in html or "•31" in html) and "<img " in html
        assert "stats:" not in html and "Tier " not in html
        assert f">{code}<" not in html
