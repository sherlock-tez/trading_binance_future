"""Live websocket integration test.

Connects to **Binance mainnet** and verifies that the websocket code path
(`LiveTradingRunner.start` -> `BinanceMarketDataService.stream_closed_klines`)
actually receives *closed* candle events.

`_on_closed_candle` is replaced with a stub that prints the received
`CandleEvent` and records it, so we are only validating the websocket /
streaming code, not the signal or execution logic.

Use a short timeframe (default ``1m``) so a closed candle arrives within a
minute instead of waiting an hour.

Run it explicitly (it is skipped by default — it is a real, ~1-2 min network
test):

    RUN_WS_INTEGRATION=1 pytest tests/test_integration_websocket.py -s -v

Optional env overrides:
    WS_TEST_SYMBOL     (default: SOLUSDC)
    WS_TEST_TIMEFRAME  (default: 1m)
    WS_TEST_CANDLES    (default: 1)    how many closed candles to wait for
    WS_TEST_TIMEOUT    (default: 150)  seconds before the test fails
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from src.config import load_settings
from src.data.binance_feed import CandleEvent
from src.runtime.live_runner import LiveTradingRunner

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=True)


def _ws_integration_enabled() -> bool:
    return os.getenv("RUN_WS_INTEGRATION", "").strip().lower() in {"1", "true", "yes", "on"}


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _ws_integration_enabled(),
        reason="Set RUN_WS_INTEGRATION=1 to run the live mainnet websocket test.",
    ),
]


def test_mainnet_websocket_delivers_closed_candles() -> None:
    symbol = os.getenv("WS_TEST_SYMBOL", "SOLUSDC").upper()
    timeframe = os.getenv("WS_TEST_TIMEFRAME", "1m").lower()
    target = int(os.getenv("WS_TEST_CANDLES", "1"))
    timeout = float(os.getenv("WS_TEST_TIMEOUT", "150"))

    base = load_settings()
    settings = dataclasses.replace(
        base,
        binance_testnet=False,          # force MAINNET
        symbols=[symbol],
        signal_timeframe=timeframe,
        sup_res_timeframes=[],          # keep warmup to a single fast REST call
        telegram_bot_token="",          # silence Telegram during the test
        telegram_chat_id="",
    )

    runner = LiveTradingRunner(settings)

    received: list[CandleEvent] = []
    done = asyncio.Event()

    async def fake_on_closed_candle(event: CandleEvent) -> None:
        received.append(event)
        print(
            f"\n[WS-TEST] closed candle #{len(received)} | "
            f"symbol={event.symbol} timeframe={event.timeframe} "
            f"open_time={event.open_time} close_time={event.close_time} "
            f"O={event.open_price} H={event.high_price} "
            f"L={event.low_price} C={event.close_price} V={event.volume}",
            flush=True,
        )
        if len(received) >= target:
            done.set()

    # Only _on_closed_candle is mocked; warmup + websocket stream run for real.
    runner._on_closed_candle = fake_on_closed_candle  # type: ignore[method-assign]

    failure: dict[str, object] = {}

    async def _run() -> None:
        task = asyncio.ensure_future(runner.start())
        try:
            await asyncio.wait_for(done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            # Surface *why* nothing arrived: did start()/warmup/connect blow up,
            # or did the websocket connect but deliver no data (silent stall)?
            if task.done():
                exc = task.exception()
                failure["reason"] = (
                    f"start() raised before any closed candle: {exc!r}"
                    if exc is not None
                    else "start() returned without delivering a closed candle"
                )
            else:
                failure["reason"] = (
                    f"start() still running after {timeout:.0f}s but NO closed "
                    f"{timeframe} candle for {symbol} was delivered — websocket "
                    f"connected but the stream is silent (no data pushed). This is "
                    f"the production 'connected then nothing' failure mode."
                )
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    asyncio.run(_run())

    if failure:
        pytest.fail(str(failure["reason"]))

    assert received, "expected at least one closed candle event"
    event = received[0]
    assert event.symbol.upper() == symbol
    assert event.timeframe == timeframe
    assert event.close_time > event.open_time
    assert event.high_price >= event.low_price
    assert event.open_price > 0 and event.close_price > 0
    assert event.volume >= 0
    assert len(received) >= target
