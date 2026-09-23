from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Callable
import time
import math

from .market_store import MarketStore
from .market_models import StreamProgress, EnrichmentCoverage
from .warera_api import WarEraMarketApi, timestamp_us


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
    transactions_enriched: int = 0
    transactions_unchanged: int = 0
    transactions_rejected: int = 0
    status: str = "complete"
    elapsed_seconds: float = 0.0


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
    def transactions_enriched(self) -> int:
        return sum(item.transactions_enriched for item in self.items)

    @property
    def transactions_unchanged(self) -> int:
        return sum(item.transactions_unchanged for item in self.items)

    @property
    def transactions_rejected(self) -> int:
        return sum(item.transactions_rejected for item in self.items)

    @property
    def error_count(self) -> int:
        return sum(1 for item in self.items if item.error is not None)


def sync_market_data(
    market_api: WarEraMarketApi, store: MarketStore, *, order_limit: int = 100,
    transaction_limit: int = 100, history_pages: int = 0,
    transaction_backfill: bool = False, lookback_days: float | None = None,
    exclude_item_codes: set[str] | None = None, observed_at: datetime | None = None,
    progress: Callable[[str], None] | None = None, verbose: bool = False,
    resync_market: bool = False, history_scope: str | None = None,
) -> MarketSyncResult:
    """Collect two global streams; report/catalog exclusions affect current quotes only."""
    if not 1 <= order_limit <= 100 or not 1 <= transaction_limit <= 100:
        raise ValueError("API limits must be between 1 and 100.")
    if history_pages < 0 or (lookback_days is not None and (not math.isfinite(lookback_days) or lookback_days < 0)):
        raise ValueError("History caps and lookback must be non-negative.")
    if resync_market != (history_scope is not None) or history_scope not in (None, "7d", "all"):
        raise ValueError("Resync requires history_scope 7d or all.")
    if resync_market and (history_pages or transaction_backfill or exclude_item_codes):
        raise ValueError("Recent resync cannot be combined with page caps, backfill or exclusions.")
    store.initialize()
    anchor = observed_at or datetime.now(timezone.utc)
    anchor = anchor.replace(tzinfo=timezone.utc) if anchor.tzinfo is None else anchor.astimezone(timezone.utc)
    boundary = (anchor - timedelta(days=7) if history_scope == "7d" else
                anchor - timedelta(days=lookback_days) if transaction_backfill and lookback_days is not None else None)
    results = []
    prices = {}
    try:
        prices = market_api.get_prices()
        excluded = {code.lower() for code in (exclude_item_codes or set())}
        prices = {code: price for code, price in prices.items() if code.lower() not in excluded}
        store.insert_price_observations(prices, anchor)
    except Exception as exc:
        results.append(ItemSyncResult("prices", 0, 0, 0, 0, error=type(exc).__name__))
        _log(progress, f"Current prices failed ({type(exc).__name__}); continuing transaction streams")
    try:
        points = market_api.get_item_production_points()
        store.upsert_item_production_points({code: points.get(code) for code in prices}, anchor)
    except Exception as exc:
        results.append(ItemSyncResult("production-points", 0, 0, 0, 0, error=type(exc).__name__))
        _log(progress, f"Production points failed ({type(exc).__name__}); continuing transaction streams")
    order_count = 0
    for code in prices:
        try:
            orders = market_api.get_top_orders(code, order_limit)
            store.insert_order_book_observations({code: orders}, anchor)
            order_count += 1
        except Exception as exc:
            results.append(ItemSyncResult(code, 0, 0, 0, 0, error=type(exc).__name__))
            _log(progress, f"{code}: current orders failed ({type(exc).__name__})")
    started = time.monotonic()
    for stream in ("trading", "itemMarket"):
        scan = _sync_stream(
            market_api, store, stream, anchor=anchor, boundary=boundary,
            mode="resync" if resync_market else "backfill" if transaction_backfill else "incremental",
            limit=100 if resync_market else transaction_limit, page_cap=history_pages, progress=progress, verbose=verbose,
        )
        results.append(scan)
    if resync_market:
        catchup_anchor = max(anchor, datetime.now(timezone.utc))
        for index, scan in enumerate(results):
            if scan.item_code not in ("trading", "itemMarket") or scan.error:
                continue
            stream = scan.item_code
            elapsed = scan.elapsed_seconds
            _log(progress, f"{stream}: catch-up from head to fixed scan anchor")
            results[index] = _sync_stream(
                market_api, store, stream, anchor=catchup_anchor, boundary=anchor,
                mode="resync", limit=100, page_cap=0, progress=progress, verbose=verbose,
                initial=StreamProgress(**store.stream_status(stream)["progress"]),
            )
            catchup_elapsed = results[index].elapsed_seconds
            store.record_stream_elapsed(stream, elapsed + catchup_elapsed)
            results[index] = replace(results[index], elapsed_seconds=elapsed + catchup_elapsed)
    result = MarketSyncResult(anchor, len(prices), order_count, results)
    store.record_market_sync(anchor, status="partial" if result.error_count or any(r.status == "partial" for r in results) else "complete")
    if progress:
        for stream, status in store.market_sync_status()["streams"].items():
            state = status["progress"]
            _log(progress, f"{stream}: {state['status']}; {state['pages']} committed pages, "
                 f"{state['inserted']} inserted / {state['enriched']} enriched / "
                 f"{state['unchanged']} unchanged / {state['rejected']} rejected; "
                 f"normalized {status['normalized']}/{status['retained']} retained; "
                 f"scan API exhaustion observed={status['latest_scan_exhausted']}; "
                 f"error={state['last_error'] or 'none'}")
        _log(progress, "API exhaustion does not establish complete game history or known inventory basis.")
    _log(progress, f"Market sync elapsed {time.monotonic() - started:.1f}s; remaining pages/ETA unknown")
    return result


