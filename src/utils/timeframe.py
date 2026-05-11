from __future__ import annotations

from typing import Tuple

import pandas as pd

SUPPORTED_BINANCE_INTERVALS = {
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
    "6h",
    "8h",
    "12h",
    "1d",
    "3d",
    "1w",
    "1M",
}


def normalize_timeframe(timeframe: str) -> str:
    tf = timeframe.strip()
    if tf.endswith("M") and tf[:-1].isdigit():
        return tf
    return tf.lower()


def needs_resample(timeframe: str) -> bool:
    tf = normalize_timeframe(timeframe)
    return tf not in SUPPORTED_BINANCE_INTERVALS


def resample_base_timeframe(timeframe: str) -> Tuple[str, int]:
    tf = normalize_timeframe(timeframe)
    if tf.endswith("h") and tf[:-1].isdigit():
        return "1h", int(tf[:-1])
    raise ValueError(f"Unsupported timeframe for resample: {timeframe}")


def resample_ohlcv(frame: pd.DataFrame, target_timeframe: str) -> pd.DataFrame:
    base_tf, factor = resample_base_timeframe(target_timeframe)
    if base_tf != "1h":
        raise ValueError(f"Only 1h base resample is currently supported: {target_timeframe}")

    if frame.empty:
        return frame.copy()

    data = frame.copy(deep=True).reset_index(drop=True)
    data = data.assign(open_dt=pd.to_datetime(data["open_time"].to_numpy(), unit="ms", utc=True))
    data = data.set_index("open_dt").sort_index()

    rule = f"{factor}h"
    resampled = data.resample(rule, label="left", closed="left").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "close_time": "last",
        }
    )
    resampled = resampled.dropna(subset=["open", "high", "low", "close"])
    resampled["open_time"] = (resampled.index.view("int64") // 10**6).astype(int)

    out = resampled[["open_time", "open", "high", "low", "close", "volume", "close_time"]]
    return out.reset_index(drop=True)
