from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from itertools import count
from typing import Any, Callable

from .api_client import WarEraApiClient


def _trpc_data(response: dict[str, Any]) -> Any:
    try:
        return response["result"]["data"]
    except (KeyError, TypeError) as exc:
        raise ValueError("Unexpected WarEra API response shape.") from exc


def _input_params(payload: dict[str, Any]) -> dict[str, str]:
    return {"input": json.dumps(payload)}


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _price_from_transaction(transaction: dict[str, Any]) -> float | None:
    money = transaction.get("money")
    quantity = transaction.get("quantity")
    try:
        quantity_float = float(quantity)
        if quantity_float <= 0:
            return None
        return float(money) / quantity_float
    except (TypeError, ValueError):
        return None


def _number(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _display_name(item_code: str) -> str:
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", item_code).replace("_", " ").replace("-", " ")
    return spaced.title()


def fetch_live_market_rows(
    client: WarEraApiClient,
    *,
    order_limit: int = 10,
    transaction_limit: int = 100,
    history_pages: int = 0,
    lookback_days: float = 1.0,
    exclude_item_codes: set[str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    def log(message: str) -> None:
        if progress:
            progress(message)

    log("Fetching current item prices...")
    prices_response = client.get_json("/itemTrading.getPrices")
    prices = _trpc_data(prices_response)
    if not isinstance(prices, dict):
        raise ValueError("Expected itemTrading.getPrices to return an item-price object.")

    excluded = {code.lower() for code in (exclude_item_codes or set())}
    if excluded:
        prices = {code: price for code, price in prices.items() if code.lower() not in excluded}

    log(f"Found {len(prices)} goods. Fetching orders and transactions...")
    rows: list[dict[str, Any]] = []
    snapshot: dict[str, Any] = {
        "lookback_days": lookback_days,
        "prices": prices_response,
        "orders": {},
        "transactions": {},
    }
    lookback_start = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    for index, (item_code, current_price) in enumerate(prices.items(), start=1):
        item_name = _display_name(item_code)
        log(f"[{index}/{len(prices)}] {item_name}: fetching top orders...")
        orders_response = client.get_json(
            "/tradingOrder.getTopOrders",
            params=_input_params({"itemCode": item_code, "limit": order_limit}),
        )
        orders = _trpc_data(orders_response)
        snapshot["orders"][item_code] = orders_response

        buy_orders = orders.get("buyOrders", []) if isinstance(orders, dict) else []
        sell_orders = orders.get("sellOrders", []) if isinstance(orders, dict) else []
        buy_prices = [price for order in buy_orders if (price := _number(order.get("price"))) is not None]
        sell_prices = [price for order in sell_orders if (price := _number(order.get("price"))) is not None]
        bid = max(buy_prices, default=None)
        ask = min(sell_prices, default=None)

        transactions: list[dict[str, Any]] = []
        cursor = None
        page_numbers = range(history_pages) if history_pages > 0 else count()
        for page_index, _ in enumerate(page_numbers, start=1):
            payload: dict[str, Any] = {
                "itemCode": item_code,
                "limit": transaction_limit,
                "transactionType": "trading",
            }
            if cursor:
                payload["cursor"] = cursor
            transactions_response = client.get_json(
                "/transaction.getPaginatedTransactions",
                params=_input_params(payload),
            )
            data = _trpc_data(transactions_response)
            page_items = data.get("items", []) if isinstance(data, dict) else []
            transactions.extend(page_items)
            snapshot["transactions"].setdefault(item_code, []).append(transactions_response)
            cursor = data.get("nextCursor") if isinstance(data, dict) else None

            newest_old_page_time = None
            if page_items:
                newest_old_page_time = _parse_time(page_items[-1].get("createdAt"))
            oldest_label = newest_old_page_time.isoformat(timespec="seconds") if newest_old_page_time else "none"
            log(
                f"[{index}/{len(prices)}] {item_name}: "
                f"transactions page {page_index}, fetched {len(transactions)} total, oldest {oldest_label}"
            )
            if not cursor or (newest_old_page_time and newest_old_page_time < lookback_start):
                break

        recent_transactions = [
            transaction
            for transaction in transactions
            if (created_at := _parse_time(transaction.get("createdAt"))) and created_at >= lookback_start
        ]
        transaction_prices = [
            price
            for transaction in recent_transactions
            if (price := _price_from_transaction(transaction)) is not None
        ]
        sorted_transactions = sorted(
            (
                (created_at, transaction)
                for transaction in recent_transactions
                if (created_at := _parse_time(transaction.get("createdAt"))) is not None
            ),
            key=lambda item: item[0],
        )

        row: dict[str, Any] = {
            "item_name": item_name,
            "item_code": item_code,
            "bid": bid,
            "ask": ask,
            "current_price": current_price,
            "trades_7d": len(recent_transactions),
            "high_7d": max(transaction_prices, default=current_price),
            "low_7d": min(transaction_prices, default=current_price),
        }
        if sorted_transactions:
            row["open_7d"] = _price_from_transaction(sorted_transactions[0][1])
            row["close_7d"] = _price_from_transaction(sorted_transactions[-1][1])
        rows.append(row)
        log(f"[{index}/{len(prices)}] {item_name}: done with {len(recent_transactions)} trades in lookback window.")

    return rows, snapshot


def rows_from_market_snapshot(snapshot: dict[str, Any], *, lookback_days: float = 1.0) -> list[dict[str, Any]]:
    prices = _trpc_data(snapshot["prices"])
    if not isinstance(prices, dict):
        raise ValueError("Expected snapshot prices to contain an item-price object.")

    rows: list[dict[str, Any]] = []
    lookback_start = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    order_snapshots = snapshot.get("orders", {})
    transaction_snapshots = snapshot.get("transactions", {})

    for item_code, current_price in prices.items():
        item_name = _display_name(item_code)
        orders_response = order_snapshots.get(item_code, {})
        orders = _trpc_data(orders_response) if orders_response else {}

        buy_orders = orders.get("buyOrders", []) if isinstance(orders, dict) else []
        sell_orders = orders.get("sellOrders", []) if isinstance(orders, dict) else []
        buy_prices = [price for order in buy_orders if (price := _number(order.get("price"))) is not None]
        sell_prices = [price for order in sell_orders if (price := _number(order.get("price"))) is not None]

        transactions: list[dict[str, Any]] = []
        for page in transaction_snapshots.get(item_code, []):
            data = _trpc_data(page)
            transactions.extend(data.get("items", []) if isinstance(data, dict) else [])

        recent_transactions = [
            transaction
            for transaction in transactions
            if (created_at := _parse_time(transaction.get("createdAt"))) and created_at >= lookback_start
        ]
        transaction_prices = [
            price
            for transaction in recent_transactions
            if (price := _price_from_transaction(transaction)) is not None
        ]
        sorted_transactions = sorted(
            (
                (created_at, transaction)
                for transaction in recent_transactions
                if (created_at := _parse_time(transaction.get("createdAt"))) is not None
            ),
            key=lambda item: item[0],
        )

        row: dict[str, Any] = {
            "item_name": item_name,
            "item_code": item_code,
            "bid": max(buy_prices, default=None),
            "ask": min(sell_prices, default=None),
            "current_price": current_price,
            "trades_7d": len(recent_transactions),
            "high_7d": max(transaction_prices, default=current_price),
            "low_7d": min(transaction_prices, default=current_price),
        }
        if sorted_transactions:
            row["open_7d"] = _price_from_transaction(sorted_transactions[0][1])
            row["close_7d"] = _price_from_transaction(sorted_transactions[-1][1])
        rows.append(row)

    return rows
