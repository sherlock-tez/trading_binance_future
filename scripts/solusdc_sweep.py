"""Grid sweep over strategy params for SOLUSDC.

Dedicated SOL sweep harness (separated from `btcusdc_sweep.py` so SOL grids stop
piling up in the BTC tool). Re-uses the parametric production-path runner
(`btcusdc_optimize.py`, picks symbol from `SWEEP_SYMBOL` env) and fast harness
(`btcusdc_fast.py`) — those modules are not BTC-specific despite the filename.

Hard constraints enforced:
  - Risk/Reward = `atr_sl_mult / atr_tp_mult` <= `--maxrr` (default 0.5).
    Skips infeasible (sl, tp) combos by construction so every scored config
    respects the cap. The user imposed R/R <= 0.5 on SOL on 2026-05-19 and the
    rule is durable across symbols (see memory feedback_risk_reward_constraint).
  - Score gates also enforced (HARD): strict-monotonic PnL across windows,
    all-positive, min trades/month >= 2, min WR > `--wrfloor`.

Leverage and `position_equity_ratio` are NOT search dimensions — they are pinned
at the config value (durable cross-symbol feedback). Only strategy/geometry
params are swept here.
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
sys.path.insert(0, os.path.dirname(__file__))  # so 'import btcusdc_optimize' works

from src.config import Settings, load_settings

# Parametric harnesses — the file names are historical but the modules are
# symbol-agnostic (driven by SWEEP_SYMBOL env). Default this script to SOLUSDC.
os.environ.setdefault("SWEEP_SYMBOL", "SOLUSDC")
import btcusdc_optimize as bo  # type: ignore[no-redef]
import btcusdc_fast as bf  # type: ignore


MAX_RISK_REWARD = 0.5  # user hard constraint (2026-05-19): reward TP >= 2x risk SL


def _risk_reward_ok(overrides: Dict[str, Any], max_rr: float) -> bool:
    sl = overrides.get("atr_sl_mult")
    tp = overrides.get("atr_tp_mult")
    if sl is None or tp is None or tp <= 0:
        return True
    return (sl / tp) <= max_rr + 1e-9


def score(window_results, wr_floor: float = 80.0, tpm_floor: float = 2.0) -> Tuple[float, bool, Dict[str, Any]]:
    """HARD: min WR > wr_floor, strict-monotonic PnL, all-positive, min tr/mo >= tpm_floor.
    Passing configs ranked primarily by 15m PnL, then avg PnL, then avg WR.
    Failing configs fall back to a legacy near-miss score capped < 10000.
    `tpm_floor=0` disables the trade-frequency gate (user dropped it 2026-05-20).

    Zero-trade windows are treated as NEUTRAL (don't break any check) per user
    decision 2026-05-20: a selective strategy that finds no qualifying setup in
    a short window isn't broken; it's correctly inactive. Checks operate on
    ACTIVE windows (trade_count > 0) only.
    """
    results = sorted(window_results, key=lambda r: r.months)
    if not results:
        return -1e9, False, {}

    rets = [r.total_return_pct for r in results]
    wrs = [r.win_rate_pct for r in results]
    trades = [r.trade_count for r in results]
    months_list = [r.months for r in results]
    trades_per_month = [t / m if m > 0 else 0.0 for t, m in zip(trades, months_list)]

    # Active windows = those with at least 1 trade. Zero-trade windows are neutral.
    active_idx = [i for i, t in enumerate(trades) if t > 0]
    active_rets = [rets[i] for i in active_idx]
    active_wrs = [wrs[i] for i in active_idx]
    active_tpm = [trades_per_month[i] for i in active_idx]

    # Strict-monotonic and all-positive checked over ACTIVE windows only.
    if len(active_rets) >= 2:
        strict_monotonic = all(active_rets[i] < active_rets[i + 1] for i in range(len(active_rets) - 1))
    else:
        strict_monotonic = len(active_rets) == 1  # single active window: trivially monotonic
    all_positive = bool(active_rets) and all(r > 0 for r in active_rets)
    min_wr = min(active_wrs) if active_wrs else 0.0
    wr_floor_ok = bool(active_wrs) and (min_wr > wr_floor)
    min_tpm = min(active_tpm) if active_tpm else 0.0
    tpm_floor_ok = min_tpm >= tpm_floor
    avg_ret = sum(rets) / len(rets)
    avg_wr = sum(wrs) / len(wrs)
    last_ret = rets[-1]  # 15m

    passes = strict_monotonic and all_positive and wr_floor_ok and tpm_floor_ok

    if passes:
        s = 10000.0 + last_ret * 2.0 + avg_ret * 0.5 + avg_wr * 0.1
    else:
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


# ----- Grids ---------------------------------------------------------------

def _grid_wr80_rr() -> Dict[str, List[Any]]:
    """Dual-constraint probe: R/R <= 0.5 AND WR >= 80 simultaneously.
    Pushes the entry side as hard as possible under the mandatory rule
    (LONG only if RSI<50, SHORT only if RSI>50). Holds the proven _10
    macd 7/24/9 edge; sweeps dlb, pivot, RSI extremity, R/R-feasible
    SL/TP, atr_period, trend filter.

    Background: prior R/R<=0.5 search converged at _10 (sl0.55/tp5/atrp9)
    with WR ~27%. ETH precedent: same dual ask was proven mutually
    exclusive across ~5140 evals (project_ethusdc_rr_constraint memory).
    Run with `--maxrr 0.5 --wrfloor 80`.
    """
    return {
        "use_atr_stops": [True],
        "rsi_period": [14],
        "require_macd_divergence": [True],
        "macd_fast": [7],
        "macd_slow": [24],
        "macd_signal": [9],
        "leverage": [8],
        "position_equity_ratio": [1.0],
        "sup_res_timeframes": [["1d", "1w"]],
        "atr_period": [12],
        "divergence_lookback": [52, 80, 120],
        "pivot_window": [6, 9, 12],
        "rsi_long_max": [30.0, 35.0, 40.0, 45.0],
        "rsi_short_min": [55.0, 60.0, 65.0, 70.0],
        "atr_sl_mult": [0.5, 0.8, 1.0],
        "atr_tp_mult": [2.0, 3.0, 4.0],
        "use_trend_filter": [False, True],
        "trend_ema_period": [200],
    }


def _grid_rr_fine() -> Dict[str, List[Any]]:
    """Between-node fine scan around _10 (R/R<=0.5 region). Re-uses the
    converged feasible champion as the anchor — kept here so the SOL tool
    is self-sufficient if a future iteration wants to re-explore the
    feasible region under a constraint change.
    """
    return {
        "use_atr_stops": [True],
        "use_trend_filter": [False],
        "rsi_period": [14],
        "require_macd_divergence": [True],
        "macd_fast": [7],
        "macd_slow": [24],
        "macd_signal": [9],
        "pivot_window": [6],
        "divergence_lookback": [52],
        "rsi_long_max": [45.0],
        "rsi_short_min": [55.0],
        "leverage": [8],
        "position_equity_ratio": [1.0],
        "sup_res_timeframes": [["1d", "1w"]],
        "atr_period": [9, 10, 11, 12],
        "atr_sl_mult": [0.5, 0.55, 0.6, 0.65, 0.7],
        "atr_tp_mult": [4.5, 5.0, 5.5, 6.0],
    }


def _grid_wr70_rr_btc() -> Dict[str, List[Any]]:
    """User-relaxed target (2026-05-20): R/R <= 0.5 AND WR > 70 (not 80).
    BTC achieves both (Loop_20260513_10: sl1.8/tp4.0 R/R 0.45, WR>=90.9%);
    SOL hadn't combined BTC's three key differentiators in any prior sweep:
      (a) `use_trend_filter: True` (EMA filter — biggest WR lever)
      (b) `require_macd_divergence: False` (RSI-div is the mandatory rule;
          MACD-div is an OPTIONAL confluence on BTC, off = more trades)
      (c) Wider SL (~1.8 vs SOL _10's 0.55) — survives noise long enough
          for the far TP to hit.
    Hold leverage 8 + position_equity_ratio 1.0 (both pinned, durable). Try
    BTC's standard 12/26/9 MACD alongside SOL's 7/24/9. Try BTC's full
    [3h,6h,12h,1d,1w] S/R alongside SOL's [1d,1w]. Run with
    --maxrr 0.5 --wrfloor 70.
    """
    return {
        "use_atr_stops": [True],
        "use_trend_filter": [True],           # BTC differentiator #1 (locked on)
        "require_macd_divergence": [False],   # BTC differentiator #2 (locked off; RSI-div still required)
        "rsi_period": [14],
        "macd_fast": [7],                     # hold SOL value; BTC 12/26 can be probed in a follow-up
        "macd_slow": [24],
        "macd_signal": [9],
        "divergence_lookback": [52],
        "leverage": [8],
        "position_equity_ratio": [1.0],
        "atr_period": [10, 14],
        "trend_ema_period": [100, 200],
        "pivot_window": [4, 6],
        "rsi_long_max": [45.0, 50.0],
        "rsi_short_min": [55.0, 60.0],
        "atr_sl_mult": [1.2, 1.5, 1.8, 2.0],  # wider stops, BTC-like
        "atr_tp_mult": [3.0, 4.0, 5.0],
        "sup_res_timeframes": [["1d", "1w"], ["3h", "6h", "12h", "1d", "1w"]],
    }


def _grid_wr70_rr_btc2() -> Dict[str, List[Any]]:
    """Follow-up to wr70_rr_btc (which had 0 pass on SOL). Calibration probe
    showed BTC's exact config on SOL = 15m +78.5/WR42/1.3tr/mo with 1m/3m/6m
    losing — confirming the recipe doesn't transfer directly. This grid
    anchors at BTC's entry edge (rsi_p 12, macd 12/26/9, pivot 4, MACD-div
    off, trend filter on) and widens SL/TP geometry + atr_period more
    aggressively than v1 (sl up to 3.0, tp up to 7.0, atrp to 18) since
    SOL's noise is larger than BTC's at 1h. Run --maxrr 0.5 --wrfloor 70.
    """
    return {
        "use_atr_stops": [True],
        "use_trend_filter": [True],
        "require_macd_divergence": [False],
        "rsi_period": [12],
        "macd_fast": [12],
        "macd_slow": [26],
        "macd_signal": [9],
        "pivot_window": [4],
        "leverage": [8],
        "position_equity_ratio": [1.0],
        "divergence_lookback": [52, 80, 120],
        "atr_period": [10, 14, 18],
        "trend_ema_period": [100, 200],
        "rsi_long_max": [45.0, 50.0],
        "rsi_short_min": [55.0, 60.0],
        "atr_sl_mult": [1.5, 1.8, 2.5, 3.0],
        "atr_tp_mult": [3.5, 4.0, 5.0, 6.0, 7.0],
        "sup_res_timeframes": [["1d", "1w"], ["3h", "6h", "12h", "1d", "1w"]],
    }


def _grid_wr70_rr_strict() -> Dict[str, List[Any]]:
    """Combine BOTH filter gates (trend EMA + MACD-divergence) under R/R<=0.5.
    Prior wr70_rr_btc grids had MACD-div off when trend filter on; this grid
    holds both ON = the most selective entry possible. Holds the proven SOL
    entry edge (macd 7/24/9, dlb52, pivot6, [1d,1w] S/R) and sweeps the
    geometry + gate + trend-ema dims. Run --maxrr 0.5 --wrfloor 70 --tpmfloor 0.
    Hypothesis: most selective gate -> highest WR achievable in feasible region.
    """
    return {
        "use_atr_stops": [True],
        "use_trend_filter": [True],
        "require_macd_divergence": [True],
        "rsi_period": [14],
        "macd_fast": [7],
        "macd_slow": [24],
        "macd_signal": [9],
        "divergence_lookback": [52],
        "pivot_window": [6],
        "leverage": [8],
        "position_equity_ratio": [1.0],
        "sup_res_timeframes": [["1d", "1w"]],
        "atr_period": [10, 14],
        "trend_ema_period": [100, 200],
        "rsi_long_max": [30.0, 35.0, 40.0, 45.0],
        "rsi_short_min": [55.0, 60.0, 65.0, 70.0],
        "atr_sl_mult": [0.5, 1.0, 1.5, 2.0],
        "atr_tp_mult": [3.0, 4.0, 5.0, 6.0],
    }


def _grid_wr70_pareto() -> Dict[str, List[Any]]:
    """User explicit relaxation (2026-05-20 round 2): trade-frequency AND PnL
    floors dropped. Only HARD now: R/R<=0.5 + WR>70 + strict-monotonic on
    active windows + all-positive on active windows. Probes corners not yet
    tested by prior wr70_rr_* sweeps:
      - R/R *near* the 0.5 cap (sl/tp pairs like 1.0/2.0=0.5, 1.5/3.0=0.5,
        1.8/4.0=0.45) — BTC's sweet spot, deeper than _10's R/R 0.11
      - `trend_ema_period: 50` (NEW: faster trend filter, catches shorter trends)
      - BTC's `[3h,6h,12h,1d,1w]` S/R set crossed with SOL's `[1d,1w]`
      - require_macd_divergence: BOTH true and false
    Run --maxrr 0.5 --wrfloor 70 --tpmfloor 0.
    """
    return {
        "use_atr_stops": [True],
        "use_trend_filter": [True],
        "require_macd_divergence": [True, False],
        "rsi_period": [14],
        "macd_fast": [7],
        "macd_slow": [24],
        "macd_signal": [9],
        "pivot_window": [6],
        "leverage": [8],
        "position_equity_ratio": [1.0],
        "atr_period": [12],
        "divergence_lookback": [52, 80],
        "trend_ema_period": [50, 100, 200],
        "rsi_long_max": [35.0, 40.0, 45.0],
        "rsi_short_min": [55.0, 60.0, 65.0],
        "atr_sl_mult": [1.0, 1.5, 1.8, 2.0],   # R/R near 0.5 (BTC sweet spot)
        "atr_tp_mult": [2.0, 3.0, 4.0, 5.0],
        "sup_res_timeframes": [["1d", "1w"], ["3h", "6h", "12h", "1d", "1w"]],
    }


GRIDS = {
    "wr80_rr": _grid_wr80_rr,
    "rr_fine": _grid_rr_fine,
    "wr70_rr_btc": _grid_wr70_rr_btc,
    "wr70_rr_btc2": _grid_wr70_rr_btc2,
    "wr70_rr_strict": _grid_wr70_rr_strict,
    "wr70_pareto": _grid_wr70_pareto,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--grid", type=str, default="wr80_rr", choices=sorted(GRIDS.keys()))
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--wrfloor", type=float, default=80.0,
                        help="Minimum win-rate floor (HARD) applied to every window.")
    parser.add_argument("--maxrr", type=float, default=0.5,
                        help="Hard Risk/Reward cap: skip combos where "
                             "atr_sl_mult/atr_tp_mult > maxrr (default 0.5 = reward>=2x risk).")
    parser.add_argument("--tpmfloor", type=float, default=2.0,
                        help="Minimum trades-per-month floor (HARD) applied to every window. "
                             "Set to 0 to disable (user dropped this on SOL 2026-05-20).")
    args = parser.parse_args()

    frames = bo.load_or_refresh_cache(refresh=False)
    sweep_symbol = os.environ.get("SWEEP_SYMBOL", "SOLUSDC")
    base_settings = load_settings(sweep_symbol)
    base_settings = replace(base_settings, symbols=[sweep_symbol])

    months_list = [1, 3, 6, 12, 15]
    grid = GRIDS[args.grid]()

    keys = list(grid.keys())
    combos = list(itertools.product(*[grid[k] for k in keys]))
    if args.maxrr is not None:
        feasible = [c for c in combos if _risk_reward_ok(dict(zip(keys, c)), args.maxrr)]
        print(f"[sweep] symbol={sweep_symbol} grid={args.grid} combinations={len(combos)} "
              f"feasible(R/R<={args.maxrr})={len(feasible)} "
              f"skipped={len(combos) - len(feasible)}")
        combos = feasible
    else:
        print(f"[sweep] symbol={sweep_symbol} grid={args.grid} combinations={len(combos)}")

    results_log: List[Tuple[float, Dict[str, Any], Dict[str, Any]]] = []
    start = time.time()

    for i, combo in enumerate(combos):
        overrides = dict(zip(keys, combo))
        settings = apply_overrides(base_settings, overrides)
        window_results = bf.run_full(settings=settings, frames=frames, months_list=months_list)
        s, ok, details = score(window_results, wr_floor=args.wrfloor, tpm_floor=args.tpmfloor)
        results_log.append((s, overrides, details))
        if (i + 1) % 25 == 0 or i + 1 == len(combos):
            elapsed = time.time() - start
            print(f"[sweep] {i+1}/{len(combos)} elapsed={elapsed:.1f}s last_score={s:.2f}", flush=True)

    results_log.sort(key=lambda x: x[0], reverse=True)
    passing = [r for r in results_log if r[0] >= 10000.0]

    print(f"\n=== SUMMARY === passing={len(passing)} / evaluated={len(results_log)} "
          f"(wrfloor={args.wrfloor}, maxrr={args.maxrr}, tpmfloor={args.tpmfloor})")

    print("\n=== TOP CONFIGS ===")
    top = results_log[: args.top]
    for rank, (s, overrides, details) in enumerate(top, 1):
        passes_marker = " (PASSES ALL HARD)" if s >= 10000.0 else ""
        print(f"\n#{rank} score={s:.2f}{passes_marker}")
        print(f"  overrides={json.dumps(overrides, sort_keys=True)}")
        print(f"  details={json.dumps(details, sort_keys=True)}")


if __name__ == "__main__":
    main()
