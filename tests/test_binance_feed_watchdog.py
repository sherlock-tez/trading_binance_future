"""No-network unit tests for the websocket watchdog + REST fallback.

Covers: URL routing builder, stall -> alert -> reconnect -> backoff,
recovery alert, connection-closed handling, REST fallback emit + dedupe,
the stall->fallback switch decision, and config env/validation.
"""

from __future__ import annotations

import asyncio
import json

import pandas as pd
import pytest
from websockets.exceptions import ConnectionClosedError

import src.data.binance_feed as bf
from src.config import ConfigError, load_settings
from src.data.binance_feed import BinanceMarketDataService, CandleEvent, _build_ws_url


# ---------------------------------------------------------------- URL builder

def test_build_ws_url_modes():
    base = "wss://fstream.binance.com"
    assert _build_ws_url(base, "solusdc@kline_1m", "legacy", False) == (
        "wss://fstream.binance.com/stream?streams=solusdc@kline_1m"
    )
    assert _build_ws_url(base, "solusdc@kline_1m", "market", False) == (
        "wss://fstream.binance.com/market/stream?streams=solusdc@kline_1m"
    )
    assert _build_ws_url(base, "solusdc@kline_1m", "raw", False) == (
        "wss://fstream.binance.com/ws/solusdc@kline_1m"
    )
    # raw is single-stream only -> multi-symbol falls back to legacy
    assert _build_ws_url(base, "a@kline_1m/b@kline_1m", "raw", False) == (
        "wss://fstream.binance.com/stream?streams=a@kline_1m/b@kline_1m"
    )
    # testnet forces legacy regardless of requested mode
    assert _build_ws_url(
        "wss://stream.binancefuture.com", "solusdc@kline_1m", "market", True
    ) == "wss://stream.binancefuture.com/stream?streams=solusdc@kline_1m"


# ----------------------------------------------------------- test scaffolding

class _StopLoop(BaseException):
    """Breaks the orchestrator's infinite loop (BaseException so the
    orchestrator's `except Exception` cannot swallow it)."""


def _kline_msg(symbol: str, close_time: int) -> str:
    return json.dumps(
        {
            "stream": f"{symbol.lower()}@kline_1m",
            "data": {
                "e": "kline",
                "k": {
                    "s": symbol,
                    "i": "1m",
                    "t": close_time - 1,
                    "T": close_time,
                    "o": "1.0",
                    "h": "2.0",
                    "l": "0.5",
                    "c": "1.5",
                    "v": "10.0",
                    "x": True,
                },
            },
        }
    )


class _FakeWS:
    """recv() replays a script; unknown/exhausted -> block forever."""

    def __init__(self, script):
        self._script = list(script)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def recv(self):
        if not self._script:
            await asyncio.Event().wait()  # block (wait_for cancels it)
        kind, *rest = self._script.pop(0)
        if kind == "msg":
            return rest[0]
        if kind == "raise":
            raise rest[0]
        await asyncio.Event().wait()  # "block"


def _connect_factory(ws_queue):
    """Return a fake websockets.connect; pops the next _FakeWS, else a
    blocking one so further reconnects keep stalling deterministically."""

    def _connect(*_a, **_k):
        if ws_queue:
            return ws_queue.pop(0)
        return _FakeWS([])

    return _connect


class _DummyClient:
    ws_base_url = "wss://fstream.binance.com"
    testnet = False


def _service():
    return BinanceMarketDataService(_DummyClient())


def _patch_sleep(monkeypatch, stop_after):
    calls = {"n": 0}

    async def fake_sleep(_seconds):
        calls["n"] += 1
        if calls["n"] >= stop_after:
            raise _StopLoop()

    monkeypatch.setattr(bf.asyncio, "sleep", fake_sleep)
    return calls


# ------------------------------------------------------------------- tests

