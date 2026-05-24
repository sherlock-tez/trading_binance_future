"""BNBUSDC optimization search harness.

Reuses the parity-verified fast vectorized engine (scripts/btcusdc_fast.py,
mathematically identical to SignalEngine + run_trade_cycle + SimulatedExecutionAdapter)
to search the strategy parameter space for a config that satisfies the user's
BNBUSDC targets:

  HARD (must pass, in priority order):
    0. Risk/Reward = atr_sl_mult / atr_tp_mult MUST be <= 0.5
       (reward TP >= 2x the risk SL) -- enforced by construction, every
       sampled/perturbed config is repaired to a feasible (sl, tp) grid pair.
    1. all windows positive (1m,3m,6m,12m,15m > 0)
    2. strict monotonic 15m > 12m > 6m > 3m > 1m  (consistency MUST)
    3. min win-rate across windows > 80%
  Subject to the above, beat the current incumbent's PnL/trade footprint,
  then maximize 15m PnL and average PnL.

Mandatory rule preserved by construction: rsi_long_max <= 50 and
rsi_short_min >= 50 in every sampled config (RSI-divergence + extremity gate).
leverage is pinned (user requirement: do not change leverage).

No new config keys are introduced; only existing strategy/trading params vary.

Usage:
  python scripts/bnbusdc_loop.py --mode random --n 2000 --seed 1
  python scripts/bnbusdc_loop.py --mode refine --center results_best.json --n 800
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from dataclasses import replace
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

# MUST set before importing the harness modules: both read SWEEP_SYMBOL at
# import time into a module-global SYMBOL used for cache filename, simulator
# bar routing, and trade-plan symbol.
os.environ["SWEEP_SYMBOL"] = "BNBUSDC"

from src.config import Settings, load_settings

import btcusdc_optimize as bo  # type: ignore
import btcusdc_fast as bf  # type: ignore

# Belt-and-suspenders: force the module globals regardless of import order.
bo.SYMBOL = "BNBUSDC"
bf.SYMBOL = "BNBUSDC"

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data_cache")
BEST_PATH = os.path.join(RESULTS_DIR, "bnb_best.json")

# Incumbent production-path baseline for Loop_20260524_1. The scorer keeps
# these explicit so future runs climb from the adopted profile instead of
# rediscovering older lower-PnL/lower-trade configurations.
BASELINE_RETURNS = {1: 43.91, 3: 132.46, 6: 422.09, 12: 831.28, 15: 2013.27}
BASELINE_TRADES = {1: 2, 3: 6, 6: 9, 12: 13, 15: 19}


def score(window_results) -> Tuple[float, bool, Dict[str, Any]]:
    results = sorted(window_results, key=lambda r: r.months)
    if not results or len(results) < 5:
        return -1e18, False, {}

    rets = [r.total_return_pct for r in results]
    wrs = [r.win_rate_pct for r in results]
    trades = [r.trade_count for r in results]
    months = [r.months for r in results]
    tpm = [t / m if m > 0 else 0.0 for t, m in zip(trades, months)]

    all_positive = all(x > 0 for x in rets)
    strict_monotonic = all(rets[i] < rets[i + 1] for i in range(len(rets) - 1))
    # Continuous monotonic-violation magnitude (0 == perfectly monotonic).
    # Normalised by step scale so refine can climb smoothly toward 0.
    viol = 0.0
    for i in range(len(rets) - 1):
        if rets[i] >= rets[i + 1]:
            viol += (rets[i] - rets[i + 1]) + 1e-6
    min_tpm = min(tpm) if tpm else 0.0
    tpm_ok = min_tpm >= 2.0
    min_wr = min(wrs) if wrs else 0.0
    avg_wr = sum(wrs) / len(wrs)
    avg_ret = sum(rets) / len(rets)
    last_ret = rets[-1]  # 15m
    wr80 = min_wr > 80.0
    max_dd = max((float(r.metrics.get("max_drawdown_pct", 0.0)) for r in results), default=0.0)
    beat_returns_all = all(r.total_return_pct > BASELINE_RETURNS.get(r.months, -1e18) for r in results)
    beat_trades_all = all(r.trade_count > BASELINE_TRADES.get(r.months, -1) for r in results)
    beat_15m_trades = trades[-1] > BASELINE_TRADES[15]
    trade_surplus = sum(r.trade_count - BASELINE_TRADES.get(r.months, 0) for r in results)
    return_surplus = sum(r.total_return_pct - BASELINE_RETURNS.get(r.months, 0.0) for r in results)

    hard_ok = all_positive and strict_monotonic and wr80

    if hard_ok and beat_returns_all and beat_trades_all:
        # Tier A+: strict reading of "more PnL and more trades" on every
        # requested window.
        s = (
            1e15
            + last_ret * 1e6
            + avg_ret * 1e5
            + trade_surplus * 1e7
            + return_surplus * 1e4
            + min_wr * 1e6
            - max_dd * 1e5
        )
    elif hard_ok and beat_returns_all and beat_15m_trades:
        # Tier A: every hard strategy target plus better return on all windows
        # and more total 15m trades than the incumbent. Keep climbing toward
        # all-window trade improvement through trade_surplus.
        s = (
            1e12
            + last_ret * 1e5
            + avg_ret * 1e4
            + trade_surplus * 1e7
            + min_wr * 1e6
            - max_dd * 1e5
        )
    elif hard_ok:
        # Tier B: WR/order/RR are intact, but the incumbent is not beaten on
        # both return and trade footprint yet.
        s = 1e11 + last_ret * 1e4 + avg_ret * 1e3 + trade_surplus * 1e6 + min_wr * 1e6 - max_dd * 1e5
    elif all_positive and strict_monotonic:
        # Tier B: ordering and profitability are present, but WR is not above
        # the strict floor. Rank primarily by min WR so refinements climb toward
        # the user's current hard target instead of reverting to low-WR PnL.
        s = 1e9 + min_wr * 1e6 + last_ret * 1e3 + avg_ret * 100.0 - max_dd * 1e4
    else:
        # Tier C: partial credit so near-misses surface for the next loop.
        # Gate on all_positive (any negative window is structurally far away),
        # then climb on: WR>=80, smaller monotonic violation, PnL.
        s = 0.0
        if all_positive:
            s += 5e7
            if wr80:
                s += 1e7
            # Reward shrinking the monotonic violation (capped contribution).
            s += 3e6 / (1.0 + viol)
            s += min_wr * 1e4
            s += min(last_ret, 1000.0) * 100.0 + avg_ret * 20.0
        else:
            # Sub-floor: not all positive. Keep ordering sane but well below.
            s += sum(1 for x in rets if x > 0) * 1e5
            s += min_wr * 100.0 + avg_ret * 0.2
        s = min(s, 9.9e8)

    detail = {
        "hard_ok": hard_ok,
        "wr80": wr80,
        "all_positive": all_positive,
        "strict_monotonic": strict_monotonic,
        "mono_violation": round(viol, 3),
        "min_trades_per_month": round(min_tpm, 2),
        "tpm_ok": tpm_ok,
        "max_dd_pct": round(max_dd, 2),
        "min_wr_pct": round(min_wr, 2),
        "avg_wr_pct": round(avg_wr, 2),
        "avg_return_pct": round(avg_ret, 2),
        "ret_15m_pct": round(last_ret, 2),
        "beat_returns_all": beat_returns_all,
        "beat_trades_all": beat_trades_all,
        "beat_15m_trades": beat_15m_trades,
        "trade_surplus": trade_surplus,
        "return_surplus": round(return_surplus, 2),
        "returns": {m: round(r, 2) for m, r in zip(months, rets)},
        "win_rates": {m: round(w, 2) for m, w in zip(months, wrs)},
        "trades": {m: t for m, t in zip(months, trades)},
        "trades_per_month": {m: round(x, 2) for m, x in zip(months, tpm)},
    }
    return s, hard_ok, detail


# Search space. Every option keeps the mandatory rule (rsi_long_max<=50, rsi_short_min>=50).
SPACE: Dict[str, List[Any]] = {
    "use_atr_stops": [True],
    "atr_sl_mult": [1.2, 1.5, 1.8, 2.0, 2.2, 2.5, 3.0],
    "atr_tp_mult": [3.0, 3.5, 4.0, 4.1, 4.5, 5.0, 6.0, 7.0, 8.0, 10.0],
    "use_trend_filter": [True, False],
    "trend_ema_period": [100, 150, 200, 225, 250, 275, 300, 350, 400],
    "sup_res_timeframes": [["1d", "1w"], ["6h", "12h", "1d", "1w"], ["3h", "6h", "12h", "1d", "1w"]],
    "leverage": [10],  # locked: user requirement — do not change leverage
    "position_equity_ratio": [0.98, 1.0],
    "pivot_window": [8, 10, 12, 14, 15, 16, 18, 20, 22],
    "divergence_lookback": [100, 120, 140, 160, 200, 220, 240, 280, 320],
    "rsi_period": [5, 7, 9, 10, 11, 12, 14, 18],
    "rsi_long_max": [25.0, 30.0, 35.0, 38.0, 40.0, 41.0, 45.0, 46.0, 48.0, 50.0],
    "rsi_short_min": [55.0, 58.0, 60.0, 62.0, 65.0, 70.0, 75.0, 80.0],
    "require_macd_divergence": [True, False],
    "macd_fast": [8, 10, 12, 14],
    "macd_slow": [21, 26, 34, 40, 50],
    "macd_signal": [7, 9, 12],
    "atr_period": [10, 12, 14, 16, 18, 21, 24, 28, 34, 36],
    "min_rr_ratio": [0.0],
    "max_sl_distance_pct": [0.0, 0.02, 0.022, 0.03, 0.04],
}


# User HARD constraint: Risk/Reward = atr_sl_mult / atr_tp_mult MUST be <= 0.5
# i.e. the reward (TP) must be at least 2x the risk (SL). This supersedes the
# old wide-SL / tiny-TP champion (Loop_20260519_2, sl 6.0 / tp 0.6 -> R/R 10.0),
# whose perfect in-sample WR was a geometry artifact with a large unrealised
# tail. Every sampled/perturbed config is repaired to the nearest feasible
# (atr_sl_mult, atr_tp_mult) grid pair so the constraint always holds.
MAX_RISK_REWARD = 0.5


def _enforce_risk_reward(o: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    sl_opts = SPACE["atr_sl_mult"]
    tp_opts = SPACE["atr_tp_mult"]
    feasible_sl = [s for s in sl_opts if any(s / t <= MAX_RISK_REWARD for t in tp_opts)]
    if o["atr_sl_mult"] not in feasible_sl:
        o["atr_sl_mult"] = rng.choice(feasible_sl)
    valid_tp = [t for t in tp_opts if o["atr_sl_mult"] / t <= MAX_RISK_REWARD]
    if o["atr_tp_mult"] not in valid_tp:
        o["atr_tp_mult"] = rng.choice(valid_tp)
    return o


def sample(rng: random.Random) -> Dict[str, Any]:
    o = {k: rng.choice(v) for k, v in SPACE.items()}
    if o["macd_slow"] <= o["macd_fast"]:
        o["macd_slow"] = o["macd_fast"] + 14
    return _enforce_risk_reward(o, rng)


def neighbors(center: Dict[str, Any], rng: random.Random) -> Dict[str, Any]:
    """Perturb a few dims of `center` to a neighboring grid value."""
    o = dict(center)
    dims = [k for k in SPACE if k in center]
    for k in rng.sample(dims, k=min(4, len(dims))):
        opts = SPACE[k]
        if center[k] in opts:
            idx = opts.index(center[k])
            choices = [opts[j] for j in (idx - 1, idx, idx + 1) if 0 <= j < len(opts)]
            o[k] = rng.choice(choices)
        else:
            o[k] = rng.choice(opts)
    if o["macd_slow"] <= o["macd_fast"]:
        o["macd_slow"] = o["macd_fast"] + 14
    return _enforce_risk_reward(o, rng)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["random", "refine"], default="random")
    ap.add_argument("--n", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--center", type=str, default=BEST_PATH)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--budget", type=float, default=0.0, help="max seconds (0=unlimited)")
    args = ap.parse_args()

    frames = bo.load_or_refresh_cache(refresh=False)
    base = load_settings("BNBUSDC")
    base = replace(base, symbols=["BNBUSDC"])
    months_list = [1, 3, 6, 12, 15]
    rng = random.Random(args.seed)

    center = None
    if args.mode == "refine":
        with open(args.center) as f:
            center = json.load(f)["overrides"]

    seen: set = set()
    best_s = -1e18
    best_rec: Dict[str, Any] = {}
    # Carry the incumbent across loop turns so progress never regresses.
    if os.path.exists(BEST_PATH):
        try:
            with open(BEST_PATH) as f:
                prev = json.load(f)
            best_s = float(prev.get("score", -1e18))
            best_rec = prev
            print(f"[incumbent] loaded score={best_s:.1f} "
                  f"detail={json.dumps(prev.get('detail', {}), sort_keys=True)}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[incumbent] none ({e})", flush=True)
    log: List[Tuple[float, Dict[str, Any], Dict[str, Any]]] = []
    start = time.time()

    i = 0
    while i < args.n:
        if args.budget and (time.time() - start) > args.budget:
            print(f"[budget] stop at {i} evals ({time.time()-start:.0f}s)", flush=True)
            break
        o = sample(rng) if args.mode == "random" else neighbors(center, rng)
        key = tuple(sorted(o.items()))
        if key in seen:
            continue
        seen.add(key)
        i += 1
        st = replace(base, **o)
        wr = bf.run_full(settings=st, frames=frames, months_list=months_list)
        s, hard_ok, det = score(wr)
        log.append((s, o, det))
        if s > best_s:
            best_s = s
            best_rec = {"score": s, "overrides": o, "detail": det}
            with open(BEST_PATH, "w") as f:
                json.dump(best_rec, f, indent=2)
            print(f"[new best] i={i} score={s:.1f} hard={hard_ok} "
                  f"wr80={det.get('wr80')} minWR={det.get('min_wr_pct')} "
                  f"15m={det.get('ret_15m_pct')} mono={det.get('strict_monotonic')} "
                  f"pos={det.get('all_positive')} tpm={det.get('min_trades_per_month')}", flush=True)
        if i % 100 == 0:
            print(f"[prog] {i}/{args.n} elapsed={time.time()-start:.0f}s best={best_s:.1f}", flush=True)

    log.sort(key=lambda x: x[0], reverse=True)
    print("\n=== TOP CONFIGS ===")
    for rank, (s, o, det) in enumerate(log[: args.top], 1):
        print(f"\n#{rank} score={s:.1f}")
        print(f"  overrides={json.dumps(o, sort_keys=True)}")
        print(f"  detail={json.dumps(det, sort_keys=True)}")


if __name__ == "__main__":
    main()
