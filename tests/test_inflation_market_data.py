from __future__ import annotations

from datetime import datetime, timezone

import pytest

from warera_quant.market_data import (
    build_inflation_index_results,
    default_inflation_index_definitions,
    load_base_period_price_inputs,
    load_period_item_prices,
    load_trailing_period_price_series,
)
from warera_quant.market_store import MarketStore


UTC = timezone.utc


def _dt(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 8, day, hour, tzinfo=UTC)


def _trade(identifier: str, at: datetime, money: float, quantity: float) -> dict[str, object]:
    return {
        "id": identifier,
        "createdAt": at.isoformat(),
        "transactionType": "trading",
        "money": money,
        "quantity": quantity,
    }


@pytest.fixture
def store(tmp_path):
    with MarketStore(tmp_path / "market.sqlite3") as result:
        yield result


def test_store_period_read_is_multi_item_half_open_and_ordered(store):
    store.upsert_transactions("lightAmmo", [
        _trade("before", _dt(1, 23), 1, 1),
        _trade("ammo", _dt(2, 2), 8, 2),
        _trade("at-end", _dt(3), 9, 1),
    ], fetched_at=_dt(4))
    store.upsert_transactions("beef", [
        _trade("later", _dt(2, 3), 10, 2),
        _trade("earlier", _dt(2, 1), 4, 1),
    ], fetched_at=_dt(4))

    rows = store.transactions_for_period(
        ("lightAmmo", "beef"), int(_dt(2).timestamp()), int(_dt(3).timestamp())
    )

    assert [(row["item_code"], row["id"]) for row in rows] == [
        ("beef", "earlier"),
        ("beef", "later"),
        ("lightAmmo", "ammo"),
    ]


def test_period_prices_use_eligible_completed_trades_and_weighted_median(store):
    store.upsert_transactions("lightAmmo", [
        _trade("start", _dt(2), 10, 1),
        _trade("weighted", _dt(2, 2), 60, 3),
        _trade("invalid", _dt(2, 3), 10, 0),
        _trade("end", _dt(3), 100, 1),
    ], fetched_at=_dt(4))

    prices = load_period_item_prices(
        store,
        item_codes=("lightAmmo", "missing"),
        period_start=_dt(2),
        period_end=_dt(3),
    )

    assert len(prices) == 1
    price = prices[0]
    assert price.item_code == "lightAmmo"
    assert price.representative_price == 20
    assert price.trade_count == 2
    assert price.traded_quantity == 4
    assert price.traded_value == 70
    assert price.first_trade_at == _dt(2)
    assert price.last_trade_at == _dt(2, 2)
    assert price.excluded_transaction_count == 1


def test_base_period_inputs_are_not_affected_by_later_volume(store):
    store.upsert_transactions("steel", [
        _trade("base", _dt(1, 12), 20, 2),
        _trade("later", _dt(3, 12), 10_000, 1_000),
    ], fetched_at=_dt(5))

    prices = load_base_period_price_inputs(
        store,
        item_codes=("steel",),
        base_period_start=_dt(1),
        base_period_end=_dt(2),
    )

    assert prices[0].representative_price == 10
    assert prices[0].traded_quantity == 2
    assert prices[0].traded_value == 20


def test_trailing_series_uses_complete_utc_boundaries_and_reports_missing_items(store):
    store.upsert_transactions("steel", [
        _trade("day-1", _dt(1, 12), 10, 1),
        _trade("day-2", _dt(2, 12), 20, 1),
    ], fetched_at=_dt(5))
    store.upsert_transactions("cement", [
        _trade("cement-day-2", _dt(2, 13), 5, 1),
    ], fetched_at=_dt(5))

    series = load_trailing_period_price_series(
        store,
        item_codes=("steel", "cement"),
        first_as_of=_dt(2),
        last_as_of=_dt(3),
        price_window_days=1,
    )

    assert [observation.as_of for observation in series] == [_dt(2), _dt(3)]
    assert series[0].priced_item_codes == ("steel",)
    assert series[0].missing_item_codes == ("cement",)
    assert series[1].priced_item_codes == ("steel", "cement")
    assert {price.item_code: price.representative_price for price in series[1].prices} == {
        "steel": 20,
        "cement": 5,
    }


