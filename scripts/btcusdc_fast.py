"""
Fast vectorized backtest for BTCUSDC parameter sweeps.

Mathematically identical to the production SignalEngine.generate_signal()
when given the same signal_frame_1h tail(600) / higher_frames tail(600).
Verified by parity test against scripts/btcusdc_optimize.py.

Speedups:
- Precompute RSI, MACD, ATR, EMA once on the FULL base series, then slice.
- Precompute pivot indices on the full base series.
- Cache higher-TF supports/resistances per higher-TF epoch.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.config import Settings
from src.execution.simulator import SimulatedExecutionAdapter
from src.models import ExecutionResult, TradePlan
from src.strategy.indicators import atr as _atr, ema as _ema, macd as _macd, rsi as _rsi
from src.strategy.support_resistance import (
    _merge_levels,
    build_multi_timeframe_levels,
    nearest_resistance,
    nearest_support,
)


SYMBOL = "BTCUSDC"


def _pivot_low_indices_full(arr: np.ndarray, window: int) -> np.ndarray:
    """Confirmed pivot low indices over the FULL array.
    Match strict semantics of src.strategy.divergence.pivot_lows:
      arr[i] <= every element in arr[i-w : i+w+1] (excluding i).
    Vectorized via centered rolling min on a 2w+1 window.
    """
    n = len(arr)
    if n <= 2 * window:
        return np.array([], dtype=np.int64)
    s = pd.Series(arr)
    roll_min = s.rolling(window=2 * window + 1, center=True, min_periods=2 * window + 1).min().to_numpy()
    mask = (arr == roll_min)
    mask[:window] = False
    mask[n - window :] = False
    return np.flatnonzero(mask)


def _pivot_high_indices_full(arr: np.ndarray, window: int) -> np.ndarray:
    n = len(arr)
    if n <= 2 * window:
        return np.array([], dtype=np.int64)
    s = pd.Series(arr)
    roll_max = s.rolling(window=2 * window + 1, center=True, min_periods=2 * window + 1).max().to_numpy()
    mask = (arr == roll_max)
    mask[:window] = False
    mask[n - window :] = False
    return np.flatnonzero(mask)


@dataclass
class WindowResult:
    months: int
    metrics: Dict[str, float | int]
    trade_count: int
    win_rate_pct: float
    total_return_pct: float


def _compute_levels_cached(
    higher_frames_full: Dict[str, pd.DataFrame],
    last_high_idx: Dict[str, int],
    pivot_window: int,
) -> Tuple[List[float], List[float]]:
    """Build supports/resistances using slices up to last_high_idx[tf] inclusive."""
    slices: Dict[str, pd.DataFrame] = {}
    for tf, idx in last_high_idx.items():
        df = higher_frames_full[tf]
        if idx < 0:
            slices[tf] = df.iloc[0:0]
        else:
            start = max(0, idx + 1 - 600)
            slices[tf] = df.iloc[start : idx + 1]
    return build_multi_timeframe_levels(slices, pivot_window=pivot_window)


def fast_run_window(
    *,
    settings: Settings,
    frames: Dict[str, pd.DataFrame],
    months: int,
) -> WindowResult:
    base_1h_full = frames["1h"].reset_index(drop=True)
    if base_1h_full.empty:
        return WindowResult(months=months, metrics={}, trade_count=0, win_rate_pct=0.0, total_return_pct=0.0)

    sup_res_timeframes = settings.sup_res_timeframes
    pivot_window = settings.pivot_window
    lookback = settings.divergence_lookback

    # Cutoff for window iteration (indicators warmup uses everything before).
    last_open = int(base_1h_full["open_time"].iloc[-1])
    cutoff_start_ts = pd.Timestamp(last_open, unit="ms", tz="UTC").tz_localize(None) - pd.DateOffset(months=months)
    cutoff_start_ms = int(cutoff_start_ts.timestamp() * 1000)
    window_mask = base_1h_full["open_time"] >= cutoff_start_ms
    iter_start_idx = int(window_mask.idxmax()) if window_mask.any() else len(base_1h_full)

    close = base_1h_full["close"].astype(float)
    high = base_1h_full["high"].astype(float)
    low = base_1h_full["low"].astype(float)
    open_time = base_1h_full["open_time"].to_numpy()
    close_time = base_1h_full["close_time"].to_numpy()

    # Precompute indicators on full series.
    rsi_full = _rsi(close, period=settings.rsi_period).to_numpy()
    macd_line_full, _, _ = _macd(close, fast=settings.macd_fast, slow=settings.macd_slow, signal=settings.macd_signal)
    macd_full = macd_line_full.to_numpy()
    atr_full = _atr(high, low, close, period=settings.atr_period).to_numpy()
    ema_full = _ema(close, period=settings.trend_ema_period).to_numpy() if settings.use_trend_filter else None
    close_arr = close.to_numpy()
    high_arr = high.to_numpy()
    low_arr = low.to_numpy()

    # Precompute pivot indices once on full close series.
    pivot_lows_idx = _pivot_low_indices_full(close_arr, pivot_window)
    pivot_highs_idx = _pivot_high_indices_full(close_arr, pivot_window)

    higher_frames_full = {tf: frames[tf].reset_index(drop=True) for tf in sup_res_timeframes}
    higher_open_arrays = {tf: higher_frames_full[tf]["open_time"].to_numpy() for tf in sup_res_timeframes}
    last_high_idx: Dict[str, int] = {tf: -1 for tf in sup_res_timeframes}

    # Precompute per-TF pivot indices on FULL higher-TF high/low arrays.
    per_tf_high_arr = {tf: higher_frames_full[tf]["high"].to_numpy() for tf in sup_res_timeframes}
    per_tf_low_arr = {tf: higher_frames_full[tf]["low"].to_numpy() for tf in sup_res_timeframes}
    per_tf_pivot_lows = {tf: _pivot_low_indices_full(per_tf_low_arr[tf], pivot_window) for tf in sup_res_timeframes}
    per_tf_pivot_highs = {tf: _pivot_high_indices_full(per_tf_high_arr[tf], pivot_window) for tf in sup_res_timeframes}

    simulator = SimulatedExecutionAdapter(settings)

    cached_supports: List[float] = []
    cached_resistances: List[float] = []
    processed: set[Tuple[str, int]] = set()
    # Cache levels by tuple of (last_high_idx[tf] for each tf) — avoids rebuilding for same state.
    levels_cache: Dict[Tuple[int, ...], Tuple[List[float], List[float]]] = {}

    n = len(base_1h_full)

    for i in range(iter_start_idx, n):
        t = int(open_time[i])
        ct = int(close_time[i])
        c = float(close_arr[i])
        h = float(high_arr[i])
        l = float(low_arr[i])

        simulator.on_bar(SYMBOL, t, high=h, low=l, close=c)

        # Need >=100 bars in 600-window for engine signal.
        bars_avail = min(600, i + 1)
        if bars_avail < 100:
            continue

        # Update higher-TF index cache.
        rebuild = False
        for tf in sup_res_timeframes:
            arr = higher_open_arrays[tf]
            idx = int(arr.searchsorted(t, side="right")) - 1
            if idx != last_high_idx[tf]:
                last_high_idx[tf] = idx
                rebuild = True
        if rebuild:
            state_key = tuple(last_high_idx[tf] for tf in sup_res_timeframes)
            cached = levels_cache.get(state_key)
            if cached is None:
                # Collect supports/resistances per TF using precomputed pivots restricted to <= last_high_idx.
                all_sup: List[float] = []
                all_res: List[float] = []
                for tf in sup_res_timeframes:
                    idx_last = last_high_idx[tf]
                    if idx_last < 0:
                        continue
                    start_idx = max(0, idx_last + 1 - 600)
                    # Pivots from extract_levels_from_frame use [w, n-w-1] within the slice.
                    # Equivalent global filter: piv_idx in [start_idx + w, idx_last - w].
                    lo_bound = start_idx + pivot_window
                    hi_bound = idx_last - pivot_window
                    pl = per_tf_pivot_lows[tf]
                    ph = per_tf_pivot_highs[tf]
                    l_l = int(np.searchsorted(pl, lo_bound, side="left"))
                    l_r = int(np.searchsorted(pl, hi_bound, side="right"))
                    h_l = int(np.searchsorted(ph, lo_bound, side="left"))
                    h_r = int(np.searchsorted(ph, hi_bound, side="right"))
                    sel_pl = pl[l_l:l_r]
                    sel_ph = ph[h_l:h_r]
                    if len(sel_pl):
                        all_sup.extend(per_tf_low_arr[tf][sel_pl].tolist())
                    if len(sel_ph):
                        all_res.extend(per_tf_high_arr[tf][sel_ph].tolist())
                merged_sup = _merge_levels(all_sup, tolerance_bps=18)
                merged_res = _merge_levels(all_res, tolerance_bps=18)
                cached_supports, cached_resistances = merged_sup, merged_res
                levels_cache[state_key] = (cached_supports, cached_resistances)
            else:
                cached_supports, cached_resistances = cached

        last_price = c
        current_rsi = float(rsi_full[i])
        atr_val = float(atr_full[i]) if not np.isnan(atr_full[i]) else 0.0

        # Trend filter (matches engine).
        if ema_full is not None:
            ema_val = float(ema_full[i])
            trend_up = last_price > ema_val
            trend_down = last_price < ema_val
        else:
            trend_up = True
            trend_down = True

        # Divergence: engine takes tail(600) then tail(lookback) and runs pivot detection.
        # Pivots in tail of length `lookback` ending at i have valid local indices [w, lookback-w-1],
        # i.e. global indices in [i - lookback + 1 + w, i - w]. Use precomputed pivots restricted to this range.
        tail_len = min(lookback, i + 1)
        if tail_len >= max(10, lookback):
            piv_lo = i - pivot_window
            piv_hi = i - lookback + 1 + pivot_window
            # We want pivots in [piv_hi, piv_lo] (inclusive).
            lo_l = int(np.searchsorted(pivot_lows_idx, piv_hi, side="left"))
            lo_r = int(np.searchsorted(pivot_lows_idx, piv_lo, side="right"))
            sel_l = pivot_lows_idx[lo_l:lo_r]
            hi_l = int(np.searchsorted(pivot_highs_idx, piv_hi, side="left"))
            hi_r = int(np.searchsorted(pivot_highs_idx, piv_lo, side="right"))
            sel_h = pivot_highs_idx[hi_l:hi_r]
            if len(sel_l) >= 2:
                i1, i2 = int(sel_l[-2]), int(sel_l[-1])
                p1, p2 = float(close_arr[i1]), float(close_arr[i2])
                r1, r2 = float(rsi_full[i1]), float(rsi_full[i2])
                m1, m2 = float(macd_full[i1]), float(macd_full[i2])
                rsi_bull_ok = p2 < p1 and r2 > r1
                macd_bull_ok = p2 < p1 and m2 > m1
            else:
                rsi_bull_ok = False
                macd_bull_ok = False
            if len(sel_h) >= 2:
                i1h, i2h = int(sel_h[-2]), int(sel_h[-1])
                ph1, ph2 = float(close_arr[i1h]), float(close_arr[i2h])
                rh1, rh2 = float(rsi_full[i1h]), float(rsi_full[i2h])
                mh1, mh2 = float(macd_full[i1h]), float(macd_full[i2h])
                rsi_bear_ok = ph2 > ph1 and rh2 < rh1
                macd_bear_ok = ph2 > ph1 and mh2 < mh1
            else:
                rsi_bear_ok = False
                macd_bear_ok = False
        else:
            rsi_bull_ok = rsi_bear_ok = macd_bull_ok = macd_bear_ok = False

        support = nearest_support(cached_supports, last_price)
        resistance = nearest_resistance(cached_resistances, last_price)

        long_div_ok = rsi_bull_ok and (not settings.require_macd_divergence or macd_bull_ok)
        short_div_ok = rsi_bear_ok and (not settings.require_macd_divergence or macd_bear_ok)
        long_rsi_ok = current_rsi < settings.rsi_long_max
        short_rsi_ok = current_rsi > settings.rsi_short_min
        long_sr_ok = support is not None and resistance is not None and last_price > support and resistance > last_price
        short_sr_ok = support is not None and resistance is not None and last_price < resistance and support < last_price
        long_ready = long_div_ok and long_rsi_ok and long_sr_ok and trend_up
        short_ready = short_div_ok and short_rsi_ok and short_sr_ok and trend_down

        plan: Optional[TradePlan] = None
        if long_ready:
            sl, tp = _resolve_long(settings, last_price, support, resistance, atr_val)
            if sl is not None and tp is not None and _valid_long(settings, last_price, sl, tp):
                plan = TradePlan(
                    symbol=SYMBOL, side="BUY", position_side="LONG",
                    entry_price=last_price, stop_loss=sl, take_profit=tp,
                    signal_time=ct, metadata={"direction": "long"},
                )
        elif short_ready:
            sl, tp = _resolve_short(settings, last_price, support, resistance, atr_val)
            if sl is not None and tp is not None and _valid_short(settings, last_price, sl, tp):
                plan = TradePlan(
                    symbol=SYMBOL, side="SELL", position_side="SHORT",
                    entry_price=last_price, stop_loss=sl, take_profit=tp,
                    signal_time=ct, metadata={"direction": "short"},
                )

        if plan is None:
            continue
        key = (SYMBOL, ct)
        if key in processed:
            continue
        processed.add(key)
        simulator.execute_trade_plan(plan, context={"timestamp": ct})

    # Force close at end of window.
    if iter_start_idx < n:
        simulator.force_close_all(int(open_time[n - 1]), {SYMBOL: float(close_arr[n - 1])})

    metrics = simulator.metrics()
    return WindowResult(
        months=months,
        metrics=metrics,
        trade_count=int(metrics.get("trade_count", 0)),
        win_rate_pct=float(metrics.get("win_rate_pct", 0.0)),
        total_return_pct=float(metrics.get("total_return_pct", 0.0)),
    )


def _resolve_long(s: Settings, entry, support, resistance, atr_val):
    if s.use_atr_stops and atr_val > 0:
        return entry - s.atr_sl_mult * atr_val, entry + s.atr_tp_mult * atr_val
    if support is None or resistance is None:
        return None, None
    return support * (1 - s.stop_loss_buffer_bps / 10000), resistance * (1 - s.take_profit_buffer_bps / 10000)


def _resolve_short(s: Settings, entry, support, resistance, atr_val):
    if s.use_atr_stops and atr_val > 0:
        return entry + s.atr_sl_mult * atr_val, entry - s.atr_tp_mult * atr_val
    if support is None or resistance is None:
        return None, None
    return resistance * (1 + s.stop_loss_buffer_bps / 10000), support * (1 + s.take_profit_buffer_bps / 10000)


def _valid_long(s: Settings, entry, sl, tp):
    if not (sl < entry < tp):
        return False
    risk = entry - sl
    reward = tp - entry
    if risk <= 0 or reward <= 0:
        return False
    if s.min_rr_ratio > 0 and (reward / risk) < s.min_rr_ratio:
        return False
    if s.max_sl_distance_pct > 0 and (risk / entry) > s.max_sl_distance_pct:
        return False
    return True


def _valid_short(s: Settings, entry, sl, tp):
    if not (tp < entry < sl):
        return False
    risk = sl - entry
    reward = entry - tp
    if risk <= 0 or reward <= 0:
        return False
    if s.min_rr_ratio > 0 and (reward / risk) < s.min_rr_ratio:
        return False
    if s.max_sl_distance_pct > 0 and (risk / entry) > s.max_sl_distance_pct:
        return False
    return True


def run_full(*, settings: Settings, frames, months_list):
    return [fast_run_window(settings=settings, frames=frames, months=m) for m in months_list]