def _sync_stream(api, store, stream, *, anchor, boundary, mode, limit, page_cap, progress, verbose, initial=None):
    started = time.monotonic()
    prior = store.stream_status(stream)
    state = StreamProgress(stream, scan_mode=mode, scan_anchor=_stamp(anchor), attempted_at=_stamp(anchor),
                           attempts=(prior["progress"] or {}).get("attempts", 0) + 1)
    if initial is not None:
        state = replace(initial, status="running")
    store.record_stream_progress(replace(state, status="running"))
    cursor = None
    cursors = set()
    previous_oldest = None
    seen = state.inserted + state.enriched + state.unchanged + state.rejected
    overlap = False
    try:
        while True:
            page = api.get_transaction_page(transaction_type=stream, limit=limit, cursor=cursor,
                                            previous_oldest_us=previous_oldest)
            seen += len(page.items)
            values = [fact.values for fact in page.items]
            oldest = min(values, key=lambda v: (v["created_at_us"], v["id"]), default=None)
            newest = max(values, key=lambda v: (v["created_at_us"], v["id"]), default=None)
            reason = None
            start = None
            # Verified interval AND an entire normalized page, strictly past the
            # previous head, are required. Equal-time pages continue normally.
            if mode == "incremental" and oldest and prior["progress"]:
                head = prior["progress"].get("newest_at")
                overlap = bool(head and oldest["created_at_us"] < timestamp_us(head)
                    and any(c["normalization_version"] == 1
                            and timestamp_us(c["start_at"]) <= oldest["created_at_us"]
                            and newest["created_at_us"] < timestamp_us(c["end_at"])
                            for c in prior["coverage"])
                    and store.normalized_page_known(stream, [v["id"] for v in values]))
            if not page.next_cursor:
                reason, start = "api-exhausted", boundary or datetime(1970, 1, 1, tzinfo=timezone.utc)
            elif boundary is not None and oldest and oldest["created_at_us"] < timestamp_us(_stamp(boundary)):
                reason, start = "history-boundary", boundary
            elif overlap:
                reason = "normalized-overlap"
                start = datetime.fromisoformat(oldest["created_at"].replace("Z", "+00:00")) + timedelta(microseconds=1)
            elif page_cap and state.pages + 1 >= page_cap:
                reason = "page-cap"
                if oldest:
                    start = datetime.fromisoformat(oldest["created_at"].replace("Z", "+00:00")) + timedelta(microseconds=1)
            if not reason and (not values or page.next_cursor in cursors):
                raise ValueError("Pagination made no progress")
            coverage = None
            if reason and start is not None and start < anchor:
                coverage = EnrichmentCoverage(stream, _stamp(start), _stamp(anchor),
                                              "global-contiguous-pagination", reason, _stamp(anchor))
            committed_newest = (state.newest_at, state.newest_id) if state.newest_at else None
            committed_oldest = (state.oldest_at, state.oldest_id) if state.oldest_at else None
            if newest and (committed_newest is None or (newest["created_at_us"], newest["id"]) > (timestamp_us(committed_newest[0]), committed_newest[1])):
                committed_newest = (newest["created_at"], newest["id"])
            if oldest and (committed_oldest is None or (oldest["created_at_us"], oldest["id"]) < (timestamp_us(committed_oldest[0]), committed_oldest[1])):
                committed_oldest = (oldest["created_at"], oldest["id"])
            candidate = replace(state, pages=state.pages + 1,
                newest_at=committed_newest[0] if committed_newest else None,
                newest_id=committed_newest[1] if committed_newest else None,
                oldest_at=committed_oldest[0] if committed_oldest else None,
                oldest_id=committed_oldest[1] if committed_oldest else None,
                status="running" if mode == "resync" and initial is None else "exhausted" if reason == "api-exhausted" else "partial" if reason == "page-cap" else "complete" if reason else "running")
            summary = store.ingest_transactions(page.items, fetched_at=anchor, progress=candidate,
                                                coverage=coverage, strict=True)
            state = replace(candidate, inserted=state.inserted + summary.inserted,
                            enriched=state.enriched + summary.enriched, unchanged=state.unchanged + summary.unchanged)
            if progress:
                _log(progress, f"{stream}: page {state.pages}, {len(values)} rows, "
                     f"{summary.inserted} inserted, {summary.enriched} enriched, {summary.unchanged} unchanged, {summary.rejected} rejected; "
                     f"committed {state.oldest_at}/{state.oldest_id} .. {state.newest_at}/{state.newest_id}")
            if reason:
                _log(progress, f"{stream}: {state.pages} page(s), stopped at {reason}")
                break
            previous_oldest = oldest["created_at_us"]
            cursor = page.next_cursor
            cursors.add(cursor)
    except Exception as exc:
        # Transport errors may embed request URLs/cursors. Persist only the class.
        state = replace(state, rejected=state.rejected + getattr(exc, "rejected", 0))
        error = f"{stream}: {type(exc).__name__}; scan incomplete"
        try:
            store.record_stream_progress(replace(state, status="failed", last_error=error))
        except Exception as state_exc:
            exc.add_note(f"Could not record stream failure: {type(state_exc).__name__}")
            raise exc from state_exc
        store.record_stream_elapsed(stream, time.monotonic() - started)
        _log(progress, error)
        return ItemSyncResult(stream, state.pages, state.inserted, state.enriched + state.unchanged, seen, error=error, transactions_enriched=state.enriched, transactions_unchanged=state.unchanged, transactions_rejected=state.rejected, status="failed", elapsed_seconds=time.monotonic() - started)
    store.record_stream_elapsed(stream, time.monotonic() - started)
    return ItemSyncResult(stream, state.pages, state.inserted, state.enriched + state.unchanged,
                          seen, stopped_at_high_water=overlap, transactions_enriched=state.enriched, transactions_unchanged=state.unchanged, transactions_rejected=state.rejected, status=state.status, elapsed_seconds=time.monotonic() - started)


def _stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _log(callback, message):
    if callback:
        callback(message)
