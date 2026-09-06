from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from functools import cmp_to_key
from typing import Any, Mapping, Optional, Sequence

import pandas as pd


HIGHLIGHT_CANDLE_INTERVALS = (("4h", "4h"), ("8h", "8h"), ("12h", "12h"), ("1D", "1D"))
HIGHLIGHT_MIN_POPULATED_CANDLES = 12
HIGHLIGHT_MIN_DISTINCT_UTC_DAYS = 3
HIGHLIGHT_SMA_MIN_CLOSES = 6
PRICE_ACTION_MAX_POPULATED_CANDLES = 180
PRICE_ACTION_DISPLAY_WINDOW_DAYS = 90
NUMERIC_COMPARISON_REL_TOL = 1e-12
NUMERIC_COMPARISON_ABS_TOL = 1e-12

MEANINGFUL_PREMIUM = "meaningful_premium"
MEANINGFUL_DISCOUNT = "meaningful_discount"
WITHIN_NORMAL_RANGE = "within_normal_range"
INSUFFICIENT_RANGE_EVIDENCE = "insufficient_evidence"

DEFAULT_INFLATION_MINIMUM_COVERAGE_PCT = 80.0
DEFAULT_INFLATION_NEUTRAL_BAND_PCT = 0.10


@dataclass(frozen=True)
class ActionCostComponent:
    """Cost contribution from one priced component of a fixed gameplay action."""

    item_code: str
    quantity: float
    representative_price: float
    cost: float


@dataclass(frozen=True)
class FixedActionCost:
    """Immutable result for pricing a fixed-quantity action basket."""

    total_cost: float | None
    coverage_pct: float
    required_component_count: int
    priced_component_count: int
    missing_item_codes: tuple[str, ...]
    components: tuple[ActionCostComponent, ...]


def calculate_fixed_action_cost(
    quantities: Mapping[str, object],
    representative_prices: Mapping[str, object],
) -> FixedActionCost:
    """Price a fixed action using representative completed-transaction prices.

    All declared quantities must be finite and positive. A missing, non-finite,
    or non-positive representative price is reported as unavailable; it is never
    replaced or treated as zero. The total is available only with full coverage.
    """
    normalized_quantities: list[tuple[str, float]] = []
    for raw_code, raw_quantity in quantities.items():
        code = str(raw_code).strip()
        if not code:
            raise ValueError("Action item codes must be non-empty.")
        try:
            quantity = float(raw_quantity)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Quantity for {code!r} must be finite and positive.") from exc
        if not math.isfinite(quantity) or quantity <= 0:
            raise ValueError(f"Quantity for {code!r} must be finite and positive.")
        normalized_quantities.append((code, quantity))
    if not normalized_quantities:
        raise ValueError("At least one action quantity is required.")

    components: list[ActionCostComponent] = []
    missing: list[str] = []
    for code, quantity in normalized_quantities:
        price = _finite_float_or_none(representative_prices.get(code))
        if price is None or price <= 0:
            missing.append(code)
            continue
        components.append(ActionCostComponent(
            item_code=code,
            quantity=quantity,
            representative_price=price,
            cost=quantity * price,
        ))
    required_count = len(normalized_quantities)
    coverage_pct = len(components) / required_count * 100
    return FixedActionCost(
        total_cost=sum(component.cost for component in components) if not missing else None,
        coverage_pct=coverage_pct,
        required_component_count=required_count,
        priced_component_count=len(components),
        missing_item_codes=tuple(missing),
        components=tuple(components),
    )


@dataclass(frozen=True)
class InflationIndexLevel:
    """A fixed-weight price-index result with missing-price diagnostics."""

    level: float | None
    coverage_pct: float
    eligible_component_count: int
    priced_component_count: int
    missing_item_codes: tuple[str, ...]


@dataclass(frozen=True)
class InflationContribution:
    """One component's additive contribution to an index change."""

    item_code: str
    contribution_pct_points: float
    price_change_pct: float
    matched_weight: float


@dataclass(frozen=True)
class MatchedInflationChange:
    """A change calculated only from components priced at both endpoints."""

    change_pct: float | None
    classification: str
    matched_coverage_pct: float
    purchasing_power_change_pct: float | None
    matched_item_codes: tuple[str, ...]
    missing_item_codes: tuple[str, ...]
    contributors: tuple[InflationContribution, ...]


def _positive_finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _trade_value(trade: object, field: str) -> object:
    if isinstance(trade, Mapping):
        return trade.get(field)
    return getattr(trade, field, None)


def quantity_weighted_median(trades: Sequence[object]) -> float | None:
    """Return the first price whose cumulative valid quantity reaches 50%.

    Each input may be a mapping or an object exposing ``unit_price`` and
    ``quantity``. Invalid, non-positive, and non-finite values are ignored.
    The input sequence and its records are never mutated.
    """
    quantities_by_price: dict[float, float] = {}
    for trade in trades:
        price = _positive_finite(_trade_value(trade, "unit_price"))
        quantity = _positive_finite(_trade_value(trade, "quantity"))
        if price is not None and quantity is not None:
            quantities_by_price[price] = quantities_by_price.get(price, 0.0) + quantity
    if not quantities_by_price:
        return None

    threshold = math.fsum(quantities_by_price.values()) / 2
    cumulative = 0.0
    for price in sorted(quantities_by_price):
        cumulative += quantities_by_price[price]
        if cumulative >= threshold:
            return price
    return None  # Defensive fallback for floating-point edge cases.


def normalize_weights(raw_weights: Mapping[str, object]) -> dict[str, float]:
    """Normalize positive finite component values; exclude invalid entries."""
    valid = {
        str(item_code): value
        for item_code, raw_value in raw_weights.items()
        if (value := _positive_finite(raw_value)) is not None
    }
    total = math.fsum(valid.values())
    if total <= 0:
        return {}
    return {item_code: value / total for item_code, value in valid.items()}


def traded_value_weights(base_traded_values: Mapping[str, object]) -> dict[str, float]:
    """Construct fixed weights from eligible base-period traded values."""
    return normalize_weights(base_traded_values)


def base_pp_weights(
    traded_quantities: Mapping[str, object],
    total_upstream_pp: Mapping[str, object],
) -> dict[str, float]:
    """Construct weights from traded embodied pre-bonus PP.

    ``total_upstream_pp`` includes the item's direct production requirement and
    the pre-bonus production points embodied in its material inputs. Unknown
    production bonuses are deliberately not inferred.
    """
    traded_pp: dict[str, float] = {}
    for item_code, raw_quantity in traded_quantities.items():
        quantity = _positive_finite(raw_quantity)
        pp = _positive_finite(total_upstream_pp.get(item_code))
        if quantity is not None and pp is not None:
            traded_pp[str(item_code)] = quantity * pp
    return normalize_weights(traded_pp)


def _validated_weights(weights: Mapping[str, object]) -> dict[str, float]:
    """Return normalized weights so all calculation paths enforce invariants."""
    return normalize_weights(weights)


def calculate_fixed_weight_index(
    weights: Mapping[str, object],
    base_prices: Mapping[str, object],
    period_prices: Mapping[str, object],
    *,
    minimum_coverage_pct: float = DEFAULT_INFLATION_MINIMUM_COVERAGE_PCT,
) -> InflationIndexLevel:
    """Calculate a base-100 level, renormalizing available fixed weights."""
    normalized = _validated_weights(weights)
    threshold = float(minimum_coverage_pct)
    if not math.isfinite(threshold) or not 0 <= threshold <= 100:
        raise ValueError("minimum_coverage_pct must be finite and between 0 and 100.")

    available: dict[str, tuple[float, float, float]] = {}
    for item_code, weight in normalized.items():
        base = _positive_finite(base_prices.get(item_code))
        period = _positive_finite(period_prices.get(item_code))
        if base is not None and period is not None:
            available[item_code] = (weight, base, period)
    available_weight = math.fsum(value[0] for value in available.values())
    coverage_pct = available_weight * 100
    missing = tuple(sorted(set(normalized) - set(available)))
    level = None
    if available_weight > 0 and coverage_pct + NUMERIC_COMPARISON_ABS_TOL >= threshold:
        level = 100 * math.fsum(
            weight * (period / base) for weight, base, period in available.values()
        ) / available_weight
    return InflationIndexLevel(
        level=level,
        coverage_pct=coverage_pct,
        eligible_component_count=len(normalized),
        priced_component_count=len(available),
        missing_item_codes=missing,
    )


