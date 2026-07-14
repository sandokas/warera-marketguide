from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Protocol


PRICES_ENDPOINT = "/itemTrading.getPrices"
TOP_ORDERS_ENDPOINT = "/tradingOrder.getTopOrders"
TRANSACTIONS_ENDPOINT = "/transaction.getPaginatedTransactions"


class JsonClient(Protocol):
    def get_json(self, endpoint: str, *, params: dict[str, Any] | None = None) -> Any: ...


class WarEraApiError(ValueError):
    """Raised when a WarEra market endpoint returns an unexpected payload."""


@dataclass(frozen=True)
class OrderLevel:
    price: float
    quantity: float


@dataclass(frozen=True)
class TopOrders:
    buy_orders: list[OrderLevel]
    sell_orders: list[OrderLevel]


@dataclass(frozen=True)
class TransactionPage:
    items: list[dict[str, Any]]
    next_cursor: str | None


class WarEraMarketApi:
    def __init__(self, client: JsonClient):
        self.client = client

    def get_prices(self) -> dict[str, float]:
        response = self.client.get_json(PRICES_ENDPOINT)
        data = _trpc_data(response)
        if not isinstance(data, dict):
            raise WarEraApiError("Expected prices response to contain an item-price object.")

        prices: dict[str, float] = {}
        for item_code, price in data.items():
            if not isinstance(item_code, str):
                raise WarEraApiError("Expected prices response item codes to be strings.")
            prices[item_code] = _required_float(price, f"price for {item_code}")
        return prices

    def get_top_orders(self, item_code: str, limit: int) -> TopOrders:
        response = self.client.get_json(
            TOP_ORDERS_ENDPOINT,
            params=_input_params({"itemCode": item_code, "limit": limit}),
        )
        data = _trpc_data(response)
        if not isinstance(data, dict):
            raise WarEraApiError("Expected top-orders response to contain an object.")

        return TopOrders(
            buy_orders=sorted(
                _order_list(data.get("buyOrders"), "buyOrders"),
                key=lambda level: level.price,
                reverse=True,
            ),
            sell_orders=sorted(
                _order_list(data.get("sellOrders"), "sellOrders"),
                key=lambda level: level.price,
            ),
        )

    def get_transaction_page(self, item_code: str, *, limit: int, cursor: str | None = None) -> TransactionPage:
        payload: dict[str, Any] = {
            "itemCode": item_code,
            "limit": limit,
            "transactionType": "trading",
        }
        if cursor:
            payload["cursor"] = cursor

        response = self.client.get_json(
            TRANSACTIONS_ENDPOINT,
            params=_input_params(payload),
        )
        data = _trpc_data(response)
        if not isinstance(data, dict):
            raise WarEraApiError("Expected transaction-page response to contain an object.")

        next_cursor = data.get("nextCursor")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise WarEraApiError("Expected transaction-page nextCursor to be a string or null.")

        return TransactionPage(
            items=_transaction_list(data.get("items"), "items"),
            next_cursor=next_cursor,
        )


def _input_params(payload: dict[str, Any]) -> dict[str, str]:
    return {"input": json.dumps(payload)}


def _trpc_data(response: Any) -> Any:
    if not isinstance(response, dict):
        raise WarEraApiError("Expected WarEra API response to be an object.")
    try:
        data = response["result"]["data"]
    except (KeyError, TypeError) as exc:
        raise WarEraApiError("Unexpected WarEra API response shape.") from exc
    if isinstance(data, dict) and set(data) == {"json"}:
        return data["json"]
    return data


def _required_float(value: Any, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise WarEraApiError(f"Expected {field_name} to be numeric.") from exc
    if not math.isfinite(result):
        raise WarEraApiError(f"Expected {field_name} to be finite.")
    return result


def _dict_list(value: Any, field_name: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise WarEraApiError(f"Expected {field_name} to be a list.")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise WarEraApiError(f"Expected {field_name}[{index}] to be an object.")
    return value


def _order_list(value: Any, field_name: str) -> list[OrderLevel]:
    entries = _dict_list(value, field_name)
    levels: list[OrderLevel] = []
    for index, entry in enumerate(entries):
        entry_name = f"{field_name}[{index}]"
        if "price" not in entry:
            raise WarEraApiError(f"Expected {entry_name}.price to be present.")
        if "quantity" not in entry:
            raise WarEraApiError(f"Expected {entry_name}.quantity to be present.")
        price = _required_float(entry["price"], f"{entry_name}.price")
        quantity = _required_float(entry["quantity"], f"{entry_name}.quantity")
        if price < 0:
            raise WarEraApiError(f"Expected {entry_name}.price to be non-negative.")
        if quantity < 0:
            raise WarEraApiError(f"Expected {entry_name}.quantity to be non-negative.")
        if quantity > 0:
            levels.append(OrderLevel(price=price, quantity=quantity))
    return levels


def _transaction_list(value: Any, field_name: str) -> list[dict[str, Any]]:
    return _dict_list(value, field_name)
