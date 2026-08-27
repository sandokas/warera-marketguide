from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd


COMMON_RECORD_KEYS = ("data", "results", "items", "markets", "records")

FIELD_ALIASES = {
    "item_name": ("item_name", "item", "name", "item_title", "title"),
    "bid": ("bid", "bid_price", "best_bid", "highest_bid", "buy", "buy_price", "highest_buy"),
    "ask": ("ask", "ask_price", "best_ask", "lowest_ask", "sell", "sell_price", "lowest_sell"),
    "last_trade_price": ("last_trade_price",),
    "trades_7d": ("trades_7d", "seven_day_trades", "trades_week", "weekly_trades", "volume_7d"),
    "trades_24h": ("trades_24h", "daily_trades", "volume_24h"),
    "high_7d": ("high_7d", "seven_day_high", "week_high", "high"),
    "low_7d": ("low_7d", "seven_day_low", "week_low", "low"),
    "open_7d": ("open_7d", "seven_day_open", "week_open", "open"),
    "close_7d": ("close_7d", "seven_day_close", "week_close", "close"),
}


def _normalize_column(column: object) -> str:
    return str(column).strip().lower().replace(" ", "_").replace(".", "_").replace("-", "_")


def _dig(data: Any, path: str) -> Any:
    current = data
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current[part]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes, bytearray)):
            current = current[int(part)]
        else:
            raise KeyError(path)
    return current


def _find_records(data: Any) -> list[Mapping[str, Any]]:
    if isinstance(data, list):
        records = data
    elif isinstance(data, Mapping):
        records = None
        for key in COMMON_RECORD_KEYS:
            value = data.get(key)
            if isinstance(value, list):
                records = value
                break
        if records is None:
            records = [data]
    else:
        raise ValueError("API response must be a JSON object or array.")

    if not all(isinstance(record, Mapping) for record in records):
        raise ValueError("API records must be JSON objects.")
    return list(records)


def market_json_to_dataframe(data: Any, *, records_path: str | None = None) -> pd.DataFrame:
    source = _dig(data, records_path) if records_path else data
    records = _find_records(source)
    df = pd.json_normalize([dict(record) for record in records], sep="_")
    df.columns = [_normalize_column(column) for column in df.columns]

    for canonical, aliases in FIELD_ALIASES.items():
        if canonical in df.columns:
            continue
        for alias in aliases:
            normalized_alias = _normalize_column(alias)
            if normalized_alias in df.columns:
                df[canonical] = df[normalized_alias]
                break

    return df