def classify_inflation(
    change_pct: object,
    *,
    neutral_band_pct: float = DEFAULT_INFLATION_NEUTRAL_BAND_PCT,
) -> str:
    """Classify a percent change using an inclusive neutral band."""
    change = _finite_float_or_none(change_pct)
    band = float(neutral_band_pct)
    if not math.isfinite(band) or band < 0:
        raise ValueError("neutral_band_pct must be a finite non-negative number.")
    if change is None:
        return "Insufficient data"
    if change > band:
        return "Inflation"
    if change < -band:
        return "Deflation"
    return "Stable"


def purchasing_power_change(change_pct: object) -> float | None:
    """Return the reciprocal purchasing-power change for a price change percent."""
    change = _finite_float_or_none(change_pct)
    if change is None:
        return None
    ratio = 1 + change / 100
    return (1 / ratio - 1) * 100 if ratio > 0 else None


def calculate_matched_index_change(
    weights: Mapping[str, object],
    start_prices: Mapping[str, object],
    end_prices: Mapping[str, object],
    *,
    minimum_coverage_pct: float = DEFAULT_INFLATION_MINIMUM_COVERAGE_PCT,
    neutral_band_pct: float = DEFAULT_INFLATION_NEUTRAL_BAND_PCT,
) -> MatchedInflationChange:
    """Calculate endpoint inflation using only components priced at both ends."""
    normalized = _validated_weights(weights)
    threshold = float(minimum_coverage_pct)
    if not math.isfinite(threshold) or not 0 <= threshold <= 100:
        raise ValueError("minimum_coverage_pct must be finite and between 0 and 100.")

    matched: dict[str, tuple[float, float, float]] = {}
    for item_code, weight in normalized.items():
        start = _positive_finite(start_prices.get(item_code))
        end = _positive_finite(end_prices.get(item_code))
        if start is not None and end is not None:
            matched[item_code] = (weight, start, end)
    matched_weight_total = math.fsum(value[0] for value in matched.values())
    coverage_pct = matched_weight_total * 100
    matched_codes = tuple(sorted(matched))
    missing_codes = tuple(sorted(set(normalized) - set(matched)))

    change_pct = None
    contributors: tuple[InflationContribution, ...] = ()
    if matched_weight_total > 0 and coverage_pct + NUMERIC_COMPARISON_ABS_TOL >= threshold:
        contribution_rows = []
        for item_code in matched_codes:
            weight, start, end = matched[item_code]
            matched_weight = weight / matched_weight_total
            price_change = (end / start - 1) * 100
            contribution_rows.append(InflationContribution(
                item_code=item_code,
                contribution_pct_points=matched_weight * price_change,
                price_change_pct=price_change,
                matched_weight=matched_weight,
            ))
        contributors = tuple(contribution_rows)
        change_pct = math.fsum(row.contribution_pct_points for row in contributors)

    return MatchedInflationChange(
        change_pct=change_pct,
        classification=classify_inflation(change_pct, neutral_band_pct=neutral_band_pct),
        matched_coverage_pct=coverage_pct,
        purchasing_power_change_pct=purchasing_power_change(change_pct),
        matched_item_codes=matched_codes,
        missing_item_codes=missing_codes,
        contributors=contributors,
    )


@dataclass(frozen=True)
class HighlightedItem:
    item_code: str
    item_name: str
    classification: str
    role: str | None
    rank_within_role: int | None
    latest_completed_price: float | None
    fair_7d: float | None
    price_p10_7d: float | None
    price_p90_7d: float | None
    min_tick: float | None
    raw_gap_pct: float | None
    severity: float | None
    band_width: float | None
    band_width_pct: float | None
    normal_band_start_position: float | None
    normal_band_end_position: float | None
    fair_normalized_position: float | None
    latest_normalized_position: float | None
    interval: str | None
    history_span: str | None
    candles: pd.DataFrame
    sma_7d: pd.Series
    display_window_days: int = PRICE_ACTION_DISPLAY_WINDOW_DAYS
    observation_count: int = 0

    @property
    def filename(self) -> str:
        if not self.role:
            raise ValueError("Only selected highlights have a role-based filename.")
        return f"{self.role.replace('_', '-')}-price-action.png"

    @property
    def gap_pct(self) -> float | None:
        """Retain the established name for the raw percentage-versus-fair value."""
        return self.raw_gap_pct


def _numerically_equal(left: float, right: float) -> bool:
    return math.isclose(
        left,
        right,
        rel_tol=NUMERIC_COMPARISON_REL_TOL,
        abs_tol=NUMERIC_COMPARISON_ABS_TOL,
    )


def _strictly_greater(left: float, right: float) -> bool:
    return left > right and not _numerically_equal(left, right)


def _strictly_less(left: float, right: float) -> bool:
    return left < right and not _numerically_equal(left, right)


def _at_least(left: float, right: float) -> bool:
    return left > right or _numerically_equal(left, right)


def _normalized_price_position(value: float, *, lower: float, upper: float, scale: float) -> float:
    midpoint = (lower + upper) / 2
    return min(100.0, max(0.0, 50 + ((value - midpoint) / scale) * 60))


def classify_price_dislocation(
    row: dict,
    *,
    min_tick: object | None = None,
) -> HighlightedItem:
    """Classify one latest completed trade against its strict 7D normal range.

    Invalid required evidence fails closed.  The returned object also supplies the
    normalized valuation-rail coordinates used by report rendering.
    """
    item_code = str(row.get("item_code") or "").strip().lower()
    item_name = str(row.get("item_name") or item_code or "Unknown")
    latest = _finite_float_or_none(row.get("last_trade_price"))
    fair = _finite_float_or_none(row.get("stable_fair_price_7d"))
    lower = _finite_float_or_none(row.get("price_p10_7d"))
    upper = _finite_float_or_none(row.get("price_p90_7d"))
    tick = _finite_float_or_none(min_tick if min_tick is not None else row.get("min_tick"))
    empty_candles = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    empty_sma = pd.Series(dtype="float64", name="7D SMA")
    usable = (
        latest is not None
        and fair is not None
        and lower is not None
        and upper is not None
        and tick is not None
        and latest > 0
        and fair > 0
        and tick > 0
        and lower <= upper
    )
    if not usable:
        raw_gap_pct = (
            (latest - fair) / fair * 100
            if latest is not None and latest > 0 and fair is not None and fair > 0
            else None
        )
        return HighlightedItem(
            item_code=item_code,
            item_name=item_name,
            classification=INSUFFICIENT_RANGE_EVIDENCE,
            role=None,
            rank_within_role=None,
            latest_completed_price=latest,
            fair_7d=fair,
            price_p10_7d=lower,
            price_p90_7d=upper,
            min_tick=tick,
            raw_gap_pct=raw_gap_pct,
            severity=None,
            band_width=None,
            band_width_pct=None,
            normal_band_start_position=None,
            normal_band_end_position=None,
            fair_normalized_position=None,
            latest_normalized_position=None,
            interval=None,
            history_span=None,
            candles=empty_candles,
            sma_7d=empty_sma,
        )

    assert latest is not None and fair is not None and lower is not None and upper is not None and tick is not None
    empirical_width = upper - lower
    scale = max(empirical_width, tick)
    raw_gap_pct = (latest - fair) / fair * 100
    classification = WITHIN_NORMAL_RANGE
    severity = 0.0
    if _strictly_greater(latest, upper) and _at_least(latest - fair, tick):
        classification = MEANINGFUL_PREMIUM
        severity = (latest - upper) / scale
    elif _strictly_less(latest, lower) and _at_least(fair - latest, tick):
        classification = MEANINGFUL_DISCOUNT
        severity = (lower - latest) / scale

    return HighlightedItem(
        item_code=item_code,
        item_name=item_name,
        classification=classification,
        role=None,
        rank_within_role=None,
        latest_completed_price=latest,
        fair_7d=fair,
        price_p10_7d=lower,
        price_p90_7d=upper,
        min_tick=tick,
        raw_gap_pct=raw_gap_pct,
        severity=severity,
        band_width=scale,
        band_width_pct=empirical_width / fair * 100,
        normal_band_start_position=_normalized_price_position(lower, lower=lower, upper=upper, scale=scale),
        normal_band_end_position=_normalized_price_position(upper, lower=lower, upper=upper, scale=scale),
        fair_normalized_position=_normalized_price_position(fair, lower=lower, upper=upper, scale=scale),
        latest_normalized_position=_normalized_price_position(latest, lower=lower, upper=upper, scale=scale),
        interval=None,
        history_span=None,
        candles=empty_candles,
        sma_7d=empty_sma,
    )