def test_trailing_series_rejects_incomplete_day_boundary(store):
    with pytest.raises(ValueError, match="UTC midnight"):
        load_trailing_period_price_series(
            store,
            item_codes=("steel",),
            first_as_of=_dt(2, 1),
            last_as_of=_dt(3),
        )


def test_default_definitions_use_validated_codes_and_disable_market_equipment():
    definitions = {definition.key: definition for definition in default_inflation_index_definitions(
        base_period_start=_dt(1), base_period_end=_dt(8)
    )}

    assert definitions["industrial_expansion"].components == ("steel", "concrete")
    assert definitions["standard_combat"].components == ("steak", "lightAmmo")
    assert definitions["premium_combat"].components == ("cookedFish", "ammo", "heavyAmmo")
    assert definitions["governance"].components == ("paper",)
    assert definitions["infrastructure_operation"].components == ("oil",)
    assert definitions["equipment_inputs"].components == ("steel", "scraps")
    assert definitions["broad_market"].components == ()
    assert definitions["market_equipment"].enabled is False
    assert "mappings" in definitions["market_equipment"].disabled_reason
    assert all(
        definition.weight_source == "base_period_trade_value"
        for key, definition in definitions.items()
        if key not in {"base_pp"}
    )
    assert definitions["base_pp"].weight_source == "base_period_traded_total_upstream_pp"


def test_definition_rejects_base_period_that_does_not_match_price_window():
    with pytest.raises(ValueError, match="duration"):
        default_inflation_index_definitions(base_period_start=_dt(1), base_period_end=_dt(9))


def test_pipeline_allows_backcast_and_base_end_is_exactly_100(store):
    store.upsert_transactions("paper", [
        _trade("early", _dt(1, 12), 10, 1),
        _trade("late", _dt(7, 12), 20, 3),
    ], fetched_at=_dt(9))
    backcast = build_inflation_index_results(
        store, base_period_start=_dt(1), base_period_end=_dt(8),
        first_as_of=_dt(7), last_as_of=_dt(8),
    )
    assert backcast

    governance = next(result for result in build_inflation_index_results(
        store, base_period_start=_dt(1), base_period_end=_dt(8),
        first_as_of=_dt(8), last_as_of=_dt(8),
    ) if result.definition.key == "governance")
    assert governance.observations[0].level == 100


def test_base_pp_uses_total_upstream_pp_and_ignores_direct_store_pp(store):
    store.upsert_transactions("steel", [_trade("steel", _dt(1, 12), 10, 1)], fetched_at=_dt(5))
    store.upsert_transactions("grain", [_trade("grain", _dt(1, 13), 20, 20)], fetched_at=_dt(5))
    store.upsert_transactions("case1", [_trade("case", _dt(1, 13), 100, 1)], fetched_at=_dt(5))
    # These direct values are intentionally wrong/missing for embodied-chain weighting.
    store.upsert_item_production_points({"steel": 999, "grain": None, "case1": 1}, _dt(5))

    results = {result.definition.key: result for result in build_inflation_index_results(
        store, base_period_start=_dt(1), base_period_end=_dt(8),
        first_as_of=_dt(8), last_as_of=_dt(8),
    )}

    assert dict(results["base_pp"].weights) == {
        "grain": pytest.approx(0.5),
        "steel": pytest.approx(0.5),
    }
    assert dict(results["base_pp"].base_prices) == {"grain": 1.0, "steel": 10.0}
    assert dict(results["base_pp"].current_prices) == {"grain": 1.0, "steel": 10.0}
    assert {code for code, _ in results["broad_market"].weights} == {"steel", "grain", "case1"}
    assert ("case1", "missing_total_upstream_pp") in {
        (exclusion.item_code, exclusion.reason) for exclusion in results["base_pp"].exclusions
    }


