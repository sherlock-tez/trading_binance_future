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


def score(window_results, wr_floor: float = 70.0) -> Tuple[float, bool, Dict[str, Any]]:
    """Score a parameter combination against user targets:
       1. min WR across all windows > `wr_floor` (HARD).
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
    wr_floor_ok = min_wr > wr_floor
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
    parser.add_argument("--grid", type=str, default="basic", choices=["basic", "wide", "fine", "monotonic", "refine", "bigreward", "strictrsi", "neighbor2", "tprange", "pivots", "macd_gate", "aggressive", "trendloose", "atrshift", "atr_push", "eqratio", "fastatr", "indicators", "moretrades", "moretrades_fine", "moretrades_scan", "bigrr", "loop10_refine", "manytrades", "rsi_period_probe", "macd_probe", "loop11_wide", "eth_tight", "eth_wide", "eth_long_filter", "eth_short_tune", "eth_refine", "eth_macd_loop3", "eth_loop4_refine", "eth_bigtp", "eth_megatp", "eth_leverage", "eth_loosen", "eth_tightpivot", "eth_trend_window", "eth_finetune", "eth_macd_params", "eth_srstops", "eth_unlock", "sol_wr80", "sol_wr80_refine", "sol_wr80_deep", "sol_wr80_macd", "sol_wr80_pnl", "sol_wr80_pnl2", "sol_wr80_pnl3"])
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--wrfloor", type=float, default=70.0,
                        help="Minimum win-rate floor (HARD) applied to every window.")
    args = parser.parse_args()

    frames = bo.load_or_refresh_cache(refresh=False)
    # Symbol parametric via SWEEP_SYMBOL env var (default BTCUSDC).
    sweep_symbol = os.environ.get("SWEEP_SYMBOL", "BTCUSDC")
    base_settings = load_settings(sweep_symbol)
    base_settings = replace(base_settings, symbols=[sweep_symbol])

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
    elif args.grid == "eth_finetune":
        # Loop_10 attempt #14: fine-resolution sweep AROUND the eth_trend_window winner
        # (trend_ema=150, atr_period=10, div_lb=60, +3018% PnL @ WR=100%). That config
        # FAILED strict monotonicity (15m == 12m tie — no trade in the oldest 3 months).
        # Goal: nudge params to place AT LEAST 1 trade in the 12-15m window AND 1 in
        # the 0-1m window, unlocking strict monotonicity + all_positive while preserving
        # the +3018% PnL profile. Probe trend_ema [130-170], atr_period [8-12],
        # divergence_lookback [50-70], pivot_window [4-6].
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [2.0],
            "atr_tp_mult": [7.0, 8.0],
            "use_trend_filter": [True],
            "trend_ema_period": [130, 140, 150, 160, 170],
            "leverage": [20],
            "position_equity_ratio": [1.0],
            "pivot_window": [4, 5, 6],
            "divergence_lookback": [50, 60, 70],
            "rsi_long_max": [30.0, 35.0],
            "rsi_short_min": [58.0],
            "atr_period": [8, 10, 12],
            "rsi_period": [11],
            "require_macd_divergence": [True],
        }
    elif args.grid == "eth_unlock":
        # Loop_10 attempt #17: BREAKTHROUGH probe. The macd_gate=False + SR mode
        # bucket in eth_srstops is the FIRST config across 15 grids producing
        # 1m trades (1m=4, 3m=5, 6m=13, 12m=22, 15m=23) — strictly monotonic
        # in trade count! But WR=65% (below 70%). Hypothesis: tighten RSI
        # extremity gate aggressively to recover WR while keeping the trade
        # distribution. Test rsi_long_max ∈ [20, 22, 25, 28] (vs Loop_9's 30)
        # and rsi_short_min ∈ [60, 62, 65, 68] (vs Loop_9's 58) — both with
        # SR mode (use_atr_stops=False) + macd_gate=False.
        grid = {
            "use_atr_stops": [False],
            "atr_sl_mult": [2.0],   # unused
            "atr_tp_mult": [8.0],   # unused
            "use_trend_filter": [True],
            "trend_ema_period": [150, 200],
            "leverage": [20],
            "position_equity_ratio": [1.0],
            "pivot_window": [5],
            "divergence_lookback": [60, 80],
            "rsi_long_max": [20.0, 22.0, 25.0, 28.0],
            "rsi_short_min": [60.0, 62.0, 65.0, 68.0],
            "atr_period": [14],
            "rsi_period": [11],
            "require_macd_divergence": [False],
            "min_rr_ratio": [0.0],
            "max_sl_distance_pct": [0.0],
        }
    elif args.grid == "eth_srstops":
        # Loop_10 attempt #16: probe use_atr_stops=False — TRULY never tested in any
        # of the 14 prior ETH grids (always pinned at True). With use_atr_stops=False
        # the bot uses support/resistance levels (3h/6h/12h/1d/1w timeframes) instead
        # of ATR multiples for SL/TP placement. Completely different stop mechanics —
        # may place SL/TP at structurally meaningful levels and shift the trade set.
        # Also activates min_rr_ratio and max_sl_distance_pct (currently 0=inactive).
        grid = {
            "use_atr_stops": [False],
            "atr_sl_mult": [2.0],   # unused when use_atr_stops=False
            "atr_tp_mult": [8.0],   # unused when use_atr_stops=False
            "use_trend_filter": [True],
            "trend_ema_period": [150, 200],
            "leverage": [20],
            "position_equity_ratio": [1.0],
            "pivot_window": [4, 5],
            "divergence_lookback": [60, 80],
            "rsi_long_max": [30.0, 35.0],
            "rsi_short_min": [55.0, 58.0],
            "atr_period": [14],
            "rsi_period": [11],
            "require_macd_divergence": [True, False],
            # Existing-but-unused gating keys — activate now.
            "min_rr_ratio": [0.0, 2.0, 3.0],
            "max_sl_distance_pct": [0.0, 0.05, 0.10],
        }
    elif args.grid == "eth_macd_params":
        # Loop_10 attempt #15: the ONE lever never touched across 13 prior ETH grids
        # — MACD parameters (fast/slow/signal). All grids pinned at 12/26/9. Faster
        # MACD may catch shorter-term divergences (more trades), slower may filter
        # to higher-conviction signals. Crossed with the eth_finetune sweet spot
        # (trend_ema=150, atr_period=8-10, div_lb=50-60) and includes a probe with
        # MACD-divergence requirement OFF so we can find configs with 5-10 trades
        # (needed to satisfy strict monotonicity across all 5 windows).
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [2.0],
            "atr_tp_mult": [7.0, 8.0],
            "use_trend_filter": [True],
            "trend_ema_period": [150, 200],
            "leverage": [20],
            "position_equity_ratio": [1.0],
            "pivot_window": [5],
            "divergence_lookback": [60, 80],
            "rsi_long_max": [30.0],
            "rsi_short_min": [58.0],
            "atr_period": [10, 14],
            "rsi_period": [11],
            "require_macd_divergence": [True, False],
            "macd_fast": [8, 10, 12, 15],
            "macd_slow": [21, 26],
            "macd_signal": [7, 9],
        }
    elif args.grid == "eth_trend_window":
        # Loop_10 attempt: probe trend_ema_period — the LAST single-symbol lever
        # never swept (always pinned at 200 in prior 12 grids). 200 on 1h = 8.3 days
        # of trend memory; shorter windows (50/100/150) may admit more pivot-divergence
        # reversals while still blocking catastrophic counter-trend entries. Also
        # secondary probe on atr_period (always 14 prior). Anchor on Loop_9 winners
        # otherwise; include MACD-gate OFF to test if shorter trend filter compensates.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [2.0],
            "atr_tp_mult": [7.0, 8.0],
            "use_trend_filter": [True],
            "trend_ema_period": [50, 100, 150, 200],
            "leverage": [20],
            "position_equity_ratio": [1.0],
            "pivot_window": [4, 5],
            "divergence_lookback": [60, 80],
            "rsi_long_max": [30.0, 35.0],
            "rsi_short_min": [55.0, 58.0],
            "atr_period": [10, 14],
            "rsi_period": [11],
            "require_macd_divergence": [True, False],
        }
    elif args.grid == "eth_tightpivot":
        # Loop_10 attempt: probe pivot_window ∈ [2,3] combined with MACD gate — never
        # tested together. Tighter pivots = more divergence candidates; MACD gate filters
        # them down. May unlock different signal set than Loop_9's pivot=5.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.8, 2.0, 2.2],
            "atr_tp_mult": [6.0, 8.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [20],
            "position_equity_ratio": [1.0],
            "pivot_window": [2, 3],
            "divergence_lookback": [40, 60, 80],
            "rsi_long_max": [30.0, 35.0, 40.0],
            "rsi_short_min": [55.0, 58.0, 60.0],
            "atr_period": [14],
            "rsi_period": [9, 11],
            "require_macd_divergence": [True],
        }
    elif args.grid == "eth_loosen":
        # Loop_10 attempt: try to unlock more trades while keeping WR>70. Vary
        # use_trend_filter (off may admit 2-3x more signals), and slightly loosen
        # RSI gates. Anchor on Loop_9 winners otherwise.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [2.0],
            "atr_tp_mult": [7.0, 8.0, 8.5],
            "use_trend_filter": [True, False],
            "trend_ema_period": [200],
            "leverage": [20],
            "position_equity_ratio": [1.0],
            "pivot_window": [4, 5, 6],
            "divergence_lookback": [60, 80, 100],
            "rsi_long_max": [35.0, 40.0, 45.0],
            "rsi_short_min": [55.0, 58.0, 60.0],
            "atr_period": [14],
            "rsi_period": [11],
            "require_macd_divergence": [True],
        }
    elif args.grid == "eth_leverage":
        # Loop_8 probe: vary leverage (untouched) to see if higher leverage scales PnL
        # acceptably given Loop_7's 14% max DD. Risk: leverage scales DD proportionally.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [2.0],
            "atr_tp_mult": [8.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10, 12, 15, 18, 20],
            "position_equity_ratio": [1.0],
            "pivot_window": [5],
            "divergence_lookback": [80],
            "rsi_long_max": [30.0],
            "rsi_short_min": [58.0],
            "atr_period": [14],
            "rsi_period": [11],
            "require_macd_divergence": [True],
        }
    elif args.grid == "eth_megatp":
        # Loop_7 attempt: TP beyond 7.0. Check if 8, 9, 10 still improve PnL.
        # Diminishing returns expected — at some point TP cap exceeds typical ETH move size.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.8, 2.0, 2.2],
            "atr_tp_mult": [7.0, 8.0, 9.0, 10.0, 12.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [1.0],
            "pivot_window": [5],
            "divergence_lookback": [80],
            "rsi_long_max": [30.0],
            "rsi_short_min": [58.0],
            "atr_period": [14],
            "rsi_period": [11],
            "require_macd_divergence": [True],
        }
    elif args.grid == "eth_bigtp":
        # Loop_6 attempt: push TP beyond 4.0 (5, 6, 7) anchored on Loop_5 winners.
        # User explicitly invited extending reward as long as PnL improves.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.5, 1.8, 2.0, 2.2],
            "atr_tp_mult": [4.0, 5.0, 6.0, 7.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [1.0],
            "pivot_window": [5],
            "divergence_lookback": [60, 80, 100],
            "rsi_long_max": [30.0, 35.0],
            "rsi_short_min": [58.0, 60.0],
            "atr_period": [14],
            "rsi_period": [9, 11],
            "require_macd_divergence": [True],
        }
    elif args.grid == "eth_loop4_refine":
        # Loop_5 attempt: refine around Loop_4 (pivot=5, MACD gate on, rsi_long=35, rsi_short=60).
        # Goal: keep WR>70 while maximizing 15m PnL — pivot=6 alt showed +191% but WR=71%.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [2.0, 2.2, 2.5, 2.8, 3.0],
            "atr_tp_mult": [3.0, 3.5, 4.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [1.0],
            "pivot_window": [5, 6],
            "divergence_lookback": [60, 80, 100],
            "rsi_long_max": [30.0, 35.0, 40.0],
            "rsi_short_min": [58.0, 60.0, 62.0],
            "atr_period": [14],
            "rsi_period": [7, 9, 11],
            "require_macd_divergence": [True],
        }
    elif args.grid == "eth_macd_loop3":
        # Loop_4 attempt: Loop_3 anchor + require_macd_divergence ON. Tests whether
        # the additional MACD-divergence confirmation lifts WR & PnL or kills trades.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [2.0, 2.5, 3.0],
            "atr_tp_mult": [2.5, 3.0, 4.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [1.0],
            "pivot_window": [5, 6, 7],
            "divergence_lookback": [60, 80, 120],
            "rsi_long_max": [35.0, 40.0],
            "rsi_short_min": [60.0, 62.0, 65.0],
            "atr_period": [14],
            "rsi_period": [9, 12],
            "require_macd_divergence": [True],
        }
    elif args.grid == "eth_refine":
        # Loop_3 attempt: Loop_2 has 2 consecutive recent LONG losses (4/18, 4/19).
        # Try wider pivot_window (5,6,7) + rsi_period variants + atr_period to dampen
        # consecutive signal noise. Anchor on Loop_2 winners.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [2.5],
            "atr_tp_mult": [2.5, 3.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [1.0],
            "pivot_window": [4, 5, 6, 7],
            "divergence_lookback": [40, 60, 80],
            "rsi_long_max": [35.0, 40.0, 45.0],
            "rsi_short_min": [62.0, 65.0, 68.0],
            "atr_period": [10, 14, 21],
            "rsi_period": [9, 12, 18, 21],
            "require_macd_divergence": [False],
        }
    elif args.grid == "eth_short_tune":
        # Loop_3 attempt: keep LONG tight (rsi_long_max=40 from Loop_2 winner) but
        # explore SHORT filter and pivot density. Shorts always profitable (WR=50%
        # but only 0-2 fire); adding more would lift overall PnL.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [2.0, 2.5],
            "atr_tp_mult": [2.5, 3.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [1.0],
            "pivot_window": [3, 4],
            "divergence_lookback": [40, 60, 80],
            "rsi_long_max": [40.0],
            "rsi_short_min": [50.0, 52.0, 55.0, 58.0, 60.0, 62.0, 65.0, 70.0],
            "atr_period": [14],
            "require_macd_divergence": [False],
        }
    elif args.grid == "eth_long_filter":
        # ETHUSDC discovery: LONGS bleed in 12m window (11 trades, WR=36%, PnL=-502).
        # SHORTS work great (WR=50%, always profitable but only 2 fires). Tighten LONG side
        # via lower rsi_long_max so longs only fire when RSI is deeply oversold.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [2.0, 2.5, 3.0],
            "atr_tp_mult": [2.5, 3.0, 4.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [1.0],
            "pivot_window": [4, 5],
            "divergence_lookback": [80, 120],
            "rsi_long_max": [25.0, 30.0, 35.0, 40.0, 45.0],
            "rsi_short_min": [55.0, 60.0, 65.0, 70.0, 75.0],
            "atr_period": [14],
            "require_macd_divergence": [False],
        }
    elif args.grid == "eth_wide":
        # ETHUSDC focused probe — anchor on eth_tight winners + require_macd_divergence variations
        # since eth_tight #10 showed positive 15m with MACD gate enabled.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.8, 2.0, 2.5],
            "atr_tp_mult": [3.0, 4.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [1.0],
            "pivot_window": [3, 4, 5, 6],
            "divergence_lookback": [40, 80, 120],
            "rsi_long_max": [50.0],
            "rsi_short_min": [55.0, 60.0, 65.0, 70.0],
            "atr_period": [14],
            "require_macd_divergence": [True, False],
        }
    elif args.grid == "eth_tight":
        # ETHUSDC needs MUCH tighter filters than BTCUSDC (baseline WR was 10-17%).
        # Tighten: higher rsi_short_min, lower rsi_long_max (still <=50 mandatory),
        # wider pivot_window, longer divergence_lookback, require_macd_divergence option.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.5, 2.0, 2.5],
            "atr_tp_mult": [3.0, 4.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [1.0],
            "pivot_window": [4, 5, 6],
            "divergence_lookback": [80, 120, 160],
            "rsi_long_max": [35.0, 40.0, 45.0, 50.0],
            "rsi_short_min": [60.0, 65.0, 70.0, 75.0],
            "atr_period": [14],
            "require_macd_divergence": [False, True],
        }
    elif args.grid == "loop11_wide":
        # Loop_11 final wide probe: combine untouched dimensions to break the structural
        # monotonic+WR>70+tpm>=2 conflict. Vary pivot_window=2 (more pivots), wider trend_ema,
        # bigger TP ratios as user permits.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.2, 1.5, 1.8],
            "atr_tp_mult": [4.0, 5.0, 6.0],
            "use_trend_filter": [True],
            "trend_ema_period": [50, 100, 150, 200],
            "leverage": [10],
            "position_equity_ratio": [1.0],
            "pivot_window": [2, 3, 4],
            "divergence_lookback": [60, 80],
            "rsi_long_max": [50.0],
            "rsi_short_min": [55.0, 60.0],
            "atr_period": [10, 14, 18],
            "require_macd_divergence": [False],
        }
    elif args.grid == "rsi_period_probe":
        # Loop_11 attempt: vary rsi_period (untouched in prior grids).
        # Anchor most params at Loop_10 winners; vary rsi_period + a few others.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.5, 1.8, 2.0],
            "atr_tp_mult": [3.0, 4.0, 5.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [1.0],
            "pivot_window": [3, 4, 5],
            "divergence_lookback": [60, 80, 120],
            "rsi_long_max": [50.0],
            "rsi_short_min": [55.0, 60.0],
            "atr_period": [14],
            "rsi_period": [6, 9, 12, 18, 21],
            "require_macd_divergence": [False],
        }
    elif args.grid == "macd_probe":
        # Loop_11 alt: vary MACD periods (also untouched). Could shift signal frequency.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [1.8],
            "atr_tp_mult": [3.0, 4.0],
            "use_trend_filter": [True],
            "trend_ema_period": [200],
            "leverage": [10],
            "position_equity_ratio": [1.0],
            "pivot_window": [4],
            "divergence_lookback": [80],
            "rsi_long_max": [50.0],
            "rsi_short_min": [55.0, 60.0],
            "atr_period": [14],
            "macd_fast": [8, 12, 16],
            "macd_slow": [21, 26, 34],
            "macd_signal": [7, 9, 12],
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
    elif args.grid == "sol_wr80":
        # SOLUSDC WR>80 search. Anchor on champion Loop_20260518_7 (sl3/tp1.5,
        # rsi 50/50, pivot 3, div_lb 50, macd-gate off, lev 5). Champion fails
        # ONLY the WR>80 target (min WR ~74 at 3m/15m). The WR levers: wider SL
        # vs tighter TP (more TP-hits), and a tighter RSI extremity gate
        # (still satisfies the mandatory <=50 / >=50 rule). Coarse pass.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [3.0, 4.0, 5.0],
            "atr_tp_mult": [1.0, 1.5, 2.0],
            "use_trend_filter": [False],
            "leverage": [5],
            "position_equity_ratio": [0.95],
            "pivot_window": [3, 4, 5],
            "divergence_lookback": [40, 50, 60],
            "rsi_long_max": [45.0, 50.0],
            "rsi_short_min": [50.0, 55.0],
            "atr_period": [14],
            "rsi_period": [14],
            "require_macd_divergence": [False, True],
        }
    elif args.grid == "sol_wr80_refine":
        # Fine neighborhood around the coarse monotonic edge bucket
        # (sl3/tp1.0, rsi 50/50, pivot3, macd off — min WR 79.84 at 12m,
        # strict-monotonic). Goal: nudge min WR over 80 WITHOUT breaking
        # monotonicity. Levers: slightly wider SL, tighter extremity gate
        # (still <=50/>=50 rule), and atr_period/div_lb/pivot reshaping which
        # trades cross the 6m/12m boundary (the consistent monotonicity break).
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [3.0, 3.25, 3.5, 4.0],
            "atr_tp_mult": [1.0, 1.25, 1.5],
            "use_trend_filter": [False],
            "leverage": [5],
            "position_equity_ratio": [0.95],
            "pivot_window": [3, 4],
            "divergence_lookback": [45, 50, 55],
            "rsi_long_max": [44.0, 47.0, 50.0],
            "rsi_short_min": [50.0, 53.0, 56.0],
            "atr_period": [10, 14],
            "rsi_period": [14],
            "require_macd_divergence": [False],
        }
    elif args.grid == "sol_wr80_deep":
        # macd-ON high-conviction regime. The only WR>80 lead from the coarse
        # pass (#13: pivot5, macd gate ON, rsi 45/55 -> min WR 80.95) breaks
        # monotonicity only at 6m. Requiring MACD divergence confluence on top
        # of RSI divergence filters to fewer, higher-quality entries. Sweep the
        # gate width, pivot/lookback, SL/TP and atr_period to find the sub-region
        # where the high-WR trade set is ALSO strictly monotonic 15>12>6>3>1.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [3.0, 3.5, 4.0, 5.0],
            "atr_tp_mult": [1.0, 1.5],
            "use_trend_filter": [False],
            "leverage": [5],
            "position_equity_ratio": [0.95],
            "pivot_window": [3, 4, 5],
            "divergence_lookback": [40, 50, 60],
            "rsi_long_max": [40.0, 45.0, 50.0],
            "rsi_short_min": [50.0, 55.0, 60.0],
            "atr_period": [10, 14],
            "rsi_period": [14],
            "require_macd_divergence": [True],
        }
    elif args.grid == "sol_wr80_macd":
        # MACD-params probe — the one lever every prior SOL sweep pinned at
        # 12/26/9. macd-ON gives WR 80-100 across all windows but the trade set
        # always loses in the ~4-6mo-ago segment (6m PnL < 3m PnL -> monotonic
        # break). Different MACD speed shifts divergence pivots, changing WHICH
        # trades fall in that segment. Hunt for a fast/slow/signal triple whose
        # high-WR trade set is net-positive and growing through 3m->6m.
        grid = {
            "use_atr_stops": [True],
            "atr_sl_mult": [3.0, 3.5],
            "atr_tp_mult": [1.0],
            "use_trend_filter": [False],
            "leverage": [5],
            "position_equity_ratio": [0.95],
            "pivot_window": [4, 5],
            "divergence_lookback": [40, 50, 60],
            "rsi_long_max": [45.0],
            "rsi_short_min": [55.0, 60.0],
            "atr_period": [10, 14],
            "rsi_period": [14],
            "require_macd_divergence": [True],
            "macd_fast": [6, 8, 12, 15],
            "macd_slow": [21, 26, 34],
            "macd_signal": [9],
        }
    elif args.grid == "sol_wr80_pnl":
        # PnL-maximization within the WR>80 feasible region. Holds the
        # Loop_20260518_31 unlock (fast MACD 6/21/9 + MACD-div confluence,
        # pivot5, rsi 45/55) and pushes the PnL levers: wider TP (user hint
        # #4 — extend reward as long as more PnL), leverage, equity ratio,
        # plus small SL/lookback/atr_period neighbors. score(wrfloor=80)
        # only surfaces configs still passing all four constraints, ranked
        # by 15m PnL.
        grid = {
            "use_atr_stops": [True],
            "use_trend_filter": [False],
            "rsi_period": [14],
            "require_macd_divergence": [True],
            "macd_fast": [6],
            "macd_slow": [21],
            "macd_signal": [9],
            "pivot_window": [5],
            "rsi_long_max": [45.0],
            "rsi_short_min": [55.0],
            "atr_sl_mult": [2.5, 3.0, 3.5],
            "atr_tp_mult": [1.0, 1.1, 1.2, 1.3, 1.5],
            "divergence_lookback": [35, 40, 45],
            "atr_period": [12, 14, 16],
            "leverage": [5, 6, 7],
            "position_equity_ratio": [0.95, 1.0],
        }
    elif args.grid == "sol_wr80_pnl2":
        # Entry-edge hunt at FIXED leverage 7 / eq 1.0. Goal: find a stricter,
        # higher-conviction gate (tighter RSI extremity, MACD-speed neighbors
        # of the 6/21/9 unlock, pivot/lookback) that buys enough WR headroom
        # to afford a WIDER take-profit (user hint #4: extend reward) while
        # still clearing min WR>80 — a more principled PnL path than scaling
        # leverage deeper into drawdown. score(wrfloor=80) ranks survivors by
        # 15m PnL; compare against the _1 champion (15m +879%).
        grid = {
            "use_atr_stops": [True],
            "use_trend_filter": [False],
            "rsi_period": [14],
            "require_macd_divergence": [True],
            "macd_fast": [5, 6, 7],
            "macd_slow": [18, 21, 24],
            "macd_signal": [9],
            "pivot_window": [4, 5, 6],
            "divergence_lookback": [40, 45, 50],
            "rsi_long_max": [40.0, 45.0],
            "rsi_short_min": [55.0, 60.0],
            "atr_sl_mult": [3.0],
            "atr_tp_mult": [1.0, 1.25, 1.5],
            "atr_period": [12],
            "leverage": [7],
            "position_equity_ratio": [1.0],
        }
    elif args.grid == "sol_wr80_pnl3":
        # Fine entry-edge scan around the Loop_20260519_2 winner
        # (mf7/ms24/piv6/dlb50), leverage fixed at 7 (drawdown-safe path).
        # Large WR headroom (min WR 86.8 vs 80 floor) suggests a still-
        # sharper gate may exist that lifts PnL further. atr_tp_mult held
        # at 1.0 (conclusively optimal under WR>80 across every gate tested).
        grid = {
            "use_atr_stops": [True],
            "use_trend_filter": [False],
            "rsi_period": [14],
            "require_macd_divergence": [True],
            "macd_fast": [6, 7, 8],
            "macd_slow": [22, 24, 26, 28],
            "macd_signal": [9],
            "pivot_window": [6, 7, 8],
            "divergence_lookback": [48, 50, 55, 60],
            "rsi_long_max": [45.0],
            "rsi_short_min": [55.0],
            "atr_sl_mult": [2.75, 3.0, 3.25],
            "atr_tp_mult": [1.0],
            "atr_period": [10, 12, 14],
            "leverage": [7],
            "position_equity_ratio": [1.0],
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
        s, ok, details = score(window_results, wr_floor=args.wrfloor)
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
