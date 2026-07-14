from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LATEST_SCHEMA_VERSION = 3


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

    def upsert_item_production_points(
        self,
        values: dict[str, float | None],
        observed_at: datetime,
    ) -> None:
        observed_at_text = _format_datetime(observed_at)
        rows = [
            (
                item_code,
                None if points is None else _required_positive_float(points, "production_points"),
                observed_at_text,
            )
            for item_code, points in values.items()
        ]
        with self._connect():
            self._connect().executemany(
                """
                insert into item_production_config (item_code, production_points, observed_at)
                values (?, ?, ?)
                on conflict(item_code) do update set
                    production_points = excluded.production_points,
                    observed_at = excluded.observed_at
                """,
                rows,
            )

    def item_production_points(self) -> dict[str, float | None]:
        rows = self._connect().execute(
            "select item_code, production_points from item_production_config order by item_code"
        ).fetchall()
        return {row["item_code"]: row["production_points"] for row in rows}

    def insert_order_book_observations(self, orders: dict[str, Any], observed_at: datetime) -> None:
        observed_at_text = _format_datetime(observed_at)
        observed_at_epoch = _epoch_seconds(observed_at)
        snapshots = [
            (item_code, *_normalized_order_book(payload))
            for item_code, payload in orders.items()
        ]
        connection = self._connect()
        with connection:
            for item_code, bids, asks, observation in snapshots:
                cursor = connection.execute(
                    """
                    insert into order_book_observations (
                        item_code, observed_at, observed_at_epoch, best_bid, best_ask,
                        bid_depth, ask_depth, spread_abs, spread_pct
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
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
                    ),
                )
                observation_id = cursor.lastrowid
                level_rows = [
                    (observation_id, side, position, level.price, level.quantity)
                    for side, levels in (("bid", bids), ("ask", asks))
                    for position, level in enumerate(levels)
                ]
                connection.executemany(
                    """
                    insert into order_book_levels (
                        observation_id, side, level_position, price, quantity
                    ) values (?, ?, ?, ?, ?)
                    """,
                    level_rows,
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

    def order_book_history_with_levels(
        self, item_code: str, since_epoch: int = 0
    ) -> list[dict[str, Any]]:
        """Return chronological snapshots and their normalized levels in two bounded queries."""
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
        observations = [_dict_from_row(row) for row in rows]
        if not observations:
            return []

        observation_ids = [row["id"] for row in observations]
        level_rows = self._connect().execute(
            """
            select levels.observation_id, levels.side, levels.level_position,
                   levels.price, levels.quantity
            from order_book_levels as levels
            join order_book_observations as observations
              on observations.id = levels.observation_id
            where observations.item_code = ? and observations.observed_at_epoch >= ?
              and levels.price > 0 and levels.quantity > 0
            order by levels.observation_id,
                     case levels.side when 'bid' then 0 else 1 end,
                     levels.level_position
            """,
            (item_code, since_epoch),
        ).fetchall()
        levels_by_observation: dict[int, list[dict[str, Any]]] = {
            observation_id: [] for observation_id in observation_ids
        }
        for level_row in level_rows:
            level = _dict_from_row(level_row)
            levels_by_observation[level.pop("observation_id")].append(level)

        for observation in observations:
            observation["observation_id"] = observation.pop("id")
            levels = levels_by_observation[observation["observation_id"]]
            observation["bids"] = [
                {key: value for key, value in level.items() if key != "side"}
                for level in levels
                if level["side"] == "bid"
            ]
            observation["asks"] = [
                {key: value for key, value in level.items() if key != "side"}
                for level in levels
                if level["side"] == "ask"
            ]
            observation["levels_available"] = bool(levels)
        return observations

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

    def order_book_levels(self, observation_id: int) -> list[dict[str, Any]]:
        rows = self._connect().execute(
            """
            select side, level_position, price, quantity
            from order_book_levels
            where observation_id = ?
              and price > 0 and quantity > 0
            order by case side when 'bid' then 0 else 1 end, level_position
            """,
            (observation_id,),
        ).fetchall()
        return [_dict_from_row(row) for row in rows]

    def latest_order_book_with_levels(self, item_code: str) -> dict[str, Any] | None:
        row = self._connect().execute(
            """
            select id, item_code, observed_at, observed_at_epoch, best_bid, best_ask,
                   bid_depth, ask_depth, spread_abs, spread_pct
            from order_book_observations
            where item_code = ?
            order by observed_at_epoch desc, id desc
            limit 1
            """,
            (item_code,),
        ).fetchone()
        if row is None:
            return None
        result = _dict_from_row(row)
        result["observation_id"] = result.pop("id")
        levels = self.order_book_levels(result["observation_id"])
        result["bids"] = [level for level in levels if level["side"] == "bid"]
        result["asks"] = [level for level in levels if level["side"] == "ask"]
        for level in result["bids"] + result["asks"]:
            level.pop("side")
        result["levels_available"] = bool(levels)
        return result

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


def migrate_to_v2(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        create table order_book_levels (
            id integer primary key autoincrement,
            observation_id integer not null,
            side text not null check (side in ('bid', 'ask')),
            level_position integer not null check (level_position >= 0),
            price real not null check (price >= 0),
            quantity real not null check (quantity > 0),
            foreign key (observation_id) references order_book_observations(id) on delete cascade,
            unique (observation_id, side, level_position)
        );

        create index idx_order_book_levels_observation_side
            on order_book_levels (observation_id, side, level_position);
        """
    )


