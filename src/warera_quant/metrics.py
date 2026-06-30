from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


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
    open_7d: Optional[float] = None
    close_7d: Optional[float] = None
    average_7d: Optional[float] = None
    vwap_7d: Optional[float] = None
    rolling_average_7d: Optional[float] = None
    mid_price: Optional[float] = None
    min_tick: float = 0.001
    spread: Optional[float] = None
    spread_pct: Optional[float] = None
    range_pct: Optional[float] = None
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


def calculate_metrics(row: dict) -> MarketMetrics:
    item_name = str(row.get("item_name") or row.get("name") or row.get("item") or "Unknown")
    item_code = row.get("item_code")
    item_code = str(item_code) if item_code is not None else None
    bid = _float_or_none(row.get("bid"))
    ask = _float_or_none(row.get("ask"))
    current_price = _float_or_none(row.get("current_price") or row.get("price") or row.get("last_trade_price"))
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

    mid_price = spread = spread_pct = range_pct = momentum_7d_pct = trading_attractiveness = None
    avg_price = None
    status = "OK"

    if missing:
        status = "Missing: " + ", ".join(missing)
    else:
        assert bid is not None and ask is not None and trades_7d is not None and high_7d is not None and low_7d is not None
        mid_price = (bid + ask) / 2
        raw_spread = ask - bid
        spread = 0 if raw_spread <= min_tick or math.isclose(raw_spread, min_tick) else raw_spread - min_tick
        if mid_price > 0:
            spread_pct = spread / mid_price * 100
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

    return MarketMetrics(
        item_name=item_name,
        item_code=item_code,
        bid=bid,
        ask=ask,
        current_price=current_price,
        trades_7d=trades_7d,
        high_7d=high_7d,
        low_7d=low_7d,
        open_7d=open_7d,
        close_7d=close_7d,
        average_7d=average_7d,
        vwap_7d=vwap_7d,
        rolling_average_7d=rolling_average_7d,
        mid_price=mid_price,
        min_tick=min_tick,
        spread=spread,
        spread_pct=spread_pct,
        range_pct=range_pct,
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

    labels: list[str] = []
    range_pct = None
    if min_value is not None and max_value is not None and average_value is not None and average_value > 0:
        range_pct = (max_value - min_value) / average_value * 100

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
