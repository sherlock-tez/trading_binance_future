"""ETHUSDC optimization search harness.

Reuses the parity-verified fast vectorized engine (scripts/btcusdc_fast.py,
mathematically identical to SignalEngine + run_trade_cycle + SimulatedExecutionAdapter)
to search the strategy parameter space for a config that satisfies the user's
ETHUSDC targets:

  HARD (must pass, in priority order):
    1. all windows positive (1m,3m,6m,12m,15m > 0)
    2. strict monotonic 15m > 12m > 6m > 3m > 1m  (consistency MUST)
    3. min trades/month >= 2.0 across all windows
  TARGET:
    4. min win-rate across windows > 80%
  Subject to the above, maximize 15m PnL then average PnL, penalise drawdown.

Mandatory rule preserved by construction: rsi_long_max <= 50 and
rsi_short_min >= 50 in every sampled config (RSI-divergence + extremity gate).

No new config keys are introduced; only existing strategy/trading params vary.

Usage:
  python scripts/ethusdc_loop.py --mode random --n 2000 --seed 1
  python scripts/ethusdc_loop.py --mode refine --center data_cache/eth_best.json --n 800
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
os.environ["SWEEP_SYMBOL"] = "ETHUSDC"

from src.config import Settings, load_settings

import btcusdc_optimize as bo  # type: ignore
import btcusdc_fast as bf  # type: ignore

# Belt-and-suspenders: force the module globals regardless of import order.
bo.SYMBOL = "ETHUSDC"
bf.SYMBOL = "ETHUSDC"

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data_cache")
BEST_PATH = os.path.join(RESULTS_DIR, "eth_best.json")


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
    wr_ok = min_wr > 70.0  # Tier A WR threshold (per current /loop)
    max_dd = max((float(r.metrics.get("max_drawdown_pct", 0.0)) for r in results), default=0.0)

    # User 2026-05-20 (post-infeasibility decision): relaxed strict_monotonic
    # to all_positive after 42k evals proved strict_mono infeasible under
    # R/R<=0.5 + WR>=70 + tpm>=2. Tier A = all_positive + WR>70. tpm and
    # mono become soft tie-breaks (heavily weighted bonuses) so search prefers
    # denser-trade, more-monotonic configs without hard-rejecting otherwise.
    mono_ok = strict_monotonic
    tpm2_ok = min_tpm >= 2.0
    hard_ok = all_positive

    if hard_ok and wr_ok:
        # Tier A: all_positive + WR>70. Maximise PnL, reward higher WR, +trades
        # bonus (capped at tpm=5), +mono bonus (a la BTC pattern). DD tie-break.
        mono_bonus = 1e4 if strict_monotonic else 1e3 / (1.0 + viol)
        s = 1e12 + last_ret * 10.0 + avg_ret * 2.0 + min_wr * 5.0 \
            + min(min_tpm, 5.0) * 200.0 + mono_bonus - max_dd * 30.0
    elif hard_ok:
        # Tier B: all_positive but WR<=70. Push WR up then PnL.
        s = 1e9 + min_wr * 1e6 + last_ret * 100.0 + avg_ret * 10.0 + min(min_tpm, 5.0) * 1e3
    else:
        # Tier C: partial credit so near-misses surface for the next loop.
        s = 0.0
        if all_positive:
            s += 5e7
            if wr_ok:
                s += 1e7
            if tpm_ok:
                s += 5e6
            s += 3e6 / (1.0 + viol)
            s += min_wr * 1e4
            s += min(last_ret, 1000.0) * 100.0 + avg_ret * 20.0
            s += min(min_tpm, 5.0) * 1e4
        else:
            s += sum(1 for x in rets if x > 0) * 1e5
            s += min_wr * 100.0 + avg_ret * 0.2
        s = min(s, 9.9e8)

    detail = {
        "hard_ok": hard_ok,
        "wr_ok": wr_ok,
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
        "returns": {m: round(r, 2) for m, r in zip(months, rets)},
        "win_rates": {m: round(w, 2) for m, w in zip(months, wrs)},
        "trades": {m: t for m, t in zip(months, trades)},
        "trades_per_month": {m: round(x, 2) for m, x in zip(months, tpm)},
    }
    return s, hard_ok, detail


# Search space. Every option keeps the mandatory rule (rsi_long_max<=50, rsi_short_min>=50).
SPACE: Dict[str, List[Any]] = {
    "use_atr_stops": [True],
    # HARD CONSTRAINT (user): Risk/Reward = atr_sl_mult / atr_tp_mult MUST be
    # <= 0.5 (tp >= 2*sl). WR target relaxed to >70% under this constraint.
    # The user pointed at btcusdc_config.yaml (sl=1.5 / tp=4.0 -> R/R=0.375)
    # as a successful instance of these criteria. Grids include BTCUSDC's
    # values exactly (sl 1.5, tp 4.0, trend_ema 225, atr_period 9, rsi 12,
    # rsi_long_max 47, rsi_short_min 62) so the seed maps to grid points.
    "atr_sl_mult": [0.5, 0.7, 0.8, 1.0, 1.2, 1.5, 1.8, 2.0, 2.5, 3.0],
    "atr_tp_mult": [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0],
    "use_trend_filter": [True, False],
    "trend_ema_period": [50, 100, 150, 200, 225, 250],
    # PINNED: leverage is NOT a tuning lever (user instruction). It is a pure
    # position-size dial — the leverage×equity frontier sweep proved it trades
    # PnL for DD ~linearly with no Calmar gain. DD must be reduced via the
    # algorithm geometry, not by de-risking size. position_equity_ratio is the
    # same pure-size dial, so it is pinned too. Both stay at the config value.
    "leverage": [17],
    "position_equity_ratio": [0.98],
    "pivot_window": [3, 4, 5, 6, 7, 8],
    "divergence_lookback": [40, 60, 80, 100, 120, 140, 160, 180, 200, 240],
    "rsi_period": [7, 9, 11, 12, 14, 18, 21, 24, 28],
    "rsi_long_max": [25.0, 30.0, 35.0, 40.0, 45.0, 47.0, 50.0],
    "rsi_short_min": [50.0, 55.0, 60.0, 62.0, 65.0, 70.0, 75.0],
    "require_macd_divergence": [True, False],
    "macd_fast": [8, 12, 16],
    "macd_slow": [21, 26, 34],
    "macd_signal": [7, 9, 12],
    "atr_period": [7, 9, 10, 14, 18, 21, 24, 28],
}


def _enforce_rr(o: Dict[str, Any]) -> None:
    """Repair atr mults so Risk/Reward = sl/tp <= 0.5 (tp >= 2*sl).

    Hard user constraint. Bumps tp up to the smallest grid value >= 2*sl
    when needed; pulls sl down if no tp in grid can reach (defensive)."""
    need = 2.0 * o["atr_sl_mult"]
    if o["atr_tp_mult"] < need:
        ok = [v for v in SPACE["atr_tp_mult"] if v >= need]
        if ok:
            o["atr_tp_mult"] = min(ok)
        else:
            cap = o["atr_tp_mult"] / 2.0
            lo = [v for v in SPACE["atr_sl_mult"] if v <= cap]
            o["atr_sl_mult"] = max(lo) if lo else min(SPACE["atr_sl_mult"])


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
    base = load_settings("ETHUSDC")
    base = replace(base, symbols=["ETHUSDC"])
    months_list = [1, 3, 6, 12, 15]
    rng = random.Random(args.seed)

    center = None
    if args.mode == "refine":
        with open(args.center) as f:
            center = json.load(f)["overrides"]

    seen: set = set()
    best_s = -1e18
    best_rec: Dict[str, Any] = {}
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
                  f"wr_ok={det.get('wr_ok')} minWR={det.get('min_wr_pct')} "
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
