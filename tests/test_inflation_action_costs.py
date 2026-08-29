from types import SimpleNamespace

import pytest

from warera_quant.market_data import (
    ActionCostDefinition,
    build_action_cost_results,
    default_action_cost_definitions,
)
from warera_quant.metrics import calculate_fixed_action_cost


def test_fixed_action_cost_calculates_components_and_total_without_mutating_inputs():
    quantities = {"steel": 2, "scraps": 3}
    prices = {"steel": 4.5, "scraps": 2}

    result = calculate_fixed_action_cost(quantities, prices)

    assert result.total_cost == 15
    assert result.coverage_pct == 100
    assert result.required_component_count == 2
    assert result.priced_component_count == 2
    assert result.missing_item_codes == ()
    assert [(row.item_code, row.quantity, row.representative_price, row.cost) for row in result.components] == [
        ("steel", 2, 4.5, 9),
        ("scraps", 3, 2, 6),
    ]
    assert quantities == {"steel": 2, "scraps": 3}
    assert prices == {"steel": 4.5, "scraps": 2}


@pytest.mark.parametrize("quantity", [0, -1, float("inf"), float("nan"), None, "bad"])
def test_fixed_action_cost_rejects_non_positive_or_invalid_quantities(quantity):
    with pytest.raises(ValueError, match="finite and positive"):
        calculate_fixed_action_cost({"steel": quantity}, {"steel": 1})


def test_fixed_action_cost_requires_a_non_empty_basket_and_item_code():
    with pytest.raises(ValueError, match="At least one"):
        calculate_fixed_action_cost({}, {})
    with pytest.raises(ValueError, match="non-empty"):
        calculate_fixed_action_cost({" ": 1}, {})


@pytest.mark.parametrize("price", [None, 0, -1, float("inf"), float("nan"), "bad"])
def test_fixed_action_cost_reports_unavailable_price_without_substitution(price):
    result = calculate_fixed_action_cost({"steel": 2, "scraps": 1}, {"steel": price, "scraps": 3})

    assert result.total_cost is None
    assert result.coverage_pct == 50
    assert result.missing_item_codes == ("steel",)
    assert [(row.item_code, row.cost) for row in result.components] == [("scraps", 3)]


def test_default_action_cost_definitions_contain_only_validated_benchmarks():
    definitions = default_action_cost_definitions()
    by_key = {definition.key: definition for definition in definitions}

    assert len(definitions) == 5
    assert by_key["company_move"].quantities == (("concrete", 5.0),)
    assert [by_key[f"mu_hq_level_{level}_hourly_upkeep"].quantities for level in range(1, 5)] == [
        (("oil", float(quantity)),) for quantity in (1, 2, 5, 10)
    ]
    assert {definition.source_url for definition in definitions} == {
        "https://warera.io/en/articles/warera-complete-game-guide-so-far-6c68c4"
    }
    assert {definition.source_published_at for definition in definitions} == {"2026-06-18"}
    assert {definition.provenance for definition in definitions} == {
        "WarEra-hosted community guide, checked 2026-08-28"
    }
    assert not any(
        forbidden in definition.key
        for definition in definitions
        for forbidden in ("equipment", "battle_order", "company_creation", "bunker", "combat")
    )


def test_build_action_cost_results_reuses_current_inflation_prices():
    inflation_results = (
        SimpleNamespace(current_prices=(("steel", 2.5), ("concrete", 4.0))),
        SimpleNamespace(current_prices=(("oil", 3.0),)),
    )

    results = build_action_cost_results(inflation_results)
    by_key = {result.definition.key: result for result in results}

    assert by_key["company_move"].total_cost == 20
    assert by_key["mu_hq_level_1_hourly_upkeep"].total_cost == 3
    assert by_key["mu_hq_level_4_hourly_upkeep"].total_cost == 30
    assert all(result.coverage_pct == 100 for result in results)


def test_build_action_cost_results_rejects_conflicting_representative_prices():
    with pytest.raises(ValueError, match="Conflicting current representative prices for oil"):
        build_action_cost_results((
            SimpleNamespace(current_prices=(("oil", 1.0),)),
            SimpleNamespace(current_prices=(("oil", 2.0),)),
        ))


def test_build_action_cost_results_exposes_missing_current_prices():
    definition = ActionCostDefinition(
        key="test_action",
        name="Test action",
        category="Test",
        action_description="A test action.",
        unit_description="one action",
        quantities=(("steel", 2), ("scraps", 3)),
        source_url="https://example.test/source",
        source_published_at="2026-01-01",
        provenance="Test fixture",
    )

    result = build_action_cost_results(
        (SimpleNamespace(current_prices=(("steel", 5),)),),
        definitions=(definition,),
    )[0]

    assert result.total_cost is None
    assert result.coverage_pct == 50
    assert result.missing_item_codes == ("scraps",)
    assert [(component.item_code, component.cost) for component in result.components] == [("steel", 10)]
