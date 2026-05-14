"""Grid sweep over strategy params for BTCUSDC.

Uses the same production-path SignalEngine + run_trade_cycle + SimulatedExecutionAdapter
as btcusdc_optimize.py, but iterates parameter combinations to find configs that
satisfy the monotonic 15m > 12m > 6m > 3m > 1m target with positive PnL and higher winrate.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from dataclasses import replace
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.config import Settings, load_settings

import btcusdc_optimize as bo  # type: ignore[no-redef]
import btcusdc_fast as bf  # type: ignore


def score(window_results) -> Tuple[float, bool, Dict[str, Any]]:
    """Score a parameter combination against user targets:
       1. min WR across all windows >= 80% (HARD)
       2. strict monotonic PnL: 15m > 12m > 6m > 3m > 1m (HARD)
       3. all positive returns (HARD)
       4. min trades/month >= 2.0 across all windows (HARD)
       Subject to those, maximize 15m PnL primarily, then avg PnL.
    """
    results = sorted(window_results, key=lambda r: r.months)
    if not results:
        return -1e9, False, {}

    rets = [r.total_return_pct for r in results]
    wrs = [r.win_rate_pct for r in results]
    trades = [r.trade_count for r in results]
    months_list = [r.months for r in results]
    trades_per_month = [t / m if m > 0 else 0.0 for t, m in zip(trades, months_list)]

    strict_monotonic = all(rets[i] < rets[i + 1] for i in range(len(rets) - 1))
    all_positive = all(r > 0 for r in rets)
    min_wr = min(wrs) if wrs else 0.0
    wr_floor_ok = min_wr >= 80.0
    min_tpm = min(trades_per_month) if trades_per_month else 0.0
    tpm_floor_ok = min_tpm >= 2.0
    avg_ret = sum(rets) / len(rets)
    avg_wr = sum(wrs) / len(wrs)
    last_ret = rets[-1]  # 15m

    passes = strict_monotonic and all_positive and wr_floor_ok and tpm_floor_ok

    if passes:
        # Once constraints pass, rank primarily by 15m PnL, then by avg PnL and avg WR.
        s = 10000.0 + last_ret * 2.0 + avg_ret * 0.5 + avg_wr * 0.1
    else:
        # Constraint-violation regime: keep the legacy score so we can see near-misses,
        # but always rank strictly below any passing config (capped < 10000).
        legacy = avg_ret + avg_wr * 0.5
        if strict_monotonic:
            legacy += 400
        if all_positive:
            legacy += 200
        if wr_floor_ok:
            legacy += 100
        if tpm_floor_ok:
            legacy += 150
        s = min(legacy, 9999.0)

    return s, passes, {
        "strict_monotonic": strict_monotonic,
        "all_positive": all_positive,
        "min_wr_pct": round(min_wr, 2),
        "wr_floor_ok": wr_floor_ok,
        "min_trades_per_month": round(min_tpm, 2),
        "tpm_floor_ok": tpm_floor_ok,
        "avg_return_pct": round(avg_ret, 2),
        "avg_win_rate_pct": round(avg_wr, 2),
        "last_return_pct": round(last_ret, 2),
        "returns_by_month": dict(zip([r.months for r in results], rets)),
        "win_rates_by_month": dict(zip([r.months for r in results], wrs)),
        "trades_by_month": dict(zip([r.months for r in results], [r.trade_count for r in results])),
        "trades_per_month": dict(zip([r.months for r in results], [round(x, 2) for x in trades_per_month])),
    }


def apply_overrides(settings: Settings, overrides: Dict[str, Any]) -> Settings:
    return replace(settings, **overrides)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--grid", type=str, default="basic", choices=["basic", "wide", "fine", "monotonic", "refine", "bigreward", "strictrsi", "neighbor2", "tprange", "pivots", "macd_gate", "aggressive", "trendloose", "atrshift", "atr_push", "eqratio", "fastatr", "indicators", "moretrades", "moretrades_fine", "moretrades_scan", "bigrr", "loop10_refine", "manytrades"])
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args()

    frames = bo.load_or_refresh_cache(refresh=False)
    base_settings = load_settings("BTCUSDC")
    base_settings = replace(base_settings, symbols=["BTCUSDC"])

    months_list = [1, 3, 6, 12, 15]

    if args.grid == "basic":
        grid: Dict[str, List[Any]] = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.0, 1.5, 2.0],
            "atr_tp_mult": [2.0, 3.0, 4.0],
            "use_trend_filter": [True, False],
            "trend_ema_period": [100, 200],
            "leverage": [3, 5, 10],
            "position_equity_ratio": [0.3, 0.6, 0.95],
            "pivot_window": [3, 5],
            "divergence_lookback": [80],
        }
    elif args.grid == "monotonic":
        # Narrow around basic-sweep winners; vary RSI extremity + pivot + RR.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.0, 1.5, 2.0],
            "atr_tp_mult": [2.0, 2.5, 3.0],
            "use_trend_filter": [True],
            "trend_ema_period": [150, 200, 250],
            "leverage": [10],
            "position_equity_ratio": [0.95],
            "pivot_window": [5, 7],
            "divergence_lookback": [80, 120],
            "rsi_long_max": [35.0, 40.0, 45.0, 50.0],
            "rsi_short_min": [50.0, 55.0, 60.0, 65.0],
        }
    elif args.grid == "wide":
        grid = {
            "use_atr_stops": [True, False],
            "atr_sl_mult": [1.0, 1.5, 2.0, 2.5],
            "atr_tp_mult": [2.0, 3.0, 4.0, 5.0],
            "use_trend_filter": [True, False],
            "trend_ema_period": [50, 100, 200],
            "leverage": [3, 5, 10],
            "position_equity_ratio": [0.3, 0.5, 0.95],
            "min_rr_ratio": [0.0, 1.5, 2.0],
            "pivot_window": [3, 5, 7],
            "divergence_lookback": [60, 80, 120],
            "require_macd_divergence": [True, False],
        }
    elif args.grid == "refine":
        # Tight refinement around the current winner (config #5): atr 1.0/2.0, pivot 5, lookback 80, rsi_short>=60, ema 200.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [0.8, 1.0, 1.2],
            "atr_tp_mult": [1.6, 2.0, 2.4, 2.8],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [0.95],
            "pivot_window": [4, 5, 6, 7],
            "divergence_lookback": [60, 80, 100, 120],
            "rsi_long_max": [50.0],
            "rsi_short_min": [55.0, 60.0, 65.0],
            "atr_period": [14],
        }
    elif args.grid == "bigreward":
        # Push the reward leg higher (RR 1:3, 1:4, 1:5) while keeping SL tight.
        # Hold pivot=5, rsi_short_min>=60, ema=200, lookback=80 as the proven baseline.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [0.8, 1.0, 1.2, 1.5],
            "atr_tp_mult": [2.5, 3.0, 3.5, 4.0, 4.5, 5.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [0.95],
            "pivot_window": [5],
            "divergence_lookback": [80],
            "rsi_long_max": [50.0],
            "rsi_short_min": [60.0, 65.0, 70.0],
            "atr_period": [14],
        }
    elif args.grid == "strictrsi":
        # Combine higher reward with stricter RSI gates.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.0, 1.2],
            "atr_tp_mult": [2.0, 2.5, 3.0, 4.0],
            "use_trend_filter": [True],
            "trend_ema_period": [150, 200, 250],
            "leverage": [10],
            "position_equity_ratio": [0.95],
            "pivot_window": [5],
            "divergence_lookback": [80],
            "rsi_long_max": [35.0, 40.0, 45.0, 50.0],
            "rsi_short_min": [55.0, 60.0, 65.0, 70.0],
            "atr_period": [14],
        }
    elif args.grid == "neighbor2":
        # Tight neighborhood around Loop_20260513_2 winner: sl=1.5 tp=2.5 rsi_short>=70.
        # Reduced from full Cartesian to keep wall-time under ~5 minutes.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.4, 1.5, 1.6, 1.75, 2.0],
            "atr_tp_mult": [2.5, 2.75, 3.0, 3.5, 4.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [0.95],
            "pivot_window": [5],
            "divergence_lookback": [80],
            "rsi_long_max": [50.0],
            "rsi_short_min": [65.0, 70.0, 75.0],
            "atr_period": [14],
        }
    elif args.grid == "tprange":
        # Stress-test tp_mult across [2.75, 4.0] around the Loop_3 winner.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.3, 1.5, 1.7, 2.0],
            "atr_tp_mult": [2.75, 3.0, 3.25, 3.5, 3.75, 4.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [0.95],
            "pivot_window": [5],
            "divergence_lookback": [80],
            "rsi_long_max": [50.0],
            "rsi_short_min": [70.0, 75.0],
            "atr_period": [14],
        }
    elif args.grid == "pivots":
        # Vary pivot_window and divergence_lookback around Loop_3 winner.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.5],
            "atr_tp_mult": [2.75],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [0.95],
            "pivot_window": [3, 4, 5, 6, 7, 8],
            "divergence_lookback": [40, 60, 80, 100, 120, 160],
            "rsi_long_max": [50.0],
            "rsi_short_min": [70.0],
            "atr_period": [14],
        }
    elif args.grid == "macd_gate":
        # Try toggling MACD-divergence requirement (RSI divergence is still mandatory).
        # Also try min_rr_ratio and max_sl_distance_pct (currently inactive 0.0).
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.5],
            "atr_tp_mult": [2.75],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [0.95],
            "pivot_window": [5],
            "divergence_lookback": [80],
            "rsi_long_max": [50.0],
            "rsi_short_min": [70.0],
            "atr_period": [14],
            "require_macd_divergence": [True, False],
            "min_rr_ratio": [0.0, 1.5, 1.8],
            "max_sl_distance_pct": [0.0, 1.0, 1.5, 2.0],
        }
    elif args.grid == "aggressive":
        # User relaxed WR floor to >80% — explore higher tp_mult and slight RSI/SL
        # variations to chase more PnL while still hitting strict monotonicity + WR>=80%.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.3, 1.5, 1.8, 2.0, 2.5],
            "atr_tp_mult": [2.75, 3.0, 3.25, 3.5, 3.75, 4.0, 4.5, 5.0, 6.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [0.95],
            "pivot_window": [5],
            "divergence_lookback": [80],
            "rsi_long_max": [50.0],
            "rsi_short_min": [55.0, 60.0, 65.0, 70.0],
            "atr_period": [14],
            "require_macd_divergence": [True],
        }
    elif args.grid == "trendloose":
        # Looser trend filter to admit more trades.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.5],
            "atr_tp_mult": [2.75, 3.0, 3.25, 3.5],
            "use_trend_filter": [True, False],
            "trend_ema_period": [50, 100, 150, 200],
            "leverage": [10],
            "position_equity_ratio": [0.95],
            "pivot_window": [4, 5],
            "divergence_lookback": [60, 80],
            "rsi_long_max": [50.0],
            "rsi_short_min": [60.0, 65.0, 70.0],
            "atr_period": [14],
            "require_macd_divergence": [True],
        }
    elif args.grid == "atrshift":
        # Probe under-explored axes: atr_period, stricter rsi_long_max, divergence_lookback edges.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.5],
            "atr_tp_mult": [2.75, 3.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [0.95],
            "pivot_window": [5],
            "divergence_lookback": [40, 60, 80, 100, 120, 160, 200],
            "rsi_long_max": [30.0, 35.0, 40.0, 45.0, 50.0],
            "rsi_short_min": [65.0, 70.0, 75.0],
            "atr_period": [7, 10, 14, 20, 28],
            "require_macd_divergence": [True],
        }
    elif args.grid == "atr_push":
        # Loop_4 discovery: atr_period=7 + tp=3.0 monotonic. Now push tp higher with
        # even faster atr_period to see if PnL keeps growing within constraints.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.2, 1.5, 1.8, 2.0],
            "atr_tp_mult": [3.0, 3.25, 3.5, 3.75, 4.0, 4.5, 5.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [0.95],
            "pivot_window": [5],
            "divergence_lookback": [60, 80],
            "rsi_long_max": [50.0],
            "rsi_short_min": [70.0],
            "atr_period": [5, 6, 7, 8, 10],
            "require_macd_divergence": [True],
        }
    elif args.grid == "eqratio":
        # Vary position_equity_ratio (capital deployment) with the Loop_4 winner anchor.
        # Higher ratio = more capital per trade = more PnL when WR is 100%.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.5],
            "atr_tp_mult": [3.0, 3.25, 3.5],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [0.7, 0.85, 0.9, 0.95, 1.0],
            "pivot_window": [5],
            "divergence_lookback": [60, 80],
            "rsi_long_max": [50.0],
            "rsi_short_min": [70.0],
            "atr_period": [5, 6, 7],
            "require_macd_divergence": [True],
        }
    elif args.grid == "fastatr":
        # Very fast ATR + fine sl/tp variations at eqratio=1.0 anchor (Loop_5 winner).
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8],
            "atr_tp_mult": [2.75, 3.0, 3.25, 3.5],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [1.0],
            "pivot_window": [5],
            "divergence_lookback": [60, 80],
            "rsi_long_max": [50.0],
            "rsi_short_min": [70.0],
            "atr_period": [3, 4, 5, 6, 7],
            "require_macd_divergence": [True],
        }
    elif args.grid == "indicators":
        # Vary RSI and MACD periods — never explored before. Loop_5 anchor everywhere else.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.5],
            "atr_tp_mult": [3.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [1.0],
            "pivot_window": [5],
            "divergence_lookback": [80],
            "rsi_long_max": [50.0],
            "rsi_short_min": [70.0],
            "atr_period": [6],
            "require_macd_divergence": [True],
            "rsi_period": [7, 10, 14, 21, 28],
            "macd_fast": [8, 12, 16],
            "macd_slow": [21, 26, 32],
            "macd_signal": [7, 9, 12],
        }
    elif args.grid == "moretrades_scan":
        # First quick scan: how does dropping require_macd_divergence + loosening rsi_short_min affect trade count?
        # Hold most other levers at Loop_7 anchor (atr_period=6, atr_sl=1.5, atr_tp=3.0, pw=5, lb=80, trend=on/200).
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.5],
            "atr_tp_mult": [2.0, 3.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [1.0],
            "pivot_window": [3, 4, 5],
            "divergence_lookback": [40, 60, 80],
            "rsi_long_max": [50.0],
            "rsi_short_min": [50.0, 55.0, 60.0, 70.0],
            "atr_period": [6],
            "require_macd_divergence": [True, False],
        }
    elif args.grid == "moretrades":
        # Drastically loosen entry filters to hit trades/month >= 2.0 floor.
        # Mandatory rule preserved: RSI divergence + extremity (rsi_long_max<=50, rsi_short_min>=50).
        # Key levers: smaller pivot_window, drop require_macd_divergence, lower rsi_short_min.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.2, 1.5, 1.8],
            "atr_tp_mult": [2.0, 2.5, 3.0],
            "use_trend_filter": [True, False],
            "trend_ema_period": [100, 200],
            "leverage": [10],
            "position_equity_ratio": [1.0],
            "pivot_window": [3, 4, 5],
            "divergence_lookback": [40, 60, 80],
            "rsi_long_max": [50.0],
            "rsi_short_min": [50.0, 55.0, 60.0],
            "atr_period": [6, 14],
            "require_macd_divergence": [False],
        }
    elif args.grid == "manytrades":
        # AGGRESSIVE loosening to hit trades/month >= 2.0 across all windows.
        # Key levers: trend-filter off (huge unlock), pivot_window=3 (finer pivots),
        # rsi_short_min=50 (baseline mandatory floor), shorter divergence_lookback.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.0, 1.5, 1.8],
            "atr_tp_mult": [2.0, 3.0, 4.0],
            "use_trend_filter": [False, True],
            "trend_ema_period": [100, 200],
            "leverage": [10],
            "position_equity_ratio": [1.0],
            "pivot_window": [3, 4],
            "divergence_lookback": [40, 80],
            "rsi_long_max": [50.0],
            "rsi_short_min": [50.0, 55.0],
            "atr_period": [6, 14],
            "require_macd_divergence": [False],
        }
    elif args.grid == "loop10_refine":
        # Refine around Loop_10 winner: atr_period=14, sl=1.8, tp=4.0, rsi_short_min=60.
        # Goal: find a config with 15m PnL >= 841% AND min_wr STRICTLY > 80%.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.6, 1.8, 2.0, 2.2],
            "atr_tp_mult": [3.5, 4.0, 4.5, 5.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [1.0],
            "pivot_window": [3, 4, 5],
            "divergence_lookback": [80],
            "rsi_long_max": [50.0],
            "rsi_short_min": [55.0, 60.0, 65.0, 70.0],
            "atr_period": [12, 14, 16, 18, 21],
            "require_macd_divergence": [False],
        }
    elif args.grid == "bigrr":
        # Push tp_mult upward while keeping RR>=2 (user said "extend the reward to 3, 4, 5").
        # Anchor at Loop_9 (pw=4, rsi_short_min=60, require_macd_divergence=False, lookback=80).
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.0, 1.2, 1.5, 1.8],
            "atr_tp_mult": [3.0, 3.5, 4.0, 4.5, 5.0, 6.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [1.0],
            "pivot_window": [4],
            "divergence_lookback": [80],
            "rsi_long_max": [50.0],
            "rsi_short_min": [55.0, 60.0],
            "atr_period": [6, 10, 14],
            "require_macd_divergence": [False],
        }
    elif args.grid == "moretrades_fine":
        # Narrower refinement around moretrades winners with finer RR.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.2, 1.5, 1.8, 2.0],
            "atr_tp_mult": [2.0, 2.5, 3.0, 3.5, 4.0],
            "use_trend_filter": [True],
            "trend_ema_period": [150, 200],
            "leverage": [10],
            "position_equity_ratio": [1.0],
            "pivot_window": [3, 4],
            "divergence_lookback": [40, 60, 80],
            "rsi_long_max": [50.0],
            "rsi_short_min": [50.0, 55.0],
            "atr_period": [6, 10, 14],
            "require_macd_divergence": [False],
        }
    else:  # fine
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.0, 1.25, 1.5, 1.75, 2.0],
            "atr_tp_mult": [2.5, 3.0, 3.5, 4.0],
            "use_trend_filter": [True],
            "trend_ema_period": [100, 150, 200, 250],
            "leverage": [3, 5],
            "position_equity_ratio": [0.5, 0.7, 0.95],
            "pivot_window": [3, 5],
            "divergence_lookback": [80, 100],
        }

    keys = list(grid.keys())
    combos = list(itertools.product(*[grid[k] for k in keys]))
    print(f"[sweep] grid={args.grid} combinations={len(combos)}")

    results_log: List[Tuple[float, Dict[str, Any], Dict[str, Any]]] = []
    start = time.time()

    for i, combo in enumerate(combos):
        overrides = dict(zip(keys, combo))
        settings = apply_overrides(base_settings, overrides)
        window_results = bf.run_full(settings=settings, frames=frames, months_list=months_list)
        s, ok, details = score(window_results)
        results_log.append((s, overrides, details))
        if (i + 1) % 10 == 0 or i + 1 == len(combos):
            elapsed = time.time() - start
            print(f"[sweep] {i+1}/{len(combos)} elapsed={elapsed:.1f}s last_score={s:.2f}", flush=True)

    results_log.sort(key=lambda x: x[0], reverse=True)

    print("\n=== TOP CONFIGS ===")
    top = results_log[: args.top]
    for rank, (s, overrides, details) in enumerate(top, 1):
        print(f"\n#{rank} score={s:.2f}")
        print(f"  overrides={json.dumps(overrides, sort_keys=True)}")
        print(f"  details={json.dumps(details, sort_keys=True)}")


if __name__ == "__main__":
    main()
