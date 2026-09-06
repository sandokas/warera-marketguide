from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import count
from typing import Callable

from .market_store import InsertSummary, MarketStore, SyncSummary as StoreSyncSummary
from .warera_api import TopOrders, WarEraMarketApi


@dataclass(frozen=True)
class ItemSyncResult:
    item_code: str
    pages_fetched: int
    transactions_inserted: int
    transactions_skipped: int
    transactions_seen: int
    stopped_at_high_water: bool = False
    stopped_at_duplicate: bool = False
    error: str | None = None


@dataclass(frozen=True)
class MarketSyncResult:
    observed_at: datetime
    prices_observed: int
    order_books_observed: int
    items: list[ItemSyncResult] = field(default_factory=list)

    @property
    def pages_fetched(self) -> int:
        return sum(item.pages_fetched for item in self.items)

    @property
    def transactions_inserted(self) -> int:
        return sum(item.transactions_inserted for item in self.items)

    @property
    def transactions_skipped(self) -> int:
        return sum(item.transactions_skipped for item in self.items)

    @property
    def error_count(self) -> int:
        return sum(1 for item in self.items if item.error is not None)


def sync_market_data(
    market_api: WarEraMarketApi,
    store: MarketStore,
    *,
    order_limit: int = 100,
    transaction_limit: int = 100,
    history_pages: int = 0,
    transaction_backfill: bool = False,
    lookback_days: float | None = None,
    exclude_item_codes: set[str] | None = None,
    observed_at: datetime | None = None,
    progress: Callable[[str], None] | None = None,
    verbose: bool = False,
) -> MarketSyncResult:
    """Fetch current WarEra market data and persist it to SQLite."""

    if order_limit < 1:
        raise ValueError("order_limit must be at least 1.")
    if order_limit > 100:
        raise ValueError("order_limit cannot exceed the WarEra API maximum of 100.")
    if transaction_limit < 1:
        raise ValueError("transaction_limit must be at least 1.")
    if history_pages < 0:
        raise ValueError("history_pages cannot be negative.")
    if lookback_days is not None and lookback_days < 0:
        raise ValueError("lookback_days cannot be negative.")

    store.initialize()
    observed_at = _as_utc(observed_at or datetime.now(timezone.utc))
    prices = market_api.get_prices()
    excluded = {code.lower() for code in (exclude_item_codes or set())}
    if excluded:
        prices = {code: price for code, price in prices.items() if code.lower() not in excluded}
    production_points = market_api.get_item_production_points()
    store.upsert_item_production_points(
        {item_code: production_points.get(item_code) for item_code in prices},
        observed_at,
    )
    store.insert_price_observations(prices, observed_at)
    _log_verbose(
        progress,
        verbose,
        f"Observed current prices for {len(prices)} item(s) at {_format_datetime(observed_at)}.",
    )

    item_codes = list(prices)
    order_books: dict[str, TopOrders] = {}
    item_results: list[ItemSyncResult] = []
    backfill_boundary = (
        observed_at - timedelta(days=lookback_days)
        if transaction_backfill and lookback_days is not None
        else None
    )

    for index, item_code in enumerate(item_codes, start=1):
        _log(progress, f"[{index}/{len(item_codes)}] {item_code}: syncing market data")
        store.mark_item_sync_attempt(item_code, observed_at)
        try:
            orders = market_api.get_top_orders(item_code, order_limit)
            order_books[item_code] = orders
            _log_verbose(
                progress,
                verbose,
                f"[{index}/{len(item_codes)}] {item_code}: "
                f"current order book fetched "
                f"({len(orders.buy_orders)} best bid(s), {len(orders.sell_orders)} best ask(s))",
            )
            result = _sync_item_transactions(
                market_api,
                store,
                item_code,
                transaction_limit=transaction_limit,
                history_pages=history_pages,
                transaction_backfill=transaction_backfill,
                backfill_boundary=backfill_boundary,
                fetched_at=observed_at,
                progress=progress,
                verbose=verbose,
            )
            item_results.append(result)
            _log(
                progress,
                f"[{index}/{len(item_codes)}] {item_code}: "
                f"{result.pages_fetched} page(s), {result.transactions_inserted} new transaction(s)",
            )
        except Exception as exc:
            try:
                store.mark_item_sync_failure(item_code, exc, attempted_at=observed_at)
            except Exception as state_exc:
                exc.add_note(f"Could not record sync failure for {item_code}: {state_exc}")
                raise exc from state_exc
            item_results.append(
                ItemSyncResult(
                    item_code=item_code,
                    pages_fetched=0,
                    transactions_inserted=0,
                    transactions_skipped=0,
                    transactions_seen=0,
                    error=str(exc),
                )
            )
            _log(progress, f"[{index}/{len(item_codes)}] {item_code}: sync failed: {exc}")

    store.insert_order_book_observations(order_books, observed_at)
    result = MarketSyncResult(
        observed_at=observed_at,
        prices_observed=len(prices),
        order_books_observed=len(order_books),
        items=item_results,
    )
    store.record_market_sync(
        observed_at,
        status="partial" if result.error_count else "complete",
    )
    return result


