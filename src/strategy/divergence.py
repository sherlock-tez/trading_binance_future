from __future__ import annotations

from typing import Dict, List

import pandas as pd


def pivot_lows(series: pd.Series, window: int) -> List[int]:
    indices: List[int] = []
    values = series.tolist()
    n = len(values)
    for i in range(window, n - window):
        center = values[i]
        left = values[i - window : i]
        right = values[i + 1 : i + window + 1]
        if all(center <= point for point in left + right):
            indices.append(i)
    return indices


def pivot_highs(series: pd.Series, window: int) -> List[int]:
    indices: List[int] = []
    values = series.tolist()
    n = len(values)
    for i in range(window, n - window):
        center = values[i]
        left = values[i - window : i]
        right = values[i + 1 : i + window + 1]
        if all(center >= point for point in left + right):
            indices.append(i)
    return indices


def bullish_divergence(
    price: pd.Series,
    indicator: pd.Series,
    *,
    pivot_window: int,
    lookback: int,
) -> Dict[str, float | bool | int]:
    if len(price) < max(10, lookback):
        return {"is_valid": False}

    price_tail = price.tail(lookback).reset_index(drop=True)
    indicator_tail = indicator.tail(lookback).reset_index(drop=True)

    lows = pivot_lows(price_tail, pivot_window)
    if len(lows) < 2:
        return {"is_valid": False}

    i1, i2 = lows[-2], lows[-1]
    p1, p2 = float(price_tail.iloc[i1]), float(price_tail.iloc[i2])
    ind1, ind2 = float(indicator_tail.iloc[i1]), float(indicator_tail.iloc[i2])

    valid = p2 < p1 and ind2 > ind1
    return {
        "is_valid": valid,
        "price_first": p1,
        "price_second": p2,
        "indicator_first": ind1,
        "indicator_second": ind2,
        "pivot_first": i1,
        "pivot_second": i2,
    }


def bearish_divergence(
    price: pd.Series,
    indicator: pd.Series,
    *,
    pivot_window: int,
    lookback: int,
) -> Dict[str, float | bool | int]:
    if len(price) < max(10, lookback):
        return {"is_valid": False}

    price_tail = price.tail(lookback).reset_index(drop=True)
    indicator_tail = indicator.tail(lookback).reset_index(drop=True)

    highs = pivot_highs(price_tail, pivot_window)
    if len(highs) < 2:
        return {"is_valid": False}

    i1, i2 = highs[-2], highs[-1]
    p1, p2 = float(price_tail.iloc[i1]), float(price_tail.iloc[i2])
    ind1, ind2 = float(indicator_tail.iloc[i1]), float(indicator_tail.iloc[i2])

    valid = p2 > p1 and ind2 < ind1
    return {
        "is_valid": valid,
        "price_first": p1,
        "price_second": p2,
        "indicator_first": ind1,
        "indicator_second": ind2,
        "pivot_first": i1,
        "pivot_second": i2,
    }
