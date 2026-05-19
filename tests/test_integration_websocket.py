"""Live market-data integration test (websocket + REST-fallback).

Connects to **Binance mainnet** and verifies that the live market-data path
(`LiveTradingRunner.start` -> `BinanceMarketDataService.stream_closed_klines`)
delivers a *closed* candle event under real network conditions.

`_on_closed_candle` is replaced with a stub that prints/records the
`CandleEvent`, so we validate only the streaming/resilience code, not the
signal or execution logic.

This test exercises the **resilient** path, not just the raw websocket:

* If the websocket data plane works (e.g. on the production server), the
  closed candle arrives over the websocket within a minute.
* If the websocket connects but is silently starved (the production
  "connected then nothing" failure mode, reproducible from some
  IPs/regions), the staleness watchdog detects it and the bot
  automatically falls back to REST polling, which still delivers the
  closed candle.

Either way a closed candle MUST arrive. To make the REST fallback engage
within the test window, the websocket resilience knobs are tightened for
this test (short staleness timeout, fall back after the first stall).

Run it explicitly (skipped by default — it is a real ~1-2 min network test):

    RUN_WS_INTEGRATION=1 pytest tests/test_integration_websocket.py -s -v

Optional env overrides:
    WS_TEST_SYMBOL          (default: SOLUSDC)
    WS_TEST_TIMEFRAME       (default: 1m)
    WS_TEST_CANDLES         (default: 1)    how many closed candles to wait for
    WS_TEST_TIMEOUT         (default: 120)  seconds before the test fails
    WS_TEST_STALENESS       (default: 20)   ws silence -> stall (seconds)
    WS_TEST_FALLBACK_AFTER  (default: 1)    stalls before REST fallback
    WS_TEST_POLL            (default: 15)   REST poll interval (seconds)
    WS_TEST_PROBE           (default: 900)  ws recovery probe interval (seconds)

To force a *pure websocket* check (no REST fallback), set
``WS_TEST_FALLBACK_AFTER`` to a large number and raise ``WS_TEST_TIMEOUT``.
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
    timeout = float(os.getenv("WS_TEST_TIMEOUT", "120"))
    staleness = float(os.getenv("WS_TEST_STALENESS", "20"))
    fallback_after = int(os.getenv("WS_TEST_FALLBACK_AFTER", "1"))
    poll_seconds = float(os.getenv("WS_TEST_POLL", "15"))
    probe_seconds = float(os.getenv("WS_TEST_PROBE", "900"))

    base = load_settings()
    settings = dataclasses.replace(
        base,
        binance_testnet=False,          # force MAINNET
        symbols=[symbol],
        signal_timeframe=timeframe,
        sup_res_timeframes=[],          # keep warmup to a single fast REST call
        telegram_bot_token="",          # silence Telegram during the test
        telegram_chat_id="",
        # Tighten resilience so a starved websocket flips to the REST
        # fallback within the test window instead of hanging silently.
        ws_staleness_timeout=staleness,
        ws_rest_fallback_after=fallback_after,
        ws_rest_poll_seconds=poll_seconds,
        ws_recover_probe_seconds=probe_seconds,
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

    # Only _on_closed_candle is mocked; warmup + websocket + REST fallback
    # all run for real.
    runner._on_closed_candle = fake_on_closed_candle  # type: ignore[method-assign]

    failure: dict[str, object] = {}

    async def _run() -> None:
        task = asyncio.ensure_future(runner.start())
        try:
            await asyncio.wait_for(done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            # No closed candle from EITHER the websocket OR the REST
            # fallback. Surface why: did start()/warmup/connect blow up,
            # or is the network fully unreachable?
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
                    f"{timeframe} candle for {symbol} arrived via the websocket "
                    f"OR the REST fallback (staleness={staleness:.0f}s, "
                    f"fallback_after={fallback_after}, poll={poll_seconds:.0f}s). "
                    f"The websocket may be starved AND REST polling is failing — "
                    f"check network/credentials/symbol."
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
