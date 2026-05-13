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
    results = sorted(window_results, key=lambda r: r.months)
    if not results:
        return -1e9, False, {}

    rets = [r.total_return_pct for r in results]
    wrs = [r.win_rate_pct for r in results]

    # Monotonic order: 1m <= 3m <= 6m <= 12m <= 15m (in terms of PnL)
    monotonic_pnl = all(rets[i] <= rets[i + 1] for i in range(len(rets) - 1))
    all_positive = all(r > 0 for r in rets)

    # Aggregate metric. Strong weight to monotonic+positive.
    avg_ret = sum(rets) / len(rets)
    avg_wr = sum(wrs) / len(wrs)

    s = avg_ret + avg_wr * 0.5
    if monotonic_pnl:
        s += 500
    if all_positive:
        s += 300
    # Bonus for minimum positive return floor
    min_ret = min(rets) if rets else 0
    s += min_ret * 0.5

    return s, monotonic_pnl and all_positive, {
        "monotonic_pnl": monotonic_pnl,
        "all_positive": all_positive,
        "avg_return_pct": round(avg_ret, 2),
        "avg_win_rate_pct": round(avg_wr, 2),
        "returns_by_month": dict(zip([r.months for r in results], rets)),
        "win_rates_by_month": dict(zip([r.months for r in results], wrs)),
        "trades_by_month": dict(zip([r.months for r in results], [r.trade_count for r in results])),
    }


def apply_overrides(settings: Settings, overrides: Dict[str, Any]) -> Settings:
    return replace(settings, **overrides)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--grid", type=str, default="basic", choices=["basic", "wide", "fine", "monotonic", "refine"])
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