def test_base_liquidity_thresholds_include_boundaries_and_report_thin_items(store):
    store.upsert_transactions("steel", [
        _trade("steel-1", _dt(1, 12), 10, 1),
        _trade("steel-2", _dt(2, 12), 10, 1),
    ], fetched_at=_dt(9))
    store.upsert_transactions("concrete", [
        _trade("concrete-1", _dt(1, 12), 10, 1),
    ], fetched_at=_dt(9))
    store.upsert_transactions("steak", [
        _trade("steak-1", _dt(1, 12), 5, 0.5),
        _trade("steak-2", _dt(2, 12), 5, 0.5),
    ], fetched_at=_dt(9))

    results = {result.definition.key: result for result in build_inflation_index_results(
        store, base_period_start=_dt(1), base_period_end=_dt(8),
        first_as_of=_dt(8), last_as_of=_dt(8),
        min_base_trade_count=2, min_base_traded_quantity=2,
    )}
    industrial = results["industrial_expansion"]

    assert industrial.weights == (("steel", 1.0),)
    assert ("concrete", "insufficient_base_trade_count") in {
        (exclusion.item_code, exclusion.reason) for exclusion in industrial.exclusions
    }
    assert ("steak", "insufficient_base_traded_quantity") in {
        (exclusion.item_code, exclusion.reason)
        for exclusion in results["standard_combat"].exclusions
    }


def test_pipeline_fetches_shared_transaction_facts_once(store, monkeypatch):
    store.upsert_transactions("paper", [_trade("paper", _dt(1, 12), 10, 1)], fetched_at=_dt(9))
    original = store.transactions_for_period
    calls = []

    def recording_read(item_codes, start_epoch, end_epoch):
        calls.append((tuple(item_codes), start_epoch, end_epoch))
        return original(item_codes, start_epoch, end_epoch)

    monkeypatch.setattr(store, "transactions_for_period", recording_read)
    results = {result.definition.key: result for result in build_inflation_index_results(
        store, base_period_start=_dt(1), base_period_end=_dt(8),
        first_as_of=_dt(8), last_as_of=_dt(8),
    )}
    assert len(calls) == 1
    assert results["market_equipment"].current_prices == ()


def test_current_prices_are_latest_trailing_representative_prices(store):
    store.upsert_transactions("paper", [
        _trade("base", _dt(1, 12), 10, 1),
        _trade("current-low-volume", _dt(8, 12), 30, 1),
        _trade("current-weighted", _dt(9, 12), 80, 4),
    ], fetched_at=_dt(16))

    governance = next(result for result in build_inflation_index_results(
        store, base_period_start=_dt(1), base_period_end=_dt(8),
        first_as_of=_dt(8), last_as_of=_dt(15),
    ) if result.definition.key == "governance")

    assert governance.current_prices == (("paper", 20.0),)


def test_index_pipeline_keeps_base_weights_fixed_when_later_volume_changes(store):
    store.upsert_transactions("steel", [
        _trade("steel-base", _dt(1, 12), 50, 5),
        _trade("steel-later", _dt(15, 12), 10_000, 1_000),
    ], fetched_at=_dt(10))
    store.upsert_transactions("concrete", [
        _trade("concrete-base", _dt(1, 13), 50, 5),
        _trade("concrete-later", _dt(15, 13), 10, 1),
    ], fetched_at=_dt(10))

    result = next(result for result in build_inflation_index_results(
        store, base_period_start=_dt(1), base_period_end=_dt(8),
        first_as_of=_dt(8), last_as_of=_dt(16),
    ) if result.definition.key == "industrial_expansion")

    assert dict(result.weights) == {"steel": 0.5, "concrete": 0.5}
    assert result.observations[-1].level == 100


def test_index_pipeline_retains_unavailable_observations_and_missing_horizons(store):
    store.upsert_transactions("paper", [
        _trade("base", _dt(1, 12), 10, 1),
        _trade("latest", _dt(16, 12), 11, 1),
    ], fetched_at=_dt(10))

    result = next(result for result in build_inflation_index_results(
        store, base_period_start=_dt(1), base_period_end=_dt(8),
        first_as_of=_dt(8), last_as_of=_dt(17),
    ) if result.definition.key == "governance")

    assert len(result.observations) == 10
    assert any(observation.level is None for observation in result.observations)
    assert [change.period_label for change in result.changes] == ["7D", "30D", "90D"]
    assert result.changes[0].change_pct is None
    assert result.changes[1].change_pct is None
    assert result.changes[1].classification == "Insufficient data"
    assert result.changes[2].change_pct is None