def test_stall_triggers_alert_and_reconnect(monkeypatch):
    svc = _service()
    alerts: list[str] = []
    closes: list[CandleEvent] = []
    monkeypatch.setattr(bf.websockets, "connect", _connect_factory([]))  # all blocking
    sleeps = _patch_sleep(monkeypatch, stop_after=2)

    async def run():
        with pytest.raises(_StopLoop):
            await svc.stream_closed_klines(
                ["SOLUSDC"], "1m", lambda e: closes.append(e),
                on_error=lambda m: alerts.append(m),
                staleness_timeout=0.02,
                rest_fallback_after=999,
            )

    asyncio.run(run())
    assert closes == []
    assert any("No market data" in a for a in alerts)
    assert sleeps["n"] >= 1  # reconnected (backoff sleep happened)


def test_recovery_after_stall_emits_recovered(monkeypatch):
    svc = _service()
    alerts: list[str] = []
    closes: list[CandleEvent] = []
    # conn1: one closed candle then block (-> stall). conn2: another candle (-> recovered)
    q = [
        _FakeWS([("msg", _kline_msg("SOLUSDC", 100))]),
        _FakeWS([("msg", _kline_msg("SOLUSDC", 200))]),
    ]
    monkeypatch.setattr(bf.websockets, "connect", _connect_factory(q))
    _patch_sleep(monkeypatch, stop_after=2)

    async def run():
        with pytest.raises(_StopLoop):
            await svc.stream_closed_klines(
                ["SOLUSDC"], "1m", lambda e: closes.append(e),
                on_error=lambda m: alerts.append(m),
                staleness_timeout=0.02,
                rest_fallback_after=999,
            )

    asyncio.run(run())
    assert [e.close_time for e in closes] == [100, 200]
    assert any("No market data" in a for a in alerts)
    assert any("RECOVERED" in a for a in alerts)


def test_connection_closed_alerts_and_reconnects(monkeypatch):
    svc = _service()
    alerts: list[str] = []
    q = [_FakeWS([("raise", ConnectionClosedError(None, None))])]
    monkeypatch.setattr(bf.websockets, "connect", _connect_factory(q))
    _patch_sleep(monkeypatch, stop_after=1)

    async def run():
        with pytest.raises(_StopLoop):
            await svc.stream_closed_klines(
                ["SOLUSDC"], "1m", lambda e: None,
                on_error=lambda m: alerts.append(m),
                staleness_timeout=5,
                rest_fallback_after=999,
            )

    asyncio.run(run())
    assert any("connection closed" in a.lower() for a in alerts)


def test_orchestrator_switches_to_rest_after_threshold(monkeypatch):
    svc = _service()
    alerts: list[str] = []
    switched = {"n": 0}

    async def fake_poll(*_a, **_k):
        switched["n"] += 1  # simulate WS recovering immediately

    monkeypatch.setattr(svc, "_poll_closed_klines", fake_poll)
    monkeypatch.setattr(bf.websockets, "connect", _connect_factory([]))  # blocking
    _patch_sleep(monkeypatch, stop_after=3)

    async def run():
        with pytest.raises(_StopLoop):
            await svc.stream_closed_klines(
                ["SOLUSDC"], "1m", lambda e: None,
                on_error=lambda m: alerts.append(m),
                staleness_timeout=0.02,
                rest_fallback_after=1,
            )

    asyncio.run(run())
    assert switched["n"] >= 1
    assert any("switching to REST" in a for a in alerts)
    assert any("RECOVERED" in a for a in alerts)


