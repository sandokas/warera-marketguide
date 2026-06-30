from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LATEST_SCHEMA_VERSION = 1


class MarketStoreError(RuntimeError):
    """Raised when the market database cannot be opened or migrated safely."""


@dataclass(frozen=True)
class InsertSummary:
    inserted: int
    skipped: int
    newest_created_at: str | None
    newest_created_at_epoch: int | None
    newest_transaction_id: str | None


@dataclass(frozen=True)
class ItemSyncState:
    item_code: str
    newest_created_at: str | None
    newest_created_at_epoch: int | None
    newest_transaction_id: str | None
    last_successful_sync_at: str | None
    last_attempted_sync_at: str | None
    last_error: str | None
    pages_fetched: int
    transactions_inserted: int


@dataclass(frozen=True)
class SyncSummary:
    pages_fetched: int = 0
    transactions_inserted: int = 0
    newest_created_at: str | None = None
    newest_created_at_epoch: int | None = None
    newest_transaction_id: str | None = None
    synced_at: datetime | None = None


class MarketStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> MarketStore:
        self.initialize()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def initialize(self) -> None:
        if self.path != Path(":memory:"):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        _ensure_schema_meta(connection)
        current_version = self.schema_version()
        if current_version > LATEST_SCHEMA_VERSION:
            raise MarketStoreError(
                f"Database schema version {current_version} is newer than supported version {LATEST_SCHEMA_VERSION}."
            )

        for version in range(current_version + 1, LATEST_SCHEMA_VERSION + 1):
            migration = MIGRATIONS[version]
            with connection:
                migration(connection)
                connection.execute(
                    "insert or replace into schema_meta (key, value) values ('version', ?)",
                    (str(version),),
                )
                connection.execute(f"pragma user_version = {version}")
        if self.user_version() != self.schema_version():
            with connection:
                connection.execute(f"pragma user_version = {self.schema_version()}")

    def schema_version(self) -> int:
        connection = self._connect()
        _ensure_schema_meta(connection)
        row = connection.execute("select value from schema_meta where key = 'version'").fetchone()
        return int(row["value"]) if row else 0

    def user_version(self) -> int:
        row = self._connect().execute("pragma user_version").fetchone()
        return int(row[0])

    def get_item_state(self, item_code: str) -> ItemSyncState | None:
        row = self._connect().execute(
            """
            select item_code, newest_created_at, newest_created_at_epoch, newest_transaction_id,
                   last_successful_sync_at, last_attempted_sync_at, last_error,
                   pages_fetched, transactions_inserted
            from item_sync_state
            where item_code = ?
            """,
            (item_code,),
        ).fetchone()
        return _item_sync_state_from_row(row) if row else None

    def mark_item_sync_attempt(self, item_code: str, attempted_at: datetime | None = None) -> None:
        attempted_at_text = _format_datetime(attempted_at or _utc_now())
        with self._connect():
            self._connect().execute(
                """
                insert into item_sync_state (item_code, last_attempted_sync_at)
                values (?, ?)
                on conflict(item_code) do update set
                    last_attempted_sync_at = excluded.last_attempted_sync_at
                """,
                (item_code, attempted_at_text),
            )

    def mark_item_sync_success(self, item_code: str, summary: SyncSummary) -> None:
        synced_at = _format_datetime(summary.synced_at or _utc_now())
        with self._connect():
            self._connect().execute(
                """
                insert into item_sync_state (
                    item_code, newest_created_at, newest_created_at_epoch, newest_transaction_id,
                    last_successful_sync_at, last_attempted_sync_at, last_error,
                    pages_fetched, transactions_inserted
                )
                values (?, ?, ?, ?, ?, ?, null, ?, ?)
                on conflict(item_code) do update set
                    newest_created_at = coalesce(excluded.newest_created_at, item_sync_state.newest_created_at),
                    newest_created_at_epoch = coalesce(
                        excluded.newest_created_at_epoch,
                        item_sync_state.newest_created_at_epoch
                    ),
                    newest_transaction_id = coalesce(
                        excluded.newest_transaction_id,
                        item_sync_state.newest_transaction_id
                    ),
                    last_successful_sync_at = excluded.last_successful_sync_at,
                    last_attempted_sync_at = excluded.last_attempted_sync_at,
                    last_error = null,
                    pages_fetched = excluded.pages_fetched,
                    transactions_inserted = excluded.transactions_inserted
                """,
                (
                    item_code,
                    summary.newest_created_at,
                    summary.newest_created_at_epoch,
                    summary.newest_transaction_id,
                    synced_at,
                    synced_at,
                    summary.pages_fetched,
                    summary.transactions_inserted,
                ),
            )

    def mark_item_sync_failure(
        self,
        item_code: str,
        error: Exception | str,
        attempted_at: datetime | None = None,
    ) -> None:
        attempted_at_text = _format_datetime(attempted_at or _utc_now())
        with self._connect():
            self._connect().execute(
                """
                insert into item_sync_state (item_code, last_attempted_sync_at, last_error)
                values (?, ?, ?)
                on conflict(item_code) do update set
                    last_attempted_sync_at = excluded.last_attempted_sync_at,
                    last_error = excluded.last_error
                """,
                (item_code, attempted_at_text, str(error)),
            )

    def upsert_transactions(
        self,
        item_code: str,
        transactions: list[dict[str, Any]],
        *,
        fetched_at: datetime | None = None,
    ) -> InsertSummary:
        fetched_at_text = _format_datetime(fetched_at or _utc_now())
        rows = [_transaction_row(item_code, transaction, fetched_at_text) for transaction in transactions]
        inserted = 0
        with self._connect():
            for row in rows:
                cursor = self._connect().execute(
                    """
                    insert or ignore into transactions (
                        id, item_code, transaction_type, created_at, created_at_epoch,
                        money, quantity, unit_price, fetched_at
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"],
                        row["item_code"],
                        row["transaction_type"],
                        row["created_at"],
                        row["created_at_epoch"],
                        row["money"],
                        row["quantity"],
                        row["unit_price"],
                        row["fetched_at"],
                    ),
                )
                inserted += cursor.rowcount

        newest = max(rows, key=lambda row: (row["created_at_epoch"], row["id"]), default=None)
        return InsertSummary(
            inserted=inserted,
            skipped=len(rows) - inserted,
            newest_created_at=newest["created_at"] if newest else None,
            newest_created_at_epoch=newest["created_at_epoch"] if newest else None,
            newest_transaction_id=newest["id"] if newest else None,
        )

    def insert_price_observations(self, prices: dict[str, float], observed_at: datetime) -> None:
        observed_at_text = _format_datetime(observed_at)
        observed_at_epoch = _epoch_seconds(observed_at)
        rows = [
            (item_code, observed_at_text, observed_at_epoch, _required_float(price, "current_price"))
            for item_code, price in prices.items()
        ]
        with self._connect():
            self._connect().executemany(
                """
                insert into price_observations (
                    item_code, observed_at, observed_at_epoch, current_price
                )
                values (?, ?, ?, ?)
                """,
                rows,
            )

    def insert_order_book_observations(self, orders: dict[str, Any], observed_at: datetime) -> None:
        observed_at_text = _format_datetime(observed_at)
        observed_at_epoch = _epoch_seconds(observed_at)
        rows = [
            (
                item_code,
                observed_at_text,
                observed_at_epoch,
                observation["best_bid"],
                observation["best_ask"],
                observation["bid_depth"],
                observation["ask_depth"],
                observation["spread_abs"],
                observation["spread_pct"],
            )
            for item_code, payload in orders.items()
            for observation in [_order_book_observation(payload)]
        ]
        with self._connect():
            self._connect().executemany(
                """
                insert into order_book_observations (
                    item_code, observed_at, observed_at_epoch, best_bid, best_ask,
                    bid_depth, ask_depth, spread_abs, spread_pct
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def transactions_for_window(self, item_code: str, since_epoch: int) -> list[dict[str, Any]]:
        rows = self._connect().execute(
            """
            select id, item_code, transaction_type, created_at, created_at_epoch,
                   money, quantity, unit_price, fetched_at
            from transactions
            where item_code = ? and created_at_epoch >= ?
            order by created_at_epoch asc, id asc
            """,
            (item_code, since_epoch),
        ).fetchall()
        return [_dict_from_row(row) for row in rows]

    def price_observations_for_window(self, item_code: str, since_epoch: int) -> list[dict[str, Any]]:
        rows = self._connect().execute(
            """
            select id, item_code, observed_at, observed_at_epoch, current_price
            from price_observations
            where item_code = ? and observed_at_epoch >= ?
            order by observed_at_epoch asc, id asc
            """,
            (item_code, since_epoch),
        ).fetchall()
        return [_dict_from_row(row) for row in rows]

    def order_book_observations_for_window(self, item_code: str, since_epoch: int) -> list[dict[str, Any]]:
        rows = self._connect().execute(
            """
            select id, item_code, observed_at, observed_at_epoch, best_bid, best_ask,
                   bid_depth, ask_depth, spread_abs, spread_pct
            from order_book_observations
            where item_code = ? and observed_at_epoch >= ?
            order by observed_at_epoch asc, id asc
            """,
            (item_code, since_epoch),
        ).fetchall()
        return [_dict_from_row(row) for row in rows]

    def item_codes(self) -> list[str]:
        rows = self._connect().execute(
            """
            select item_code from transactions
            union
            select item_code from price_observations
            union
            select item_code from order_book_observations
            order by item_code
            """
        ).fetchall()
        return [row["item_code"] for row in rows]

    def latest_price_observations(self) -> dict[str, dict[str, Any]]:
        rows = self._connect().execute(
            """
            select id, item_code, observed_at, observed_at_epoch, current_price
            from price_observations
            where id in (
                select id
                from (
                    select id,
                           row_number() over (
                               partition by item_code
                               order by observed_at_epoch desc, id desc
                           ) as row_number
                    from price_observations
                )
                where row_number = 1
            )
            """
        ).fetchall()
        return {row["item_code"]: _dict_from_row(row) for row in rows}

    def latest_order_book_observations(self) -> dict[str, dict[str, Any]]:
        rows = self._connect().execute(
            """
            select id, item_code, observed_at, observed_at_epoch, best_bid, best_ask,
                   bid_depth, ask_depth, spread_abs, spread_pct
            from order_book_observations
            where id in (
                select id
                from (
                    select id,
                           row_number() over (
                               partition by item_code
                               order by observed_at_epoch desc, id desc
                           ) as row_number
                    from order_book_observations
                )
                where row_number = 1
            )
            """
        ).fetchall()
        return {row["item_code"]: _dict_from_row(row) for row in rows}

    def table_names(self) -> set[str]:
        rows = self._connect().execute(
            "select name from sqlite_master where type = 'table' and name not like 'sqlite_%'"
        ).fetchall()
        return {row["name"] for row in rows}

    def _connect(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connection = sqlite3.connect(self.path)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("pragma foreign_keys = on")
        return self._connection


def migrate_to_v1(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        create table if not exists transactions (
            id text primary key,
            item_code text not null,
            transaction_type text,
            created_at text not null,
            created_at_epoch integer not null,
            money real,
            quantity real,
            unit_price real,
            fetched_at text not null
        );

        create index if not exists idx_transactions_item_created
            on transactions (item_code, created_at_epoch desc, id);

        create table if not exists price_observations (
            id integer primary key autoincrement,
            item_code text not null,
            observed_at text not null,
            observed_at_epoch integer not null,
            current_price real not null
        );

        create index if not exists idx_price_observations_item_time
            on price_observations (item_code, observed_at_epoch desc);

        create table if not exists order_book_observations (
            id integer primary key autoincrement,
            item_code text not null,
            observed_at text not null,
            observed_at_epoch integer not null,
            best_bid real,
            best_ask real,
            bid_depth real,
            ask_depth real,
            spread_abs real,
            spread_pct real
        );

        create index if not exists idx_order_book_observations_item_time
            on order_book_observations (item_code, observed_at_epoch desc);

        create table if not exists item_sync_state (
            item_code text primary key,
            newest_created_at text,
            newest_created_at_epoch integer,
            newest_transaction_id text,
            last_successful_sync_at text,
            last_attempted_sync_at text,
            last_error text,
            pages_fetched integer not null default 0,
            transactions_inserted integer not null default 0
        );
        """
    )


MIGRATIONS = {
    1: migrate_to_v1,
}


def _ensure_schema_meta(connection: sqlite3.Connection) -> None:
    with connection:
        connection.execute(
            """
            create table if not exists schema_meta (
                key text primary key,
                value text not null
            )
            """
        )


def _transaction_row(item_code: str, transaction: dict[str, Any], fetched_at: str) -> dict[str, Any]:
    created_at = _required_string(
        _first_present(transaction, "created_at", "createdAt"),
        "created_at",
    )
    created_at_epoch = _epoch_seconds(_parse_datetime(created_at, "created_at"))
    transaction_type = _optional_string(_first_present(transaction, "transaction_type", "transactionType", "type"))
    money = _optional_float(transaction.get("money"))
    quantity = _optional_float(transaction.get("quantity"))
    unit_price = (money / quantity) if money is not None and quantity and quantity > 0 else None
    transaction_id = _optional_string(_first_present(transaction, "id", "_id", "transaction_id", "transactionId"))
    if transaction_id is None:
        transaction_id = _derive_transaction_id(item_code, created_at, transaction_type, money, quantity)

    return {
        "id": transaction_id,
        "item_code": item_code,
        "transaction_type": transaction_type,
        "created_at": _format_datetime(_parse_datetime(created_at, "created_at")),
        "created_at_epoch": created_at_epoch,
        "money": money,
        "quantity": quantity,
        "unit_price": unit_price,
        "fetched_at": fetched_at,
    }


def _derive_transaction_id(
    item_code: str,
    created_at: str,
    transaction_type: str | None,
    money: float | None,
    quantity: float | None,
) -> str:
    raw = "".join(
        (
            item_code,
            _format_datetime(_parse_datetime(created_at, "created_at")),
            transaction_type or "",
            _number_for_hash(money),
            _number_for_hash(quantity),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _order_book_observation(payload: Any) -> dict[str, float | None]:
    buy_orders = _orders_from_payload(payload, "buy_orders", "buyOrders", "bids")
    sell_orders = _orders_from_payload(payload, "sell_orders", "sellOrders", "asks")
    bid_prices = [_optional_float(order.get("price")) for order in buy_orders]
    ask_prices = [_optional_float(order.get("price")) for order in sell_orders]
    quantities_for_bids = [_optional_float(order.get("quantity")) for order in buy_orders]
    quantities_for_asks = [_optional_float(order.get("quantity")) for order in sell_orders]

    best_bid = max((price for price in bid_prices if price is not None), default=None)
    best_ask = min((price for price in ask_prices if price is not None), default=None)
    bid_depth = sum(quantity for quantity in quantities_for_bids if quantity is not None)
    ask_depth = sum(quantity for quantity in quantities_for_asks if quantity is not None)
    spread_abs = best_ask - best_bid if best_bid is not None and best_ask is not None else None
    midpoint = (best_bid + best_ask) / 2 if best_bid is not None and best_ask is not None else None
    spread_pct = (spread_abs / midpoint * 100) if spread_abs is not None and midpoint and midpoint > 0 else None

    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "bid_depth": bid_depth,
        "ask_depth": ask_depth,
        "spread_abs": spread_abs,
        "spread_pct": spread_pct,
    }


def _orders_from_payload(payload: Any, snake_key: str, camel_key: str, compact_key: str) -> list[dict[str, Any]]:
    if hasattr(payload, snake_key):
        value = getattr(payload, snake_key)
    elif isinstance(payload, dict):
        value = payload.get(snake_key, payload.get(camel_key, payload.get(compact_key, [])))
    else:
        value = []
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(order, dict) for order in value):
        raise ValueError(f"Expected {snake_key} to be a list of order dictionaries.")
    return value


def _item_sync_state_from_row(row: sqlite3.Row) -> ItemSyncState:
    return ItemSyncState(
        item_code=row["item_code"],
        newest_created_at=row["newest_created_at"],
        newest_created_at_epoch=row["newest_created_at_epoch"],
        newest_transaction_id=row["newest_transaction_id"],
        last_successful_sync_at=row["last_successful_sync_at"],
        last_attempted_sync_at=row["last_attempted_sync_at"],
        last_error=row["last_error"],
        pages_fetched=row["pages_fetched"],
        transactions_inserted=row["transactions_inserted"],
    )


def _dict_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Expected {field_name} to be a non-empty string.")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _required_float(value: Any, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Expected {field_name} to be numeric.") from exc


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Expected numeric value.") from exc


def _parse_datetime(value: str, field_name: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"Expected {field_name} to be an ISO datetime.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _epoch_seconds(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.astimezone(timezone.utc).timestamp())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _number_for_hash(value: float | None) -> str:
    return "" if value is None else f"{value:.12g}"
