import json

import pytest

from warera_quant.warera_api import (
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

    assert page.items == [{"id": "tx-1", "createdAt": "2026-06-30T10:00:00Z"}]
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
