"""Run a single set of overrides against BTCUSDC windows. CLI override of strategy params."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.config import load_settings

import btcusdc_optimize as bo  # type: ignore


def _coerce(value: str):
    low = value.lower()
    if low in {"true", "false"}:
        return low == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--windows", type=str, default="1,3,6,12,15")
    parser.add_argument("--override", action="append", default=[], help="key=value override of Settings")
    args = parser.parse_args()

    overrides = {}
    for o in args.override:
        k, v = o.split("=", 1)
        overrides[k.strip()] = _coerce(v.strip())

    frames = bo.load_or_refresh_cache(refresh=False)
    settings = load_settings("BTCUSDC")
    settings = replace(settings, symbols=["BTCUSDC"], **overrides)

    print(f"[overrides] {overrides}")
    months_list = [int(x) for x in args.windows.split(",") if x.strip()]
    results = bo.run_full(settings=settings, frames=frames, months_list=months_list)

    out = []
    for r in results:
        out.append({
            "months": r.months,
            "total_return_pct": r.total_return_pct,
            "win_rate_pct": r.win_rate_pct,
            "trade_count": r.trade_count,
            "max_drawdown_pct": r.metrics.get("max_drawdown_pct"),
            "sharpe": r.metrics.get("sharpe"),
        })
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
