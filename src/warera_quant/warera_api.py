from __future__ import annotations

import json
import math
import hashlib
import logging
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from .market_models import TransactionFacts, ScalarField, OrderEntry, OrderLevel
from dataclasses import dataclass
from typing import Any, Protocol


PRICES_ENDPOINT = "/itemTrading.getPrices"
TOP_ORDERS_ENDPOINT = "/tradingOrder.getTopOrders"
TRANSACTIONS_ENDPOINT = "/transaction.getPaginatedTransactions"
GAME_CONFIG_ENDPOINT = "/gameConfig.getGameConfig"


class JsonClient(Protocol):
    def get_json(self, endpoint: str, *, params: dict[str, Any] | None = None) -> Any: ...


class WarEraApiError(ValueError):
    """Raised when a WarEra market endpoint returns an unexpected payload."""

    def __init__(self, message: str, *, rejected: int = 0):
        super().__init__(message)
        self.rejected = rejected


@dataclass(frozen=True)
class TopOrders:
    buy_orders: list[OrderLevel]
    sell_orders: list[OrderLevel]
    entries: tuple[OrderEntry, ...] = ()


@dataclass(frozen=True)
class TransactionPage:
    items: list[TransactionFacts]
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

    def get_item_production_points(self) -> dict[str, float | None]:
        """Return official Production Points consumed to produce one unit of each tradable item."""
        response = self.client.get_json(GAME_CONFIG_ENDPOINT)
        data = _trpc_data(response)
        if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
            raise WarEraApiError("Expected game config to contain an items object.")

        production_points: dict[str, float | None] = {}
        for item_code, item in data["items"].items():
            if not isinstance(item_code, str) or not isinstance(item, dict):
                raise WarEraApiError("Expected game-config items to map item codes to objects.")
            if item.get("isTradable") is not True:
                continue
            value = item.get("productionPoints")
            if value is None:
                production_points[item_code] = None
                continue
            points = _required_float(value, f"productionPoints for {item_code}")
            if points <= 0:
                raise WarEraApiError(f"Expected productionPoints for {item_code} to be positive.")
            production_points[item_code] = points
        return production_points

    def get_top_orders(self, item_code: str, limit: int) -> TopOrders:
        response = self.client.get_json(
            TOP_ORDERS_ENDPOINT,
            params=_input_params({"itemCode": item_code, "limit": limit}),
        )
        data = _trpc_data(response)
        if not isinstance(data, dict):
            raise WarEraApiError("Expected top-orders response to contain an object.")

        buy_levels = _order_list(data.get("buyOrders"), "buyOrders")
        sell_levels = _order_list(data.get("sellOrders"), "sellOrders")
        entries = tuple(
            normalize_order(entry, item_code, side, position)
            for side, key in (("bid", "buyOrders"), ("ask", "sellOrders"))
            for position, entry in enumerate(_dict_list(data.get(key), key))
        )
        return TopOrders(
            entries=entries,
            buy_orders=sorted(
                buy_levels,
                key=lambda level: level.price,
                reverse=True,
            ),
            sell_orders=sorted(
                sell_levels,
                key=lambda level: level.price,
            ),
        )

    def get_transaction_page(self, item_code: str | None = None, *, limit: int, cursor: str | None = None,
                             transaction_type: str = "trading", previous_oldest_us: int | None = None) -> TransactionPage:
        if transaction_type not in ("trading", "itemMarket") or not 1 <= limit <= 100:
            raise WarEraApiError("Expected market stream and limit between 1 and 100.")
        payload: dict[str, Any] = {"limit": limit, "transactionType": transaction_type}
        if item_code is not None:
            payload["itemCode"] = item_code
        if cursor:
            payload["cursor"] = cursor

        response = self.client.get_json(
            TRANSACTIONS_ENDPOINT,
            params=_input_params(payload),
        )
        data = _trpc_data(response)
        if not isinstance(data, dict):
            raise WarEraApiError("Expected transaction-page response to contain an object.")

        if not isinstance(data.get("items"), list):
            raise WarEraApiError("Expected transaction-page items list.")
        next_cursor = data.get("nextCursor")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise WarEraApiError("Expected transaction-page nextCursor to be a string or null.")

        items = []
        rejected = 0
        for item in _transaction_list(data.get("items"), "items"):
            try:
                items.append(normalize_transaction(item, item_code, transaction_type=transaction_type))
            except (ValueError, TypeError):
                rejected += 1
        if rejected:
            raise WarEraApiError("Rejected transaction page; no rows committed", rejected=rejected)
        previous = previous_oldest_us
        for fact in items:
            values = fact.values
            if values["transaction_type"] != transaction_type:
                raise WarEraApiError("Transaction type does not match requested stream.", rejected=1)
            if item_code is not None and values["item_code"] != item_code:
                raise WarEraApiError("Transaction code does not match requested item.", rejected=1)
            stamp = values["created_at_us"]
            if previous is not None and stamp > previous:
                raise WarEraApiError("Transactions are not in descending timestamp order.")
            previous = stamp
        return TransactionPage(items=items, next_cursor=next_cursor)



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
        # Zero-price orders are not executable market depth and occasionally
        # appear as placeholders in the upstream response.
        if price > 0 and quantity > 0:
            levels.append(OrderLevel(price=price, quantity=quantity))
    return levels