def test_rest_fallback_emits_and_dedupes(monkeypatch):
    svc = _service()
    closes: list[CandleEvent] = []

    # frame: row[-1] is the in-progress candle, row[-2] is the closed one.
    frame = pd.DataFrame(
        {
            "open_time": [10, 20, 30],
            "open": [1.0, 1.0, 1.0],
            "high": [2.0, 2.0, 2.0],
            "low": [0.5, 0.5, 0.5],
            "close": [1.5, 1.6, 1.7],
            "volume": [10.0, 11.0, 12.0],
            "close_time": [19, 29, 39],  # closed candle = row[-2] -> close_time 29
        }
    )
    monkeypatch.setattr(svc, "fetch_klines_frame", lambda *a, **k: frame)

    probe_calls = {"n": 0}

    async def fake_probe(_url, **_k):
        probe_calls["n"] += 1
        return probe_calls["n"] >= 2  # fail first, recover on the 2nd probe

    monkeypatch.setattr(svc, "_probe_ws", fake_probe)
    _patch_sleep(monkeypatch, stop_after=99)  # poll sleeps are harmless no-ops

    async def run():
        await svc._poll_closed_klines(
            ["SOLUSDC"], "1m",
            lambda e: closes.append(e),
            lambda m: None,
            {}, "wss://x", 0.0, 0.0,
        )

    asyncio.run(run())
    # polled multiple times but the same closed candle is emitted exactly once
    assert len(closes) == 1
    ev = closes[0]
    assert ev.symbol == "SOLUSDC"
    assert ev.close_time == 29
    assert ev.close_price == 1.6


def test_rest_only_mode_never_opens_websocket(monkeypatch):
    svc = _service()
    closes: list[CandleEvent] = []

    def _no_ws(*_a, **_k):
        raise AssertionError("websocket must not be opened in rest mode")

    async def _no_probe(*_a, **_k):
        raise AssertionError("probe must not run in rest-only mode")

    frame = pd.DataFrame(
        {
            "open_time": [10, 20, 30],
            "open": [1.0, 1.0, 1.0],
            "high": [2.0, 2.0, 2.0],
            "low": [0.5, 0.5, 0.5],
            "close": [1.5, 1.6, 1.7],
            "volume": [10.0, 11.0, 12.0],
            "close_time": [19, 29, 39],  # closed candle = row[-2] -> close_time 29
        }
    )
    monkeypatch.setattr(bf.websockets, "connect", _no_ws)
    monkeypatch.setattr(svc, "_probe_ws", _no_probe)
    monkeypatch.setattr(svc, "fetch_klines_frame", lambda *a, **k: frame)
    _patch_sleep(monkeypatch, stop_after=2)

    async def run():
        with pytest.raises(_StopLoop):
            await svc.stream_closed_klines(
                ["SOLUSDC"], "1m", lambda e: closes.append(e),
                on_error=lambda m: None,
                mode="rest",
                rest_poll_seconds=0.0,
                recover_probe_seconds=0.0,
            )

    asyncio.run(run())
    # REST emitted the closed candle exactly once (deduped on the 2nd poll),
    # and neither websockets.connect nor _probe_ws was ever called.
    assert len(closes) == 1
    assert closes[0].close_time == 29


# --------------------------------------------------------------- config knobs

def test_settings_ws_env_override(monkeypatch):
    monkeypatch.setenv("WS_STREAM_PATH_MODE", "market")
    monkeypatch.setenv("WS_STALENESS_TIMEOUT", "45")
    monkeypatch.setenv("WS_REST_FALLBACK_AFTER", "2")
    s = load_settings()
    assert s.ws_stream_path_mode == "market"
    assert s.ws_staleness_timeout == 45.0
    assert s.ws_rest_fallback_after == 2


def test_settings_invalid_ws_mode_raises(monkeypatch):
    monkeypatch.setenv("WS_STREAM_PATH_MODE", "bogus")
    with pytest.raises(ConfigError):
        load_settings()


def test_settings_market_data_mode_env(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_MODE", "rest")
    s = load_settings()
    assert s.market_data_mode == "rest"


def test_settings_invalid_market_data_mode_raises(monkeypatch):
    monkeypatch.setenv("MARKET_DATA_MODE", "bogus")
    with pytest.raises(ConfigError):
        load_settings()


def test_settings_frame_lookback_default_and_env(monkeypatch):
    assert load_settings().frame_lookback == 600
    monkeypatch.setenv("FRAME_LOOKBACK", "1500")
    assert load_settings().frame_lookback == 1500


def test_settings_invalid_frame_lookback_raises(monkeypatch):
    monkeypatch.setenv("FRAME_LOOKBACK", "0")
    with pytest.raises(ConfigError):
        load_settings()