def migrate_to_v3(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        create table item_production_config (
            item_code text primary key,
            production_points real check (production_points > 0),
            observed_at text not null
        );
        """
    )


MIGRATIONS = {
    1: migrate_to_v1,
    2: migrate_to_v2,
    3: migrate_to_v3,
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


def _normalized_order_book(payload: Any) -> tuple[list[Any], list[Any], dict[str, float | None]]:
    try:
        buy_orders = list(payload.buy_orders)
        sell_orders = list(payload.sell_orders)
    except (AttributeError, TypeError) as exc:
        raise ValueError("Expected normalized order data with buy_orders and sell_orders.") from exc
    bids = _aggregate_levels(buy_orders, reverse=True)
    asks = _aggregate_levels(sell_orders, reverse=False)
    best_bid = bids[0].price if bids else None
    best_ask = asks[0].price if asks else None
    bid_depth = sum(level.quantity for level in bids)
    ask_depth = sum(level.quantity for level in asks)
    spread_abs = best_ask - best_bid if best_bid is not None and best_ask is not None else None
    midpoint = (best_bid + best_ask) / 2 if best_bid is not None and best_ask is not None else None
    spread_pct = (spread_abs / midpoint * 100) if spread_abs is not None and midpoint and midpoint > 0 else None

    observation = {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "bid_depth": bid_depth,
        "ask_depth": ask_depth,
        "spread_abs": spread_abs,
        "spread_pct": spread_pct,
    }
    return bids, asks, observation


def _aggregate_levels(levels: list[Any], *, reverse: bool) -> list[Any]:
    by_price: dict[float, float] = {}
    level_type = None
    for level in levels:
        level_type = type(level)
        if level.price < 0 or level.quantity <= 0:
            raise ValueError("Order-book prices must be non-negative and quantities must be positive.")
        if level.price == 0:
            continue
        by_price[level.price] = by_price.get(level.price, 0.0) + level.quantity
    if level_type is None:
        return []
    return [
        level_type(price=price, quantity=by_price[price])
        for price in sorted(by_price, reverse=reverse)
    ]


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


def _required_positive_float(value: Any, field_name: str) -> float:
    result = _required_float(value, field_name)
    if result <= 0:
        raise ValueError(f"Expected {field_name} to be positive.")
    return result


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