def _transaction_list(value: Any, field_name: str) -> list[dict[str, Any]]:
    return _dict_list(value, field_name)


# Presence-aware boundary normalization. No source objects escape these models.
def decimal_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise WarEraApiError("Boolean is not a numeric market fact.")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise WarEraApiError("Expected decimal market fact.") from exc
    if not number.is_finite():
        raise WarEraApiError("Expected finite decimal market fact.")
    return format(number, "f")


def timestamp_us(value: str) -> int:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        delta = parsed.astimezone(timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)
        return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
    except (ValueError, AttributeError) as exc:
        raise WarEraApiError("Expected ISO timestamp.") from exc


def _scalar_leaves(value: Any, path: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "__v":
                continue
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            yield from _scalar_leaves(child, path + "/" + escaped)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _scalar_leaves(child, path + "/" + str(index))
    elif value is None:
        yield ScalarField(path, "null", None)
    elif isinstance(value, bool):
        yield ScalarField(path, "boolean", "true" if value else "false")
    elif isinstance(value, (int, float, Decimal)):
        yield ScalarField(path, "number", decimal_text(value))
    elif isinstance(value, str):
        yield ScalarField(path, "string", value)
    else:
        raise WarEraApiError(f"Unsupported value at {path}")


def _mapped(source, mapping, numeric=(), timestamps=()):
    result = {}
    for source_key, dest in mapping.items():
        if source_key not in source:
            continue
        value = source[source_key]
        if dest in numeric:
            value = decimal_text(value)
        elif value is not None and not isinstance(value, str):
            raise WarEraApiError(f"Expected string or null for {source_key}")
        if value is not None and dest in timestamps:
            timestamp_us(value)
        result[dest] = value
    return result


def normalize_transaction(source: dict[str, Any], item_code: str | None = None, *, transaction_type: str | None = None) -> TransactionFacts:
    mapping = {"_id": "id", "id": "id", "transaction_id": "id", "transactionId": "id",
               "createdAt": "created_at", "created_at": "created_at",
               "transactionType": "transaction_type", "transaction_type": "transaction_type", "type": "transaction_type",
               "itemCode": "item_code", "item_code": "item_code",
               "money": "money_decimal", "quantity": "quantity_decimal",
               "offerCreatedAt": "offer_created_at", "updatedAt": "updated_at"}
    values = _mapped(source, mapping, ("money_decimal", "quantity_decimal"),
                     ("created_at", "updated_at", "offer_created_at"))
    source_transaction_type = values.get("transaction_type")
    values.setdefault("item_code", item_code)
    if transaction_type is not None:
        values.setdefault("transaction_type", transaction_type)
    if not values.get("item_code") or not values.get("created_at"):
        raise WarEraApiError("Transaction requires item code and created timestamp.")
    values["created_at_us"] = timestamp_us(values["created_at"])
    values["created_at_epoch"] = values["created_at_us"] // 1000000
    if "updated_at" in values:
        values["updated_at_us"] = timestamp_us(values["updated_at"]) if values["updated_at"] else None
    for name in ("money", "quantity"):
        if name + "_decimal" in values:
            values[name + "_precision"] = ("decoded_float" if isinstance(source.get(name), float) else "decimal") if source.get(name) is not None else None
            values[name] = float(values[name + "_decimal"]) if values[name + "_decimal"] is not None else None
            if values[name] is not None and not math.isfinite(values[name]):
                raise WarEraApiError("Numeric projection out of range")
    if not values.get("id"):
        # Preserve the historical fallback ID algorithm for ID-less callers.
        dt = datetime.fromisoformat(values["created_at"].replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        stamp = dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        raw = values["item_code"] + stamp + (source_transaction_type or "")
        raw += "".join("" if values.get(k) is None else f"{values[k]:.12g}" for k in ("money", "quantity"))
        values["id"] = hashlib.sha256(raw.encode()).hexdigest()
    known = {"/" + key for key in mapping}
    participants = {}
    for side, prefix in (("buy", "buyer"), ("sell", "seller")):
        refs = {prefix + suffix: dest for suffix, dest in
                (("Id", "user_id"), ("MuId", "mu_id"), ("CountryId", "country_id"), ("PartyId", "party_id"))}
        known.update("/" + key for key in refs)
        fields = _mapped(source, refs)
        if fields:
            participants[side] = fields
    equipment, stats = {}, {}
    presence = {}
    if "item" in source:
        presence["equipment"] = source["item"] is None
        if source["item"] is not None:
            if not isinstance(source["item"], dict):
                raise WarEraApiError("Expected equipment object or null")
            item = source["item"]
            emap = {"_id": "instance_id", "code": "equipment_code", "type": "equipment_type",
                    "state": "state", "maxState": "max_state", "quantity": "item_quantity",
                    "lastAcquisitionAt": "last_acquisition_at"}
            equipment = _mapped(item, emap, ("state", "max_state", "item_quantity"), ("last_acquisition_at",))
            known.update("/item/" + key for key in emap)
            if "skills" in item:
                presence["equipment.skills"] = item["skills"] is None
                if item["skills"] is not None:
                    if not isinstance(item["skills"], dict):
                        raise WarEraApiError("Expected skill object or null")
                    stats = {key: decimal_text(value) for key, value in item["skills"].items() if key != "__v"}
                    known.update("/item/skills/" + key.replace("~", "~0").replace("/", "~1") for key in stats)
            known.add("/item/skills")
        known.add("/item")
    leaves = tuple(_scalar_leaves(source))
    extras = tuple(leaf for leaf in leaves if leaf.path not in known)
    for leaf in extras:
        logging.getLogger(__name__).warning("Unknown transaction scalar retained: %s", leaf.path)
    # Structural presence uses normalized names; scalar presence is stored beside each typed field.
    return TransactionFacts(values, participants, equipment, stats, extras, presence,
                            tuple("unknown scalar " + leaf.path for leaf in extras))


def normalize_order(source: dict[str, Any], item_code: str, side: str, position: int) -> OrderEntry:
    mapping = {"_id": "order_id", "itemCode": "item_code", "type": "source_type",
               "user": "user_id", "mu": "mu_id", "country": "country_id", "party": "party_id",
               "price": "price_decimal", "quantity": "quantity_decimal", "offerAt": "offer_at"}
    values = _mapped(source, mapping, ("price_decimal", "quantity_decimal"), ("offer_at",))
    values.setdefault("item_code", item_code)
    for key in ("price_decimal", "quantity_decimal"):
        if values.get(key) is None or Decimal(values[key]) < 0:
            raise WarEraApiError(f"Expected non-negative {key}")
    for name in ("price", "quantity"):
        values[name + "_precision"] = "decoded_float" if isinstance(source[name], float) else "decimal"
    extras = tuple(leaf for leaf in _scalar_leaves(source) if leaf.path not in {"/" + key for key in mapping})
    if values["item_code"] != item_code:
        raise WarEraApiError("Order code does not match requested item.")
    if values.get("source_type") not in (None, "buy" if side == "bid" else "sell"):
        raise WarEraApiError("Order type does not match envelope side.")
    for leaf in extras:
        logging.getLogger(__name__).warning("Unknown order scalar retained: %s", leaf.path)
    return OrderEntry(side, position, values, extras, tuple(values))
