from __future__ import annotations

import re
import math
from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from collections.abc import Iterable
from typing import Any

from .market_store import MarketStore
from .metrics import (
    DEFAULT_INFLATION_MINIMUM_COVERAGE_PCT,
    FORECAST_MODEL_VERSION,
    ForecastEvaluationRow,
    ForecastValidationResult,
    FairValueGuidance,
    FlipAssumptions,
    InflationContribution,
    ActionCostComponent,
    calculate_book_sweep,
    calculate_fair_value_guidance,
    calculate_direction_signal,
    calculate_flip_opportunity,
    calculate_liquidity_score,
    classify_future_bid_outcome,
    classify_tendency,
    summarize_forecast_evaluations,
    summarize_order_book,
    quantity_weighted_median,
    base_pp_weights,
    calculate_fixed_weight_index,
    calculate_matched_index_change,
    traded_value_weights,
    total_upstream_production_points,
    calculate_fixed_action_cost,
)


SUPPORTED_REPORT_WINDOWS = ("1D", "7D", "30D", "90D", "1Y")
DEFAULT_REPORT_WINDOWS = ("1D", "7D", "30D")
HIGHLIGHT_HISTORY_DAYS = 30
FORECAST_TRAILING_SECONDS = 7 * 24 * 60 * 60
DEFAULT_INFLATION_MIN_BASE_TRADE_COUNT = 1
DEFAULT_INFLATION_MIN_BASE_TRADED_QUANTITY = 1e-12


@dataclass(frozen=True)
class PeriodItemPrice:
    """Completed-transaction price facts for one item and half-open UTC period."""

    item_code: str
    period_start: datetime
    period_end: datetime
    representative_price: float
    trade_count: int
    traded_quantity: float
    traded_value: float
    first_trade_at: datetime
    last_trade_at: datetime
    excluded_transaction_count: int = 0


@dataclass(frozen=True)
class HistoricalPeriodPrices:
    """Coverage-ready prices at one historical UTC boundary."""

    as_of: datetime
    period_start: datetime
    period_end: datetime
    prices: tuple[PeriodItemPrice, ...]
    requested_item_codes: tuple[str, ...]

    @property
    def priced_item_codes(self) -> tuple[str, ...]:
        return tuple(price.item_code for price in self.prices)

    @property
    def missing_item_codes(self) -> tuple[str, ...]:
        priced = set(self.priced_item_codes)
        return tuple(code for code in self.requested_item_codes if code not in priced)


@dataclass(frozen=True)
class IndexDefinition:
    key: str
    name: str
    description: str
    version: str
    effective_from: datetime
    method: str
    components: tuple[str, ...]
    weight_source: str
    base_period_start: datetime
    base_period_end: datetime
    minimum_coverage_pct: float = DEFAULT_INFLATION_MINIMUM_COVERAGE_PCT
    price_window_days: int = 7
    min_base_trade_count: int = DEFAULT_INFLATION_MIN_BASE_TRADE_COUNT
    min_base_traded_quantity: float = DEFAULT_INFLATION_MIN_BASE_TRADED_QUANTITY
    enabled: bool = True
    disabled_reason: str | None = None


@dataclass(frozen=True)
class IndexObservation:
    index_key: str
    index_version: str
    as_of: datetime
    is_provisional: bool
    level: float | None
    coverage_pct: float
    eligible_component_count: int
    priced_component_count: int
    missing_item_codes: tuple[str, ...]


@dataclass(frozen=True)
class IndexChange:
    index_key: str
    index_version: str
    period_label: str
    start_at: datetime
    end_at: datetime
    change_pct: float | None
    classification: str
    matched_coverage_pct: float
    purchasing_power_change_pct: float | None
    contributors: tuple[InflationContribution, ...]
    missing_item_codes: tuple[str, ...]
    annualized_change_pct: float | None = None


@dataclass(frozen=True)
class InflationEvolutionPoint:
    """Cumulative matched-basket price change from the displayed month's start."""

    as_of: datetime
    change_pct: float | None
    matched_coverage_pct: float


@dataclass(frozen=True)
class InflationIndexResult:
    definition: IndexDefinition
    weights: tuple[tuple[str, float], ...]
    base_prices: tuple[tuple[str, float], ...]
    observations: tuple[IndexObservation, ...]
    changes: tuple[IndexChange, ...]
    exclusions: tuple["IndexComponentExclusion", ...] = ()
    current_prices: tuple[tuple[str, float], ...] = ()
    monthly_evolution: tuple[InflationEvolutionPoint, ...] = ()


@dataclass(frozen=True)
class IndexComponentExclusion:
    item_code: str
    reason: str


@dataclass(frozen=True)
class ActionCostDefinition:
    """Sourced fixed quantities for one concrete gameplay action."""

    key: str
    name: str
    category: str
    action_description: str
    unit_description: str
    quantities: tuple[tuple[str, float], ...]
    source_url: str
    source_published_at: str
    provenance: str


@dataclass(frozen=True)
class ActionCostResult:
    """Current completed-market cost of one fixed gameplay action."""

    definition: ActionCostDefinition
    total_cost: float | None
    coverage_pct: float
    required_component_count: int
    priced_component_count: int
    missing_item_codes: tuple[str, ...]
    components: tuple[ActionCostComponent, ...]


_ACTION_COST_SOURCE_URL = "https://warera.io/en/articles/warera-complete-game-guide-so-far-6c68c4"
_ACTION_COST_SOURCE_PUBLISHED_AT = "2026-06-18"
_ACTION_COST_PROVENANCE = "WarEra-hosted community guide, checked 2026-08-28"


def default_action_cost_definitions() -> tuple[ActionCostDefinition, ...]:
    """Return only fixed action quantities stated exactly by the cited source."""
    source = dict(
        source_url=_ACTION_COST_SOURCE_URL,
        source_published_at=_ACTION_COST_SOURCE_PUBLISHED_AT,
        provenance=_ACTION_COST_PROVENANCE,
    )
    definitions = [ActionCostDefinition(
        key="company_move",
        name="Move a company",
        category="Industrial expansion",
        action_description="Move one company to another location.",
        unit_description="one company move",
        quantities=(("concrete", 5.0),),
        **source,
    )]
    definitions.extend(ActionCostDefinition(
        key=f"mu_hq_level_{level}_hourly_upkeep",
        name=f"MU HQ level {level} hourly upkeep",
        category="Infrastructure operation",
        action_description=f"Keep one level {level} military-unit headquarters operating for one hour.",
        unit_description="one hour of MU HQ operation",
        quantities=(("oil", float(oil)),),
        **source,
    ) for level, oil in enumerate((1, 2, 5, 10), start=1))
    return tuple(definitions)


