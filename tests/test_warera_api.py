import json

import pytest

from warera_quant.warera_api import (
    GAME_CONFIG_ENDPOINT,
    TOP_ORDERS_ENDPOINT,
    TRANSACTIONS_ENDPOINT,
    OrderLevel,
    WarEraApiError,
    WarEraMarketApi,
)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get_json(self, endpoint, *, params=None):
        self.calls.append((endpoint, params))
        if not self.responses:
            raise AssertionError(f"Unexpected call to {endpoint}")
        return self.responses.pop(0)


def _trpc(data):
    return {"result": {"data": data}}


def test_get_prices_parses_trpc_payload():
    api = WarEraMarketApi(FakeClient([_trpc({"bread": 1.25, "steel": "3.5"})]))

    assert api.get_prices() == {"bread": 1.25, "steel": 3.5}


def test_get_prices_parses_trpc_json_payload():
    api = WarEraMarketApi(FakeClient([_trpc({"json": {"bread": 1.25}})]))

    assert api.get_prices() == {"bread": 1.25}


def test_get_item_production_points_uses_official_config_and_marks_undefined_items():
    client = FakeClient([_trpc({"items": {
        "iron": {"isTradable": True, "productionPoints": 1},
        "steel": {"isTradable": True, "productionPoints": "10"},
        "case1": {"isTradable": True},
        "gun": {"isTradable": False},
    }})])
    api = WarEraMarketApi(client)

    assert api.get_item_production_points() == {"iron": 1.0, "steel": 10.0, "case1": None}
    assert client.calls == [(GAME_CONFIG_ENDPOINT, None)]


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"result": {}},
        _trpc(["not", "an", "object"]),
        _trpc({"bread": object()}),
    ],
)
def test_get_prices_rejects_malformed_responses(response):
    api = WarEraMarketApi(FakeClient([response]))

    with pytest.raises(WarEraApiError):
        api.get_prices()


def test_get_top_orders_parses_orders_and_builds_input_params():
    client = FakeClient([
        _trpc({
            "buyOrders": [{"price": "1.20", "quantity": 10}],
            "sellOrders": [{"price": "1.35", "quantity": 5}],
        })
    ])
    api = WarEraMarketApi(client)

    orders = api.get_top_orders("bread", 7)

    assert orders.buy_orders == [OrderLevel(price=1.2, quantity=10.0)]
    assert orders.sell_orders == [OrderLevel(price=1.35, quantity=5.0)]
    endpoint, params = client.calls[0]
    assert endpoint == TOP_ORDERS_ENDPOINT
    assert json.loads(params["input"]) == {"itemCode": "bread", "limit": 7}


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"buyOrders": "bad", "sellOrders": []},
        {"buyOrders": [{}], "sellOrders": ["bad"]},
    ],
)
def test_get_top_orders_rejects_malformed_responses(payload):
    api = WarEraMarketApi(FakeClient([_trpc(payload)]))

    with pytest.raises(WarEraApiError):
        api.get_top_orders("bread", 10)


def test_get_top_orders_sorts_levels_and_discards_zero_quantity():
    api = WarEraMarketApi(FakeClient([_trpc({
        "buyOrders": [
            {"price": "1.1", "quantity": "2"},
            {"price": "1.2", "quantity": "3"},
            {"price": "9", "quantity": "0"},
            {"price": "0", "quantity": "999999"},
        ],
        "sellOrders": [
            {"price": "1.5", "quantity": "1"},
            {"price": "1.4", "quantity": "4"},
        ],
    })]))

    orders = api.get_top_orders("bread", 10)

    assert orders.buy_orders == [OrderLevel(1.2, 3), OrderLevel(1.1, 2)]
    assert orders.sell_orders == [OrderLevel(1.4, 4), OrderLevel(1.5, 1)]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("price", None),
        ("price", "bad"),
        ("price", float("inf")),
        ("price", -1),
        ("quantity", None),
        ("quantity", "bad"),
        ("quantity", float("nan")),
        ("quantity", -1),
    ],
)
def test_get_top_orders_rejects_invalid_level_fields_clearly(field, value):
    entry = {"price": 1, "quantity": 2}
    entry[field] = value
    api = WarEraMarketApi(FakeClient([_trpc({"buyOrders": [entry], "sellOrders": []})]))

    with pytest.raises(WarEraApiError, match=rf"buyOrders\[0\]\.{field}"):
        api.get_top_orders("bread", 10)


