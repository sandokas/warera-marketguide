from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from warera_quant.market_store import MarketStore, SyncSummary
from warera_quant.sync import sync_market_data
from warera_quant.warera_api import TopOrders, TransactionPage


@dataclass
class PageResponse:
    items: list[dict[str, object]]
    next_cursor: str | None = None


class FakeMarketApi:
    def __init__(self, pages: dict[tuple[str, str | None], PageResponse]):
        self.pages = pages
        self.calls: list[tuple[str, object]] = []

    def get_prices(self) -> dict[str, float]:
        self.calls.append(("get_prices", None))
        return {"bread": 3.25}

    def get_top_orders(self, item_code: str, limit: int) -> TopOrders:
        self.calls.append(("get_top_orders", (item_code, limit)))
        return TopOrders(
            buy_orders=[{"price": "3.10", "quantity": "4"}],
            sell_orders=[{"price": "3.40", "quantity": "2"}],
            raw_response={},
        )

    def get_transaction_page(self, item_code: str, *, limit: int, cursor: str | None = None) -> TransactionPage:
        self.calls.append(("get_transaction_page", (item_code, limit, cursor)))
        response = self.pages[(item_code, cursor)]
        return TransactionPage(items=response.items, next_cursor=response.next_cursor, raw_response={})


def _store(tmp_path: Path) -> MarketStore:
    store = MarketStore(tmp_path / "market.sqlite3")
    store.initialize()
    return store


def _transaction(transaction_id: str, created_at: str, money: str = "12", quantity: str = "4") -> dict[str, object]:
    return {
        "id": transaction_id,
        "createdAt": created_at,
        "transactionType": "trading",
        "money": money,
        "quantity": quantity,
    }


def test_incremental_sync_persists_observations_and_stops_at_high_water(tmp_path):
    observed_at = datetime(2026, 6, 30, 10, 0, tzinfo=timezone.utc)
    old_transaction = _transaction("tx-old", "2026-06-30T09:00:00Z")
    api = FakeMarketApi(
        {
            (
                "bread",
                None,
            ): PageResponse(
                items=[
                    _transaction("tx-new", "2026-06-30T09:30:00Z"),
                    old_transaction,
                ],
                next_cursor="next-page",
            ),
            ("bread", "next-page"): PageResponse(
                items=[_transaction("tx-older", "2026-06-30T08:30:00Z")],
            ),
        }
    )

    with _store(tmp_path) as store:
        store.upsert_transactions("bread", [old_transaction], fetched_at=observed_at)
        store.mark_item_sync_success(
            "bread",
            SyncSummary(
                pages_fetched=1,
                transactions_inserted=1,
                newest_created_at="2026-06-30T09:00:00Z",
                newest_created_at_epoch=1782810000,
                newest_transaction_id="tx-old",
                synced_at=observed_at,
            ),
        )

        result = sync_market_data(api, store, order_limit=5, observed_at=observed_at)

        assert result.prices_observed == 1
        assert result.order_books_observed == 1
        assert result.pages_fetched == 1
        assert result.transactions_inserted == 1
        assert result.items[0].stopped_at_high_water is True
        assert ("get_transaction_page", ("bread", 100, "next-page")) not in api.calls
        assert [row["id"] for row in store.transactions_for_window("bread", 0)] == ["tx-old", "tx-new"]
        assert store.price_observations_for_window("bread", 0)[0]["current_price"] == 3.25
        assert store.order_book_observations_for_window("bread", 0)[0]["best_bid"] == 3.1

        state = store.get_item_state("bread")
        assert state is not None
        assert state.newest_transaction_id == "tx-new"
        assert state.pages_fetched == 1
        assert state.transactions_inserted == 1


def test_backfill_ignores_high_water_marks_and_dedupes_transactions(tmp_path):
    observed_at = datetime(2026, 6, 30, 10, 0, tzinfo=timezone.utc)
    old_transaction = _transaction("tx-old", "2026-06-30T09:00:00Z")
    api = FakeMarketApi(
        {
            (
                "bread",
                None,
            ): PageResponse(
                items=[
                    _transaction("tx-new", "2026-06-30T09:30:00Z"),
                    old_transaction,
                ],
                next_cursor="next-page",
            ),
            ("bread", "next-page"): PageResponse(
                items=[_transaction("tx-older", "2026-06-30T08:30:00Z")],
            ),
        }
    )

    with _store(tmp_path) as store:
        store.upsert_transactions("bread", [old_transaction], fetched_at=observed_at)
        store.mark_item_sync_success(
            "bread",
            SyncSummary(
                pages_fetched=1,
                transactions_inserted=1,
                newest_created_at="2026-06-30T09:00:00Z",
                newest_created_at_epoch=1782810000,
                newest_transaction_id="tx-old",
                synced_at=observed_at,
            ),
        )

        result = sync_market_data(
            api,
            store,
            transaction_backfill=True,
            observed_at=observed_at,
        )

        assert result.pages_fetched == 2
        assert result.transactions_inserted == 2
        assert result.items[0].stopped_at_high_water is False
        assert ("get_transaction_page", ("bread", 100, "next-page")) in api.calls
        assert [row["id"] for row in store.transactions_for_window("bread", 0)] == [
            "tx-older",
            "tx-old",
            "tx-new",
        ]
