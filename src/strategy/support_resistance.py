from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd

from src.strategy.divergence import pivot_highs, pivot_lows


def _merge_levels(levels: Iterable[float], *, tolerance_bps: float) -> List[float]:
    sorted_levels = sorted(float(level) for level in levels)
    if not sorted_levels:
        return []

    merged: List[List[float]] = [[sorted_levels[0]]]
    for value in sorted_levels[1:]:
        anchor = merged[-1][-1]
        if abs(value - anchor) / max(anchor, 1e-12) * 10000 <= tolerance_bps:
            merged[-1].append(value)
        else:
            merged.append([value])
    return [sum(group) / len(group) for group in merged]


def extract_levels_from_frame(
    frame: pd.DataFrame,
    *,
    pivot_window: int,
    tolerance_bps: float = 18,
) -> Tuple[List[float], List[float]]:
    if frame.empty:
        return [], []

    highs = frame["high"].astype(float).reset_index(drop=True)
    lows = frame["low"].astype(float).reset_index(drop=True)

    support_points = [float(lows.iloc[i]) for i in pivot_lows(lows, pivot_window)]
    resistance_points = [float(highs.iloc[i]) for i in pivot_highs(highs, pivot_window)]

    supports = _merge_levels(support_points, tolerance_bps=tolerance_bps)
    resistances = _merge_levels(resistance_points, tolerance_bps=tolerance_bps)
    return supports, resistances


def build_multi_timeframe_levels(
    frames: Dict[str, pd.DataFrame],
    *,
    pivot_window: int,
    tolerance_bps: float = 18,
) -> Tuple[List[float], List[float]]:
    supports: List[float] = []
    resistances: List[float] = []

    for _, frame in frames.items():
        frame_supports, frame_resistances = extract_levels_from_frame(
            frame,
            pivot_window=pivot_window,
            tolerance_bps=tolerance_bps,
        )
        supports.extend(frame_supports)
        resistances.extend(frame_resistances)

    return (
        _merge_levels(supports, tolerance_bps=tolerance_bps),
        _merge_levels(resistances, tolerance_bps=tolerance_bps),
    )


def nearest_support(levels: List[float], price: float) -> Optional[float]:
    below = [level for level in levels if level < price]
    if not below:
        return None
    return max(below)


def nearest_resistance(levels: List[float], price: float) -> Optional[float]:
    above = [level for level in levels if level > price]
    if not above:
        return None
    return min(above)
