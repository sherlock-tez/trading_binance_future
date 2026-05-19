from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import pandas as pd
import websockets
from websockets.exceptions import ConnectionClosed

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

# Websocket resilience knobs.
STREAM_STALENESS_TIMEOUT = 90.0      # no message in N s -> reconnect
RECONNECT_BACKOFF_BASE = 1.0
RECONNECT_BACKOFF_CAP = 60.0
MAX_CONNECTION_SECONDS = 23 * 3600   # rotate before Binance's 24h hard cap
ALERT_THROTTLE_SECONDS = 300.0       # min gap between repeated failure alerts


def _build_ws_url(base: str, combined: str, mode: str, is_testnet: bool) -> str:
    """Build the stream URL for the requested routing mode.

    `combined` is the `/`-joined stream list (e.g. ``a@kline_1m/b@kline_1m``).
    Testnet base does not support routed paths, so force legacy there.
    `raw` is single-stream only; multi-symbol falls back to legacy.
    """
    if is_testnet and mode != "legacy":
        logger.info("testnet base has no routed paths; using legacy stream mode")
        mode = "legacy"
    if mode == "market":
        return f"{base}/market/stream?streams={combined}"
    if mode == "raw":
        if "/" in combined:
            logger.info("raw mode is single-stream only; using legacy for multi-symbol")
            return f"{base}/stream?streams={combined}"
        return f"{base}/ws/{combined}"
    return f"{base}/stream?streams={combined}"


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
        *,
        on_error: Optional[Callable[[str], Any]] = None,
        staleness_timeout: float = STREAM_STALENESS_TIMEOUT,
        stream_path_mode: str = "legacy",
        rest_fallback_after: int = 3,
        rest_poll_seconds: float = 30.0,
        recover_probe_seconds: float = 300.0,
    ) -> None:
        combined = "/".join(f"{symbol.lower()}@kline_{timeframe}" for symbol in symbols)
        ws_url = _build_ws_url(
            self.client.ws_base_url, combined, stream_path_mode, self.client.testnet
        )

        last_close_time: Dict[str, int] = {}
        last_failure_alert_at = 0.0

        async def _emit(message: str) -> None:
            if on_error is None:
                return
            try:
                result = on_error(message)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("on_error callback failed")

        async def _emit_failure(message: str) -> None:
            nonlocal last_failure_alert_at
            now = time.monotonic()
            if now - last_failure_alert_at >= ALERT_THROTTLE_SECONDS:
                last_failure_alert_at = now
                await _emit(message)

        async def _dispatch(kline: Dict[str, Any]) -> None:
            sym = str(kline["s"]).upper()
            close_time = int(kline["T"])
            if close_time <= last_close_time.get(sym, 0):
                return
            last_close_time[sym] = close_time
            event = CandleEvent(
                symbol=str(kline["s"]),
                timeframe=str(kline["i"]),
                open_time=int(kline["t"]),
                close_time=close_time,
                open_price=float(kline["o"]),
                high_price=float(kline["h"]),
                low_price=float(kline["l"]),
                close_price=float(kline["c"]),
                volume=float(kline["v"]),
            )
            result = on_close(event)
            if asyncio.iscoroutine(result):
                await result

        backoff = RECONNECT_BACKOFF_BASE
        consecutive_stalls = 0
        degraded = False

        while True:
            if consecutive_stalls >= rest_fallback_after:
                await _emit(
                    f"WS degraded ({consecutive_stalls} stalls); switching to REST "
                    f"polling every {rest_poll_seconds:.0f}s"
                )
                await self._poll_closed_klines(
                    symbols,
                    timeframe,
                    on_close,
                    _emit_failure,
                    last_close_time,
                    ws_url,
                    rest_poll_seconds,
                    recover_probe_seconds,
                )
                await _emit("WS RECOVERED; leaving REST fallback")
                consecutive_stalls = 0
                degraded = False
                backoff = RECONNECT_BACKOFF_BASE
                continue

            try:
                async with websockets.connect(
                    ws_url, ping_interval=20, ping_timeout=20
                ) as ws:
                    connected_at = time.monotonic()
                    got_first = False
                    logger.info("Connected websocket: %s", ws_url)
                    while True:
                        if time.monotonic() - connected_at >= MAX_CONNECTION_SECONDS:
                            logger.info(
                                "Rotating websocket connection before 24h limit"
                            )
                            backoff = RECONNECT_BACKOFF_BASE
                            break
                        try:
                            message = await asyncio.wait_for(
                                ws.recv(), timeout=staleness_timeout
                            )
                        except asyncio.TimeoutError:
                            consecutive_stalls += 1
                            degraded = True
                            age = time.monotonic() - connected_at
                            logger.warning(
                                "Websocket STALE: no message in %.0fs "
                                "(conn alive %.0fs, stall #%d) - reconnecting",
                                staleness_timeout,
                                age,
                                consecutive_stalls,
                            )
                            await _emit_failure(
                                f"No market data for {staleness_timeout:.0f}s "
                                f"(stall #{consecutive_stalls}); reconnecting"
                            )
                            break

                        if not got_first:
                            got_first = True
                            logger.info(
                                "Websocket stream live: first message %.1fs after connect",
                                time.monotonic() - connected_at,
                            )
                            backoff = RECONNECT_BACKOFF_BASE
                            consecutive_stalls = 0
                            if degraded:
                                degraded = False
                                await _emit("Market data RECOVERED on websocket")

                        payload = json.loads(message)
                        kline_payload = payload.get("data", {}).get("k", {})
                        if not kline_payload:
                            continue
                        if not bool(kline_payload.get("x")):
                            continue
                        await _dispatch(kline_payload)
            except ConnectionClosed as exc:
                logger.warning("Websocket closed (%s) - reconnecting", exc)
                degraded = True
                await _emit_failure(
                    f"Stream connection closed ({type(exc).__name__}); reconnecting"
                )
            except Exception as exc:
                logger.exception("WebSocket stream failed: %s", exc)
                degraded = True
                await _emit_failure(
                    f"Stream error: {type(exc).__name__}; reconnecting"
                )

            sleep_for = min(backoff, RECONNECT_BACKOFF_CAP)
            logger.info("Reconnecting websocket in %.0fs", sleep_for)
            await asyncio.sleep(sleep_for)
            backoff = min(backoff * 2, RECONNECT_BACKOFF_CAP)

    async def _probe_ws(self, ws_url: str, *, recv_timeout: float = 10.0) -> bool:
        """Open a short-lived connection and return True if any message arrives."""
        try:
            async with websockets.connect(
                ws_url, ping_interval=20, ping_timeout=20
            ) as ws:
                await asyncio.wait_for(ws.recv(), timeout=recv_timeout)
                return True
        except Exception:
            return False

    async def _poll_closed_klines(
        self,
        symbols: List[str],
        timeframe: str,
        on_close: Callable[[CandleEvent], Any],
        emit_failure: Callable[[str], Any],
        last_close_time: Dict[str, int],
        ws_url: str,
        poll_seconds: float,
        probe_seconds: float,
    ) -> None:
        """REST-poll closed klines until the websocket recovers.

        Emits each newly-closed candle through the same `on_close` path,
        deduped via the shared `last_close_time` map. Returns when a
        websocket probe succeeds (caller resumes websocket mode).
        """
        logger.warning(
            "REST fallback engaged: polling %s every %.0fs (ws probe every %.0fs)",
            ",".join(symbols),
            poll_seconds,
            probe_seconds,
        )
        last_probe = time.monotonic()
        while True:
            for symbol in symbols:
                try:
                    frame = self.fetch_klines_frame(symbol, timeframe, limit=3)
                except Exception as exc:
                    logger.exception("REST poll failed for %s: %s", symbol, exc)
                    result = emit_failure(
                        f"REST poll error for {symbol}: {type(exc).__name__}"
                    )
                    if asyncio.iscoroutine(result):
                        await result
                    continue

                if frame is None or frame.empty or len(frame) < 2:
                    continue

                closed = frame.iloc[-2]
                close_time = int(closed["close_time"])
                sym_key = symbol.upper()
                if close_time <= last_close_time.get(sym_key, 0):
                    continue
                last_close_time[sym_key] = close_time

                event = CandleEvent(
                    symbol=sym_key,
                    timeframe=timeframe,
                    open_time=int(closed["open_time"]),
                    close_time=close_time,
                    open_price=float(closed["open"]),
                    high_price=float(closed["high"]),
                    low_price=float(closed["low"]),
                    close_price=float(closed["close"]),
                    volume=float(closed["volume"]),
                )
                logger.info(
                    "[rest-fallback] closed candle %s %s close_time=%s",
                    sym_key,
                    timeframe,
                    close_time,
                )
                result = on_close(event)
                if asyncio.iscoroutine(result):
                    await result

            if time.monotonic() - last_probe >= probe_seconds:
                last_probe = time.monotonic()
                if await self._probe_ws(ws_url):
                    logger.info("Websocket probe succeeded; leaving REST fallback")
                    return

            await asyncio.sleep(poll_seconds)
