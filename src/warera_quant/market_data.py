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
    FORECAST_MODEL_VERSION,
    ForecastEvaluationRow,
    ForecastValidationResult,
    FairValueGuidance,
    FlipAssumptions,
    calculate_book_sweep,
    calculate_fair_value_guidance,
    calculate_notional_book_sweep,
    calculate_direction_signal,
    calculate_flip_opportunity,
    calculate_liquidity_score,
    classify_future_bid_outcome,
    classify_tendency,
    summarize_forecast_evaluations,
    summarize_order_book,
)


SUPPORTED_REPORT_WINDOWS = ("1D", "7D", "30D", "90D", "1Y")
DEFAULT_REPORT_WINDOWS = ("1D", "7D", "30D")
FORECAST_TRAILING_SECONDS = 7 * 24 * 60 * 60
EXECUTION_BUDGETS = (100.0, 1_000.0, 10_000.0)


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
        current_price = _select_current_price(last_trade_price, quote_price, mid_price)
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
        row["order_book_executions"] = []
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
                row["order_book_executions"] = [
                    {
                        "budget": budget,
                        "buy": asdict(calculate_notional_book_sweep(asks, side="buy", value=budget)),
                        "sell": asdict(calculate_notional_book_sweep(bids, side="sell", value=budget)),
                    }
                    for budget in EXECUTION_BUDGETS
                ]
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
    current_price = _select_current_price(last_trade_price, quote_price, mid_price)
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


def _select_current_price(last_trade_price: float | None, quote_price: float | None, mid_price: float | None) -> float | None:
    if last_trade_price is not None:
        return last_trade_price
    if quote_price is not None:
        return quote_price
    return mid_price


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
