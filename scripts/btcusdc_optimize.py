"""
BTCUSDC iterative backtest harness.

- Downloads 15 months of 1h kline data ONCE and caches to parquet.
- Resamples all higher timeframes locally from the 1h dataset (3h, 6h, 12h, 1d, 1w).
- Runs production-path backtest (via run_trade_cycle + SimulatedExecutionAdapter)
  for windows [1, 3, 6, 12, 15] months counted from the end of the cached data.

This mimics 100% the production logic by:
1. Using the same SignalEngine.generate_signal()
2. Using the same run_trade_cycle() orchestration
3. Using SimulatedExecutionAdapter with the same rejection semantics
4. Same SL/TP/quantity/maker fee math as the live adapter (via shared Settings)

Usage:
  python scripts/btcusdc_optimize.py            # baseline run
  python scripts/btcusdc_optimize.py --refresh  # re-download cache
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.config import Settings, load_settings
from src.data.binance_api import BinanceFuturesClient
from src.data.binance_feed import klines_to_frame
from src.execution.simulator import SimulatedExecutionAdapter
from src.runtime.backtest_runner import _strategy_params_from_settings, write_trade_history
from src.runtime.trade_cycle import run_trade_cycle
from src.strategy.signal_engine import SignalEngine
from src.utils.timeframe import needs_resample, normalize_timeframe, resample_ohlcv


CACHE_DIR = Path(__file__).resolve().parent.parent / "data_cache"
SYMBOL = "BTCUSDC"
CACHE_MONTHS = 15  # max window we ever request

# Resample order required: 3h needs 1h; 6h/12h/1d/1w can all derive from 1h.
ALL_TIMEFRAMES = ["1h", "3h", "6h", "12h", "1d", "1w"]


def _months_ago_ms(months: int) -> int:
    now = pd.Timestamp.utcnow().tz_localize(None)
    start = now - pd.DateOffset(months=months)
    return int(start.timestamp() * 1000)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _download_1h(client: BinanceFuturesClient, symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    all_rows: List[List[object]] = []
    cursor = start_ms
    while True:
        chunk = client.get_klines(symbol, "1h", start_time=cursor, end_time=end_ms, limit=1000)
        if not chunk:
            break
        all_rows.extend(chunk)
        last_open = int(chunk[-1][0])
        if last_open >= end_ms or len(chunk) < 1000:
            break
        cursor = last_open + 1
    frame = klines_to_frame(all_rows)
    if not frame.empty:
        frame = frame.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    return frame


def _ensure_1d_resample(frame: pd.DataFrame) -> pd.DataFrame:
    """Resample 1h to 1d locally (binance supports 1d but we still derive locally for consistency)."""
    if frame.empty:
        return frame
    data = frame.copy().reset_index(drop=True)
    data = data.assign(open_dt=pd.to_datetime(data["open_time"].to_numpy(), unit="ms", utc=True))
    data = data.set_index("open_dt").sort_index()
    resampled = data.resample("1D", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "close_time": "last"}
    )
    resampled = resampled.dropna(subset=["open", "high", "low", "close"])
    resampled["open_time"] = (resampled.index.view("int64") // 10**6).astype(int)
    return resampled[["open_time", "open", "high", "low", "close", "volume", "close_time"]].reset_index(drop=True)


def _ensure_1w_resample(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    data = frame.copy().reset_index(drop=True)
    data = data.assign(open_dt=pd.to_datetime(data["open_time"].to_numpy(), unit="ms", utc=True))
    data = data.set_index("open_dt").sort_index()
    resampled = data.resample("1W", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "close_time": "last"}
    )
    resampled = resampled.dropna(subset=["open", "high", "low", "close"])
    resampled["open_time"] = (resampled.index.view("int64") // 10**6).astype(int)
    return resampled[["open_time", "open", "high", "low", "close", "volume", "close_time"]].reset_index(drop=True)


def load_or_refresh_cache(refresh: bool = False) -> Dict[str, pd.DataFrame]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    base_file = CACHE_DIR / f"{SYMBOL}_1h.csv"

    base_1h: pd.DataFrame
    if base_file.exists() and not refresh:
        base_1h = pd.read_csv(base_file)
        print(f"[cache] loaded {SYMBOL} 1h from {base_file} rows={len(base_1h)}")
    else:
        settings = load_settings(SYMBOL)
        client = BinanceFuturesClient(
            api_key=settings.binance_futures_api_key,
            api_secret=settings.binance_futures_api_secret,
            testnet=False,  # mainnet for historical data
        )
        start_ms = _months_ago_ms(CACHE_MONTHS)
        end_ms = _now_ms()
        print(f"[cache] downloading {SYMBOL} 1h start={start_ms} end={end_ms}")
        base_1h = _download_1h(client, SYMBOL, start_ms, end_ms)
        base_1h.to_csv(base_file, index=False)
        print(f"[cache] wrote {base_file} rows={len(base_1h)}")

    # Derive every higher timeframe locally from 1h.
    frames: Dict[str, pd.DataFrame] = {"1h": base_1h}
    for tf in ["3h", "6h", "12h"]:
        frames[tf] = resample_ohlcv(base_1h, tf)
    frames["1d"] = _ensure_1d_resample(base_1h)
    frames["1w"] = _ensure_1w_resample(base_1h)
    return frames


def _build_engine(settings: Settings) -> SignalEngine:
    return SignalEngine(_strategy_params_from_settings(settings))


@dataclass(frozen=True)
class WindowResult:
    months: int
    metrics: Dict[str, float | int]
    trade_count: int
    win_rate_pct: float
    total_return_pct: float


def run_window(
    *,
    settings: Settings,
    frames: Dict[str, pd.DataFrame],
    months: int,
    sup_res_timeframes: List[str],
) -> WindowResult:
    engine = _build_engine(settings)
    simulator = SimulatedExecutionAdapter(settings)
    processed_signals: Set[Tuple[str, int]] = set()

    # Build slice for the window: last `months` from the cached 1h frame end.
    base_1h_full = frames["1h"]
    if base_1h_full.empty:
        return WindowResult(months=months, metrics={}, trade_count=0, win_rate_pct=0.0, total_return_pct=0.0)

    last_open = int(base_1h_full["open_time"].iloc[-1])
    cutoff_start_ts = pd.Timestamp(last_open, unit="ms", tz="UTC").tz_localize(None) - pd.DateOffset(months=months)
    cutoff_start_ms = int(cutoff_start_ts.timestamp() * 1000)

    # Start with full 1h data up to cutoff for indicators warmup but iterate only inside window.
    base_full = base_1h_full.reset_index(drop=True)
    window_mask = base_full["open_time"] >= cutoff_start_ms
    iter_start_idx = int(window_mask.idxmax()) if window_mask.any() else len(base_full)

    higher_frames_full = {tf: frames[tf].reset_index(drop=True) for tf in sup_res_timeframes}
    # Precompute sorted open_time arrays for each higher TF for fast searchsorted.
    higher_open_arrays = {tf: higher_frames_full[tf]["open_time"].to_numpy() for tf in sup_res_timeframes}

    # Cache higher-frame index per bar (only update when crossing new higher-TF bar).
    last_high_idx = {tf: -1 for tf in sup_res_timeframes}
    higher_slices_cache: Dict[str, pd.DataFrame] = {}

    n = len(base_full)
    for i in range(iter_start_idx, n):
        row = base_full.iloc[i]
        t = int(row["open_time"])
        high_p = float(row["high"])
        low_p = float(row["low"])
        close_p = float(row["close"])

        simulator.on_bar(SYMBOL, t, high=high_p, low=low_p, close=close_p)

        # Signal slice: last 600 bars ending at i.
        start_i = max(0, i - 599)
        signal_slice = base_full.iloc[start_i : i + 1]
        if len(signal_slice) < 100:
            continue

        # Higher TF slices: use searchsorted to find latest higher-TF bar <= t. Reuse cache when index unchanged.
        rebuild = False
        for tf in sup_res_timeframes:
            arr = higher_open_arrays[tf]
            idx = int(arr.searchsorted(t, side="right")) - 1
            if idx != last_high_idx[tf]:
                last_high_idx[tf] = idx
                rebuild = True
        if rebuild:
            higher_slices_cache = {}
            for tf in sup_res_timeframes:
                idx = last_high_idx[tf]
                if idx < 0:
                    higher_slices_cache[tf] = higher_frames_full[tf].iloc[0:0]
                else:
                    start_hi = max(0, idx + 1 - 600)
                    higher_slices_cache[tf] = higher_frames_full[tf].iloc[start_hi : idx + 1]

        outcome = run_trade_cycle(
            symbol=SYMBOL,
            signal_frame=signal_slice,
            higher_frames=higher_slices_cache,
            execution_timestamp=int(row["close_time"]),
            engine=engine,
            executor=simulator,
            processed_signals=processed_signals,
        )
        _ = outcome

    if iter_start_idx < n:
        final_row = base_full.iloc[n - 1]
        final_time = int(final_row["open_time"])
        simulator.force_close_all(final_time, {SYMBOL: float(final_row["close"])})

    metrics = simulator.metrics()
    history = simulator.trade_history()
    path = write_trade_history(
        history,
        history_dir=settings.backtest_history_dir,
        loop_id=settings.loop_id,
        months=months,
    )
    print(f"[history] wrote {len(history)} trades for months={months} -> {path}")
    return WindowResult(
        months=months,
        metrics=metrics,
        trade_count=int(metrics.get("trade_count", 0)),
        win_rate_pct=float(metrics.get("win_rate_pct", 0.0)),
        total_return_pct=float(metrics.get("total_return_pct", 0.0)),
    )


def run_full(*, settings: Settings, frames: Dict[str, pd.DataFrame], months_list: List[int]) -> List[WindowResult]:
    sup_res_tfs = settings.sup_res_timeframes
    out: List[WindowResult] = []
    for m in months_list:
        res = run_window(settings=settings, frames=frames, months=m, sup_res_timeframes=sup_res_tfs)
        out.append(res)
    return out


def consistency_score(results: List[WindowResult]) -> float:
    """Higher = more consistent. Reward monotonic improvement and PnL."""
    # Sort by months ascending
    results = sorted(results, key=lambda r: r.months)
    if not results:
        return -1e9

    score = 0.0
    # Per-window pnl + winrate
    for r in results:
        score += r.total_return_pct * 0.5
        score += r.win_rate_pct * 1.0
    # Monotonic bonus: 15>=12>=6>=3>=1 for total_return
    monotonic_bonus = 0.0
    prev = None
    for r in results:
        if prev is not None and r.total_return_pct >= prev:
            monotonic_bonus += 50
        prev = r.total_return_pct
    score += monotonic_bonus
    return score


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Refresh cached data from Binance")
    parser.add_argument("--windows", type=str, default="1,3,6,12,15")
    args = parser.parse_args()

    months_list = [int(x) for x in args.windows.split(",") if x.strip()]
    frames = load_or_refresh_cache(refresh=args.refresh)

    settings = load_settings(SYMBOL)
    # Force single symbol BTCUSDC for this study
    settings = replace(settings, symbols=["BTCUSDC"])
    print(f"[settings] strategy: rsi={settings.rsi_period} macd={settings.macd_fast}/{settings.macd_slow}/{settings.macd_signal} "
          f"div_lookback={settings.divergence_lookback} pivot={settings.pivot_window} "
          f"sl_bps={settings.stop_loss_buffer_bps} tp_bps={settings.take_profit_buffer_bps} "
          f"leverage={settings.leverage} eq_ratio={settings.position_equity_ratio}")

    results = run_full(settings=settings, frames=frames, months_list=months_list)

    summary = []
    for r in results:
        summary.append({
            "months": r.months,
            "total_return_pct": r.total_return_pct,
            "win_rate_pct": r.win_rate_pct,
            "trade_count": r.trade_count,
            "max_drawdown_pct": r.metrics.get("max_drawdown_pct"),
            "sharpe": r.metrics.get("sharpe"),
        })
    print(json.dumps(summary, indent=2))
    print(f"[consistency_score] {consistency_score(results):.2f}")


if __name__ == "__main__":
    main()