def build_action_cost_results(
    inflation_results: Iterable[InflationIndexResult],
    *,
    definitions: Iterable[ActionCostDefinition] | None = None,
) -> tuple[ActionCostResult, ...]:
    """Price benchmarks from representative prices already loaded for inflation."""
    current_prices: dict[str, float] = {}
    for inflation_result in inflation_results:
        for item_code, price in inflation_result.current_prices:
            existing = current_prices.get(item_code)
            if existing is not None and not math.isclose(existing, price, rel_tol=1e-12, abs_tol=1e-12):
                raise ValueError(f"Conflicting current representative prices for {item_code}.")
            current_prices[item_code] = price

    benchmark_definitions = (
        default_action_cost_definitions() if definitions is None else tuple(definitions)
    )
    results: list[ActionCostResult] = []
    for definition in benchmark_definitions:
        calculated = calculate_fixed_action_cost(dict(definition.quantities), current_prices)
        results.append(ActionCostResult(
            definition=definition,
            total_cost=calculated.total_cost,
            coverage_pct=calculated.coverage_pct,
            required_component_count=calculated.required_component_count,
            priced_component_count=calculated.priced_component_count,
            missing_item_codes=calculated.missing_item_codes,
            components=calculated.components,
        ))
    return tuple(results)


_PURPOSE_INDEXES = (
    ("industrial_expansion", "Industrial Expansion Index", "Industrial building, moving, and upgrade price pressure.", ("steel", "concrete")),
    ("standard_combat", "Standard Combat Index", "Routine war-consumption price pressure.", ("steak", "lightAmmo")),
    ("premium_combat", "Premium Combat Index", "Wealthier war-consumption price pressure.", ("cookedFish", "ammo", "heavyAmmo")),
    ("governance", "Governance and Battle Administration Index", "Pact, law, and battle-order price pressure.", ("paper",)),
    ("infrastructure_operation", "Infrastructure Operation Index", "Bonus-building operating price pressure.", ("oil",)),
    ("equipment_inputs", "Crafted Equipment Input Index", "Steel and scrap material-price pressure on crafting.", ("steel", "scraps")),
)


def default_inflation_index_definitions(
    *, base_period_start: datetime, base_period_end: datetime, version: str = "1",
    price_window_days: int = 7,
    min_base_trade_count: int = DEFAULT_INFLATION_MIN_BASE_TRADE_COUNT,
    min_base_traded_quantity: float = DEFAULT_INFLATION_MIN_BASE_TRADED_QUANTITY,
) -> tuple[IndexDefinition, ...]:
    start = _require_utc_day_boundary(base_period_start, "base_period_start")
    end = _require_utc_day_boundary(base_period_end, "base_period_end")
    if price_window_days < 1:
        raise ValueError("price_window_days must be at least 1.")
    if end - start != timedelta(days=price_window_days):
        raise ValueError("Base period duration must equal price_window_days.")
    if min_base_trade_count < 1:
        raise ValueError("min_base_trade_count must be at least 1.")
    if not math.isfinite(min_base_traded_quantity) or min_base_traded_quantity <= 0:
        raise ValueError("min_base_traded_quantity must be finite and positive.")
    liquidity = dict(
        min_base_trade_count=min_base_trade_count,
        min_base_traded_quantity=float(min_base_traded_quantity),
        price_window_days=price_window_days,
    )
    definitions = [IndexDefinition(
        key="broad_market", name="Broad Market Price Index",
        description="General movement in eligible completed-trade prices.", version=version,
        effective_from=end,
        method="broad_value", components=(), weight_source="base_period_trade_value",
        base_period_start=start, base_period_end=end,
        **liquidity,
    )]
    definitions.extend(IndexDefinition(
        key=key, name=name, description=description, version=version,
        effective_from=end,
        method="broad_value", components=components,
        weight_source="base_period_trade_value", base_period_start=start, base_period_end=end,
        **liquidity,
    ) for key, name, description, components in _PURPOSE_INDEXES)
    definitions.append(IndexDefinition(
        key="base_pp", name="Total Upstream PP Inflation Index",
        description="Market value of total upstream embodied production effort before bonuses.", version=version,
        effective_from=end,
        method="base_pp_value", components=(), weight_source="base_period_traded_total_upstream_pp",
        base_period_start=start, base_period_end=end,
        **liquidity,
    ))
    definitions.append(IndexDefinition(
        key="market_equipment", name="Market Equipment Index",
        description="Completed-market movement for eligible finished equipment.", version=version,
        effective_from=end,
        method="broad_value", components=(), weight_source="base_period_trade_value",
        base_period_start=start, base_period_end=end, enabled=False,
        disabled_reason="Validated finished-equipment item mappings are not yet available.",
        **liquidity,
    ))
    return tuple(definitions)