def _sync_item_transactions(
    market_api: WarEraMarketApi,
    store: MarketStore,
    item_code: str,
    *,
    transaction_limit: int,
    history_pages: int,
    transaction_backfill: bool,
    backfill_boundary: datetime | None,
    fetched_at: datetime,
    progress: Callable[[str], None] | None,
    verbose: bool,
) -> ItemSyncResult:
    state = store.get_item_state(item_code)
    high_water_epoch = None if transaction_backfill or state is None else state.newest_created_at_epoch
    cursor = None
    pages_fetched = 0
    transactions_seen = 0
    transactions_inserted = 0
    transactions_skipped = 0
    stopped_at_high_water = False
    stopped_at_duplicate = False
    newest_summary: InsertSummary | None = None
    page_numbers = range(history_pages) if history_pages > 0 else count()

    for page_index, _ in enumerate(page_numbers, start=1):
        _log_verbose(
            progress,
            verbose,
            f"{item_code}: fetching transaction page {page_index} "
            f"(cursor={'first' if cursor is None else cursor}, limit={transaction_limit})",
        )
        page = market_api.get_transaction_page(item_code, limit=transaction_limit, cursor=cursor)
        pages_fetched += 1
        transactions_seen += len(page.items)
        insert_summary = store.upsert_transactions(item_code, page.items, fetched_at=fetched_at)
        transactions_inserted += insert_summary.inserted
        transactions_skipped += insert_summary.skipped
        newest_summary = _newest_insert_summary(newest_summary, insert_summary)

        oldest_epoch = _oldest_transaction_epoch(page.items)
        _log_verbose(
            progress,
            verbose,
            f"{item_code}: page {page_index} imported "
            f"{len(page.items)} transaction(s), inserted {insert_summary.inserted}, "
            f"skipped {insert_summary.skipped}, "
            f"oldest={_format_epoch(oldest_epoch)}, "
            f"newest={insert_summary.newest_created_at or 'none'}, "
            f"next_cursor={'yes' if page.next_cursor else 'no'}",
        )
        if high_water_epoch is not None and oldest_epoch is not None and oldest_epoch <= high_water_epoch:
            stopped_at_high_water = True
            _log(progress, f"{item_code}: reached stored high-water mark on page {page_index}")
            break
        if not transaction_backfill and insert_summary.skipped > 0:
            stopped_at_duplicate = True
            _log(
                progress,
                f"{item_code}: found {insert_summary.skipped} duplicate transaction(s) on page {page_index}; stopping",
            )
            break
        if backfill_boundary is not None and oldest_epoch is not None and oldest_epoch <= int(backfill_boundary.timestamp()):
            _log(progress, f"{item_code}: reached backfill lookback boundary on page {page_index}")
            break
        if not page.next_cursor:
            _log_verbose(progress, verbose, f"{item_code}: stopped because API returned no next cursor.")
            break
        if history_pages > 0 and page_index >= history_pages:
            _log_verbose(progress, verbose, f"{item_code}: stopped after configured page cap ({history_pages}).")
            break
        cursor = page.next_cursor

    state_newest = _newest_state(
        state_epoch=state.newest_created_at_epoch if state else None,
        state_created_at=state.newest_created_at if state else None,
        state_transaction_id=state.newest_transaction_id if state else None,
        page_summary=newest_summary,
    )
    store.mark_item_sync_success(
        item_code,
        StoreSyncSummary(
            pages_fetched=pages_fetched,
            transactions_inserted=transactions_inserted,
            newest_created_at=state_newest[0],
            newest_created_at_epoch=state_newest[1],
            newest_transaction_id=state_newest[2],
            synced_at=fetched_at,
        ),
    )
    return ItemSyncResult(
        item_code=item_code,
        pages_fetched=pages_fetched,
        transactions_inserted=transactions_inserted,
        transactions_skipped=transactions_skipped,
        transactions_seen=transactions_seen,
        stopped_at_high_water=stopped_at_high_water,
        stopped_at_duplicate=stopped_at_duplicate,
    )


def _newest_insert_summary(current: InsertSummary | None, candidate: InsertSummary) -> InsertSummary | None:
    if candidate.newest_created_at_epoch is None:
        return current
    if current is None or current.newest_created_at_epoch is None:
        return candidate
    current_key = (current.newest_created_at_epoch, current.newest_transaction_id or "")
    candidate_key = (candidate.newest_created_at_epoch, candidate.newest_transaction_id or "")
    return candidate if candidate_key > current_key else current


def _oldest_transaction_epoch(transactions: list[dict[str, object]]) -> int | None:
    epochs = [
        epoch
        for transaction in transactions
        if (epoch := _transaction_created_at_epoch(transaction)) is not None
    ]
    return min(epochs, default=None)


def _transaction_created_at_epoch(transaction: dict[str, object]) -> int | None:
    value = transaction.get("created_at", transaction.get("createdAt"))
    if not isinstance(value, str):
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.astimezone(timezone.utc).timestamp())


def _newest_state(
    *,
    state_epoch: int | None,
    state_created_at: str | None,
    state_transaction_id: str | None,
    page_summary: InsertSummary | None,
) -> tuple[str | None, int | None, str | None]:
    if page_summary is None or page_summary.newest_created_at_epoch is None:
        return state_created_at, state_epoch, state_transaction_id
    if state_epoch is not None:
        state_key = (state_epoch, state_transaction_id or "")
        page_key = (page_summary.newest_created_at_epoch, page_summary.newest_transaction_id or "")
        if state_key > page_key:
            return state_created_at, state_epoch, state_transaction_id
    return (
        page_summary.newest_created_at,
        page_summary.newest_created_at_epoch,
        page_summary.newest_transaction_id,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_datetime(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _format_epoch(value: int | None) -> str:
    if value is None:
        return "none"
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _log(progress: Callable[[str], None] | None, message: str) -> None:
    if progress:
        progress(message)


def _log_verbose(progress: Callable[[str], None] | None, verbose: bool, message: str) -> None:
    if verbose:
        _log(progress, message)