def test_get_transaction_page_parses_items_next_cursor_and_input_params():
    client = FakeClient([
        _trpc({
            "items": [{"id": "tx-1", "createdAt": "2026-06-30T10:00:00Z"}],
            "nextCursor": "next-page",
        })
    ])
    api = WarEraMarketApi(client)

    page = api.get_transaction_page("steel", limit=25, cursor="current-page")

    assert page.items[0].values["id"] == "tx-1"
    assert page.items[0].values["created_at"] == "2026-06-30T10:00:00Z"
    assert page.items[0].values["transaction_type"] == "trading"
    assert page.next_cursor == "next-page"
    endpoint, params = client.calls[0]
    assert endpoint == TRANSACTIONS_ENDPOINT
    assert json.loads(params["input"]) == {
        "itemCode": "steel",
        "limit": 25,
        "transactionType": "trading",
        "cursor": "current-page",
    }


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"items": "bad", "nextCursor": None},
        {"items": ["bad"], "nextCursor": None},
        {"items": [], "nextCursor": 123},
    ],
)
def test_get_transaction_page_rejects_malformed_responses(payload):
    api = WarEraMarketApi(FakeClient([_trpc(payload)]))

    with pytest.raises(WarEraApiError):
        api.get_transaction_page("steel", limit=25)


@pytest.mark.parametrize("kind", ["trading", "itemMarket"])
def test_global_pages_preserve_observed_fields(kind):
    from pathlib import Path
    from decimal import Decimal
    response = json.loads((Path(__file__).parent / "fixtures" / "market_contracts" /
                          f"{kind}.observed.json").read_text(), parse_float=Decimal)
    # The representative subset is not itself a pagination-order fixture.
    response["result"]["data"]["items"].sort(key=lambda r: r["createdAt"], reverse=True)
    client = FakeClient([response])
    page = WarEraMarketApi(client).get_transaction_page(transaction_type=kind, limit=100)
    assert "itemCode" not in json.loads(client.calls[0][1]["input"])
    for source, fact in zip(response["result"]["data"]["items"], page.items):
        assert not fact.extras
        assert fact.values["money_decimal"] == str(source["money"])
        assert fact.values["offer_created_at"] == source["offerCreatedAt"]
        for prefix, side in (("buyer", "buy"), ("seller", "sell")):
            for suffix, dest in (("Id", "user_id"), ("MuId", "mu_id"), ("CountryId", "country_id")):
                if prefix + suffix in source:
                    assert fact.participants[side][dest] == source[prefix + suffix]
        if "item" in source:
            assert fact.equipment["instance_id"] == source["item"]["_id"]
            assert fact.stats == {k: str(v) for k, v in source["item"]["skills"].items()}
            assert fact.equipment.get("equipment_type") == source["item"].get("type")
            for raw, dest in (("state", "state"), ("maxState", "max_state"), ("quantity", "item_quantity")):
                assert fact.equipment[dest] == str(source["item"][raw])
            assert fact.equipment["last_acquisition_at"] == source["item"]["lastAcquisitionAt"]


def test_unknown_fields_visible_and_optional_equipment_fields_preserved(caplog):
    from warera_quant.warera_api import normalize_transaction, normalize_order
    source = {"_id": "x", "transactionType": "itemMarket", "itemCode": "newCode",
              "createdAt": "2026-09-23T01:02:03.123456Z", "buyerId": "actor",
              "buyerCountryId": "country", "buyerMuId": "mu", "sellerPartyId": "party",
              "sellerId": None, "item": {"code": "newCode", "skills": {"new/skill": 0, "other": None},
                                        "state": None, "__v": 2},
              "new/field": [True, None, {"~key": "value"}], "__v": 3}
    fact = normalize_transaction(source)
    assert fact.participants["buy"] == {"user_id": "actor", "mu_id": "mu", "country_id": "country"}
    assert fact.participants["sell"] == {"user_id": None, "party_id": "party"}
    assert fact.stats == {"new/skill": "0", "other": None}
    assert fact.equipment == {"equipment_code": "newCode", "state": None}
    assert {e.path for e in fact.extras} == {"/new~1field/0", "/new~1field/1", "/new~1field/2/~0key"}
    assert "Unknown transaction scalar retained" in caplog.text
    order = normalize_order({"_id": "o", "price": 0, "quantity": 0, "country": "c",
                             "party": "p", "custom": False, "__v": 1}, "bread", "bid", 0)
    assert order.values["country_id"] == "c" and order.values["party_id"] == "p"
    assert [e.path for e in order.extras] == ["/custom"]
    assert "Unknown order scalar retained" in caplog.text


@pytest.mark.parametrize("change", [{"transactionType": "itemMarket"}, {"itemCode": None},
    {"createdAt": "bad"}, {"buyerMuId": {}}, {"item": {"skills": []}}, {"money": True}])
def test_global_page_rejects_invalid_types_and_optional_shapes(change):
    row = {"_id": "x", "itemCode": "newCode", "transactionType": "trading",
           "createdAt": "2026-09-23T00:00:00Z", **change}
    api = WarEraMarketApi(FakeClient([_trpc({"items": [row]})]))
    with pytest.raises(WarEraApiError):
        api.get_transaction_page(limit=100)