def build_inflation_index_results(
    store: MarketStore,
    *,
    base_period_start: datetime,
    base_period_end: datetime,
    first_as_of: datetime,
    last_as_of: datetime,
    version: str = "1",
    price_window_days: int = 7,
    min_base_trade_count: int = DEFAULT_INFLATION_MIN_BASE_TRADE_COUNT,
    min_base_traded_quantity: float = DEFAULT_INFLATION_MIN_BASE_TRADED_QUANTITY,
) -> tuple[InflationIndexResult, ...]:
    """Build reproducible Phase 1 histories and latest 7/30/90-day changes."""
    definitions = default_inflation_index_definitions(
        base_period_start=base_period_start, base_period_end=base_period_end, version=version,
        price_window_days=price_window_days,
        min_base_trade_count=min_base_trade_count,
        min_base_traded_quantity=min_base_traded_quantity,
    )
    first = _require_utc_day_boundary(first_as_of, "first_as_of")
    last = _require_utc_day_boundary(last_as_of, "last_as_of")
    if last < first:
        raise ValueError("last_as_of cannot be before first_as_of.")
    all_codes = tuple(store.item_codes())
    earliest = min(
        definitions[0].base_period_start,
        first - timedelta(days=definitions[0].price_window_days),
    )
    rows = store.transactions_for_period(all_codes, int(earliest.timestamp()), int(last.timestamp()))
    row_index = _index_transaction_rows(rows)
    all_base_inputs = _period_item_prices_from_index(
        row_index, codes=all_codes, period_start=definitions[0].base_period_start,
        period_end=definitions[0].base_period_end,
    )
    all_base_by_code = {price.item_code: price for price in all_base_inputs}
    shared_series = _trailing_period_price_series_from_index(
        row_index, codes=all_codes, first_as_of=first, last_as_of=last,
        price_window_days=definitions[0].price_window_days,
    ) if all_codes else ()
    results: list[InflationIndexResult] = []
    for definition in definitions:
        if not definition.enabled:
            results.append(InflationIndexResult(definition, (), (), (), (), (), ()))
            continue
        candidate_codes = definition.components or all_codes
        base_inputs = tuple(
            all_base_by_code[code] for code in candidate_codes if code in all_base_by_code
        )
        base_by_code = {price.item_code: price for price in base_inputs}
        exclusions: list[IndexComponentExclusion] = []
        liquid_inputs: list[PeriodItemPrice] = []
        for code in candidate_codes:
            price = base_by_code.get(code)
            if price is None or price.trade_count < definition.min_base_trade_count:
                exclusions.append(IndexComponentExclusion(code, "insufficient_base_trade_count"))
            elif price.traded_quantity < definition.min_base_traded_quantity:
                exclusions.append(IndexComponentExclusion(code, "insufficient_base_traded_quantity"))
            else:
                liquid_inputs.append(price)
        if definition.key == "base_pp":
            embodied_pp = {
                price.item_code: total_upstream_production_points(item_code=price.item_code)
                for price in liquid_inputs
            }
            for price in liquid_inputs:
                if embodied_pp[price.item_code] is None:
                    exclusions.append(IndexComponentExclusion(price.item_code, "missing_total_upstream_pp"))
            weights = base_pp_weights(
                {price.item_code: price.traded_quantity for price in liquid_inputs}, embodied_pp
            )
        else:
            weights = traded_value_weights({price.item_code: price.traded_value for price in liquid_inputs})
        base_prices = {
            price.item_code: price.representative_price
            for price in liquid_inputs if price.item_code in weights
        }
        component_codes = tuple(weights)
        component_set = set(component_codes)
        series = tuple(HistoricalPeriodPrices(
            as_of=period.as_of, period_start=period.period_start, period_end=period.period_end,
            prices=tuple(price for price in period.prices if price.item_code in component_set),
            requested_item_codes=component_codes,
        ) for period in shared_series) if component_codes else ()
        prices_by_as_of: dict[datetime, dict[str, float]] = {}
        observations: list[IndexObservation] = []
        for period in series:
            period_prices = {price.item_code: price.representative_price for price in period.prices}
            prices_by_as_of[period.as_of] = period_prices
            level = calculate_fixed_weight_index(
                weights, base_prices, period_prices,
                minimum_coverage_pct=definition.minimum_coverage_pct,
            )
            observations.append(IndexObservation(
                index_key=definition.key, index_version=definition.version, as_of=period.as_of,
                is_provisional=False, level=level.level, coverage_pct=level.coverage_pct,
                eligible_component_count=level.eligible_component_count,
                priced_component_count=level.priced_component_count,
                missing_item_codes=level.missing_item_codes,
            ))
        end = last
        changes: list[IndexChange] = []
        for days in (7, 30, 90):
            start = end - timedelta(days=days)
            matched = calculate_matched_index_change(
                weights, prices_by_as_of.get(start, {}), prices_by_as_of.get(end, {}),
                minimum_coverage_pct=definition.minimum_coverage_pct,
            )
            changes.append(IndexChange(
                index_key=definition.key, index_version=definition.version,
                period_label=f"{days}D", start_at=start, end_at=end,
                change_pct=matched.change_pct, classification=matched.classification,
                matched_coverage_pct=matched.matched_coverage_pct,
                purchasing_power_change_pct=matched.purchasing_power_change_pct,
                contributors=matched.contributors, missing_item_codes=matched.missing_item_codes,
                annualized_change_pct=(
                    ((1.0 + matched.change_pct / 100.0) ** (365.0 / days) - 1.0) * 100.0
                    if matched.change_pct is not None else None
                ),
            ))
        month_start = last - timedelta(days=30)
        month_start_prices = prices_by_as_of.get(month_start, {})
        monthly_evolution = []
        for as_of in sorted(date for date in prices_by_as_of if date >= month_start):
            matched = calculate_matched_index_change(
                weights, month_start_prices, prices_by_as_of[as_of],
                minimum_coverage_pct=definition.minimum_coverage_pct,
            )
            monthly_evolution.append(InflationEvolutionPoint(
                as_of=as_of, change_pct=matched.change_pct,
                matched_coverage_pct=matched.matched_coverage_pct,
            ))
        results.append(InflationIndexResult(
            definition=definition, weights=tuple(weights.items()), base_prices=tuple(base_prices.items()),
            current_prices=tuple(prices_by_as_of.get(last, {}).items()),
            observations=tuple(observations), changes=tuple(changes), exclusions=tuple(exclusions),
            monthly_evolution=tuple(monthly_evolution),
        ))
    return tuple(results)


def load_period_item_prices(
    store: MarketStore,
    *,
    item_codes: Iterable[str],
    period_start: datetime,
    period_end: datetime,
) -> tuple[PeriodItemPrice, ...]:
    """Aggregate eligible completed transactions inside a half-open UTC period."""
    start = _as_utc(period_start)
    end = _as_utc(period_end)
    if end <= start:
        raise ValueError("period_end must be after period_start.")
    codes = _normalized_item_codes(item_codes)
    if not codes:
        return ()
    rows = store.transactions_for_period(codes, int(start.timestamp()), int(end.timestamp()))
    return _period_item_prices_from_rows(rows, codes=codes, period_start=start, period_end=end)


def load_base_period_price_inputs(
    store: MarketStore,
    *,
    item_codes: Iterable[str],
    base_period_start: datetime,
    base_period_end: datetime,
) -> tuple[PeriodItemPrice, ...]:
    """Load immutable base-period prices and trade totals for weight construction."""
    return load_period_item_prices(
        store,
        item_codes=item_codes,
        period_start=base_period_start,
        period_end=base_period_end,
    )


def load_trailing_period_price_series(
    store: MarketStore,
    *,
    item_codes: Iterable[str],
    first_as_of: datetime,
    last_as_of: datetime,
    price_window_days: int = 7,
) -> tuple[HistoricalPeriodPrices, ...]:
    """Build one trailing-window observation per complete UTC-day boundary.

    Both ``first_as_of`` and ``last_as_of`` must be UTC midnight boundaries and
    are included. Callers may separately construct a labelled provisional point
    for an incomplete day; this function never silently mixes one in.
    """
    if price_window_days < 1:
        raise ValueError("price_window_days must be at least 1.")
    first = _require_utc_day_boundary(first_as_of, "first_as_of")
    last = _require_utc_day_boundary(last_as_of, "last_as_of")
    if last < first:
        raise ValueError("last_as_of cannot be before first_as_of.")
    codes = _normalized_item_codes(item_codes)
    if not codes:
        return ()

    earliest = first - timedelta(days=price_window_days)
    rows = store.transactions_for_period(codes, int(earliest.timestamp()), int(last.timestamp()))
    return _trailing_period_price_series_from_index(
        _index_transaction_rows(rows), codes=codes, first_as_of=first,
        last_as_of=last, price_window_days=price_window_days,
    )


def _trailing_period_price_series_from_index(
    row_index: dict[str, tuple[list[int], list[dict[str, Any]]]],
    *,
    codes: tuple[str, ...],
    first_as_of: datetime,
    last_as_of: datetime,
    price_window_days: int,
) -> tuple[HistoricalPeriodPrices, ...]:
    observations: list[HistoricalPeriodPrices] = []
    as_of = first_as_of
    while as_of <= last_as_of:
        period_start = as_of - timedelta(days=price_window_days)
        observations.append(HistoricalPeriodPrices(
            as_of=as_of,
            period_start=period_start,
            period_end=as_of,
            prices=_period_item_prices_from_index(
                row_index, codes=codes, period_start=period_start, period_end=as_of
            ),
            requested_item_codes=codes,
        ))
        as_of += timedelta(days=1)
    return tuple(observations)