def classify_price_dislocations(
    rows: Sequence[dict],
    *,
    min_tick: object,
) -> list[HighlightedItem]:
    return [classify_price_dislocation(row, min_tick=min_tick) for row in rows]


def price_dislocation_fields(item: HighlightedItem) -> dict[str, object]:
    """Return unambiguous compatibility/export fields for one shared result."""
    return {
        "dislocation_classification": item.classification,
        "dislocation_raw_gap_pct": item.raw_gap_pct,
        "dislocation_severity": item.severity,
        "dislocation_band_width": item.band_width,
        "dislocation_band_width_pct": item.band_width_pct,
        "dislocation_normal_band_start_position": item.normal_band_start_position,
        "dislocation_normal_band_end_position": item.normal_band_end_position,
        "dislocation_fair_position": item.fair_normalized_position,
        "dislocation_latest_position": item.latest_normalized_position,
        "dislocation_min_tick": item.min_tick,
    }


def meaningful_dislocation_item_codes(items: Sequence[HighlightedItem]) -> list[str]:
    return [
        item.item_code
        for item in items
        if item.item_code and item.classification in {MEANINGFUL_PREMIUM, MEANINGFUL_DISCOUNT}
    ]


def build_price_action_candles(transactions: Sequence[dict], *, interval: str) -> pd.DataFrame:
    """Build unmodified OHLC and positive units volume for populated UTC intervals only."""
    if not transactions:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    frame = pd.DataFrame({
        "timestamp": [transaction.get("created_at") for transaction in transactions],
        "price": [transaction.get("price") for transaction in transactions],
        "quantity": [transaction.get("quantity") for transaction in transactions],
        "order": range(len(transactions)),
    })
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    frame["price"] = pd.to_numeric(frame["price"], errors="coerce")
    frame["quantity"] = pd.to_numeric(frame["quantity"], errors="coerce")
    frame = frame[
        frame["timestamp"].notna()
        & frame["price"].between(0, float("inf"), inclusive="neither")
        & frame["quantity"].between(0, float("inf"), inclusive="neither")
    ].sort_values(["timestamp", "order"])
    if frame.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    frame = frame.set_index("timestamp")
    resample_kwargs = {} if interval == "1D" else {"origin": "epoch"}
    ohlc = frame["price"].resample(interval, **resample_kwargs).ohlc()
    volume = frame["quantity"].resample(interval, **resample_kwargs).sum().rename("Volume")
    candles = pd.concat([ohlc, volume], axis=1).dropna(subset=["open", "high", "low", "close"])
    return candles.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})


def select_price_action_interval(transactions: Sequence[dict]) -> tuple[str, pd.DataFrame] | None:
    """Choose the finest publishable interval with sufficient authentic evidence.

    A populated-candle ceiling keeps dense histories legible at the fixed report
    image size. Sparse histories retain the finest (4h) detail; empty buckets do
    not count toward the density limit.
    """
    coarsest_eligible: tuple[str, pd.DataFrame] | None = None
    for label, frequency in HIGHLIGHT_CANDLE_INTERVALS:
        candles = build_price_action_candles(transactions, interval=frequency)
        distinct_days = len({timestamp.date() for timestamp in candles.index})
        if len(candles) >= HIGHLIGHT_MIN_POPULATED_CANDLES and distinct_days >= HIGHLIGHT_MIN_DISTINCT_UTC_DAYS:
            coarsest_eligible = (label, candles)
            if len(candles) <= PRICE_ACTION_MAX_POPULATED_CANDLES:
                return coarsest_eligible
    return coarsest_eligible


def _price_action_history_values(history: object) -> tuple[Sequence[dict], int, int]:
    """Normalize a trade sequence or Phase 1 PriceActionHistory domain object."""
    if hasattr(history, "trades") and hasattr(history, "window_days"):
        transactions = getattr(history, "trades")
        window_days = int(getattr(history, "window_days"))
        coverage = getattr(history, "coverage", None)
        observation_count = int(getattr(coverage, "observation_count", len(transactions)))
        return transactions, window_days, observation_count
    transactions = history  # type: ignore[assignment]
    return transactions, PRICE_ACTION_DISPLAY_WINDOW_DAYS, len(transactions)  # type: ignore[arg-type]


def time_based_sma_7d(candles: pd.DataFrame) -> pd.Series:
    """Return a 7-calendar-day close SMA, gated by close-count evidence."""
    if candles.empty:
        return pd.Series(index=candles.index, dtype="float64", name="7D SMA")
    result = candles["Close"].rolling("7D", min_periods=HIGHLIGHT_SMA_MIN_CLOSES).mean()
    return result.rename("7D SMA")


def prepare_price_action_item(
    row: dict,
    transactions: Sequence[dict] | object,
    *,
    role: str = "price_action",
    rank_within_role: int = 1,
    min_tick: object | None = None,
) -> HighlightedItem | None:
    """Prepare one item's chart domain values, or return None when evidence is insufficient."""
    item = classify_price_dislocation(row, min_tick=min_tick)
    if (
        not item.item_code
        or item.latest_completed_price is None
        or item.latest_completed_price <= 0
        or item.fair_7d is None
        or item.fair_7d <= 0
    ):
        return None
    trade_rows, window_days, observation_count = _price_action_history_values(transactions)
    selected_interval = select_price_action_interval(trade_rows)
    if selected_interval is None:
        return None
    interval, candles = selected_interval
    first, last = candles.index[0], candles.index[-1]
    span = f"{max(1, (last.date() - first.date()).days + 1)}-day span"
    return replace(
        item,
        role=role,
        rank_within_role=rank_within_role,
        interval=interval,
        history_span=span,
        candles=candles,
        sma_7d=time_based_sma_7d(candles),
        display_window_days=window_days,
        observation_count=observation_count,
    )


