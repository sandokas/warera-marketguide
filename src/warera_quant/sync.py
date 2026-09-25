from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Callable
import time
import math
from pathlib import Path
import hashlib
from .display_assets import normalize_image, asset_data_uri

from .market_store import MarketStore
from .market_models import StreamProgress, StreamCheckpoint, EnrichmentCoverage
from .warera_api import WarEraMarketApi, timestamp_us


def refresh_display_cache(api: WarEraMarketApi, store: MarketStore, identities,
                          *, asset_dir: str | Path, max_profiles: int = 30,
                          max_assets: int = 35, max_age_hours: float = 24,
                          now: datetime | None = None, equipment: bool = True, force_refresh: bool = False) -> dict:
    """Refresh only the explicit displayed population, never market history.

    Limits count attempts, including failures. Profile age uses last attempt to
    back off transient errors. Downloads use a separate credential-free client.
    """
    if not 0 <= max_profiles <= 100 or not 0 <= max_assets <= 100 or max_age_hours < 0:
        raise ValueError("Invalid display refresh bounds")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Display refresh requires aware time")
    stamp = now.isoformat()
    root = Path(asset_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    result = {"profiles_attempted": 0, "assets_attempted": 0, "errors": [], "deferred": 0}
    urls = set()

    def fresh(cached):
        return cached and now - datetime.fromisoformat(cached["attempted_at"].replace("Z", "+00:00")) < timedelta(hours=max_age_hours)

    pending = list(dict.fromkeys(identities))
    queued = set(pending)
    for kind, entity_id in pending:
        cached = store.entity_name(kind, entity_id)
        if force_refresh or not fresh({"attempted_at": cached["lookup_attempted_at"]} if cached else None):
            if result["profiles_attempted"] >= max_profiles:
                result["deferred"] += 1
            else:
                result["profiles_attempted"] += 1
                try:
                    store.cache_identity(api.get_identity(kind, entity_id), stamp, force_refresh=force_refresh)
                except Exception as exc:
                    store.cache_entity_name(kind, entity_id, None, stamp, "unavailable")
                    result["errors"].append(f"{kind} {entity_id}: {type(exc).__name__}")
                cached = store.entity_name(kind, entity_id)
        if kind == "user" and cached and cached.get("citizenship_id"):
            country_key = ("country", cached["citizenship_id"])
            if country_key not in queued:
                queued.add(country_key)
                pending.append(country_key)
        if cached and cached.get("image_url"):
            urls.add(cached["image_url"])
    if equipment:
        existing = store.equipment_display()
        if not existing or any(now - datetime.fromisoformat(r["observed_at"]) >= timedelta(hours=max_age_hours) for r in existing.values()):
            try:
                for item in api.get_equipment_display():
                    store.cache_equipment_display(item, stamp)
            except Exception as exc:
                result["errors"].append(f"equipment: {type(exc).__name__}")
        urls.update(row["image_url"] for row in store.equipment_display().values())
    for url in sorted(urls):
        cached = store.cached_asset(url)
        valid_file = bool(asset_data_uri(cached))
        if valid_file:
            store.link_identity_asset(url)
        if fresh(cached) and (valid_file or cached["status"] == "unavailable"):
            continue
        if result["assets_attempted"] >= max_assets:
            result["deferred"] += 1
            continue
        result["assets_attempted"] += 1
        try:
            content, mime = api.client.get_public_bytes(url)
            content, mime, width, height = normalize_image(content, mime)
            digest = hashlib.sha256(content).hexdigest()
            path = root / (digest + (".svg" if mime == "image/svg+xml" else ".png"))
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_bytes(content)
            temporary.replace(path)
            store.cache_asset(dict(source_url=url, local_path=str(path), sha256=digest,
                mime_type=mime, width=width, height=height, byte_count=len(content),
                observed_at=stamp, status="ok", attempted_at=stamp))
            store.link_identity_asset(url)
        except Exception as exc:
            store.cache_asset({**(cached or {}), "source_url": url, "status": "unavailable", "attempted_at": stamp})
            result["errors"].append(f"asset {url}: {type(exc).__name__}")
    return result


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
    resume_market: bool = False,
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
    if resume_market and not (resync_market and history_scope == "all"):
        raise ValueError("Durable resume requires an all-history resync")
    store.initialize()
    anchor = observed_at or datetime.now(timezone.utc)
    anchor = anchor.replace(tzinfo=timezone.utc) if anchor.tzinfo is None else anchor.astimezone(timezone.utc)
    checkpoints = {stream: store.stream_checkpoint(stream) if resume_market else None
                   for stream in ("trading", "itemMarket")}
    anchors = {point.scan_anchor for point in checkpoints.values() if point is not None}
    if len(anchors) > 1:
        raise ValueError("Market checkpoints belong to different scans")
    if anchors:
        anchor = datetime.fromisoformat(anchors.pop().replace("Z", "+00:00"))
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
            checkpointed=resume_market, resume=checkpoints[stream],
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


def _sync_stream(api, store, stream, *, anchor, boundary, mode, limit, page_cap, progress, verbose,
                 initial=None, checkpointed=False, resume=None):
    started = time.monotonic()
    prior = store.stream_status(stream)
    state = StreamProgress(stream, scan_mode=mode, scan_anchor=_stamp(anchor), attempted_at=_stamp(anchor),
                           attempts=(prior["progress"] or {}).get("attempts", 0) + 1)
    if initial is not None:
        state = replace(initial, status="running")
    if resume is not None:
        saved = prior["progress"]
        if (resume.normalization_version != 1 or resume.page_size != limit
                or resume.stream != stream or resume.scan_anchor != _stamp(anchor)
                or resume.phase not in ("history", "history-complete") or resume.pages < 1
                or (resume.phase == "history-complete" and resume.next_cursor is not None)
                or not saved or saved["scan_mode"] != "resync"
                or saved["normalization_version"] != resume.normalization_version
                or saved["scan_anchor"] != resume.scan_anchor
                or saved["pages"] < resume.pages
                or (resume.phase == "history" and (saved["pages"] != resume.pages or not resume.next_cursor
                    or resume.previous_oldest_us is None or not saved["oldest_at"]
                    or resume.previous_oldest_us != timestamp_us(saved["oldest_at"])))):
            raise ValueError("Checkpoint does not match committed stream progress")
        state = replace(StreamProgress(**saved), status="running", last_error=None,
                        attempts=saved["attempts"] + 1, attempted_at=_stamp(datetime.now(timezone.utc)))
        _log(progress, f"{stream}: resuming committed page {resume.pages}; phase={resume.phase}")
    store.record_stream_progress(replace(state, status="running"))
    cursor = resume.next_cursor if resume else None
    cursors = {cursor} if cursor else set()
    previous_oldest = resume.previous_oldest_us if resume else None
    seen = state.inserted + state.enriched + state.unchanged + state.rejected
    if resume is not None and resume.phase == "history-complete":
        return ItemSyncResult(stream, state.pages, state.inserted, state.enriched + state.unchanged,
            seen, transactions_enriched=state.enriched, transactions_unchanged=state.unchanged,
            transactions_rejected=state.rejected, status="running")
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
                reason = "api-exhausted"
                # Exhaustion may be a retention floor, not the start of game
                # history. Never certify unseen dates back to the Unix epoch.
                earliest = oldest["created_at"] if oldest else state.oldest_at
                start = datetime.fromisoformat(earliest.replace("Z", "+00:00")) if earliest else None
                if boundary is not None and start is not None:
                    start = max(start, boundary)
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
            checkpoint = (StreamCheckpoint(stream, _stamp(anchor),
                "history-complete" if reason == "api-exhausted" else "history",
                None if reason == "api-exhausted" else page.next_cursor,
                oldest["created_at_us"] if oldest else previous_oldest, candidate.pages)
                if checkpointed else None)
            summary = store.ingest_transactions(page.items, fetched_at=anchor, progress=candidate,
                coverage=coverage, strict=True, checkpoint=checkpoint,
                exhaustion_observed_at=_stamp(anchor) if reason == "api-exhausted" else None)
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
