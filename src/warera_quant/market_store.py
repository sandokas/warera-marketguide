from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from collections.abc import Iterable

from .market_models import TransactionFacts, StreamProgress, StreamCheckpoint, EnrichmentCoverage, OrderLevel, RejectedTransactionPage


LATEST_SCHEMA_VERSION = 6


class MarketStoreError(RuntimeError):
    """Raised when the market database cannot be opened or migrated safely."""


@dataclass(frozen=True)
class InsertSummary:
    inserted: int
    skipped: int
    newest_created_at: str | None
    newest_created_at_epoch: int | None
    newest_transaction_id: str | None
    enriched: int = 0
    unchanged: int = 0
    rejected: int = 0


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


@dataclass(frozen=True)
class HousekeepingSummary:
    cutoff_at: str
    transactions_deleted: int
    price_observations_deleted: int
    order_book_observations_deleted: int
    vacuumed: bool

    @property
    def rows_deleted(self) -> int:
        return (
            self.transactions_deleted
            + self.price_observations_deleted
            + self.order_book_observations_deleted
        )


@dataclass(frozen=True)
class MarketSyncMetadata:
    synced_at: str
    status: str


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
        # DDL and BOTH version markers share one explicit transaction, across all upgrades.
        # executescript is forbidden here: Python's sqlite driver commits before running it.
        try:
            connection.execute("begin immediate")
            connection.execute("create table if not exists schema_meta (key text primary key, value text not null)")
            row = connection.execute("select value from schema_meta where key = 'version'").fetchone()
            current_version = int(row[0]) if row else 0
            user_version = int(connection.execute("pragma user_version").fetchone()[0])
            if max(current_version, user_version) > LATEST_SCHEMA_VERSION:
                raise MarketStoreError("Database schema version is newer than supported version.")
            if user_version not in (0, current_version):
                raise MarketStoreError("Database version markers disagree.")
            for version in range(current_version + 1, LATEST_SCHEMA_VERSION + 1):
                MIGRATIONS[version](connection)
                connection.execute("insert or replace into schema_meta values ('version', ?)", (str(version),))
                connection.execute(f"pragma user_version = {version}")
            connection.execute(f"pragma user_version = {LATEST_SCHEMA_VERSION}")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        _backfill_market_sync_metadata(connection)

    def schema_version(self) -> int:
        connection = self._connect()
        row = connection.execute("select value from schema_meta where key = 'version'").fetchone()
        return int(row["value"]) if row else 0

    def database_inventory(self) -> dict[str, Any]:
        """Inspect an existing database without initialization or schema mutation."""
        connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            connection.execute("begin")
            tables = sorted(row[0] for row in connection.execute(
                "select name from sqlite_master where type='table' and name not like 'sqlite_%'"))
            counts = {table: connection.execute(
                'select count(*) from "' + table.replace('"', '""') + '"').fetchone()[0]
                for table in tables}
            return {
                "schema_version": int(connection.execute(
                    "select value from schema_meta where key='version'").fetchone()[0]),
                "user_version": connection.execute("pragma user_version").fetchone()[0],
                "integrity": [row[0] for row in connection.execute("pragma integrity_check")],
                "counts": counts,
                "transactions": [dict(zip(("stream", "count", "oldest", "newest"), row))
                    for row in connection.execute("select transaction_type,count(*),min(created_at),max(created_at) from transactions group by transaction_type")],
            }
        finally:
            connection.close()

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

    def record_market_sync(self, synced_at: datetime, *, status: str) -> None:
        if status not in {"complete", "partial"}:
            raise ValueError("market sync status must be 'complete' or 'partial'.")
        with self._connect():
            self._connect().executemany(
                "insert or replace into schema_meta (key, value) values (?, ?)",
                (
                    ("last_market_sync_at", _format_datetime(synced_at)),
                    ("last_market_sync_status", status),
                ),
            )

    def market_sync_metadata(self) -> MarketSyncMetadata | None:
        connection = self._connect()
        _backfill_market_sync_metadata(connection)
        rows = connection.execute(
            "select key, value from schema_meta where key in (?, ?)",
            ("last_market_sync_at", "last_market_sync_status"),
        ).fetchall()
        values = {row["key"]: row["value"] for row in rows}
        synced_at = values.get("last_market_sync_at")
        if synced_at is None:
            return None
        return MarketSyncMetadata(
            synced_at=synced_at,
            status=values.get("last_market_sync_status", "inferred"),
        )

    def upsert_transactions(
        self,
        item_code: str,
        transactions: Iterable[TransactionFacts | dict[str, Any]],
        *,
        fetched_at: datetime | None = None,
    ) -> InsertSummary:
        # Compatibility adapter for existing callers. Source parsing stays at the API boundary.
        from .warera_api import normalize_transaction
        facts = []
        rejected = 0
        for transaction in transactions:
            try:
                facts.append(transaction if isinstance(transaction, TransactionFacts)
                             else normalize_transaction(transaction, item_code, transaction_type="trading"))
            except (ValueError, TypeError):
                rejected += 1
        return self.ingest_transactions(facts, fetched_at=fetched_at, rejected=rejected)

    def ingest_transactions(
        self, transactions: Iterable[TransactionFacts], *, fetched_at: datetime | None = None,
        progress: StreamProgress | None = None, coverage: EnrichmentCoverage | None = None,
        rejected: int = 0, strict: bool = False, checkpoint: StreamCheckpoint | None = None,
        exhaustion_observed_at: str | None = None,
    ) -> InsertSummary:
        """Commit a normalized page and progress together; isolate malformed records with savepoints.

        Conflicts require a strictly newer source updated_at. Missing fields never delete facts.
        Null is retained as presence, but cannot erase a known value without that newer revision.
        """
        connection = self._connect()
        fetched = _format_datetime(fetched_at or _utc_now())
        counts = {"inserted": 0, "enriched": 0, "unchanged": 0}
        accepted = []
        with connection:
            connection.execute("savepoint market_page")
            try:
                for fact in transactions:
                    connection.execute("savepoint market_record")
                    try:
                        outcome = self._merge_transaction(fact, fetched)
                    except (ValueError, TypeError, sqlite3.IntegrityError, OverflowError):
                        connection.execute("rollback to market_record")
                        rejected += 1
                    else:
                        counts[outcome] += 1
                        accepted.append(fact.values)
                    finally:
                        connection.execute("release market_record")
                if strict and rejected:
                    raise RejectedTransactionPage(rejected)
                if progress is not None:
                    progress = replace(progress, **{key: getattr(progress, key) + value for key, value in counts.items()},
                                       rejected=progress.rejected + rejected)
                    if rejected and progress.status == "exhausted":
                        raise ValueError("Rejected rows cannot establish exhaustion")
                    self._write_progress(progress)
                if checkpoint is not None:
                    if (progress is None or checkpoint.stream != progress.stream
                            or checkpoint.pages != progress.pages
                            or checkpoint.scan_anchor != progress.scan_anchor):
                        raise ValueError("Checkpoint must describe the same committed page")
                    self._write_checkpoint(checkpoint)
                if coverage is not None:
                    if rejected:
                        raise ValueError("Rejected rows cannot establish enrichment coverage")
                    self._write_coverage(coverage)
                    if coverage.completion_reason == "api-exhausted":
                        exhaustion_observed_at = coverage.observed_at
                if exhaustion_observed_at is not None:
                    if progress is None or rejected:
                        raise ValueError("Exhaustion requires accepted page progress")
                    connection.execute("insert or replace into schema_meta (key,value) values (?,?)",
                                       (f"market_exhaustion_{progress.stream}", exhaustion_observed_at))
                connection.execute("release market_page")
            except BaseException:
                connection.execute("rollback to market_page")
                connection.execute("release market_page")
                raise
        newest = max(accepted, key=lambda v: (v["created_at_us"], v["id"]), default=None)
        return InsertSummary(counts["inserted"], counts["enriched"] + counts["unchanged"] + rejected,
                             newest["created_at"] if newest else None,
                             newest["created_at_epoch"] if newest else None,
                             newest["id"] if newest else None,
                             counts["enriched"], counts["unchanged"], rejected)

    def _merge_transaction(self, fact: TransactionFacts, fetched: str) -> str:
        c = self._connect()
        v = fact.values
        transaction_id = v["id"]
        if not isinstance(transaction_id, str) or not transaction_id:
            raise ValueError("Transaction ID must be nonempty text")
        for number in ("money_decimal", "quantity_decimal"):
            if number in v:
                _validate_decimal(v[number])
        old = c.execute("select * from transactions where id = ?", (transaction_id,)).fetchone()
        inserted = old is None
        if not inserted and (old["item_code"] != v["item_code"] or
                             (old["transaction_type"] and v.get("transaction_type") and
                              old["transaction_type"] != v["transaction_type"])):
            raise ValueError("Conflicting transaction identity")
        if inserted:
            c.execute("insert into transactions (id,item_code,created_at,created_at_epoch,fetched_at,first_fetched_at,last_fetched_at) values (?,?,?,?,?,?,?)",
                      (transaction_id, v["item_code"], v["created_at"], v["created_at_epoch"], fetched, fetched, fetched))
        revision = v.get("updated_at_us")
        if old:
            for number in ("money", "quantity"):
                if (old[number + "_decimal"] is None and old[number] is not None and
                    v.get(number) is not None and old[number] != v[number] and
                    not (revision is not None and (old["updated_at_us"] is None or revision > old["updated_at_us"]))):
                    raise ValueError("Conflicting legacy numeric projection without a newer source revision")
        changed = False

        def merge(table, keys, field, value, path):
            nonlocal changed
            where = " and ".join(k + " = ?" for k in keys)
            current = c.execute(f"select {field} from {table} where {where}", tuple(keys.values())).fetchone()
            state = c.execute("select is_null, source_updated_us from transaction_field_state where transaction_id=? and field_path=?",
                              (transaction_id, path)).fetchone()
            prior_revision = state["source_updated_us"] if state else (old["updated_at_us"] if old else None)
            newer = revision is not None and (prior_revision is None or revision > prior_revision)
            existing = current[0] if current else None
            # Explicit null from a newer observation also protects against older non-null replay.
            allowed = newer or (existing is None and (state is None or prior_revision is None))
            if current is None:
                columns = list(keys) + [field]
                c.execute(f"insert into {table} ({','.join(columns)}) values ({','.join('?' for _ in columns)})", (*keys.values(), value))
            elif existing != value and allowed:
                c.execute(f"update {table} set {field}=? where {where}", (value, *keys.values()))
            elif existing != value:
                return
            if state is None or existing != value or newer:
                c.execute("insert into transaction_field_state values (?,?,?,?) on conflict(transaction_id,field_path) do update set is_null=excluded.is_null,source_updated_us=excluded.source_updated_us",
                          (transaction_id, path, int(value is None), revision))
                changed = True

        allowed_columns = {"transaction_type", "created_at", "created_at_epoch", "created_at_us", "money", "quantity",
                           "money_decimal", "quantity_decimal", "money_precision", "quantity_precision", "offer_created_at", "updated_at", "updated_at_us"}
        for field, value in v.items():
            if field in allowed_columns:
                merge("transactions", {"id": transaction_id}, field, value, field)
        for side, fields in fact.participants.items():
            for field, value in fields.items():
                if field not in {"user_id", "mu_id", "country_id", "party_id"}:
                    raise ValueError("Unknown normalized participant column")
                merge("transaction_participants", {"transaction_id": transaction_id, "side": side}, field, value, side + "." + field)
        for field, value in fact.equipment.items():
            if field not in {"instance_id", "equipment_code", "equipment_type", "state", "max_state", "item_quantity", "last_acquisition_at"}:
                raise ValueError("Unknown normalized equipment column")
            merge("transaction_equipment", {"transaction_id": transaction_id}, field, value, "equipment." + field)
        if fact.stats and not c.execute("select 1 from transaction_equipment where transaction_id=?", (transaction_id,)).fetchone():
            c.execute("insert or ignore into transaction_equipment (transaction_id) values (?)", (transaction_id,))
        for skill, value in fact.stats.items():
            _validate_decimal(value)
            merge("transaction_equipment_stats", {"transaction_id": transaction_id, "skill_code": skill}, "value_decimal", value, "skill." + skill)
        for path, is_null in fact.presence.items():
            state = c.execute("select is_null,source_updated_us from transaction_field_state where transaction_id=? and field_path=?", (transaction_id, path)).fetchone()
            if state is None or (revision is not None and (state[1] is None or revision > state[1])):
                c.execute("insert into transaction_field_state values (?,?,?,?) on conflict(transaction_id,field_path) do update set is_null=excluded.is_null,source_updated_us=excluded.source_updated_us", (transaction_id, path, int(is_null), revision))
                changed = changed or state is None or state[0] != int(is_null)
        for extra in fact.extras:
            _validate_scalar(extra.value_type, extra.value)
            keys = {"transaction_id": transaction_id, "field_path": extra.path}
            prior = c.execute("select value_type,scalar_value from transaction_extra_fields where transaction_id=? and field_path=?", tuple(keys.values())).fetchone()
            state = c.execute("select source_updated_us from transaction_field_state where transaction_id=? and field_path=?", (transaction_id, "extra:" + extra.path)).fetchone()
            newer = revision is not None and (state is None or state[0] is None or revision > state[0])
            if prior is None or newer:
                if prior is None or tuple(prior) != (extra.value_type, extra.value):
                    changed = True
                c.execute("insert into transaction_extra_fields values (?,?,?,?) on conflict(transaction_id,field_path) do update set value_type=excluded.value_type,scalar_value=excluded.scalar_value",
                          (transaction_id, extra.path, extra.value_type, extra.value))
                c.execute("insert into transaction_field_state values (?,?,?,?) on conflict(transaction_id,field_path) do update set is_null=excluded.is_null,source_updated_us=excluded.source_updated_us",
                          (transaction_id, "extra:" + extra.path, int(extra.value is None), revision))
        if old and old["normalization_version"] < fact.normalization_version:
            changed = True
        if not inserted and not changed:
            return "unchanged"
        latest_fetch = fetched
        if old and old["last_fetched_at"] and _parse_datetime(old["last_fetched_at"], "last_fetched_at") > _parse_datetime(fetched, "fetched_at"):
            latest_fetch = old["last_fetched_at"]
        c.execute("update transactions set unit_price=case when quantity>0 then money/quantity else null end, first_fetched_at=coalesce(first_fetched_at,fetched_at), last_fetched_at=?, normalization_version=max(normalization_version,?), normalization_status=case when exists(select 1 from transaction_extra_fields where transaction_id=?) then 'extensions' else 'normalized' end where id=?",
                  (latest_fetch, fact.normalization_version, transaction_id, transaction_id))
        return "inserted" if inserted else "enriched" if changed else "unchanged"

    def transaction_details(self, transaction_id: str) -> dict[str, Any] | None:
        c = self._connect()
        row = c.execute("select * from transactions where id=?", (transaction_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key, table in (("participants", "transaction_participants"), ("equipment", "transaction_equipment"),
                           ("stats", "transaction_equipment_stats"), ("extras", "transaction_extra_fields"),
                           ("presence", "transaction_field_state")):
            result[key] = [dict(r) for r in c.execute(f"select * from {table} where transaction_id=?", (transaction_id,))]
        return result

    def participant_history_query(self, start: datetime, end: datetime) -> tuple[str, tuple]:
        """Source query shared by streaming and EXPLAIN; no ownership rules in SQL.

        Window references select candidates, including ambiguous references. UNION
        deduplicates history IDs before child joins. Existing reference indexes
        avoid one history scan per entity. Epoch bounds are coarse index bounds;
        microsecond predicates enforce the exact half-open window.
        """
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise ValueError("Expected aware increasing participant window")
        columns = ("user_id", "mu_id", "country_id", "party_id")
        candidates = " union ".join(
            f"select p.transaction_id from transaction_participants p join "
            f"(select distinct {column} from active where {column} is not null) a "
            f"on p.{column}=a.{column}" for column in columns)
        query = f"""
            with window_ids as materialized (
                select id from transactions
                where transaction_type in ('trading','itemMarket')
                  and created_at_epoch >= ? and created_at_epoch <= ?
                  and coalesce(created_at_us,source_timestamp_us(created_at)) >= ?
                  and coalesce(created_at_us,source_timestamp_us(created_at)) < ?
            ), active as materialized (
                select p.* from transaction_participants p join window_ids w
                on w.id=p.transaction_id
            ), history_ids as ({candidates} union select id from window_ids)
            select t.* from history_ids h cross join transactions t on t.id=h.transaction_id
            where t.transaction_type in ('trading','itemMarket')
              and coalesce(t.created_at_us,source_timestamp_us(t.created_at)) < ?
            order by coalesce(t.created_at_us,source_timestamp_us(t.created_at)),t.id
        """
        return query, (math.floor(start.timestamp()), math.floor(end.timestamp()),
                       _datetime_us(start), _datetime_us(end), _datetime_us(end))

    def participant_query_plan(self, start: datetime, end: datetime) -> list[str]:
        query, parameters = self.participant_history_query(start, end)
        return [row[3] for row in self._connect().execute("explain query plan " + query, parameters)]

    def iter_participant_history(self, start: datetime, end: datetime, *, batch_size: int = 500):
        """Yield chronological normalized source batches, bounded to 500 parents.

        One ordered history cursor plus four child reads per batch, never per row.
        SQLite may spill its candidate deduplication/order to temporary storage.
        Callers should finish iteration before writing through this connection.
        """
        if not 1 <= batch_size <= 500:
            raise ValueError("batch_size must be between 1 and 500")
        query, parameters = self.participant_history_query(start, end)
        yield from self._iter_source_query(query, parameters, batch_size)

    def iter_equipment_sales(self, start: datetime, end: datetime, *, batch_size: int = 500):
        """Window-only equipment export, independent of commodity report discovery."""
        _, parameters = self.participant_history_query(start, end)
        if not 1 <= batch_size <= 500:
            raise ValueError("batch_size must be between 1 and 500")
        query = """select * from transactions where transaction_type='itemMarket'
            and created_at_epoch >= ? and created_at_epoch <= ?
            and coalesce(created_at_us,source_timestamp_us(created_at)) >= ?
            and coalesce(created_at_us,source_timestamp_us(created_at)) < ?
            order by coalesce(created_at_us,source_timestamp_us(created_at)),id"""
        yield from self._iter_source_query(query, parameters[:4], batch_size)

    def _iter_source_query(self, query, parameters, batch_size):
        cursor = self._connect().execute(query, parameters)
        try:
            while rows := cursor.fetchmany(batch_size):
                batch = {row["id"]: dict(row) for row in rows}
                placeholders = ",".join("?" for _ in batch)
                for key, table in (("participants", "transaction_participants"),
                                   ("equipment", "transaction_equipment"),
                                   ("stats", "transaction_equipment_stats"),
                                   ("presence", "transaction_field_state")):
                    for row in batch.values():
                        row[key] = []
                    for child in self._connect().execute(
                            f"select * from {table} where transaction_id in ({placeholders})", tuple(batch)):
                        batch[child["transaction_id"]][key].append(dict(child))
                yield from batch.values()
        finally:
            cursor.close()

    def participant_names(self, keys: Iterable[tuple[str, str]]) -> dict[tuple[str, str], dict]:
        """Bounded cache lookups; missing names never trigger a network request."""
        keys = iter(keys)
        result = {}
        from itertools import islice
        while batch := list(islice(keys, 250)):
            predicate = " or ".join("(entity_kind=? and entity_id=?)" for _ in batch)
            for row in self._connect().execute(
                    "select * from market_entities where " + predicate,
                    tuple(value for key in batch for value in key)):
                result[(row["entity_kind"], row["entity_id"])] = dict(row)
        return result

    def _write_progress(self, progress: StreamProgress) -> None:
        if progress.pages == 0:
            self._clear_checkpoint(progress.stream)
            self._connect().execute("delete from schema_meta where key in (?,?)",
                                    (f"market_exhaustion_{progress.stream}", f"market_elapsed_{progress.stream}"))
        values = asdict(progress)
        columns = list(values)
        self._connect().execute(f"insert into market_ingestion_state ({','.join(columns)}) values ({','.join('?' for _ in columns)}) on conflict(stream) do update set " +
                                ','.join(f"{k}=excluded.{k}" for k in columns if k != "stream"), tuple(values.values()))

    def record_stream_progress(self, progress: StreamProgress) -> None:
        with self._connect():
            self._write_progress(progress)

    def _clear_checkpoint(self, stream: str) -> None:
        self._connect().execute("delete from schema_meta where key glob ?", (f"market_resume_{stream}_*",))

    def _write_checkpoint(self, checkpoint: StreamCheckpoint) -> None:
        if (checkpoint.stream not in ("trading", "itemMarket")
                or checkpoint.phase not in ("history", "history-complete")
                or checkpoint.normalization_version != 1 or checkpoint.page_size != 100
                or checkpoint.pages < 1
                or (checkpoint.phase == "history" and not checkpoint.next_cursor)
                or (checkpoint.phase == "history-complete" and checkpoint.next_cursor is not None)):
            raise ValueError("Invalid market checkpoint")
        self._clear_checkpoint(checkpoint.stream)
        self._connect().executemany("insert into schema_meta (key,value) values (?,?)",
            [(f"market_resume_{checkpoint.stream}_{key}", str(value))
             for key, value in asdict(checkpoint).items() if value is not None])

    def stream_checkpoint(self, stream: str) -> StreamCheckpoint | None:
        """Private operational continuation; callers must never log opaque cursors."""
        prefix = f"market_resume_{stream}_"
        fields = {row["key"][len(prefix):]: row["value"] for row in self._connect().execute(
            "select key,value from schema_meta where key glob ?", (prefix + "*",))}
        if not fields:
            return None
        for key in ("pages", "normalization_version", "page_size", "previous_oldest_us"):
            if key in fields:
                fields[key] = int(fields[key])
        fields.setdefault("next_cursor", None)
        fields.setdefault("previous_oldest_us", None)
        return StreamCheckpoint(**fields)

    def _write_coverage(self, coverage: EnrichmentCoverage) -> None:
        values = asdict(coverage)
        if _parse_datetime(coverage.end_at, "end_at") <= _parse_datetime(coverage.start_at, "start_at"):
            raise ValueError("Coverage must have a positive interval")
        self._connect().execute(f"insert or ignore into market_enrichment_coverage ({','.join(values)}) values ({','.join('?' for _ in values)})", tuple(values.values()))
        if coverage.stream == "trading":
            start = math.ceil(_parse_datetime(coverage.start_at, "start_at").timestamp())
            end = int(_parse_datetime(coverage.end_at, "end_at").timestamp())
            if start < end:
                self._connect().executemany(
                    "insert or ignore into transaction_coverage (item_code,start_epoch,end_epoch,source) values (?,?,?,?)",
                    [(code, start, end, coverage.source) for code in self.item_codes(transaction_type="trading")])

    def record_enrichment_coverage(self, coverage: EnrichmentCoverage) -> None:
        with self._connect():
            self._write_coverage(coverage)

    def repair_exhaustion_floor(self, stream: str, *, observed_at: str, oldest_at: str) -> int:
        """Correct former epoch-wide exhaustion coverage using a recorded scan floor.

        Changes coverage metadata only; source history and aggregates are retained.
        """
        oldest = _parse_datetime(oldest_at, "oldest_at")
        if stream not in ("trading", "itemMarket") or oldest >= _parse_datetime(observed_at, "observed_at"):
            raise ValueError("Invalid observed exhaustion floor")
        with self._connect() as connection:
            rows = connection.execute("select * from market_enrichment_coverage where stream=? "
                "and observed_at=? and completion_reason='api-exhausted' "
                "and start_at='1970-01-01T00:00:00Z'", (stream, observed_at)).fetchall()
            for row in rows:
                connection.execute("update market_enrichment_coverage set start_at=? where stream=? "
                    "and normalization_version=? and start_at=? and end_at=? and source=?",
                    (oldest_at, stream, row['normalization_version'], row['start_at'], row['end_at'], row['source']))
                if stream == "trading":
                    connection.execute("update transaction_coverage set start_epoch=? where start_epoch=0 "
                        "and end_epoch=? and source=?", (math.ceil(oldest.timestamp()),
                        int(_parse_datetime(row['end_at'], "end_at").timestamp()), row['source']))
        return len(rows)

    def normalized_page_known(self, stream: str, ids: list[str]) -> bool:
        if not ids:
            return False
        rows = self._connect().execute(
            "select id from transactions where transaction_type=? and normalization_version>=1 "
            "and id in (" + ",".join("?" for _ in ids) + ")", (stream, *ids))
        return {row[0] for row in rows} == set(ids)

    def stream_status(self, stream: str) -> dict[str, Any]:
        c = self._connect()
        row = c.execute("select * from market_ingestion_state where stream=?", (stream,)).fetchone()
        return {"progress": dict(row) if row else None,
                "coverage": [dict(r) for r in c.execute("select * from market_enrichment_coverage where stream=? order by start_at", (stream,))]}

    def record_stream_elapsed(self, stream: str, elapsed: float) -> None:
        with self._connect() as connection:
            connection.execute("insert or replace into schema_meta (key,value) values (?,?)",
                               (f"market_elapsed_{stream}", str(elapsed)))

    def market_sync_status(self) -> dict[str, Any]:
        streams = {}
        for stream in ("trading", "itemMarket"):
            status = self.stream_status(stream)
            counts = self._connect().execute(
                "select count(*) as retained, coalesce(sum(normalization_version>=1),0) as normalized "
                "from transactions where transaction_type=?", (stream,)).fetchone()
            timing = self._connect().execute("select value from schema_meta where key=?", (f"market_elapsed_{stream}",)).fetchone()
            status.update(dict(counts))
            status["elapsed_seconds_last_finished_scan"] = float(timing[0]) if timing else None
            status["remaining_pages"] = status["eta_seconds"] = None
            status["api_exhaustion_observed_at"] = [c["observed_at"] for c in status["coverage"] if c["completion_reason"] == "api-exhausted"]
            exhaustion = self._connect().execute("select value from schema_meta where key=?", (f"market_exhaustion_{stream}",)).fetchone()
            status["latest_scan_exhausted"] = exhaustion is not None
            status["latest_scan_exhausted_at"] = exhaustion[0] if exhaustion else None
            status["unverified_retained"] = counts["retained"] - counts["normalized"]
            checkpoint = self.stream_checkpoint(stream)
            status["resume_checkpoint"] = ({"phase": checkpoint.phase, "pages": checkpoint.pages,
                "scan_anchor": checkpoint.scan_anchor, "continuation_saved": bool(checkpoint.next_cursor)}
                if checkpoint else None)
            streams[stream] = status
        return {"streams": streams, "limitations":
                "Coverage describes observed API pagination only, not complete game history or known inventory basis. "
                "Uncovered intervals and failed/running/partial scans remain unverified. "
                "Without an opted-in checkpoint, restart replays from the head. Saved opaque continuations "
                "are upstream-dependent; invalid continuations fail visibly and require explicit head replay."}

    def cache_entity_name(self, entity_kind: str, entity_id: str, name: str | None, observed_at: str, lookup_status: str) -> None:
        attempted = _parse_datetime(observed_at, "observed_at")
        with self._connect():
            previous = self.entity_name(entity_kind, entity_id)
            if previous and attempted < _parse_datetime(previous["lookup_attempted_at"], "lookup_attempted_at"):
                return
            name_time = observed_at if name is not None else (previous["name_observed_at"] if previous else None)
            cached_name = name if name is not None else (previous["name"] if previous else None)
            self._connect().execute("insert into market_entities (entity_kind,entity_id,name,name_observed_at,lookup_status,lookup_attempted_at) values (?,?,?,?,?,?) on conflict(entity_kind,entity_id) do update set name=excluded.name,name_observed_at=excluded.name_observed_at,lookup_status=excluded.lookup_status,lookup_attempted_at=excluded.lookup_attempted_at",
                                    (entity_kind, entity_id, cached_name, name_time, lookup_status, observed_at))

    def entity_name(self, entity_kind: str, entity_id: str) -> dict[str, Any] | None:
        row = self._connect().execute("select * from market_entities where entity_kind=? and entity_id=?", (entity_kind, entity_id)).fetchone()
        return dict(row) if row else None

    def cache_identity(self, identity, observed_at: str, force_refresh: bool = False) -> None:
        previous = self.entity_name(identity.entity_kind, identity.entity_id)
        if not force_refresh and previous and _parse_datetime(observed_at, "observed_at") < _parse_datetime(previous["lookup_attempted_at"], "attempted"):
            return
        self.cache_entity_name(identity.entity_kind, identity.entity_id, identity.name, observed_at, "ok")
        with self._connect():
            self._connect().execute("update market_entities set image_url=?,country_code=?,level=?,citizenship_id=?,prestige=? where entity_kind=? and entity_id=?",
                (identity.image_url, identity.country_code, identity.level, identity.citizenship_id, 1 if identity.prestige else 0, identity.entity_kind, identity.entity_id))

    def link_identity_asset(self, url: str) -> None:
        with self._connect():
            self._connect().execute("update market_entities set image_cache_url=? where image_url=?", (url, url))

    def cached_asset(self, url: str) -> dict | None:
        row = self._connect().execute("select * from display_assets where source_url=?", (url,)).fetchone()
        return dict(row) if row else None

    def cache_asset(self, asset: dict) -> None:
        previous = self.cached_asset(asset["source_url"])
        if previous and _parse_datetime(asset["attempted_at"], "attempted") < _parse_datetime(previous["attempted_at"], "attempted"):
            return
        fields = ("source_url", "local_path", "sha256", "mime_type", "width", "height", "byte_count", "observed_at", "status", "attempted_at")
        with self._connect():
            self._connect().execute("insert into display_assets values (?,?,?,?,?,?,?,?,?,?) on conflict(source_url) do update set "
                + ",".join(f"{k}=excluded.{k}" for k in fields[1:]), tuple(asset.get(k) for k in fields))

    def cache_equipment_display(self, item: dict, observed_at: str) -> None:
        fields = ("item_code", "rarity", "tier", "color_scheme", "frame_color", "frame_end", "text_color", "image_url")
        with self._connect():
            self._connect().execute("insert into equipment_display values (?,?,?,?,?,?,?,?,?) on conflict(item_code) do update set "
                + ",".join(f"{k}=excluded.{k}" for k in (*fields[1:], "observed_at")),
                (*[item[k] for k in fields], observed_at))

    def equipment_display(self) -> dict[str, dict]:
        return {r["item_code"]: dict(r) for r in self._connect().execute("select * from equipment_display")}

    def backup(self, destination: str | Path) -> Path:
        """Consistent online snapshot, including committed WAL pages; never overwrites a backup."""
        target = Path(destination)
        if target.resolve() == self.path.resolve() or target.exists():
            raise MarketStoreError("Backup destination must be a new distinct path.")
        target.parent.mkdir(parents=True, exist_ok=True)
        if self._connect().in_transaction:
            raise MarketStoreError("Cannot back up an uncommitted transaction.")
        # Reserve the path exclusively before opening SQLite.
        with target.open("xb"):
            pass
        try:
            destination_connection = sqlite3.connect(target)
            try:
                self._connect().backup(destination_connection)
            finally:
                destination_connection.close()
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return target

    def restore(self, source: str | Path) -> None:
        """Restore a validated snapshot through SQLite backup, without copying or deleting WAL files.

        Caller must stop other application writers before restoring.
        """
        source = Path(source)
        if not source.is_file() or source.resolve() == self.path.resolve():
            raise MarketStoreError("Restore requires a distinct existing backup.")
        if self._connect().in_transaction:
            raise MarketStoreError("Cannot restore over an uncommitted transaction.")
        backup = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            if backup.execute("pragma integrity_check").fetchone()[0] != "ok":
                raise MarketStoreError("Backup integrity check failed.")
            version = int(backup.execute("select value from schema_meta where key='version'").fetchone()[0])
            if version > LATEST_SCHEMA_VERSION or backup.execute("pragma user_version").fetchone()[0] not in (0, version):
                raise MarketStoreError("Unsupported backup version markers.")
            backup.backup(self._connect())
        finally:
            backup.close()

    def migrate_with_backup(self) -> Path:
        if not self.path.is_file():
            raise MarketStoreError("Migration requires an existing database.")
        target = self.path.with_name(self.path.name + ".backup-" + _utc_now().strftime("%Y%m%dT%H%M%S%fZ"))
        self.backup(target)
        try:
            self.initialize()
        except Exception as exc:
            raise MarketStoreError(f"Migration failed; consistent backup retained at {target}: {exc}") from exc
        return target

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
                for entry in getattr(orders[item_code], "entries", ()):
                    values = {"observation_id": observation_id, "side": entry.side, "entry_position": entry.position, **entry.values}
                    allowed = {"observation_id", "side", "entry_position", "order_id", "item_code", "source_type", "user_id", "mu_id", "country_id", "party_id", "price_decimal", "quantity_decimal", "price_precision", "quantity_precision", "offer_at"}
                    if not set(values) <= allowed:
                        raise ValueError("Unknown normalized order column")
                    connection.execute(f"insert into order_book_entries ({','.join(values)}) values ({','.join('?' for _ in values)})", tuple(values.values()))
                    for field in entry.presence:
                        connection.execute("insert into order_entry_field_state values (?,?,?,?,?)", (observation_id, entry.side, entry.position, field, int(entry.values[field] is None)))
                    for extra in entry.extras:
                        _validate_scalar(extra.value_type, extra.value)
                        connection.execute("insert into order_entry_extra_fields values (?,?,?,?,?,?)", (observation_id, entry.side, entry.position, extra.path, extra.value_type, extra.value))
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

    def order_entries(self, observation_id: int) -> list[dict[str, Any]]:
        c = self._connect()
        entries = [dict(r) for r in c.execute("select * from order_book_entries where observation_id=? order by side,entry_position", (observation_id,))]
        for entry in entries:
            keys = (observation_id, entry["side"], entry["entry_position"])
            entry["extras"] = [dict(r) for r in c.execute("select field_path,value_type,scalar_value from order_entry_extra_fields where observation_id=? and side=? and entry_position=?", keys)]
            entry["presence"] = [dict(r) for r in c.execute("select field_path,is_null from order_entry_field_state where observation_id=? and side=? and entry_position=?", keys)]
        return entries

    def run_housekeeping(
        self,
        *,
        retention_days: int,
        transaction_retention_days: int | str | None = None,
        vacuum_interval_days: int = 30,
        now: datetime | None = None,
    ) -> HousekeepingSummary:
        if isinstance(retention_days, bool) or not isinstance(retention_days, int) or retention_days < 1:
            raise ValueError("retention_days must be an integer of at least 1.")
        if (
            isinstance(vacuum_interval_days, bool)
            or not isinstance(vacuum_interval_days, int)
            or vacuum_interval_days < 0
        ):
            raise ValueError("vacuum_interval_days must be a non-negative integer.")

        transaction_days = retention_days if transaction_retention_days is None else transaction_retention_days
        if transaction_days != "all" and (isinstance(transaction_days, bool) or not isinstance(transaction_days, int) or transaction_days < 1):
            raise ValueError("transaction_retention_days must be all or a positive integer.")
        housekeeping_at = _as_utc(now or _utc_now())
        cutoff_at = housekeeping_at - timedelta(days=retention_days)
        cutoff_epoch = _epoch_seconds(cutoff_at)
        connection = self._connect()
        with connection:
            transactions_deleted = 0
            if transaction_days != "all":
                transaction_cutoff_at = housekeeping_at - timedelta(days=transaction_days)
                transaction_cutoff_epoch = _epoch_seconds(transaction_cutoff_at)
                connection.execute(
                    "insert or replace into schema_meta (key, value) values ('transaction_retention_cutoff_epoch', ?)",
                    (str(transaction_cutoff_epoch),),
                )
                connection.execute("delete from transaction_coverage where end_epoch <= ?", (transaction_cutoff_epoch,))
                connection.execute(
                    "update transaction_coverage set start_epoch = ? where start_epoch < ?",
                    (transaction_cutoff_epoch, transaction_cutoff_epoch),
                )
                transactions_deleted = connection.execute(
                    "delete from transactions where coalesce(created_at_us,created_at_epoch*1000000) < ?",
                    (transaction_cutoff_epoch * 1000000 + transaction_cutoff_at.microsecond,),
                ).rowcount
                cutoff_text = _format_datetime(transaction_cutoff_at)
                for row in connection.execute("select rowid,* from market_enrichment_coverage").fetchall():
                    if _parse_datetime(row["start_at"], "start_at") < transaction_cutoff_at:
                        connection.execute("delete from market_enrichment_coverage where rowid=?", (row["rowid"],))
                        if _parse_datetime(row["end_at"], "end_at") > transaction_cutoff_at:
                            self._write_coverage(EnrichmentCoverage(row["stream"], cutoff_text, row["end_at"], row["source"], "retention-pruned", row["observed_at"], row["normalization_version"]))
                if transactions_deleted:
                    connection.execute("update market_ingestion_state set status='partial',oldest_at=null,oldest_id=null where oldest_at is not null and julianday(oldest_at)<julianday(?)", (cutoff_text,))
            price_observations_deleted = connection.execute(
                "delete from price_observations where observed_at_epoch < ?",
                (cutoff_epoch,),
            ).rowcount
            order_book_observations_deleted = connection.execute(
                "delete from order_book_observations where observed_at_epoch < ?",
                (cutoff_epoch,),
            ).rowcount

        vacuumed = False
        if vacuum_interval_days > 0 and _vacuum_is_due(
            connection,
            housekeeping_at,
            vacuum_interval_days,
        ):
            connection.execute("vacuum")
            with connection:
                connection.execute(
                    "insert or replace into schema_meta (key, value) values (?, ?)",
                    ("housekeeping_last_vacuum_at", _format_datetime(housekeeping_at)),
                )
            vacuumed = True

        return HousekeepingSummary(
            cutoff_at=_format_datetime(cutoff_at),
            transactions_deleted=transactions_deleted,
            price_observations_deleted=price_observations_deleted,
            order_book_observations_deleted=order_book_observations_deleted,
            vacuumed=vacuumed,
        )

    def transactions_for_window(self, item_code: str, since_epoch: int, *, transaction_type: str = "trading") -> list[dict[str, Any]]:
        rows = self._connect().execute(
            """
            select id, item_code, transaction_type, created_at, created_at_epoch,
                   money, quantity, unit_price, fetched_at
            from transactions
            where item_code = ? and transaction_type = ? and created_at_epoch >= ?
            order by created_at_epoch asc, id asc
            """,
            (item_code, transaction_type, since_epoch),
        ).fetchall()
        return [_dict_from_row(row) for row in rows]

    def record_transaction_coverage(
        self, item_code: str, start_epoch: int, end_epoch: int, *, source: str,
    ) -> None:
        """Record a completed contiguous API scan, never inferred trade coverage.

        Call only after all pages covering the interval were successfully read.
        Retention clamps provenance just as it clamps the underlying facts.
        """
        if end_epoch <= start_epoch:
            return
        connection = self._connect()
        # A new verified backfill has reinserted its complete scanned interval.
        # Previous pruning trims old provenance, but must not permanently prevent
        # a later, longer download from restoring that history.
        with connection:
            connection.execute(
                "insert or ignore into transaction_coverage "
                "(item_code, start_epoch, end_epoch, source) values (?, ?, ?, ?)",
                (item_code, int(start_epoch), int(end_epoch), source),
            )

    def transaction_coverage(self, item_codes: Iterable[str] | None = None) -> dict[str, list[tuple[int, int]]]:
        """Return merged verified intervals; legacy metadata is not provenance."""
        connection = self._connect()
        if "transaction_coverage" not in self.table_names():
            return {}
        allowed = set(item_codes) if item_codes is not None else None
        result: dict[str, list[tuple[int, int]]] = {}
        for row in connection.execute(
            "select item_code, start_epoch, end_epoch from transaction_coverage "
            "order by item_code, start_epoch, end_epoch"
        ):
            code = str(row["item_code"])
            if allowed is not None and code not in allowed:
                continue
            intervals = result.setdefault(code, [])
            start, end = int(row["start_epoch"]), int(row["end_epoch"])
            if intervals and start <= intervals[-1][1]:
                intervals[-1] = (intervals[-1][0], max(intervals[-1][1], end))
            else:
                intervals.append((start, end))
        return result

    def completed_daily_facts(
        self, item_codes: Iterable[str], start_epoch: int, end_epoch: int,
    ) -> list[dict[str, Any]]:
        """Bounded UTC-day transaction aggregates for price/turnover read models."""
        codes = tuple(dict.fromkeys(item_codes))
        if not codes or end_epoch <= start_epoch:
            return []
        placeholders = ",".join("?" for _ in codes)
        rows = self._connect().execute(
            f"select item_code, (created_at_epoch / 86400) * 86400 as day_epoch, "
            "sum(quantity) as quantity, sum(unit_price * quantity) as turnover, "
            "count(*) as trade_count, max(created_at_epoch) as last_trade_epoch "
            f"from transactions where item_code in ({placeholders}) "
            "and transaction_type = 'trading' "
            "and created_at_epoch >= ? and created_at_epoch < ? "
            "and unit_price > 0 and unit_price < 1e308 and quantity > 0 and quantity < 1e308 "
            "group by item_code, day_epoch order by day_epoch, item_code",
            (*codes, int(start_epoch), int(end_epoch)),
        ).fetchall()
        return [_dict_from_row(row) for row in rows]

    def transactions_for_period(
        self,
        item_codes: list[str] | tuple[str, ...],
        start_epoch: int,
        end_epoch: int,
    ) -> list[dict[str, Any]]:
        """Return chronological transaction facts in ``[start_epoch, end_epoch)``.

        This bounded, multi-item read supports historical calculations without
        embedding database access or one-query-per-item loops in the read-model
        layer.
        """
        codes = tuple(dict.fromkeys(str(code).strip() for code in item_codes if str(code).strip()))
        if not codes or end_epoch <= start_epoch:
            return []
        placeholders = ", ".join("?" for _ in codes)
        rows = self._connect().execute(
            f"""
            select id, item_code, transaction_type, created_at, created_at_epoch,
                   money, quantity, unit_price, fetched_at
            from transactions
            where item_code in ({placeholders})
              and transaction_type = 'trading'
              and created_at_epoch >= ?
              and created_at_epoch < ?
            order by item_code asc, created_at_epoch asc, id asc
            """,
            (*codes, start_epoch, end_epoch),
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

    def item_codes(self, *, transaction_type: str | None = "trading") -> list[str]:
        """Discover a market scope; None includes all types and unclassified legacy facts."""
        c = self._connect()
        if transaction_type is None:
            query = "select item_code from transactions"
            params = ()
        else:
            query = "select item_code from transactions where transaction_type=?"
            params = (transaction_type,)
        if transaction_type in (None, "trading"):
            query += " union select item_code from price_observations union select item_code from order_book_observations"
        rows = c.execute(query + " order by item_code", params).fetchall()
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
            connection = sqlite3.connect(self.path, timeout=30.0)
            try:
                connection.row_factory = sqlite3.Row
                # Legacy rows retain source subsecond precision in timestamp text
                # even before enrichment fills created_at_us. IDs are not clocks.
                connection.create_function("source_timestamp_us", 1,
                    lambda value: _datetime_us(_parse_datetime(value, "created_at")), deterministic=True)
                connection.execute("pragma foreign_keys = on")
                # Readers can keep their snapshot while sync commits new pages.
                connection.execute("pragma journal_mode = wal").fetchone()
            except Exception:
                connection.close()
                raise
            self._connection = connection
        return self._connection


def migrate_to_v1(connection: sqlite3.Connection) -> None:
    _execute_statements(connection,
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
    _execute_statements(connection,
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
    _execute_statements(connection,
        """
        create table item_production_config (
            item_code text primary key,
            production_points real check (production_points > 0),
            observed_at text not null
        );
        """
    )


def migrate_to_v4(connection: sqlite3.Connection) -> None:
    connection.execute(
        "create table transaction_coverage ("
        "item_code text not null, start_epoch integer not null, end_epoch integer not null, "
        "source text not null, check (end_epoch > start_epoch))"
    )


def _execute_statements(connection: sqlite3.Connection, script: str) -> None:
    """Execute static DDL without executescript's implicit COMMIT."""
    for statement in script.split(";"):
        if statement.strip():
            connection.execute(statement)


def migrate_to_v5(connection: sqlite3.Connection) -> None:
    for column in (
        "offer_created_at text", "updated_at text", "created_at_us integer", "updated_at_us integer",
        "first_fetched_at text", "last_fetched_at text", "money_decimal text", "quantity_decimal text",
        "money_precision text check(money_precision in ('decimal','decoded_float'))",
        "quantity_precision text check(quantity_precision in ('decimal','decoded_float'))",
        "normalization_version integer not null default 0 check(normalization_version>=0)",
        "normalization_status text not null default 'legacy' check(normalization_status in ('legacy','normalized','extensions','error'))",
    ):
        connection.execute("alter table transactions add column " + column)
    # No decimal or identity backfill: legacy REAL projections cannot recover source facts.
    connection.execute("update transactions set first_fetched_at=fetched_at,last_fetched_at=fetched_at")
    _execute_statements(connection, """
        create index idx_transactions_type_time on transactions(transaction_type,created_at_epoch,id);
        create table transaction_participants (
            transaction_id text not null references transactions(id) on delete cascade,
            side text not null check(side in ('buy','sell')),
            user_id text, mu_id text, country_id text, party_id text,
            primary key(transaction_id,side)
        );
        create table transaction_equipment (
            transaction_id text not null primary key references transactions(id) on delete cascade,
            instance_id text, equipment_code text, equipment_type text, state text, max_state text,
            item_quantity text, last_acquisition_at text
        );
        create index idx_equipment_instance on transaction_equipment(instance_id,transaction_id);
        create table transaction_equipment_stats (
            transaction_id text not null references transaction_equipment(transaction_id) on delete cascade,
            skill_code text not null, value_decimal text,
            primary key(transaction_id,skill_code)
        );
        create table transaction_field_state (
            transaction_id text not null references transactions(id) on delete cascade,
            field_path text not null, is_null integer not null check(is_null in (0,1)), source_updated_us integer,
            primary key(transaction_id,field_path)
        );
        create table transaction_extra_fields (
            transaction_id text not null references transactions(id) on delete cascade,
            field_path text not null, value_type text not null check(value_type in ('null','string','number','boolean')),
            scalar_value text, check((value_type='null' and scalar_value is null) or (value_type!='null' and scalar_value is not null)),
            primary key(transaction_id,field_path)
        );
        create table order_book_entries (
            observation_id integer not null references order_book_observations(id) on delete cascade,
            side text not null check(side in ('bid','ask')), entry_position integer not null check(entry_position>=0),
            order_id text, item_code text not null, source_type text,
            user_id text, mu_id text, country_id text, party_id text,
            price_decimal text not null, quantity_decimal text not null, offer_at text,
            price_precision text check(price_precision in ('decimal','decoded_float')),
            quantity_precision text check(quantity_precision in ('decimal','decoded_float')),
            primary key(observation_id,side,entry_position)
        );
        create index idx_order_entry_id on order_book_entries(observation_id,order_id);
        create table order_entry_field_state (
            observation_id integer not null, side text not null, entry_position integer not null,
            field_path text not null, is_null integer not null check(is_null in (0,1)),
            primary key(observation_id,side,entry_position,field_path),
            foreign key(observation_id,side,entry_position) references order_book_entries(observation_id,side,entry_position) on delete cascade
        );
        create table order_entry_extra_fields (
            observation_id integer not null, side text not null, entry_position integer not null,
            field_path text not null, value_type text not null check(value_type in ('null','string','number','boolean')),
            scalar_value text, check((value_type='null' and scalar_value is null) or (value_type!='null' and scalar_value is not null)),
            primary key(observation_id,side,entry_position,field_path),
            foreign key(observation_id,side,entry_position) references order_book_entries(observation_id,side,entry_position) on delete cascade
        );
        create table market_entities (
            entity_kind text not null check(entity_kind in ('user','mu','country','party')),
            entity_id text not null, name text, name_observed_at text, lookup_status text not null, lookup_attempted_at text not null,
            image_url text, country_code text, image_cache_url text, level integer, citizenship_id text, prestige integer default 0,
            primary key(entity_kind,entity_id)
        );
        create table market_ingestion_state (
            stream text not null primary key check(stream in ('trading','itemMarket')),
            normalization_version integer not null check(normalization_version>0),
            scan_mode text not null check(scan_mode in ('incremental','resync','backfill')),
            scan_anchor text, oldest_at text, oldest_id text, newest_at text, newest_id text,
            attempted_at text, status text not null check(status in ('running','partial','complete','failed','exhausted')),
            attempts integer not null check(attempts>=0),
            pages integer not null check(pages>=0), inserted integer not null check(inserted>=0),
            enriched integer not null check(enriched>=0), unchanged integer not null check(unchanged>=0),
            rejected integer not null check(rejected>=0), last_error text
        );
        create table market_enrichment_coverage (
            stream text not null check(stream in ('trading','itemMarket')),
            normalization_version integer not null check(normalization_version>0),
            start_at text not null, end_at text not null, source text not null,
            completion_reason text not null, observed_at text not null,
            primary key(stream,normalization_version,start_at,end_at,source)
        );
    """)
    for kind in ("user", "mu", "country", "party"):
        connection.execute(f"create index idx_participant_{kind} on transaction_participants({kind}_id,transaction_id)")


def _validate_decimal(value: str | None) -> None:
    from decimal import Decimal, InvalidOperation
    if value is None:
        return
    try:
        if not isinstance(value, str) or not Decimal(value).is_finite():
            raise ValueError("Expected finite decimal text")
    except InvalidOperation as exc:
        raise ValueError("Expected decimal text") from exc


def _validate_scalar(value_type: str, value: str | None) -> None:
    if value_type not in {"string", "number", "boolean", "null"}:
        raise ValueError("Unknown scalar type")
    if (value_type == "null") != (value is None) or (value is not None and not isinstance(value, str)):
        raise ValueError("Invalid scalar value")
    if value_type == "number":
        _validate_decimal(value)
    if value_type == "boolean" and value not in {"true", "false"}:
        raise ValueError("Invalid boolean value")


def migrate_to_v6(connection: sqlite3.Connection) -> None:
    connection.execute("alter table market_entities add column image_url text")
    connection.execute("alter table market_entities add column country_code text")
    connection.execute("alter table market_entities add column image_cache_url text")
    connection.execute("alter table market_entities add column level integer")
    connection.execute("alter table market_entities add column citizenship_id text")
    connection.execute("alter table market_entities add column prestige integer default 0")
    connection.execute("""create table display_assets (
        source_url text primary key, local_path text, sha256 text, mime_type text,
        width integer, height integer, byte_count integer, observed_at text,
        status text not null, attempted_at text not null)""")
    connection.execute("""create table equipment_display (
        item_code text primary key, rarity text not null, tier integer not null,
        color_scheme text not null, frame_color text not null, frame_end text not null,
        text_color text not null, image_url text not null, observed_at text not null)""")


MIGRATIONS = {
    1: migrate_to_v1,
    2: migrate_to_v2,
    3: migrate_to_v3,
    4: migrate_to_v4,
    5: migrate_to_v5,
    6: migrate_to_v6,
}


def _backfill_market_sync_metadata(connection: sqlite3.Connection) -> None:
    existing = connection.execute(
        "select 1 from schema_meta where key = 'last_market_sync_at'"
    ).fetchone()
    if existing is not None:
        return
    row = connection.execute(
        """
        select observed_at
        from (
            select observed_at, observed_at_epoch from price_observations
            union all
            select observed_at, observed_at_epoch from order_book_observations
        )
        order by observed_at_epoch desc
        limit 1
        """
    ).fetchone()
    if row is None:
        return
    with connection:
        connection.executemany(
            "insert or replace into schema_meta (key, value) values (?, ?)",
            (
                ("last_market_sync_at", row["observed_at"]),
                ("last_market_sync_status", "inferred"),
            ),
        )


def _vacuum_is_due(
    connection: sqlite3.Connection,
    housekeeping_at: datetime,
    vacuum_interval_days: int,
) -> bool:
    free_pages = int(connection.execute("pragma freelist_count").fetchone()[0])
    if free_pages == 0:
        return False
    row = connection.execute(
        "select value from schema_meta where key = 'housekeeping_last_vacuum_at'"
    ).fetchone()
    if row is None:
        return True
    last_vacuum_at = _parse_datetime(row["value"], "housekeeping_last_vacuum_at")
    return housekeeping_at - last_vacuum_at >= timedelta(days=vacuum_interval_days)


def _normalized_order_book(payload: Any) -> tuple[list[Any], list[Any], dict[str, float | None]]:
    try:
        buy_orders = list(payload.buy_orders)
        sell_orders = list(payload.sell_orders)
    except (AttributeError, TypeError) as exc:
        raise ValueError("Expected normalized order data with buy_orders and sell_orders.") from exc
    if getattr(payload, "entries", ()):
        buy_orders, sell_orders = [], []
        for entry in payload.entries:
            for key in ("price_decimal", "quantity_decimal"):
                _validate_decimal(entry.values[key])
                if entry.values[key] is None:
                    raise ValueError("Order numerics cannot be null")
            price, quantity = float(entry.values["price_decimal"]), float(entry.values["quantity_decimal"])
            if price < 0 or quantity < 0 or not all(math.isfinite(n) for n in (price, quantity)):
                raise ValueError("Order numerics must be finite and non-negative")
            if price == 0 or quantity == 0:
                continue
            (buy_orders if entry.side == "bid" else sell_orders).append(OrderLevel(price, quantity))
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


def _datetime_us(value: datetime) -> int:
    delta = value - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


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


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