def price_action_chart_filename(item_code: str) -> str:
    """Return a stable item-based filename for non-report chart exports."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(item_code).strip().lower()).strip("-")
    return f"{slug or 'item'}-price-action.png"


def select_highlighted_items(
    rows: Sequence[dict] | Sequence[HighlightedItem],
    trades_by_item: dict[str, Sequence[dict] | object],
    *,
    min_tick: object = 0.001,
    require_chart_history: bool = True,
) -> list[HighlightedItem]:
    """Rank meaningful candidates and fall through unsupported chart histories."""
    items = [
        item if isinstance(item, HighlightedItem) else classify_price_dislocation(item, min_tick=min_tick)
        for item in rows
    ]

    def compare(left: HighlightedItem, right: HighlightedItem) -> int:
        left_severity = left.severity or 0.0
        right_severity = right.severity or 0.0
        if not _numerically_equal(left_severity, right_severity):
            return -1 if left_severity > right_severity else 1
        return -1 if left.item_code < right.item_code else 1 if left.item_code > right.item_code else 0

    def chart_capable(candidates: list[HighlightedItem]) -> list[HighlightedItem]:
        supported: list[HighlightedItem] = []
        for item in sorted(candidates, key=cmp_to_key(compare)):
            if not require_chart_history:
                supported.append(item)
                continue
            history = trades_by_item.get(item.item_code, ())
            trade_rows, window_days, observation_count = _price_action_history_values(history)
            selected_interval = select_price_action_interval(trade_rows)
            if selected_interval is None:
                continue
            interval, candles = selected_interval
            first, last = candles.index[0], candles.index[-1]
            supported.append(replace(
                item,
                interval=interval,
                history_span=f"{max(1, (last.date() - first.date()).days + 1)}-day span",
                candles=candles,
                sma_7d=time_based_sma_7d(candles),
                display_window_days=window_days,
                observation_count=observation_count,
            ))
        return supported

    discounts = chart_capable([
        item for item in items if item.item_code and item.classification == MEANINGFUL_DISCOUNT
    ])
    premiums = chart_capable([
        item for item in items if item.item_code and item.classification == MEANINGFUL_PREMIUM
    ])
    chosen: list[tuple[HighlightedItem, str, int]] = []
    if discounts and premiums:
        chosen = [(discounts[0], "largest_discount", 1), (premiums[0], "largest_premium", 1)]
    elif discounts:
        chosen = [(item, "largest_discount" if rank == 1 else "second_largest_discount", rank)
                  for rank, item in enumerate(discounts[:2], 1)]
    elif premiums:
        chosen = [(item, "largest_premium" if rank == 1 else "second_largest_premium", rank)
                  for rank, item in enumerate(premiums[:2], 1)]

    output: list[HighlightedItem] = []
    for item, role, rank in chosen:
        output.append(replace(item, role=role, rank_within_role=rank))
    return output


FORECAST_MODEL_VERSION = "direction-v1"

# Fixed WarEra production-chain mechanics. Values include the direct recipe PP plus the PP needed
# to produce every consumed ingredient. Update these values when the game recipes change.
TOTAL_UPSTREAM_PRODUCTION_POINTS = {
    "grain": 1.0,
    "limestone": 1.0,
    "lead": 1.0,
    "petroleum": 1.0,
    "mysteriousplant": 1.0,
    "coca": 1.0,
    "iron": 1.0,
    "wood": 1.0,
    "livestock": 20.0,
    "fish": 40.0,
    "steel": 20.0,
    "concrete": 20.0,
    "oil": 2.0,
    "paper": 2.0,
    "bread": 20.0,
    "steak": 40.0,
    "cookedfish": 80.0,
    "lightammo": 2.0,
    "ammo": 8.0,
    "heavyammo": 32.0,
    "pill": 400.0,
    "cocain": 400.0,
    "cocaine": 400.0,
}


def total_upstream_production_points(*, item_code: object = None, item_name: object = None) -> float | None:
    """Return fixed full-chain Production Points required for one factory item."""
    for value in (item_code, item_name):
        if value is None:
            continue
        key = re.sub(r"[^a-z0-9]", "", str(value).lower())
        if key in TOTAL_UPSTREAM_PRODUCTION_POINTS:
            return TOTAL_UPSTREAM_PRODUCTION_POINTS[key]
    return None


@dataclass(frozen=True)
class DirectionSignal:
    signal: str
    score: int
    reason_codes: tuple[str, ...]
    reason_labels: tuple[str, ...]


@dataclass(frozen=True)
class MarketTrendPattern:
    """Descriptive cross-horizon transaction-price pattern."""

    label: str
    directions: tuple[tuple[str, str], ...]

    @property
    def description(self) -> str:
        if not self.directions:
            return "No usable completed-transaction horizons"
        return ", ".join(f"{horizon}: {direction}" for horizon, direction in self.directions)


def classify_market_trend_pattern(
    *,
    change_1d_pct: object = None,
    change_7d_pct: object = None,
    change_30d_pct: object = None,
) -> MarketTrendPattern:
    """Classify persistence and reversal using the table spec's ±0.5% neutral band."""

    changes = (
        ("1D", _finite_float_or_none(change_1d_pct)),
        ("7D", _finite_float_or_none(change_7d_pct)),
        ("30D", _finite_float_or_none(change_30d_pct)),
    )
    directions = tuple(
        (horizon, "Up" if change > 0.5 else "Down" if change < -0.5 else "Flat")
        for horizon, change in changes
        if change is not None
    )
    direction_by_horizon = dict(directions)
    usable = tuple(direction_by_horizon.values())

    if len(usable) < 2:
        label = "Insufficient history"
    elif all(direction == "Flat" for direction in usable):
        label = "Flat"
    elif usable.count("Up") >= 2 and "Down" not in usable:
        label = "Persistent rise"
    elif usable.count("Down") >= 2 and "Up" not in usable:
        label = "Persistent fall"
    else:
        short = direction_by_horizon.get("1D")
        longer = [direction_by_horizon[horizon] for horizon in ("7D", "30D") if horizon in direction_by_horizon]
        if short == "Up" and "Down" in longer and "Up" not in longer:
            label = "Rebound"
        elif short == "Down" and "Up" in longer and "Down" not in longer:
            label = "Pullback"
        else:
            label = "Mixed"
    return MarketTrendPattern(label=label, directions=directions)


def calculate_range_position_pct(*, last_price: object, low: object, high: object) -> float | None:
    """Return a price's clamped position in a completed-transaction range."""

    last = _finite_float_or_none(last_price)
    low_value = _finite_float_or_none(low)
    high_value = _finite_float_or_none(high)
    if last is None or low_value is None or high_value is None or high_value <= low_value:
        return None
    return min(100.0, max(0.0, (last - low_value) / (high_value - low_value) * 100))


@dataclass(frozen=True)
class ForecastEvaluationRow:
    model_version: str
    feature_timestamp: str
    feature_timestamp_epoch: int
    target_timestamp: str
    target_timestamp_epoch: int
    prediction: str
    outcome: str
    correct: bool | None
    future_bid_return_pct: float
    gross_flip_return_pct: float | None = None


@dataclass(frozen=True)
class ForecastValidationResult:
    item_code: str
    model_version: str
    horizon_hours: float
    candidate_samples: int
    evaluable_samples: int
    execution_evaluable_samples: int
    up_predictions: int
    down_predictions: int
    correct_predictions: int
    accuracy_pct: float | None
    baseline_accuracy_pct: float | None
    median_future_bid_return_pct: float | None
    p10_future_bid_return_pct: float | None
    p90_future_bid_return_pct: float | None
    gross_flip_return_p10_pct: float | None
    gross_flip_return_median_pct: float | None
    gross_flip_return_p90_pct: float | None
    current_signal: str
    current_reason_codes: tuple[str, ...]
    evidence_label: str
    current_observed_at: str | None = None
    evaluated_rows: tuple[ForecastEvaluationRow, ...] = ()


@dataclass(frozen=True)
class FlipAssumptions:
    quantity: float = 1.0
    fee_pct_per_side: float = 0.0
    minimum_net_margin_pct: float = 1.0
    forecast_horizon_hours: float = 24.0
    max_quote_age_minutes: float = 30.0

    def __post_init__(self) -> None:
        values = {
            "quantity": self.quantity,
            "fee_pct_per_side": self.fee_pct_per_side,
            "minimum_net_margin_pct": self.minimum_net_margin_pct,
            "forecast_horizon_hours": self.forecast_horizon_hours,
            "max_quote_age_minutes": self.max_quote_age_minutes,
        }
        for name, value in values.items():
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite.")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive.")
        if not 0 <= self.fee_pct_per_side < 100:
            raise ValueError("fee_pct_per_side must be in [0, 100).")
        if self.minimum_net_margin_pct < 0:
            raise ValueError("minimum_net_margin_pct cannot be negative.")
        if self.forecast_horizon_hours <= 0:
            raise ValueError("forecast_horizon_hours must be positive.")
        if self.max_quote_age_minutes < 0:
            raise ValueError("max_quote_age_minutes cannot be negative.")


@dataclass(frozen=True)
class FairValueGuidance:
    quantity: float
    entry_action: str
    holder_action: str
    executable_ask_vwap: float | None
    executable_bid_vwap: float | None
    fair_price: float | None
    ask_gap_pct: float | None
    bid_gap_pct: float | None
    max_entry_price: float | None
    rich_exit_price: float | None
    net_to_fair_pct: float | None
    entry_fully_filled: bool
    exit_fully_filled: bool


