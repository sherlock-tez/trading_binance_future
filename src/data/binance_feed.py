from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

import pandas as pd
import websockets

from src.data.binance_api import BinanceFuturesClient
from src.utils.logging import get_logger
from src.utils.timeframe import (
    needs_resample,
    normalize_timeframe,
    resample_base_timeframe,
    resample_ohlcv,
)

logger = get_logger(__name__)

MAX_KLINE_LIMIT = 1500


@dataclass
class CandleEvent:
    symbol: str
    timeframe: str
    open_time: int
    close_time: int
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float


def klines_to_frame(raw_klines: List[List[Any]]) -> pd.DataFrame:
    if not raw_klines:
        return pd.DataFrame(
            columns=[
                "open_time",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
            ]
        )

    rows = []
    for item in raw_klines:
        rows.append(
            {
                "open_time": int(item[0]),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
                "close_time": int(item[6]),
            }
        )
    return pd.DataFrame(rows)


class BinanceMarketDataService:
    def __init__(self, client: BinanceFuturesClient):
        self.client = client
        self._cache: Dict[str, Dict[str, pd.DataFrame]] = {}

    def _fetch_klines(
        self,
        symbol: str,
        interval: str,
        *,
        limit: int,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> List[List[Any]]:
        if limit <= MAX_KLINE_LIMIT:
            return self.client.get_klines(
                symbol=symbol,
                interval=interval,
                limit=limit,
                start_time=start_time,
                end_time=end_time,
            )

        rows: List[List[Any]] = []
        remaining = limit

        if start_time is not None:
            cursor = start_time
            while remaining > 0:
                chunk_limit = min(MAX_KLINE_LIMIT, remaining)
                chunk = self.client.get_klines(
                    symbol=symbol,
                    interval=interval,
                    limit=chunk_limit,
                    start_time=cursor,
                    end_time=end_time,
                )
                if not chunk:
                    break
                rows.extend(chunk)
                remaining -= len(chunk)
                if len(chunk) < chunk_limit:
                    break
                cursor = int(chunk[-1][0]) + 1
            return self._dedupe_klines(rows)[-limit:]

        cursor_end = end_time
        chunks: List[List[List[Any]]] = []
        while remaining > 0:
            chunk_limit = min(MAX_KLINE_LIMIT, remaining)
            chunk = self.client.get_klines(
                symbol=symbol,
                interval=interval,
                limit=chunk_limit,
                end_time=cursor_end,
            )
            if not chunk:
                break
            chunks.insert(0, chunk)
            remaining -= len(chunk)
            if len(chunk) < chunk_limit:
                break
            cursor_end = int(chunk[0][0]) - 1

        for chunk in chunks:
            rows.extend(chunk)
        return self._dedupe_klines(rows)[-limit:]

    @staticmethod
    def _dedupe_klines(rows: List[List[Any]]) -> List[List[Any]]:
        keyed = {int(row[0]): row for row in rows}
        return [keyed[key] for key in sorted(keyed)]

    def fetch_klines_frame(
        self,
        symbol: str,
        timeframe: str,
        *,
        limit: int = 500,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> pd.DataFrame:
        tf = normalize_timeframe(timeframe)
        if needs_resample(tf):
            # Binance Futures does not expose every requested interval (for example 3h).
            base_tf, factor = resample_base_timeframe(tf)
            base_raw = self._fetch_klines(
                symbol=symbol,
                interval=base_tf,
                limit=limit * factor,
                start_time=start_time,
                end_time=end_time,
            )
            base_frame = klines_to_frame(base_raw)
            return resample_ohlcv(base_frame, tf).tail(limit).reset_index(drop=True)

        raw = self._fetch_klines(
            symbol=symbol,
            interval=tf,
            limit=limit,
            start_time=start_time,
            end_time=end_time,
        )
        return klines_to_frame(raw)

    def warmup(self, symbols: List[str], timeframes: List[str], *, limit: int = 500) -> None:
        for symbol in symbols:
            self._cache.setdefault(symbol, {})
            for timeframe in timeframes:
                self._cache[symbol][timeframe] = self.fetch_klines_frame(
                    symbol, timeframe, limit=limit
                )

    def refresh_symbol_timeframes(
        self,
        symbol: str,
        timeframes: List[str],
        *,
        limit: int = 500,
    ) -> Dict[str, pd.DataFrame]:
        self._cache.setdefault(symbol, {})
        for timeframe in timeframes:
            self._cache[symbol][timeframe] = self.fetch_klines_frame(symbol, timeframe, limit=limit)
        return {k: v.copy() for k, v in self._cache[symbol].items()}

    def get_cached(self, symbol: str) -> Dict[str, pd.DataFrame]:
        return {k: v.copy() for k, v in self._cache.get(symbol, {}).items()}

    async def stream_closed_klines(
        self,
        symbols: List[str],
        timeframe: str,
        on_close: Callable[[CandleEvent], Any],
    ) -> None:
        streams = "/".join(f"{symbol.lower()}@kline_{timeframe}" for symbol in symbols)
        ws_url = f"{self.client.ws_base_url}/stream?streams={streams}"

        while True:
            try:
                async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                    logger.info("Connected websocket: %s", ws_url)
                    async for message in ws:
                        payload = json.loads(message)
                        print("Received WebSocket message:", payload)
                        kline_payload = payload.get("data", {}).get("k", {})
                        if not kline_payload:
                            continue
                        if not bool(kline_payload.get("x")):
                            continue

                        event = CandleEvent(
                            symbol=str(kline_payload["s"]),
                            timeframe=str(kline_payload["i"]),
                            open_time=int(kline_payload["t"]),
                            close_time=int(kline_payload["T"]),
                            open_price=float(kline_payload["o"]),
                            high_price=float(kline_payload["h"]),
                            low_price=float(kline_payload["l"]),
                            close_price=float(kline_payload["c"]),
                            volume=float(kline_payload["v"]),
                        )
                        result = on_close(event)
                        if asyncio.iscoroutine(result):
                            await result
            except Exception as exc:
                logger.exception("WebSocket stream failed: %s", exc)
                await asyncio.sleep(3)