def test_global_mixed_codes_allowed_but_timestamp_reversal_rejected():
    rows = [{"_id": "a", "itemCode": "old", "createdAt": "2026-09-23T00:00:00.001Z"},
            {"_id": "b", "itemCode": "new", "createdAt": "2026-09-23T00:00:00.002Z"}]
    api = WarEraMarketApi(FakeClient([_trpc({"items": rows[::-1]}), _trpc({"items": rows})]))
    assert len(api.get_transaction_page(limit=100).items) == 2
    with pytest.raises(WarEraApiError, match="descending"):
        api.get_transaction_page(limit=100)


def test_missing_items_is_not_exhaustion():
    api = WarEraMarketApi(FakeClient([_trpc({})]))
    with pytest.raises(WarEraApiError, match="items list"):
        api.get_transaction_page(limit=100)


def test_search_mu_filters_by_exact_name():
    client = FakeClient([_trpc({"items": [
        {"_id": "mu-1", "name": "Alpha MU"},
        {"_id": "mu-2", "name": "Alpha MU Copy"},
        {"_id": "mu-3", "name": "Beta MU"},
    ]})])
    api = WarEraMarketApi(client)

    def mock_get_identity(kind, entity_id):
        from warera_quant.market_models import DisplayIdentity
        mapping = {"mu-1": "Alpha MU", "mu-2": "Alpha MU Copy", "mu-3": "Beta MU"}
        return DisplayIdentity(kind, entity_id, mapping[entity_id])

    original_get_identity = api.get_identity
    api.get_identity = mock_get_identity
    results = api.search_mu("Alpha MU")
    api.get_identity = original_get_identity

    assert len(results) == 1
    assert results[0].entity_id == "mu-1"
    assert results[0].name == "Alpha MU"
    assert client.calls == [("/mu.getManyPaginated", {"input": '{"search": "Alpha MU", "limit": 100}'})]


@pytest.mark.parametrize("first_name", ["Alpha MU Copy", "Alpha MU"])
def test_search_mu_reads_later_pages_and_deduplicates_ids(first_name, monkeypatch):
    from warera_quant.market_models import DisplayIdentity

    client = FakeClient([
        _trpc({"items": [{"_id": "mu-1"}], "nextCursor": "page-2"}),
        _trpc({"items": [{"_id": "mu-1"}, {"_id": "mu-2"}], "nextCursor": None}),
    ])
    api = WarEraMarketApi(client)
    profile_calls = []

    def get_identity(kind, entity_id):
        profile_calls.append((kind, entity_id))
        return DisplayIdentity(kind, entity_id, first_name if entity_id == "mu-1" else "ALPHA MU")

    monkeypatch.setattr(api, "get_identity", get_identity)
    results = api.search_mu("Alpha MU")

    assert [row.entity_id for row in results] == (["mu-1", "mu-2"] if first_name == "Alpha MU" else ["mu-2"])
    assert profile_calls == [("mu", "mu-1"), ("mu", "mu-2")]
    assert [json.loads(params["input"]) for _, params in client.calls] == [
        {"search": "Alpha MU", "limit": 100},
        {"search": "Alpha MU", "limit": 100, "cursor": "page-2"},
    ]


@pytest.mark.parametrize("cursor", [123, "", "page-2"])
def test_search_mu_rejects_invalid_or_repeated_cursors(cursor):
    client = FakeClient([
        _trpc({"items": [], "nextCursor": "page-2"}),
        _trpc({"items": [], "nextCursor": cursor}),
    ])
    with pytest.raises(WarEraApiError, match="cursor"):
        WarEraMarketApi(client).search_mu("Alpha MU")


def test_search_country_filters_by_exact_name():
    client = FakeClient([_trpc([
        {"_id": "country-1", "name": "Atlantis", "code": "atl"},
        {"_id": "country-2", "name": "Atlantis Copy", "code": "atl2"},
        {"_id": "country-3", "name": "Lemuria", "code": "lem"},
    ]), _trpc({"_id": "country-1", "name": "Atlantis", "code": "atl"})])
    api = WarEraMarketApi(client)
    results = api.search_country("ATLANTIS")

    assert len(results) == 1
    assert results[0].entity_id == "country-1"
    assert results[0].name == "Atlantis"
    assert results[0].country_code == "atl"
    assert client.calls == [
        ("/country.getAllCountries", None),
        ("/country.getCountryById", {"input": '{"countryId": "country-1"}'}),
    ]


def test_search_country_no_match_does_not_fetch_profiles():
    client = FakeClient([_trpc([
        {"_id": "country-1", "name": "Lemuria"},
        {"_id": "country-2", "name": None},
    ])])
    assert WarEraMarketApi(client).search_country("Atlantis") == []
    assert client.calls == [("/country.getAllCountries", None)]