def calculate_fair_value_guidance(
    *,
    fair_price: object,
    rich_exit_price: object,
    price_p10: object = None,
    price_p25: object = None,
    market_state: object = None,
    executable_ask_vwap: object,
    executable_bid_vwap: object,
    entry_fully_filled: bool,
    exit_fully_filled: bool,
    assumptions: FlipAssumptions,
) -> FairValueGuidance:
    """Build position-specific guidance from valuation and executable prices.

    Fair is the expected exit reference for an entry decision. Falling or
    volatile markets cap Max Buy at P25; a market with both labels uses P10.
    The rich-exit threshold requires the configured premium over Fair, capped
    by the empirical upper transaction percentile. It intentionally does not
    claim a holder profit because inventory cost basis is unknown.
    """
    fair = _finite_float_or_none(fair_price)
    empirical_rich_exit = _finite_float_or_none(rich_exit_price)
    p10 = _finite_float_or_none(price_p10)
    p25 = _finite_float_or_none(price_p25)
    ask = _finite_float_or_none(executable_ask_vwap) if entry_fully_filled else None
    bid = _finite_float_or_none(executable_bid_vwap) if exit_fully_filled else None
    fee_rate = assumptions.fee_pct_per_side / 100
    minimum_margin = assumptions.minimum_net_margin_pct / 100

    max_entry = None
    if fair is not None and fair > 0:
        max_entry = fair * (1 - fee_rate) / ((1 + fee_rate) * (1 + minimum_margin))
        state_text = str(market_state or "").lower()
        falling = "falling" in state_text
        volatile = "volatile" in state_text
        if falling and volatile:
            max_entry = min(max_entry, p10) if p10 is not None and p10 > 0 else None
        elif falling or volatile:
            max_entry = min(max_entry, p25) if p25 is not None and p25 > 0 else None
    rich_exit = None
    if fair is not None and fair > 0:
        margin_exit = fair * (1 + minimum_margin)
        rich_exit = (
            max(fair, min(empirical_rich_exit, margin_exit))
            if empirical_rich_exit is not None and empirical_rich_exit > 0
            else margin_exit
        )

    ask_gap = (ask - fair) / fair * 100 if ask is not None and fair is not None and fair > 0 else None
    bid_gap = (bid - fair) / fair * 100 if bid is not None and fair is not None and fair > 0 else None
    net_to_fair = (
        (fair * (1 - fee_rate) - ask * (1 + fee_rate)) / (ask * (1 + fee_rate)) * 100
        if ask is not None and ask > 0 and fair is not None and fair > 0
        else None
    )

    entry_action = (
        "BUY"
        if ask is not None and max_entry is not None and ask <= max_entry
        else "WAIT"
    )
    holder_action = (
        "SELL"
        if bid is not None and rich_exit is not None and rich_exit > 0 and bid >= rich_exit
        else "HOLD"
    )
    return FairValueGuidance(
        quantity=float(assumptions.quantity),
        entry_action=entry_action,
        holder_action=holder_action,
        executable_ask_vwap=ask,
        executable_bid_vwap=bid,
        fair_price=fair,
        ask_gap_pct=ask_gap,
        bid_gap_pct=bid_gap,
        max_entry_price=max_entry,
        rich_exit_price=rich_exit,
        net_to_fair_pct=net_to_fair,
        entry_fully_filled=bool(entry_fully_filled),
        exit_fully_filled=bool(exit_fully_filled),
    )


@dataclass(frozen=True)
class FlipOpportunity:
    item_code: str | None
    item_name: str
    verdict: str
    reason_codes: tuple[str, ...]
    quantity: float
    snapshot_at: str | None
    quote_age_minutes: float | None
    entry_fully_filled: bool
    entry_filled_quantity: float
    entry_average_price: float | None
    entry_worst_price: float | None
    entry_gross_value: float | None
    entry_fee: float | None
    total_entry_cost: float | None
    break_even_exit_vwap: float | None
    forecast_signal: str
    forecast_evidence: str
    forecast_samples: int
    forecast_exit_vwap_p10: float | None
    forecast_exit_vwap_median: float | None
    forecast_exit_vwap_p90: float | None
    net_margin_p10_pct: float | None
    net_margin_median_pct: float | None
    net_margin_p90_pct: float | None
    net_profit_median: float | None
    passive_limit_price: float | None


_DIRECTION_REASON_LABELS = {
    "strong_positive_momentum": "price rose strongly",
    "positive_momentum": "price is rising",
    "strong_negative_momentum": "price fell strongly",
    "negative_momentum": "price is falling",
    "below_fair": "below fair value",
    "above_fair": "above fair value",
}


def calculate_direction_signal(*, momentum_pct: object = None, fair_gap_pct: object = None) -> DirectionSignal:
    """Apply the fixed direction-v1 model using only the supplied feature values."""
    momentum = _finite_float_or_none(momentum_pct)
    fair_gap = _finite_float_or_none(fair_gap_pct)
    score = 0
    reasons: list[str] = []
    if momentum is not None:
        if momentum >= 6:
            score += 2
            reasons.append("strong_positive_momentum")
        elif momentum >= 1:
            score += 1
            reasons.append("positive_momentum")
        elif momentum <= -6:
            score -= 2
            reasons.append("strong_negative_momentum")
        elif momentum <= -1:
            score -= 1
            reasons.append("negative_momentum")
    if fair_gap is not None:
        if fair_gap <= -2:
            score += 1
            reasons.append("below_fair")
        elif fair_gap >= 2:
            score -= 1
            reasons.append("above_fair")
    signal = "Up" if score >= 1 else "Down" if score <= -1 else "No clear move"
    return DirectionSignal(
        signal=signal,
        score=score,
        reason_codes=tuple(reasons),
        reason_labels=tuple(_DIRECTION_REASON_LABELS[reason] for reason in reasons),
    )


def classify_future_bid_outcome(
    *, current_bid: object, future_bid: object, min_tick: object = 0.001
) -> tuple[str, float]:
    current = _finite_float_or_none(current_bid)
    future = _finite_float_or_none(future_bid)
    tick = _finite_float_or_none(min_tick)
    if current is None or current <= 0 or future is None or future <= 0:
        raise ValueError("current_bid and future_bid must be finite positive numbers.")
    if tick is None or tick < 0:
        raise ValueError("min_tick must be a finite non-negative number.")
    return_pct = (future - current) / current * 100
    delta = future - current
    if delta > tick and not math.isclose(delta, tick, rel_tol=1e-12, abs_tol=1e-12):
        return "Up", return_pct
    if delta < -tick and not math.isclose(delta, -tick, rel_tol=1e-12, abs_tol=1e-12):
        return "Down", return_pct
    return "Flat", return_pct


def forecast_evidence_label(
    *, evaluable_samples: int, min_samples: int, accuracy_pct: float | None, baseline_accuracy_pct: float | None
) -> str:
    if min_samples < 1:
        raise ValueError("min_samples must be at least 1.")
    if evaluable_samples < min_samples:
        return "Insufficient"
    if accuracy_pct is None or baseline_accuracy_pct is None or accuracy_pct <= baseline_accuracy_pct:
        return "Weak"
    if accuracy_pct - baseline_accuracy_pct < 5:
        return "Limited"
    return "Supported"


