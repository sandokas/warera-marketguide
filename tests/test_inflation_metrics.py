from __future__ import annotations

import math

import pytest

from warera_quant.metrics import (
    base_pp_weights,
    calculate_fixed_weight_index,
    calculate_matched_index_change,
    classify_inflation,
    normalize_weights,
    purchasing_power_change,
    quantity_weighted_median,
    traded_value_weights,
)


@pytest.mark.parametrize(
    ("trades", "expected"),
    [
        ([{"unit_price": 7, "quantity": 2}], 7),
        ([{"unit_price": 3, "quantity": 3}, {"unit_price": 1, "quantity": 1}], 3),
        ([{"unit_price": 2, "quantity": 2}, {"unit_price": 1, "quantity": 2}], 1),
        ([{"unit_price": 4, "quantity": 1}, {"unit_price": 4, "quantity": 2}], 4),
        ([{"unit_price": 9, "quantity": 0.25}, {"unit_price": 5, "quantity": 0.75}], 5),
        ([{"unit_price": 8, "quantity": 2}, {"unit_price": 2, "quantity": 3}], 2),
    ],
)
def test_quantity_weighted_median(trades, expected):
    assert quantity_weighted_median(trades) == expected


def test_quantity_weighted_median_excludes_invalid_values_and_does_not_mutate():
    trades = [
        {"unit_price": 9, "quantity": 2},
        {"unit_price": 0, "quantity": 100},
        {"unit_price": math.inf, "quantity": 100},
        {"unit_price": 1, "quantity": 0},
        {"unit_price": 1, "quantity": -1},
        {"unit_price": "bad", "quantity": 5},
    ]
    original = [row.copy() for row in trades]

    assert quantity_weighted_median(trades) == 9
    assert trades == original
    assert quantity_weighted_median(trades[1:]) is None


def test_weight_construction_normalizes_only_positive_finite_values():
    raw = {"steel": 30, "cement": 10, "bad": math.nan, "zero": 0}

    assert normalize_weights(raw) == {"steel": 0.75, "cement": 0.25}
    assert traded_value_weights(raw) == {"steel": 0.75, "cement": 0.25}
    assert math.isclose(math.fsum(normalize_weights(raw).values()), 1.0)
    assert normalize_weights({"bad": None, "zero": 0}) == {}


def test_base_pp_weights_use_total_upstream_pre_bonus_pp_and_exclude_invalid_values():
    quantities = {"steel": 10, "cement": 20, "paper": 8, "oil": 4}
    total_upstream_pp = {
        "steel": 2,  # Direct PP plus PP embodied in material inputs.
        "cement": 1,
        "paper": 0,
        "oil": math.nan,
    }

    assert base_pp_weights(quantities, total_upstream_pp) == {"steel": 0.5, "cement": 0.5}


def test_fixed_weight_index_matches_hand_calculation_and_ignores_later_volume():
    weights = traded_value_weights({"steel": 75, "cement": 25})
    result = calculate_fixed_weight_index(
        weights,
        {"steel": 10, "cement": 20},
        {"steel": 12, "cement": 18},
    )

    assert result.level == pytest.approx(112.5)
    assert result.coverage_pct == pytest.approx(100)
    assert result.missing_item_codes == ()


def test_level_renormalizes_available_weight_at_exact_threshold():
    result = calculate_fixed_weight_index(
        {"steel": 0.8, "cement": 0.2},
        {"steel": 10, "cement": 10},
        {"steel": 11},
        minimum_coverage_pct=80,
    )

    assert result.level == pytest.approx(110)
    assert result.coverage_pct == pytest.approx(80)
    assert result.priced_component_count == 1
    assert result.missing_item_codes == ("cement",)


def test_level_is_unavailable_just_below_threshold():
    result = calculate_fixed_weight_index(
        {"steel": 0.799, "cement": 0.201},
        {"steel": 10, "cement": 10},
        {"steel": 11},
        minimum_coverage_pct=80,
    )

    assert result.level is None
    assert result.coverage_pct == pytest.approx(79.9)


def test_matched_change_uses_same_components_at_both_endpoints():
    result = calculate_matched_index_change(
        {"steel": 0.5, "cement": 0.3, "paper": 0.2},
        {"steel": 10, "cement": 20},
        {"steel": 12, "paper": 8},
        minimum_coverage_pct=50,
    )

    assert result.change_pct == pytest.approx(20)
    assert result.matched_coverage_pct == pytest.approx(50)
    assert result.matched_item_codes == ("steel",)
    assert result.missing_item_codes == ("cement", "paper")
    assert result.classification == "Inflation"
    assert sum(row.contribution_pct_points for row in result.contributors) == pytest.approx(result.change_pct)


def test_two_item_contributions_are_additive_at_full_precision():
    result = calculate_matched_index_change(
        {"a": 3, "b": 1},
        {"a": 10, "b": 20},
        {"a": 11, "b": 18},
    )

    assert result.change_pct == pytest.approx(5)
    assert [row.item_code for row in result.contributors] == ["a", "b"]
    assert math.fsum(row.contribution_pct_points for row in result.contributors) == result.change_pct


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (0.100001, "Inflation"),
        (0.1, "Stable"),
        (0, "Stable"),
        (-0.1, "Stable"),
        (-0.100001, "Deflation"),
        (None, "Insufficient data"),
    ],
)
def test_inflation_classification(change, expected):
    assert classify_inflation(change) == expected


def test_insufficient_matched_coverage_has_no_change_or_contributions():
    result = calculate_matched_index_change(
        {"a": 0.7, "b": 0.3},
        {"a": 10},
        {"a": 12},
    )

    assert result.change_pct is None
    assert result.classification == "Insufficient data"
    assert result.purchasing_power_change_pct is None
    assert result.contributors == ()


def test_purchasing_power_uses_reciprocal_calculation():
    assert purchasing_power_change(10) == pytest.approx(-9.0909090909)
    assert purchasing_power_change(-100) is None
    assert purchasing_power_change(None) is None


def test_single_component_uses_common_paths():
    level = calculate_fixed_weight_index({"paper": 1}, {"paper": 5}, {"paper": 6})
    change = calculate_matched_index_change({"paper": 1}, {"paper": 5}, {"paper": 6})

    assert level.level == pytest.approx(120)
    assert change.change_pct == pytest.approx(20)
    assert change.contributors[0].matched_weight == 1


@pytest.mark.parametrize("threshold", [-1, 101, math.nan])
def test_invalid_coverage_threshold_is_rejected(threshold):
    with pytest.raises(ValueError, match="minimum_coverage_pct"):
        calculate_fixed_weight_index({}, {}, {}, minimum_coverage_pct=threshold)
    with pytest.raises(ValueError, match="minimum_coverage_pct"):
        calculate_matched_index_change({}, {}, {}, minimum_coverage_pct=threshold)


@pytest.mark.parametrize("band", [-0.1, math.inf, math.nan])
def test_invalid_neutral_band_is_rejected(band):
    with pytest.raises(ValueError, match="neutral_band_pct"):
        classify_inflation(0, neutral_band_pct=band)
    with pytest.raises(ValueError, match="neutral_band_pct"):
        calculate_matched_index_change({}, {}, {}, neutral_band_pct=band)
