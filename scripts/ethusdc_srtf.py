"""ETHUSDC support/resistance-timeframe sweep.

The main random/refine search (scripts/ethusdc_loop.py) holds
`sup_res_timeframes` fixed. Narrowing that set changes which S/R levels gate
entries, which changes the actual trade set (not just position sizing) — on
SOLUSDC this lever produced large gains. This sweep holds the converged
Loop_20260519_10 strategy/trading params fixed and only varies
`sup_res_timeframes` over subsets resampleable from the 1h cache, scoring
against the ETHUSDC targets (same scoring as ethusdc_loop.py).

`sup_res_timeframes` is an existing config key — only its value changes; no
new config keys are introduced. Mandatory rule (RSI divergence + extremity
gate) is untouched.

Usage: python scripts/ethusdc_srtf.py
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import replace

os.environ["SWEEP_SYMBOL"] = "ETHUSDC"
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

from src.config import load_settings

import btcusdc_optimize as bo  # type: ignore
import btcusdc_fast as bf  # type: ignore
from ethusdc_loop import score  # type: ignore

bo.SYMBOL = bf.SYMBOL = "ETHUSDC"

# Subsets must be resampleable from the 1h cache (available: 3h,6h,12h,1d,1w).
SUBSETS = [
    ["3h", "6h", "12h", "1d", "1w"],   # current / baseline
    ["1d", "1w"],                       # SOLUSDC winner shape
    ["12h", "1d", "1w"],
    ["6h", "12h", "1d", "1w"],
    ["3h", "12h", "1d", "1w"],
    ["12h", "1d"],
    ["6h", "1d", "1w"],
    ["3h", "1d", "1w"],
    ["1d"],
    ["1w"],
    ["6h", "12h", "1d"],
    ["3h", "6h", "1d", "1w"],
]


def main() -> None:
    frames = bo.load_or_refresh_cache(refresh=False)
    base = replace(load_settings("ETHUSDC"), symbols=["ETHUSDC"])
    months_list = [1, 3, 6, 12, 15]

    rows = []
    for sub in SUBSETS:
        st = replace(base, sup_res_timeframes=sub)
        wr = bf.run_full(settings=st, frames=frames, months_list=months_list)
        s, hard_ok, det = score(wr)
        rows.append((s, sub, hard_ok, det))
        print(f"srtf={sub} score={s:.1f} hard={hard_ok} wr80={det.get('wr80')} "
              f"minWR={det.get('min_wr_pct')} 15m={det.get('ret_15m_pct')} "
              f"mono={det.get('strict_monotonic')} pos={det.get('all_positive')} "
              f"tpm={det.get('min_trades_per_month')} dd={det.get('max_dd_pct')} "
              f"returns={det.get('returns')}", flush=True)

    rows.sort(key=lambda x: x[0], reverse=True)
    print("\n=== RANKED ===")
    for s, sub, hard_ok, det in rows:
        print(f"{s:.1f}  srtf={sub}  hard={hard_ok} wr80={det.get('wr80')} "
              f"15m={det.get('ret_15m_pct')} minWR={det.get('min_wr_pct')}")
    best = rows[0]
    print("\nBEST srtf:", json.dumps(best[1]), "score", round(best[0], 1))
    # Baseline is SUBSETS[0]; report whether the best beats it.
    base_score = next(s for s, sub, _, _ in rows if sub == SUBSETS[0])
    print(f"baseline([3h,6h,12h,1d,1w]) score={base_score:.1f} ; "
          f"best beats baseline: {best[0] > base_score}")


if __name__ == "__main__":
    main()
