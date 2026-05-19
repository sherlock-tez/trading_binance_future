"""BTCUSDC optimization search harness.

Reuses the parity-verified fast vectorized engine (scripts/btcusdc_fast.py,
mathematically identical to SignalEngine + run_trade_cycle +
SimulatedExecutionAdapter) to search the strategy parameter space for a config
that satisfies the user's BTCUSDC targets:

  HARD (must pass, in priority order):
    1. all windows positive (1m,3m,6m,12m,15m > 0)
    2. strict monotonic 15m > 12m > 6m > 3m > 1m  (consistency MUST)
    3. min trades/month >= 2.0 across all windows
  TARGET:
    4. min win-rate across windows > 80%
  Subject to the above, maximize 15m PnL then average PnL (DD-penalised).

Mandatory rule preserved by construction: rsi_long_max <= 50 and
rsi_short_min >= 50 in every sampled config (RSI-divergence + extremity gate).
Reward leg constrained to RR = atr_tp_mult / atr_sl_mult >= 2.0 so the search
keeps the user's "extend the reward to 3,4,5" directive and never degenerates
into a tight-TP win-rate exploit.

No new config keys are introduced; only existing strategy/trading params vary.

Usage:
  python scripts/btcusdc_loop.py --mode random --n 2000 --seed 1
  python scripts/btcusdc_loop.py --mode refine --center data_cache/btc_best.json --n 800
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
os.environ["SWEEP_SYMBOL"] = "BTCUSDC"

from src.config import Settings, load_settings

import btcusdc_optimize as bo  # type: ignore
import btcusdc_fast as bf  # type: ignore

# Belt-and-suspenders: force the module globals regardless of import order.
bo.SYMBOL = "BTCUSDC"
bf.SYMBOL = "BTCUSDC"

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data_cache")
BEST_PATH = os.path.join(RESULTS_DIR, "btc_best.json")

MIN_RR = 2.0  # reward leg must be at least 2x risk (user "extend the reward")


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
    total_trades = sum(trades)

    # User priority: WR>80 is a MUST (target #1), strict monotonic is a MUST
    # (target #3b), THEN trade frequency toward 2-5/mo (target #3), THEN PnL
    # (target #2). A config that violates WR>80 is NOT acceptable no matter
    # how many trades it makes, so the WR>80 gate dominates the ranking.
    deployable = all_positive and strict_monotonic and wr80

    if deployable and tpm_ok:
        # Tier S: every target satisfied incl. >=2 trades/month. The dream.
        s = 1e12 + last_ret * 10.0 + avg_ret * 2.0 + min_wr * 5.0 - max_dd * 30.0
    elif deployable:
        # Tier A: the realistically-adoptable config — WR strictly >80 on
        # every window, strict monotonic, all positive. Among these, PnL is
        # the primary objective (user #2; WR #1 and monotonic #3b are already
        # gated). Trade frequency (user #3) is a secondary tiebreaker that
        # only becomes significant as it climbs toward the 2/mo target — it
        # must NOT let trades/mo noise (e.g. 0.42 vs 0.47) outweigh a large
        # PnL gap. DD-penalised. This is the tier we adopt from.
        s = 1e9
        s += min(last_ret, 1e7) * 100.0        # 15m PnL — primary
        s += avg_ret * 20.0                    # PnL across windows
        s += min_wr * 5e3                       # WR margin above 80
        s += total_trades * 5e3                # mild: more trades better
        s += min(min_tpm, 2.0) * 2e4           # mild frequency nudge toward 2/mo
        s -= max_dd * 500.0                    # drawdown penalty
    else:
        # Below the must-haves. Partial credit so near-misses surface for the
        # next loop, but ALWAYS ranked below any deployable (WR>80) config.
        s = 0.0
        if all_positive:
            s += 4e7
            if wr80:
                s += 2e8                        # WR>80 is the dominant near-miss signal
            if strict_monotonic:
                s += 1e8
            else:
                s += 3e6 / (1.0 + viol)
            if tpm_ok:
                s += 5e6
            s += min_wr * 5e5                   # climb toward WR>80
            s += min(last_ret, 5000.0) * 100.0 + avg_ret * 20.0
            s += min(min_tpm, 5.0) * 1e4
        else:
            s += sum(1 for x in rets if x > 0) * 1e5
            s += min_wr * 100.0 + avg_ret * 0.2
        s = min(s, 9.5e8)

    detail = {
        "tier_s": deployable and tpm_ok,
        "deployable": deployable,
        "wr80": wr80,
        "all_positive": all_positive,
        "strict_monotonic": strict_monotonic,
        "total_trades": total_trades,
        "mono_violation": round(viol, 3),
        "min_trades_per_month": round(min_tpm, 2),
        "tpm_ok": tpm_ok,
        "max_dd_pct": round(max_dd, 2),
        "min_wr_pct": round(min_wr, 2),
        "avg_wr_pct": round(avg_wr, 2),
        "avg_return_pct": round(avg_ret, 2),
        "ret_15m_pct": round(last_ret, 2),
        "returns": {m: round(r, 2) for m, r in zip(months, rets)},
        "win_rates": {m: round(w, 2) for m, w in zip(months, wrs)},
        "trades": {m: t for m, t in zip(months, trades)},
        "trades_per_month": {m: round(x, 2) for m, x in zip(months, tpm)},
    }
    return s, deployable, detail


# Search space. Every option keeps the mandatory rule (rsi_long_max<=50,
# rsi_short_min>=50). RR (tp/sl) is forced >= MIN_RR by sample()/neighbors().
SPACE: Dict[str, List[Any]] = {
    "use_atr_stops": [True],
    "atr_sl_mult": [1.0, 1.2, 1.5, 1.8, 2.0, 2.5, 3.0],
    "atr_tp_mult": [2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0, 7.0, 8.0],
    "use_trend_filter": [True, False],
    "trend_ema_period": [50, 100, 150, 200, 250],
    "leverage": [10, 15, 20, 25],
    "position_equity_ratio": [0.9, 0.95, 1.0],
    "pivot_window": [3, 4, 5, 6, 7, 8],
    "divergence_lookback": [40, 60, 80, 100, 120, 160],
    "rsi_period": [7, 9, 11, 12, 14, 21],
    "rsi_long_max": [25.0, 30.0, 35.0, 40.0, 45.0, 50.0],
    "rsi_short_min": [50.0, 55.0, 60.0, 65.0, 70.0, 75.0],
    "require_macd_divergence": [True, False],
    "macd_fast": [8, 12, 16],
    "macd_slow": [21, 26, 34],
    "macd_signal": [7, 9, 12],
    "atr_period": [7, 10, 14, 21],
}


def _enforce_rr(o: Dict[str, Any]) -> None:
    """Keep reward leg >= MIN_RR by bumping tp_mult to the smallest grid value
    that satisfies tp/sl >= MIN_RR (clamps at the largest tp option)."""
    sl = o["atr_sl_mult"]
    need = MIN_RR * sl
    if o["atr_tp_mult"] >= need:
        return
    for tp in SPACE["atr_tp_mult"]:
        if tp >= need:
            o["atr_tp_mult"] = tp
            return
    o["atr_tp_mult"] = SPACE["atr_tp_mult"][-1]


def sample(rng: random.Random) -> Dict[str, Any]:
    o = {k: rng.choice(v) for k, v in SPACE.items()}
    if o["macd_slow"] <= o["macd_fast"]:
        o["macd_slow"] = o["macd_fast"] + 14
    _enforce_rr(o)
    return o


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
    _enforce_rr(o)
    return o


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
    base = load_settings("BTCUSDC")
    base = replace(base, symbols=["BTCUSDC"])
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
        s, deployable, det = score(wr)
        log.append((s, o, det))
        if s > best_s:
            best_s = s
            best_rec = {"score": s, "overrides": o, "detail": det}
            with open(BEST_PATH, "w") as f:
                json.dump(best_rec, f, indent=2)
            print(f"[new best] i={i} score={s:.1f} deploy={deployable} "
                  f"tierS={det.get('tier_s')} wr80={det.get('wr80')} minWR={det.get('min_wr_pct')} "
                  f"15m={det.get('ret_15m_pct')} mono={det.get('strict_monotonic')} "
                  f"pos={det.get('all_positive')} tpm={det.get('min_trades_per_month')} "
                  f"totTr={det.get('total_trades')}", flush=True)
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