def _period_item_prices_from_rows(
    rows: Iterable[dict[str, Any]],
    *,
    codes: tuple[str, ...],
    period_start: datetime,
    period_end: datetime,
) -> tuple[PeriodItemPrice, ...]:
    return _period_item_prices_from_index(
        _index_transaction_rows(rows), codes=codes,
        period_start=period_start, period_end=period_end,
    )


def _index_transaction_rows(
    rows: Iterable[dict[str, Any]],
) -> dict[str, tuple[list[int], list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        code = str(row.get("item_code", "")).strip()
        grouped.setdefault(code, []).append(row)
    result = {}
    for code, code_rows in grouped.items():
        ordered = sorted(code_rows, key=lambda row: (int(row["created_at_epoch"]), str(row.get("id", ""))))
        result[code] = ([int(row["created_at_epoch"]) for row in ordered], ordered)
    return result


def _period_item_prices_from_index(
    row_index: dict[str, tuple[list[int], list[dict[str, Any]]]],
    *,
    codes: tuple[str, ...],
    period_start: datetime,
    period_end: datetime,
) -> tuple[PeriodItemPrice, ...]:
    folded_index = {code.casefold(): value for code, value in row_index.items()}

    results: list[PeriodItemPrice] = []
    for code in codes:
        epochs, all_rows = folded_index.get(code.casefold(), ([], []))
        start = bisect_left(epochs, int(period_start.timestamp()))
        end = bisect_left(epochs, int(period_end.timestamp()))
        code_rows = all_rows[start:end]
        eligible: list[dict[str, Any]] = []
        excluded = 0
        for row in code_rows:
            price = _positive_float(row.get("unit_price"))
            quantity = _positive_float(row.get("quantity"))
            if price is None or quantity is None:
                excluded += 1
                continue
            eligible.append({**row, "unit_price": price, "quantity": quantity})
        representative_price = quantity_weighted_median(eligible)
        if representative_price is None:
            continue
        quantities = [float(row["quantity"]) for row in eligible]
        values = [float(row["unit_price"]) * float(row["quantity"]) for row in eligible]
        first_at = _as_utc(datetime.fromtimestamp(int(eligible[0]["created_at_epoch"]), timezone.utc))
        last_at = _as_utc(datetime.fromtimestamp(int(eligible[-1]["created_at_epoch"]), timezone.utc))
        results.append(PeriodItemPrice(
            item_code=code,
            period_start=period_start,
            period_end=period_end,
            representative_price=representative_price,
            trade_count=len(eligible),
            traded_quantity=sum(quantities),
            traded_value=sum(values),
            first_trade_at=first_at,
            last_trade_at=last_at,
            excluded_transaction_count=excluded,
        ))
    return tuple(results)


def _normalized_item_codes(item_codes: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in item_codes:
        code = str(value).strip()
        folded = code.casefold()
        if code and folded not in seen:
            seen.add(folded)
            result.append(code)
    return tuple(result)


def _require_utc_day_boundary(value: datetime, field_name: str) -> datetime:
    result = _as_utc(value)
    if result.time() != datetime.min.time():
        raise ValueError(f"{field_name} must be a UTC midnight boundary.")
    return result


def evaluate_item_forecast(
    store: MarketStore,
    *,
    item_code: str,
    horizon_hours: float = 24.0,
    target_max_lag_hours: float = 6.0,
    min_samples: int = 30,
    quantity: float = 1.0,
    min_tick: float = 0.001,
) -> ForecastValidationResult:
    if not math.isfinite(horizon_hours) or horizon_hours <= 0:
        raise ValueError("horizon_hours must be positive.")
    if not math.isfinite(target_max_lag_hours) or target_max_lag_hours < 0:
        raise ValueError("target_max_lag_hours cannot be negative.")
    if min_samples < 1:
        raise ValueError("min_samples must be at least 1.")
    # Validate quantity before a sparse history can bypass sweep calculation.
    calculate_book_sweep([], side="buy", quantity=quantity)

    observations = store.order_book_history_with_levels(item_code)
    if not observations:
        return summarize_forecast_evaluations(
            item_code=item_code,
            horizon_hours=horizon_hours,
            rows=(),
            current_signal="Unavailable",
            min_samples=min_samples,
        )
    earliest_epoch = int(observations[0]["observed_at_epoch"]) - FORECAST_TRAILING_SECONDS
    transactions = store.transactions_for_window(item_code, earliest_epoch)
    features = build_forecast_features(observations, transactions)

    newest_observation = observations[-1]
    current_feature = next(
        (feature for feature in reversed(features) if feature["observation_id"] == newest_observation["observation_id"]),
        None,
    )
    current_signal = (
        calculate_direction_signal(
            momentum_pct=current_feature["momentum_pct"],
            fair_gap_pct=current_feature["fair_gap_pct"],
        )
        if current_feature is not None
        else None
    )

    horizon_seconds = horizon_hours * 60 * 60
    max_lag_seconds = target_max_lag_hours * 60 * 60
    observation_epochs = [int(observation["observed_at_epoch"]) for observation in observations]
    evaluated: list[ForecastEvaluationRow] = []
    for feature in features:
        target_epoch = feature["observed_at_epoch"] + horizon_seconds
        target_index = bisect_left(observation_epochs, target_epoch)
        if target_index >= len(observations):
            continue
        target = observations[target_index]
        if target["observed_at_epoch"] > target_epoch + max_lag_seconds:
            continue
        future_bid = _positive_float(target.get("best_bid"))
        if future_bid is None:
            continue
        direction = calculate_direction_signal(
            momentum_pct=feature["momentum_pct"], fair_gap_pct=feature["fair_gap_pct"]
        )
        outcome, future_return = classify_future_bid_outcome(
            current_bid=feature["best_bid"], future_bid=future_bid, min_tick=min_tick
        )
        gross_flip_return = _realized_gross_flip_return(
            feature=feature,
            target=target,
            quantity=quantity,
        )
        correct = direction.signal == outcome if direction.signal in {"Up", "Down"} and outcome in {"Up", "Down"} else None
        evaluated.append(ForecastEvaluationRow(
            model_version=FORECAST_MODEL_VERSION,
            feature_timestamp=str(feature["observed_at"]),
            feature_timestamp_epoch=int(feature["observed_at_epoch"]),
            target_timestamp=str(target["observed_at"]),
            target_timestamp_epoch=int(target["observed_at_epoch"]),
            prediction=direction.signal,
            outcome=outcome,
            correct=correct,
            future_bid_return_pct=future_return,
            gross_flip_return_pct=gross_flip_return,
        ))

    return summarize_forecast_evaluations(
        item_code=item_code,
        horizon_hours=horizon_hours,
        rows=evaluated,
        current_signal=current_signal.signal if current_signal is not None else "Unavailable",
        current_reason_codes=current_signal.reason_codes if current_signal is not None else (),
        min_samples=min_samples,
        current_observed_at=str(newest_observation["observed_at"]),
    )


def build_forecast_features(
    observations: list[dict[str, Any]], transactions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build chronological seven-day features without reading beyond each feature time."""
    ordered_observations = sorted(
        observations, key=lambda row: (int(row["observed_at_epoch"]), int(row["observation_id"]))
    )
    ordered_transactions = sorted(
        transactions, key=lambda row: (int(row["created_at_epoch"]), str(row.get("id", "")))
    )
    transaction_epochs = [int(row["created_at_epoch"]) for row in ordered_transactions]
    features: list[dict[str, Any]] = []
    for observation in ordered_observations:
        best_bid = _positive_float(observation.get("best_bid"))
        best_ask = _positive_float(observation.get("best_ask"))
        if best_bid is None or best_ask is None:
            continue
        feature_epoch = int(observation["observed_at_epoch"])
        start = bisect_left(transaction_epochs, feature_epoch - FORECAST_TRAILING_SECONDS)
        end = bisect_right(transaction_epochs, feature_epoch)
        trailing = ordered_transactions[start:end]
        priced = [
            (float(row["unit_price"]), _positive_float(row.get("quantity")))
            for row in trailing
            if _positive_float(row.get("unit_price")) is not None
        ]
        prices = [price for price, _ in priced]
        vwap_quantity = sum(quantity for _, quantity in priced if quantity is not None)
        vwap_value = sum(price * quantity for price, quantity in priced if quantity is not None)
        vwap = vwap_value / vwap_quantity if vwap_quantity > 0 else None
        median = _percentile(prices, 50)
        p10 = _percentile(prices, 10)
        p25 = _percentile(prices, 25)
        p90 = _percentile(prices, 90)
        rolling_average = sum(prices[-5:]) / len(prices[-5:]) if prices else None
        fair_inputs = [
            (value, weight) for value, weight in ((vwap, 0.5), (median, 0.3), (rolling_average, 0.2))
            if value is not None
        ]
        fair = _weighted_average(fair_inputs, fallback=None)
        midpoint = (best_bid + best_ask) / 2
        momentum = (prices[-1] - prices[0]) / prices[0] * 100 if len(prices) >= 2 and prices[0] > 0 else None
        fair_gap = (midpoint - fair) / fair * 100 if fair is not None and fair > 0 else None
        bid_depth = _number_or_none(observation.get("bid_depth")) or 0.0
        ask_depth = _number_or_none(observation.get("ask_depth")) or 0.0
        total_depth = bid_depth + ask_depth
        features.append({
            **observation,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "midpoint": midpoint,
            "spread_pct": (best_ask - best_bid) / midpoint * 100 if midpoint > 0 else None,
            "depth_imbalance_pct": (bid_depth - ask_depth) / total_depth * 100 if total_depth > 0 else None,
            "trailing_open": prices[0] if prices else None,
            "trailing_close": prices[-1] if prices else None,
            "trailing_vwap": vwap,
            "trailing_median": median,
            "trailing_p10": p10,
            "trailing_p25": p25,
            "trailing_p90": p90,
            "trailing_volume": sum(_positive_float(row.get("quantity")) or 0.0 for row in trailing),
            "trailing_count": len(trailing),
            "momentum_pct": momentum,
            "stable_fair_price": fair,
            "fair_gap_pct": fair_gap,
        })
    return features


def _realized_gross_flip_return(
    *, feature: dict[str, Any], target: dict[str, Any], quantity: float
) -> float | None:
    if not feature.get("levels_available") or not target.get("levels_available"):
        return None
    entry = calculate_book_sweep(feature.get("asks", ()), side="buy", quantity=quantity)
    exit_sweep = calculate_book_sweep(target.get("bids", ()), side="sell", quantity=quantity)
    if not entry.fully_filled or not exit_sweep.fully_filled or entry.gross_value <= 0:
        return None
    return (exit_sweep.gross_value - entry.gross_value) / entry.gross_value * 100


def estimate_latest_execution(
    store: MarketStore,
    *,
    item_code: str,
    side: str,
    quantity: float,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    snapshot = store.latest_order_book_with_levels(item_code)
    if snapshot is None:
        return None
    observed_at = datetime.fromtimestamp(snapshot["observed_at_epoch"], tz=timezone.utc)
    reference_time = _as_utc(now or datetime.now(timezone.utc))
    result = dict(snapshot)
    result["snapshot_age_seconds"] = max(0.0, (reference_time - observed_at).total_seconds())
    result["execution"] = None
    if snapshot["levels_available"]:
        levels = snapshot["asks"] if side == "buy" else snapshot["bids"]
        result["execution"] = calculate_book_sweep(levels, side=side, quantity=quantity)
    else:
        # Validate caller inputs consistently even when a legacy snapshot has no levels.
        calculate_book_sweep([], side=side, quantity=quantity)
    return result


@dataclass(frozen=True)
class ReportWindow:
    label: str
    days: float

    @property
    def key(self) -> str:
        return self.label.lower()


def parse_report_window(value: str) -> ReportWindow:
    normalized = value.strip().upper()
    if normalized not in SUPPORTED_REPORT_WINDOWS:
        allowed = ", ".join(SUPPORTED_REPORT_WINDOWS)
        raise ValueError(f"Unsupported report window {value!r}. Expected one of: {allowed}.")

    if normalized.endswith("D"):
        return ReportWindow(label=normalized, days=int(normalized[:-1]))
    return ReportWindow(label=normalized, days=365)


def load_market_rows(
    store: MarketStore,
    *,
    windows: list[str] | tuple[str, ...] | None = None,
    lookback_days: float | None = None,
    now: datetime | None = None,
    forecast_horizon_hours: float = 24.0,
    forecast_target_max_lag_hours: float = 6.0,
    forecast_min_samples: int = 30,
    forecast_quantity: float = 1.0,
    min_tick: float = 0.001,
    flip_assumptions: FlipAssumptions | None = None,
) -> list[dict[str, Any]]:
    if lookback_days is not None and lookback_days < 0:
        raise ValueError("lookback_days cannot be negative.")

    report_windows = _resolve_windows(windows, lookback_days)
    now = _as_utc(now or datetime.now(timezone.utc))
    assumptions = flip_assumptions or FlipAssumptions(
        quantity=forecast_quantity,
        forecast_horizon_hours=forecast_horizon_hours,
    )
    since_epochs = {
        window.label: int((now - timedelta(days=window.days)).timestamp())
        for window in report_windows
    }
    earliest_since_epoch = min(since_epochs.values())

    latest_prices = store.latest_price_observations()
    latest_books = store.latest_order_book_observations()
    production_points = store.item_production_points()

    rows: list[dict[str, Any]] = []
    for item_code in store.item_codes():
        trades = store.transactions_for_window(item_code, earliest_since_epoch)
        price_observations = store.price_observations_for_window(item_code, earliest_since_epoch)
        order_observations = store.order_book_observations_for_window(item_code, earliest_since_epoch)
        latest_price = latest_prices.get(item_code, {})
        latest_book = latest_books.get(item_code, {})

        last_trade_price = _latest_trade_price(trades)
        quote_price = _latest_quote_price(price_observations, latest_price)
        mid_price = _mid_price(latest_book)
        current_price = last_trade_price
        quote_gap_pct = _quote_gap_pct(last_trade_price, quote_price)
        depth_imbalance_pct = _depth_imbalance_pct(latest_book)

        row: dict[str, Any] = {
            "item_name": _display_name(item_code),
            "item_code": item_code,
            "production_points": production_points.get(item_code),
            "latest_price": current_price,
            "latest_price_observed_at": latest_price.get("observed_at"),
            "latest_bid": latest_book.get("best_bid"),
            "latest_ask": latest_book.get("best_ask"),
            "latest_spread": latest_book.get("spread_abs"),
            "latest_spread_pct": latest_book.get("spread_pct"),
            "bid": latest_book.get("best_bid"),
            "ask": latest_book.get("best_ask"),
            "last_trade_price": last_trade_price,
            "quote_price": quote_price,
            "mid_price": mid_price,
            "current_price": current_price,
            "quote_gap_pct": quote_gap_pct,
            "latest_depth_imbalance_pct": depth_imbalance_pct,
            "depth_imbalance_pct": depth_imbalance_pct,
        }

        window_stats: dict[str, dict[str, Any]] = {}
        for window in report_windows:
            stats = _window_stats(
                trades=_rows_since(trades, since_epochs[window.label], "created_at_epoch"),
                prices=_rows_since(price_observations, since_epochs[window.label], "observed_at_epoch"),
                orders=_rows_since(order_observations, since_epochs[window.label], "observed_at_epoch"),
                latest_price=quote_price,
                latest_book=latest_book,
                last_trade_price=last_trade_price,
                quote_price=quote_price,
                mid_price=mid_price,
                quote_gap_pct=quote_gap_pct,
                depth_imbalance_pct=depth_imbalance_pct,
            )
            window_stats[window.label] = stats
            _add_flattened_window_stats(row, window, stats)

        row["windows"] = window_stats
        row["trend_path_30d"] = (
            _daily_last_trade_prices(_rows_since(trades, since_epochs["30D"], "created_at_epoch"))
            if "30D" in since_epochs
            else []
        )
        row["trend_path_30d_start_epoch"] = since_epochs.get("30D")
        row["trend_path_30d_end_epoch"] = int(now.timestamp()) if "30D" in since_epochs else None
        _add_legacy_metric_fields(row, report_windows, window_stats)
        forecast = evaluate_item_forecast(
            store,
            item_code=item_code,
            horizon_hours=assumptions.forecast_horizon_hours,
            target_max_lag_hours=forecast_target_max_lag_hours,
            min_samples=forecast_min_samples,
            quantity=assumptions.quantity,
            min_tick=min_tick,
        )
        row.update({
            "forecast_model_version": forecast.model_version,
            "forecast_horizon_hours": forecast.horizon_hours,
            "forecast_candidate_samples": forecast.candidate_samples,
            "forecast_evaluable_samples": forecast.evaluable_samples,
            "forecast_execution_evaluable_samples": forecast.execution_evaluable_samples,
            "forecast_up_predictions": forecast.up_predictions,
            "forecast_down_predictions": forecast.down_predictions,
            "forecast_correct_predictions": forecast.correct_predictions,
            "forecast_accuracy_pct": forecast.accuracy_pct,
            "forecast_baseline_accuracy_pct": forecast.baseline_accuracy_pct,
            "forecast_median_future_bid_return_pct": forecast.median_future_bid_return_pct,
            "forecast_p10_future_bid_return_pct": forecast.p10_future_bid_return_pct,
            "forecast_p90_future_bid_return_pct": forecast.p90_future_bid_return_pct,
            "forecast_gross_flip_return_p10_pct": forecast.gross_flip_return_p10_pct,
            "forecast_gross_flip_return_median_pct": forecast.gross_flip_return_median_pct,
            "forecast_gross_flip_return_p90_pct": forecast.gross_flip_return_p90_pct,
            "forecast_current_signal": forecast.current_signal,
            "forecast_current_reason_codes": ", ".join(forecast.current_reason_codes),
            "forecast_evidence": forecast.evidence_label,
            "forecast_current_observed_at": forecast.current_observed_at,
        })
        snapshot = store.latest_order_book_with_levels(item_code)
        snapshot_at = str(snapshot["observed_at"]) if snapshot is not None else None
        quote_age_minutes = None
        entry_sweep = None
        exit_sweep = None
        asks = None
        best_bid = None
        best_ask = None
        row["order_book"] = None
        if snapshot is not None:
            best_bid = snapshot.get("best_bid")
            best_ask = snapshot.get("best_ask")
            observed = datetime.fromtimestamp(int(snapshot["observed_at_epoch"]), tz=timezone.utc)
            quote_age_minutes = max(0.0, (now - observed).total_seconds() / 60)
            if snapshot.get("levels_available"):
                bids = snapshot.get("bids", ())
                asks = snapshot.get("asks", ())
                entry_sweep = calculate_book_sweep(asks, side="buy", quantity=assumptions.quantity)
                exit_sweep = calculate_book_sweep(bids, side="sell", quantity=assumptions.quantity)
                book_summary = summarize_order_book(bids=bids, asks=asks)
                row["order_book"] = asdict(book_summary)
        opportunity = calculate_flip_opportunity({
            "item_code": item_code,
            "item_name": row["item_name"],
            "snapshot_at": snapshot_at,
            "quote_age_minutes": quote_age_minutes,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "asks": asks,
            "entry_sweep": entry_sweep,
            "min_tick": min_tick,
            "forecast_signal": forecast.current_signal,
            "forecast_evidence": forecast.evidence_label,
            "forecast_samples": forecast.evaluable_samples,
            "gross_flip_return_p10_pct": forecast.gross_flip_return_p10_pct,
            "gross_flip_return_median_pct": forecast.gross_flip_return_median_pct,
            "gross_flip_return_p90_pct": forecast.gross_flip_return_p90_pct,
        }, assumptions)
        row.update(opportunity_fields(opportunity))
        guidance_window = next(
            (window for window in report_windows if window.label == "7D"),
            report_windows[0],
        )
        guidance_stats = window_stats[guidance_window.label]
        guidance = calculate_fair_value_guidance(
            fair_price=guidance_stats.get("stable_fair_price"),
            rich_exit_price=guidance_stats.get("price_p90"),
            price_p10=guidance_stats.get("price_p10"),
            price_p25=guidance_stats.get("price_p25"),
            market_state=guidance_stats.get("tendency_labels"),
            executable_ask_vwap=entry_sweep.average_price if entry_sweep is not None else None,
            executable_bid_vwap=exit_sweep.average_price if exit_sweep is not None else None,
            entry_fully_filled=bool(entry_sweep and entry_sweep.fully_filled),
            exit_fully_filled=bool(exit_sweep and exit_sweep.fully_filled),
            assumptions=assumptions,
        )
        row.update(guidance_fields(guidance))
        rows.append(row)

    return rows


def opportunity_fields(opportunity: object) -> dict[str, Any]:
    values = asdict(opportunity)  # type: ignore[arg-type]
    return {
        "flip_reason_codes": ",".join(values.pop("reason_codes")),
        **{f"flip_{key}": value for key, value in values.items() if key not in {"item_code", "item_name"}},
    }


def guidance_fields(guidance: FairValueGuidance) -> dict[str, Any]:
    return {f"guide_{key}": value for key, value in asdict(guidance).items()}


def load_chart_trades(
    store: MarketStore,
    *,
    item_code: str,
    window: str,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    report_window = parse_report_window(window)
    now = _as_utc(now or datetime.now(timezone.utc))
    since_epoch = int((now - timedelta(days=report_window.days)).timestamp())
    return _chart_trades_from_rows(store.transactions_for_window(item_code, since_epoch))


def load_chart_data(
    store: MarketStore,
    *,
    item_code: str,
    window: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    report_window = parse_report_window(window)
    now = _as_utc(now or datetime.now(timezone.utc))
    since_epoch = int((now - timedelta(days=report_window.days)).timestamp())
    trades = _chart_trades_from_rows(store.transactions_for_window(item_code, since_epoch))
    spread_observations = [
        {
            "item_code": row["item_code"],
            "observed_at": row["observed_at"],
            "observed_at_epoch": row["observed_at_epoch"],
            "bid": row["best_bid"],
            "ask": row["best_ask"],
            "spread": row["spread_abs"],
            "spread_pct": row["spread_pct"],
        }
        for row in store.order_book_observations_for_window(item_code, since_epoch)
        if row.get("spread_abs") is not None
    ]
    return {
        "item_code": item_code,
        "window": report_window.label,
        "trades": trades,
        "spread_observations": spread_observations,
    }


def load_highlight_trade_history(
    store: MarketStore,
    *,
    item_codes: Iterable[str],
    now: datetime | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Load at most the trailing 30 calendar days of completed trades for highlights."""
    now = _as_utc(now or datetime.now(timezone.utc))
    since_epoch = int((now - timedelta(days=HIGHLIGHT_HISTORY_DAYS)).timestamp())
    end_epoch = int(now.timestamp())
    histories: dict[str, list[dict[str, Any]]] = {}
    for item_code in dict.fromkeys(item_codes):
        storage_code = str(item_code).strip()
        if not storage_code:
            continue
        rows = store.transactions_for_window(storage_code, since_epoch)
        if not rows and storage_code != storage_code.lower():
            rows = store.transactions_for_window(storage_code.lower(), since_epoch)
        histories[storage_code.lower()] = _chart_trades_from_rows([
            row for row in rows
            if int(row.get("created_at_epoch", end_epoch + 1)) <= end_epoch
        ])
    return histories


def _resolve_windows(
    windows: list[str] | tuple[str, ...] | None,
    lookback_days: float | None,
) -> list[ReportWindow]:
    if windows is not None:
        if not windows:
            raise ValueError("At least one report window is required.")
        return [parse_report_window(window) for window in windows]

    if lookback_days is not None:
        return [_window_from_days(lookback_days)]

    return [parse_report_window(window) for window in DEFAULT_REPORT_WINDOWS]


def _chart_trades_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "item_code": row["item_code"],
            "created_at": row["created_at"],
            "created_at_epoch": row["created_at_epoch"],
            "price": row["unit_price"],
            "quantity": row["quantity"],
            "value": row["money"],
            "transaction_type": row["transaction_type"],
        }
        for row in rows
        if row.get("unit_price") is not None
    ]


def _window_from_days(days: float) -> ReportWindow:
    if days <= 0:
        return ReportWindow(label="0D", days=0)
    day_count = float(days)
    if day_count.is_integer():
        label = f"{int(day_count)}D"
    else:
        label = f"{day_count:g}D"
    return ReportWindow(label=label, days=day_count)


def _window_stats(
    *,
    trades: list[dict[str, Any]],
    prices: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    latest_price: float | None,
    latest_book: dict[str, Any],
    last_trade_price: float | None = None,
    quote_price: float | None = None,
    mid_price: float | None = None,
    quote_gap_pct: float | None = None,
    depth_imbalance_pct: float | None = None,
) -> dict[str, Any]:
    priced_trades = [trade for trade in trades if trade.get("unit_price") is not None]
    trade_prices = [float(trade["unit_price"]) for trade in priced_trades]
    distinct_trade_timestamps = {
        trade.get("created_at_epoch", trade.get("created_at"))
        for trade in priced_trades
        if trade.get("created_at_epoch", trade.get("created_at")) is not None
    }
    volume = _sum_numeric(trade.get("quantity") for trade in trades)
    traded_value = _sum_numeric(trade.get("money") for trade in trades)
    vwap_quantity = _sum_numeric(
        trade.get("quantity")
        for trade in priced_trades
        if _positive_number(trade.get("quantity"))
    )
    vwap_value = _sum_numeric(
        (float(trade["unit_price"]) * float(trade["quantity"]))
        for trade in priced_trades
        if _positive_number(trade.get("quantity"))
    )

    open_price = float(priced_trades[0]["unit_price"]) if priced_trades else None
    close_price = float(priced_trades[-1]["unit_price"]) if priced_trades else None
    percent_change = (
        (close_price - open_price) / open_price * 100
        if (
            len(priced_trades) >= 2
            and len(distinct_trade_timestamps) >= 2
            and open_price is not None
            and close_price is not None
            and open_price > 0
        )
        else None
    )
    spread_pct = _number_or_none(latest_book.get("spread_pct"))
    average_price = (sum(trade_prices) / len(trade_prices)) if trade_prices else None
    current_price = last_trade_price
    rolling_average = _rolling_average(trade_prices, fallback=average_price)
    median_price = _median(trade_prices)
    price_p10 = _percentile(trade_prices, 10)
    price_p25 = _percentile(trade_prices, 25)
    price_p90 = _percentile(trade_prices, 90)
    stable_range_pct = (
        (price_p90 - price_p10) / median_price * 100
        if price_p10 is not None and price_p90 is not None and median_price is not None and median_price > 0
        else None
    )
    stable_fair_price = _weighted_average(
        [
            (stats_price, weight)
            for stats_price, weight in [
                ((vwap_value / vwap_quantity) if vwap_quantity > 0 else None, 0.50),
                (median_price, 0.30),
                (rolling_average, 0.20),
            ]
            if stats_price is not None
        ],
        fallback=None,
    )
    bid_depth = _number_or_none(latest_book.get("bid_depth")) or 0.0
    ask_depth = _number_or_none(latest_book.get("ask_depth")) or 0.0
    total_depth = bid_depth + ask_depth
    depth_imbalance_pct = ((bid_depth - ask_depth) / total_depth * 100) if total_depth > 0 else None
    distance_from_rolling_average = (
        close_price - rolling_average
        if close_price is not None and rolling_average is not None
        else None
    )
    distance_from_rolling_average_pct = (
        distance_from_rolling_average / rolling_average * 100
        if distance_from_rolling_average is not None and rolling_average is not None and rolling_average > 0
        else None
    )

    stats = {
        "trade_count": len(trades),
        "priced_trade_count": len(priced_trades),
        "distinct_trade_timestamp_count": len(distinct_trade_timestamps),
        "volume": volume,
        "traded_quantity": volume,
        "traded_value": traded_value,
        "min": min(trade_prices) if trade_prices else None,
        "max": max(trade_prices) if trade_prices else None,
        "average": average_price,
        "vwap": (vwap_value / vwap_quantity) if vwap_quantity > 0 else None,
        "median": median_price,
        "price_p10": price_p10,
        "price_p25": price_p25,
        "price_p90": price_p90,
        "stable_fair_price": stable_fair_price,
        "stable_range_pct": stable_range_pct,
        "open": open_price,
        "close": close_price,
        "change_abs": (
            close_price - open_price
            if open_price is not None and close_price is not None
            else None
        ),
        "percent_change": percent_change,
        "latest_price": current_price,
        "last_trade_price": last_trade_price,
        "quote_price": quote_price,
        "mid_price": mid_price,
        "current_price": current_price,
        "quote_gap_pct": quote_gap_pct,
        "latest_bid": latest_book.get("best_bid"),
        "latest_ask": latest_book.get("best_ask"),
        "latest_bid_depth": latest_book.get("bid_depth"),
        "latest_ask_depth": latest_book.get("ask_depth"),
        "latest_depth_imbalance_pct": depth_imbalance_pct,
        "depth_imbalance_pct": depth_imbalance_pct,
        "latest_spread": latest_book.get("spread_abs"),
        "latest_spread_pct": spread_pct,
        "average_spread": _average_numeric(order.get("spread_abs") for order in orders),
        "average_spread_pct": _average_numeric(order.get("spread_pct") for order in orders),
        "rolling_average": rolling_average,
        "distance_from_rolling_average": distance_from_rolling_average,
        "distance_from_rolling_average_pct": distance_from_rolling_average_pct,
    }
    stats["liquidity"] = calculate_liquidity_score(
        bid_depth=stats["latest_bid_depth"],
        ask_depth=stats["latest_ask_depth"],
        spread_pct=spread_pct,
    )
    tendency_labels = classify_tendency(
        open_price=stats["open"],
        close_price=stats["close"],
        min_price=stats["min"],
        max_price=stats["max"],
        average_price=stats["average"],
        rolling_average=stats["rolling_average"],
        trade_count=stats["trade_count"],
        volume=stats["volume"],
        spread_pct=stats["latest_spread_pct"],
        stable_range_pct=stats["stable_range_pct"],
    )
    stats["tendency"] = tendency_labels[0]
    stats["tendency_labels"] = ", ".join(tendency_labels)
    return stats


def _add_flattened_window_stats(row: dict[str, Any], window: ReportWindow, stats: dict[str, Any]) -> None:
    for key, value in stats.items():
        row[f"{key}_{window.key}"] = value


def _add_legacy_metric_fields(
    row: dict[str, Any],
    report_windows: list[ReportWindow],
    window_stats: dict[str, dict[str, Any]],
) -> None:
    metric_window = next((window for window in report_windows if window.label == "7D"), report_windows[0])
    stats = window_stats[metric_window.label]
    row["trades_7d"] = stats["trade_count"]
    row["high_7d"] = stats["max"]
    row["low_7d"] = stats["min"]
    row["open_7d"] = stats["open"]
    row["close_7d"] = stats["close"]


def _rows_since(rows: list[dict[str, Any]], since_epoch: int, field: str) -> list[dict[str, Any]]:
    return [row for row in rows if row[field] >= since_epoch]


def _daily_last_trade_prices(trades: list[dict[str, Any]]) -> list[dict[str, float | int]]:
    """Return the last valid completed-trade price for each UTC day."""
    daily: dict[str, dict[str, float | int]] = {}
    for trade in trades:
        epoch = trade.get("created_at_epoch")
        price = _number_or_none(trade.get("unit_price"))
        if epoch is None or price is None or price <= 0:
            continue
        timestamp = int(epoch)
        day = datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()
        previous = daily.get(day)
        if previous is None or timestamp >= int(previous["timestamp"]):
            daily[day] = {"timestamp": timestamp, "price": price}
    return sorted(daily.values(), key=lambda point: int(point["timestamp"]))


def _display_name(item_code: str) -> str:
    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", item_code).replace("_", " ").replace("-", " ")
    return spaced.title()


def _latest_trade_price(trades: list[dict[str, Any]]) -> float | None:
    for trade in reversed(trades):
        price = _number_or_none(trade.get("unit_price"))
        if price is not None:
            return price
    return None


def _latest_quote_price(price_observations: list[dict[str, Any]], latest_price: dict[str, Any]) -> float | None:
    if price_observations:
        for observation in reversed(price_observations):
            price = _number_or_none(observation.get("current_price"))
            if price is not None:
                return price
    return _number_or_none(latest_price.get("current_price"))


def _mid_price(latest_book: dict[str, Any]) -> float | None:
    bid = _number_or_none(latest_book.get("best_bid"))
    ask = _number_or_none(latest_book.get("best_ask"))
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2


def _quote_gap_pct(last_trade_price: float | None, quote_price: float | None) -> float | None:
    if last_trade_price is None or quote_price is None or last_trade_price == 0:
        return None
    return (quote_price - last_trade_price) / last_trade_price * 100


def _depth_imbalance_pct(latest_book: dict[str, Any]) -> float | None:
    bid_depth = _number_or_none(latest_book.get("bid_depth")) or 0.0
    ask_depth = _number_or_none(latest_book.get("ask_depth")) or 0.0
    total_depth = bid_depth + ask_depth
    if total_depth <= 0:
        return None
    return (bid_depth - ask_depth) / total_depth * 100


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _sum_numeric(values: Iterable[object]) -> float:
    total = 0.0
    for value in values:
        number = _number_or_none(value)
        if number is not None:
            total += number
    return total


def _average_numeric(values: Iterable[object]) -> float | None:
    numbers = [number for value in values if (number := _number_or_none(value)) is not None]
    return sum(numbers) / len(numbers) if numbers else None


def _rolling_average(values: list[float], *, fallback: float | None) -> float | None:
    if not values:
        return fallback
    window = values[-min(len(values), 5):]
    return sum(window) / len(window)


def _median(values: list[float]) -> float | None:
    return _percentile(values, 50)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile / 100
    lower_index = int(rank)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = rank - lower_index
    return ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * fraction


def _weighted_average(values: list[tuple[float, float]], *, fallback: float | None) -> float | None:
    total_weight = sum(weight for _, weight in values if weight > 0)
    if total_weight <= 0:
        return fallback
    return sum(value * weight for value, weight in values if weight > 0) / total_weight


def _number_or_none(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (str, int, float)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _positive_number(value: object) -> bool:
    number = _number_or_none(value)
    return number is not None and number > 0


def _positive_float(value: object) -> float | None:
    number = _number_or_none(value)
    if number is None or not math.isfinite(number) or number <= 0:
        return None
    return number