def summarize_forecast_evaluations(
    *,
    item_code: str,
    horizon_hours: float,
    rows: Sequence[ForecastEvaluationRow],
    current_signal: str,
    current_reason_codes: Sequence[str] = (),
    min_samples: int = 30,
    current_observed_at: str | None = None,
) -> ForecastValidationResult:
    copied_rows = tuple(rows)
    directional = tuple(
        row for row in copied_rows
        if row.prediction in {"Up", "Down"} and row.outcome in {"Up", "Down"}
    )
    correct = sum(row.prediction == row.outcome for row in directional)
    accuracy = correct / len(directional) * 100 if directional else None
    outcome_up = sum(row.outcome == "Up" for row in directional)
    outcome_down = sum(row.outcome == "Down" for row in directional)
    baseline = max(outcome_up, outcome_down) / len(directional) * 100 if directional else None
    evidence = forecast_evidence_label(
        evaluable_samples=len(directional),
        min_samples=min_samples,
        accuracy_pct=accuracy,
        baseline_accuracy_pct=baseline,
    )
    matching_returns = [
        row.future_bid_return_pct for row in copied_rows
        if current_signal in {"Up", "Down"} and row.prediction == current_signal
    ]
    matching_flips = [
        row.gross_flip_return_pct for row in copied_rows
        if current_signal in {"Up", "Down"}
        and row.prediction == current_signal
        and row.gross_flip_return_pct is not None
    ]
    bid_interval = _forecast_interval(matching_returns)
    flip_interval = _forecast_interval(matching_flips)
    return ForecastValidationResult(
        item_code=item_code,
        model_version=FORECAST_MODEL_VERSION,
        horizon_hours=float(horizon_hours),
        candidate_samples=len(copied_rows),
        evaluable_samples=len(directional),
        execution_evaluable_samples=sum(row.gross_flip_return_pct is not None for row in copied_rows),
        up_predictions=sum(row.prediction == "Up" for row in copied_rows),
        down_predictions=sum(row.prediction == "Down" for row in copied_rows),
        correct_predictions=correct,
        accuracy_pct=accuracy,
        baseline_accuracy_pct=baseline,
        median_future_bid_return_pct=bid_interval[1],
        p10_future_bid_return_pct=bid_interval[0],
        p90_future_bid_return_pct=bid_interval[2],
        gross_flip_return_p10_pct=flip_interval[0],
        gross_flip_return_median_pct=flip_interval[1],
        gross_flip_return_p90_pct=flip_interval[2],
        current_signal=current_signal,
        current_reason_codes=tuple(current_reason_codes),
        evidence_label=evidence,
        current_observed_at=current_observed_at,
        evaluated_rows=copied_rows,
    )


def _forecast_interval(values: Sequence[float | None]) -> tuple[float | None, float | None, float | None]:
    numeric = [float(value) for value in values if value is not None]
    if len(numeric) < 10:
        return None, None, None
    return (_linear_percentile(numeric, 10), _linear_percentile(numeric, 50), _linear_percentile(numeric, 90))


def _linear_percentile(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile / 100
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _finite_float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


@dataclass(frozen=True)
class SweepResult:
    side: str
    requested_quantity: float
    filled_quantity: float
    unfilled_quantity: float
    fully_filled: bool
    gross_value: float
    average_price: float | None
    best_price: float | None
    worst_price: float | None
    slippage_abs: float | None
    slippage_pct: float | None


@dataclass(frozen=True)
class BookLevelDepth:
    price: float
    quantity: float
    order_value: float
    cumulative_quantity: float
    cumulative_value: float
    is_wall: bool


@dataclass(frozen=True)
class OrderBookSummary:
    best_bid: float | None
    best_ask: float | None
    bid_quantity: float
    ask_quantity: float
    bid_value: float
    ask_value: float
    pressure_pct: float | None
    spread_abs: float | None
    spread_pct: float | None
    bids: tuple[BookLevelDepth, ...]
    asks: tuple[BookLevelDepth, ...]


def summarize_order_book(*, bids: object, asks: object) -> OrderBookSummary:
    """Summarize normalized levels without hiding depth behind a composite score."""
    bid_levels = _normalized_levels(bids, reverse=True)
    ask_levels = _normalized_levels(asks, reverse=False)

    def depth_rows(levels: list[tuple[float, float]]) -> tuple[BookLevelDepth, ...]:
        wall_index = max(range(len(levels)), key=lambda index: levels[index][1], default=-1)
        cumulative_quantity = 0.0
        cumulative_value = 0.0
        rows = []
        for index, (price, quantity) in enumerate(levels):
            value = price * quantity
            cumulative_quantity += quantity
            cumulative_value += value
            rows.append(BookLevelDepth(
                price=price,
                quantity=quantity,
                order_value=value,
                cumulative_quantity=cumulative_quantity,
                cumulative_value=cumulative_value,
                is_wall=index == wall_index,
            ))
        return tuple(rows)

    bid_rows = depth_rows(bid_levels)
    ask_rows = depth_rows(ask_levels)
    bid_value = sum(level.order_value for level in bid_rows)
    ask_value = sum(level.order_value for level in ask_rows)
    total_value = bid_value + ask_value
    best_bid = bid_rows[0].price if bid_rows else None
    best_ask = ask_rows[0].price if ask_rows else None
    spread = best_ask - best_bid if best_bid is not None and best_ask is not None else None
    midpoint = (best_bid + best_ask) / 2 if best_bid is not None and best_ask is not None else None
    return OrderBookSummary(
        best_bid=best_bid,
        best_ask=best_ask,
        bid_quantity=sum(level.quantity for level in bid_rows),
        ask_quantity=sum(level.quantity for level in ask_rows),
        bid_value=bid_value,
        ask_value=ask_value,
        pressure_pct=(bid_value - ask_value) / total_value * 100 if total_value > 0 else None,
        spread_abs=spread,
        spread_pct=spread / midpoint * 100 if spread is not None and midpoint and midpoint > 0 else None,
        bids=bid_rows,
        asks=ask_rows,
    )


def _normalized_levels(levels: object, *, reverse: bool) -> list[tuple[float, float]]:
    try:
        source_levels = list(levels)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError("levels must be an iterable of price and quantity values.") from exc
    by_price: dict[float, float] = {}
    for index, level in enumerate(source_levels):
        try:
            if isinstance(level, dict):
                price = float(level["price"])
                quantity = float(level["quantity"])
            else:
                price = float(level.price)
                quantity = float(level.quantity)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid level at position {index}.") from exc
        if not math.isfinite(price) or price <= 0 or not math.isfinite(quantity) or quantity <= 0:
            raise ValueError(f"Invalid level at position {index}.")
        by_price[price] = by_price.get(price, 0.0) + quantity
    return [(price, by_price[price]) for price in sorted(by_price, reverse=reverse)]


def calculate_book_sweep(levels: object, *, side: str, quantity: float) -> SweepResult:
    if side not in {"buy", "sell"}:
        raise ValueError("side must be 'buy' or 'sell'.")
    try:
        requested = float(quantity)
    except (TypeError, ValueError) as exc:
        raise ValueError("quantity must be a finite positive number.") from exc
    if not math.isfinite(requested) or requested <= 0:
        raise ValueError("quantity must be a finite positive number.")

    normalized: list[tuple[float, float]] = []
    try:
        source_levels = list(levels)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError("levels must be an iterable of price and quantity values.") from exc
    for index, level in enumerate(source_levels):
        try:
            if isinstance(level, dict):
                price = float(level["price"])
                available = float(level["quantity"])
            else:
                price = float(level.price)
                available = float(level.quantity)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid level at position {index}.") from exc
        if not math.isfinite(price) or price < 0 or not math.isfinite(available) or available <= 0:
            raise ValueError(f"Invalid level at position {index}.")
        normalized.append((price, available))

    normalized.sort(key=lambda level: level[0], reverse=side == "sell")
    remaining = requested
    gross = 0.0
    filled = 0.0
    worst_price = None
    for price, available in normalized:
        fill = min(remaining, available)
        gross += fill * price
        filled += fill
        remaining -= fill
        if fill > 0:
            worst_price = price
        if remaining <= 0:
            remaining = 0.0
            break

    best_price = normalized[0][0] if normalized else None
    average = gross / filled if filled > 0 else None
    slippage_abs = None
    slippage_pct = None
    if average is not None and best_price is not None:
        slippage_abs = average - best_price if side == "buy" else best_price - average
        if best_price > 0:
            slippage_pct = slippage_abs / best_price * 100
    return SweepResult(
        side=side,
        requested_quantity=requested,
        filled_quantity=filled,
        unfilled_quantity=remaining,
        fully_filled=remaining == 0,
        gross_value=gross,
        average_price=average,
        best_price=best_price,
        worst_price=worst_price,
        slippage_abs=slippage_abs,
        slippage_pct=slippage_pct,
    )


def passive_limit_candidate(*, best_bid: object, best_ask: object, min_tick: object = 0.001) -> float | None:
    """Return a passive price idea without making any fill assumption."""
    bid = _finite_float_or_none(best_bid)
    ask = _finite_float_or_none(best_ask)
    tick = _finite_float_or_none(min_tick)
    if bid is None or ask is None or tick is None or bid <= 0 or ask <= 0 or tick < 0 or bid >= ask:
        return None
    improved = bid + tick
    return improved if improved < ask and not math.isclose(improved, ask) else bid


def calculate_flip_opportunity(data: dict, assumptions: FlipAssumptions) -> FlipOpportunity:
    """Convert one already-normalized market/forecast input into a fail-closed opportunity."""
    item_code = str(data["item_code"]) if data.get("item_code") is not None else None
    item_name = str(data.get("item_name") or data.get("name") or item_code or "Unknown")
    snapshot_at = str(data["snapshot_at"]) if data.get("snapshot_at") is not None else None
    quote_age = _finite_float_or_none(data.get("quote_age_minutes"))
    bid = _finite_float_or_none(data.get("best_bid", data.get("bid")))
    ask = _finite_float_or_none(data.get("best_ask", data.get("ask")))
    min_tick = _finite_float_or_none(data.get("min_tick"))
    min_tick = 0.001 if min_tick is None else min_tick
    signal = str(data.get("forecast_signal") or "Unavailable")
    evidence = str(data.get("forecast_evidence") or "Insufficient")
    try:
        samples = max(0, int(data.get("forecast_samples") or 0))
    except (TypeError, ValueError):
        samples = 0

    supplied_sweep = data.get("entry_sweep")
    sweep = supplied_sweep if isinstance(supplied_sweep, SweepResult) else None
    if sweep is None and data.get("asks") is not None:
        try:
            sweep = calculate_book_sweep(data.get("asks"), side="buy", quantity=assumptions.quantity)
        except ValueError:
            sweep = None

    p10_return = _finite_float_or_none(data.get("gross_flip_return_p10_pct"))
    median_return = _finite_float_or_none(data.get("gross_flip_return_median_pct"))
    p90_return = _finite_float_or_none(data.get("gross_flip_return_p90_pct"))
    interval_available = all(value is not None for value in (p10_return, median_return, p90_return))
    book_available = bid is not None and ask is not None and sweep is not None
    forecast_available = signal != "Unavailable" and bool(str(data.get("forecast_evidence") or "").strip())
    passive = passive_limit_candidate(best_bid=bid, best_ask=ask, min_tick=min_tick)

    verdict = "Unavailable"
    reasons: tuple[str, ...]
    if not book_available:
        reasons = ("missing_order_book",)
    elif snapshot_at is None or quote_age is None:
        reasons = ("missing_timestamp",)
    elif not forecast_available:
        reasons = ("missing_forecast",)
    elif not interval_available:
        reasons = ("missing_execution_interval",)
    else:
        reasons = ()

    entry_filled = sweep.filled_quantity if sweep is not None else 0.0
    entry_full = bool(sweep and sweep.fully_filled)

    # Partial executions are deliberately not costed as if the requested trade filled.
    entry_average = entry_worst = entry_gross = entry_fee = total_entry = break_even = None
    exit_p10 = exit_median = exit_p90 = None
    margin_p10 = margin_median = margin_p90 = profit_median = None

    if not reasons:
        assert sweep is not None and quote_age is not None and bid is not None and ask is not None
        if quote_age > assumptions.max_quote_age_minutes:
            verdict, reasons = "No trade", ("stale_quote",)
        elif not sweep.fully_filled:
            verdict, reasons = "No trade", ("insufficient_ask_depth",)
        elif bid <= 0 or ask <= 0 or bid >= ask or sweep.gross_value <= 0 or min_tick < 0:
            verdict, reasons = "No trade", ("invalid_book",)
        else:
            fee_rate = assumptions.fee_pct_per_side / 100
            entry_average = sweep.average_price
            entry_worst = sweep.worst_price
            entry_gross = sweep.gross_value
            entry_fee = entry_gross * fee_rate
            total_entry = entry_gross + entry_fee
            break_even = total_entry / (assumptions.quantity * (1 - fee_rate))

            def outcome(return_pct: float) -> tuple[float, float, float]:
                exit_gross = entry_gross * (1 + return_pct / 100)
                exit_vwap = exit_gross / assumptions.quantity
                profit = exit_gross * (1 - fee_rate) - total_entry
                margin = profit / total_entry * 100
                return exit_vwap, margin, profit

            try:
                exit_p10, margin_p10, _ = outcome(p10_return)  # type: ignore[arg-type]
                exit_median, margin_median, profit_median = outcome(median_return)  # type: ignore[arg-type]
                exit_p90, margin_p90, _ = outcome(p90_return)  # type: ignore[arg-type]
                calculated = (
                    entry_average, entry_worst, entry_fee, total_entry, break_even,
                    exit_p10, exit_median, exit_p90, margin_p10, margin_median,
                    margin_p90, profit_median,
                )
                if any(value is None or not math.isfinite(value) for value in calculated):
                    raise ValueError
                if any(value <= 0 for value in (exit_p10, exit_median, exit_p90, break_even)):
                    raise ValueError
            except (ArithmeticError, ValueError):
                verdict, reasons = "No trade", ("invalid_book",)
                entry_average = entry_worst = entry_gross = entry_fee = total_entry = break_even = None
                exit_p10 = exit_median = exit_p90 = None
                margin_p10 = margin_median = margin_p90 = profit_median = None
            else:
                assert margin_median is not None
                if margin_median <= 0:
                    verdict, reasons = "No trade", ("margin_non_positive",)
                elif evidence in {"Insufficient", "Weak", "Limited"}:
                    reason = "forecast_insufficient" if evidence == "Insufficient" else "forecast_not_above_baseline"
                    verdict, reasons = "Watch", (reason,)
                elif margin_median < assumptions.minimum_net_margin_pct:
                    verdict, reasons = "Watch", ("margin_below_threshold",)
                elif evidence == "Supported":
                    verdict, reasons = "Potential flip", ("supported_positive_margin",)
                else:
                    verdict, reasons = "Watch", ("forecast_not_above_baseline",)

    return FlipOpportunity(
        item_code=item_code,
        item_name=item_name,
        verdict=verdict,
        reason_codes=reasons,
        quantity=float(assumptions.quantity),
        snapshot_at=snapshot_at,
        quote_age_minutes=quote_age,
        entry_fully_filled=entry_full,
        entry_filled_quantity=entry_filled,
        entry_average_price=entry_average,
        entry_worst_price=entry_worst,
        entry_gross_value=entry_gross,
        entry_fee=entry_fee,
        total_entry_cost=total_entry,
        break_even_exit_vwap=break_even,
        forecast_signal=signal,
        forecast_evidence=evidence,
        forecast_samples=samples,
        forecast_exit_vwap_p10=exit_p10,
        forecast_exit_vwap_median=exit_median,
        forecast_exit_vwap_p90=exit_p90,
        net_margin_p10_pct=margin_p10,
        net_margin_median_pct=margin_median,
        net_margin_p90_pct=margin_p90,
        net_profit_median=profit_median,
        passive_limit_price=passive,
    )


@dataclass(frozen=True)
class MarketMetrics:
    item_name: str
    item_code: Optional[str]
    bid: Optional[float]
    ask: Optional[float]
    current_price: Optional[float]
    trades_7d: Optional[float]
    high_7d: Optional[float]
    low_7d: Optional[float]
    total_production_points: Optional[float] = None
    open_7d: Optional[float] = None
    close_7d: Optional[float] = None
    average_7d: Optional[float] = None
    vwap_7d: Optional[float] = None
    median_7d: Optional[float] = None
    price_p10_7d: Optional[float] = None
    price_p90_7d: Optional[float] = None
    stable_fair_price_7d: Optional[float] = None
    fair_reference_7d: Optional[float] = None
    fair_gap_7d_pct: Optional[float] = None
    rolling_average_7d: Optional[float] = None
    mid_price: Optional[float] = None
    min_tick: float = 0.001
    spread: Optional[float] = None
    spread_pct: Optional[float] = None
    crossing_loss_pct: Optional[float] = None
    range_pct: Optional[float] = None
    stable_range_pct_7d: Optional[float] = None
    depth_imbalance_pct: Optional[float] = None
    momentum_7d_pct: Optional[float] = None
    trading_attractiveness: Optional[float] = None
    status: str = "OK"


TREND_LABELS = ("Rising", "Falling", "Range-bound", "Volatile", "Thin", "Stable")


def _valid_number(value: object) -> bool:
    if value is None:
        return False
    try:
        f = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f)


def _float_or_none(value: object) -> Optional[float]:
    return float(value) if _valid_number(value) else None


def calculate_liquidity_score(
    *,
    bid_depth: object,
    ask_depth: object,
    spread_pct: object = None,
) -> float:
    """Return a depth-based liquidity score penalized by the current spread."""
    bid = _float_or_none(bid_depth)
    ask = _float_or_none(ask_depth)
    depth = max(bid or 0.0, 0.0) + max(ask or 0.0, 0.0)
    if depth <= 0:
        return 0.0

    spread = _float_or_none(spread_pct)
    spread_floor = max(spread or 0.0, 0.5)
    return depth / (1 + spread_floor / 100)


def calculate_metrics(row: dict) -> MarketMetrics:
    item_name = str(row.get("item_name") or row.get("name") or row.get("item") or "Unknown")
    item_code = row.get("item_code")
    item_code = str(item_code) if item_code is not None else None
    total_production_points = total_upstream_production_points(item_code=item_code, item_name=item_name)
    bid = _float_or_none(row.get("bid"))
    ask = _float_or_none(row.get("ask"))
    current_price = _float_or_none(row.get("last_trade_price"))
    trades_7d = _float_or_none(row.get("trades_7d"))
    trades_24h = _float_or_none(row.get("trades_24h"))
    if trades_7d is None and trades_24h is not None:
        trades_7d = trades_24h * 7

    high_7d = _float_or_none(row.get("high_7d") or row.get("high"))
    low_7d = _float_or_none(row.get("low_7d") or row.get("low"))
    open_7d = _float_or_none(row.get("open_7d") or row.get("open"))
    close_7d = _float_or_none(row.get("close_7d") or row.get("close") or current_price)
    min_tick = _float_or_none(row.get("min_tick"))
    if min_tick is None or min_tick < 0:
        min_tick = 0.001

    missing = []
    for field_name, value in {
        "bid": bid,
        "ask": ask,
        "trades_7d": trades_7d,
        "high_7d": high_7d,
        "low_7d": low_7d,
    }.items():
        if value is None:
            missing.append(field_name)

    mid_price = spread = spread_pct = crossing_loss_pct = range_pct = momentum_7d_pct = trading_attractiveness = None
    avg_price = None
    status = "OK"

    if bid is not None and ask is not None:
        mid_price = (bid + ask) / 2
        raw_spread = ask - bid
        spread = 0 if raw_spread <= min_tick or math.isclose(raw_spread, min_tick) else raw_spread - min_tick
        if mid_price > 0:
            spread_pct = spread / mid_price * 100
        if ask > 0:
            crossing_loss_pct = max(raw_spread, 0.0) / ask * 100

    if missing:
        status = "Missing: " + ", ".join(missing)
    else:
        assert bid is not None and ask is not None and trades_7d is not None and high_7d is not None and low_7d is not None
        avg_price = (high_7d + low_7d) / 2
        if avg_price > 0:
            range_pct = (high_7d - low_7d) / avg_price * 100
        if open_7d is not None and close_7d is not None and open_7d > 0:
            momentum_7d_pct = (close_7d - open_7d) / open_7d * 100
        if spread_pct is None or spread_pct <= 0 or range_pct is None or range_pct <= 0 or trades_7d <= 0:
            status = "Insufficient score data"
        else:
            trading_attractiveness = (spread_pct * trades_7d) / range_pct

    average_7d = _float_or_none(row.get("average_7d")) or avg_price
    rolling_average_7d = _float_or_none(row.get("rolling_average_7d")) or average_7d
    vwap_7d = _float_or_none(row.get("vwap_7d"))
    median_7d = _float_or_none(row.get("median_7d"))
    price_p10_7d = _float_or_none(row.get("price_p10_7d"))
    price_p90_7d = _float_or_none(row.get("price_p90_7d"))
    stable_fair_price_7d = _float_or_none(row.get("stable_fair_price_7d"))
    stable_range_pct_7d = _float_or_none(row.get("stable_range_pct_7d"))
    depth_imbalance_pct = _float_or_none(
        row.get("latest_depth_imbalance_pct")
        if row.get("latest_depth_imbalance_pct") is not None
        else row.get("depth_imbalance_pct")
    )
    fair_reference_7d = next(
        (
            value
            for value in (stable_fair_price_7d, vwap_7d, median_7d, average_7d, rolling_average_7d)
            if value is not None and value > 0
        ),
        None,
    )
    fair_gap_7d_pct = (
        (current_price - fair_reference_7d) / fair_reference_7d * 100
        if current_price is not None and fair_reference_7d is not None
        else None
    )

    return MarketMetrics(
        item_name=item_name,
        item_code=item_code,
        bid=bid,
        ask=ask,
        current_price=current_price,
        trades_7d=trades_7d,
        high_7d=high_7d,
        low_7d=low_7d,
        total_production_points=total_production_points,
        open_7d=open_7d,
        close_7d=close_7d,
        average_7d=average_7d,
        vwap_7d=vwap_7d,
        median_7d=median_7d,
        price_p10_7d=price_p10_7d,
        price_p90_7d=price_p90_7d,
        stable_fair_price_7d=stable_fair_price_7d,
        fair_reference_7d=fair_reference_7d,
        fair_gap_7d_pct=fair_gap_7d_pct,
        rolling_average_7d=rolling_average_7d,
        mid_price=mid_price,
        min_tick=min_tick,
        spread=spread,
        spread_pct=spread_pct,
        crossing_loss_pct=crossing_loss_pct,
        range_pct=range_pct,
        stable_range_pct_7d=stable_range_pct_7d,
        depth_imbalance_pct=depth_imbalance_pct,
        momentum_7d_pct=momentum_7d_pct,
        trading_attractiveness=trading_attractiveness,
        status=status,
    )


def classify_tendency(
    *,
    open_price: object = None,
    close_price: object = None,
    min_price: object = None,
    max_price: object = None,
    average_price: object = None,
    rolling_average: object = None,
    trade_count: object = None,
    volume: object = None,
    spread_pct: object = None,
    stable_range_pct: object = None,
) -> list[str]:
    """Return descriptive market tendency labels for a report window."""
    open_value = _float_or_none(open_price)
    close_value = _float_or_none(close_price)
    min_value = _float_or_none(min_price)
    max_value = _float_or_none(max_price)
    average_value = _float_or_none(average_price)
    rolling_value = _float_or_none(rolling_average) or average_value
    trade_count_value = _float_or_none(trade_count) or 0.0
    volume_value = _float_or_none(volume) or 0.0
    spread_pct_value = _float_or_none(spread_pct)
    stable_range_pct_value = _float_or_none(stable_range_pct)

    labels: list[str] = []
    range_pct = None
    if min_value is not None and max_value is not None and average_value is not None and average_value > 0:
        range_pct = (max_value - min_value) / average_value * 100
    if stable_range_pct_value is not None:
        range_pct = stable_range_pct_value

    if trade_count_value < 3 or volume_value <= 0:
        labels.append("Thin")

    if (
        open_value is not None
        and close_value is not None
        and rolling_value is not None
        and open_value > 0
    ):
        change_pct = (close_value - open_value) / open_value * 100
        if change_pct >= 2 and close_value >= rolling_value:
            labels.append("Rising")
        elif change_pct <= -2 and close_value <= rolling_value:
            labels.append("Falling")

    if range_pct is not None:
        if range_pct >= 12:
            labels.append("Volatile")
        elif range_pct <= 3:
            labels.append("Range-bound")

    if (
        range_pct is not None
        and range_pct <= 3
        and trade_count_value >= 3
        and volume_value > 0
        and (spread_pct_value is None or spread_pct_value <= 3)
    ):
        labels.append("Stable")

    return labels or ["Stable"]
