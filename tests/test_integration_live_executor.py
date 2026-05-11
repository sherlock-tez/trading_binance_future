import os
import time
from dataclasses import replace
from pathlib import Path

import pytest
from dotenv import load_dotenv

from src.config import load_settings
from src.data.binance_api import BinanceFuturesClient
from src.execution.binance_futures import BinanceFuturesExecutor
from src.models import TradePlan


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=True)


def _integration_enabled() -> bool:
    value = os.getenv("RUN_INTEGRATION_TESTS", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _live_order_enabled() -> bool:
    value = os.getenv("RUN_LIVE_TESTNET_ORDERS", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _testnet_credentials() -> tuple[str, str] | None:
    api_key = os.getenv("BINANCE_TESTNET_FUTURE_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_TESTNET_FUTURE_API_SECRET", "").strip()
    if not api_key or not api_secret:
        return None
    return api_key, api_secret


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _integration_enabled(),
        reason="Set RUN_INTEGRATION_TESTS=1 to run integration tests.",
    ),
]


def _build_executor() -> tuple[BinanceFuturesExecutor, str]:
    credentials = _testnet_credentials()
    if credentials is None:
        pytest.skip("Missing BINANCE_TESTNET_FUTURE_API_KEY or BINANCE_TESTNET_FUTURE_API_SECRET")

    settings = replace(
        load_settings(),
        binance_testnet=True,
        max_open_positions=999,
    )
    symbol = settings.symbols[0]

    client = BinanceFuturesClient(
        api_key=credentials[0],
        api_secret=credentials[1],
        testnet=True,
        timeout=20,
    )
    executor = BinanceFuturesExecutor(client, settings)
    return executor, symbol


def _cleanup_symbol_orders_and_positions(client: BinanceFuturesClient, symbol: str) -> None:
    try:
        for order in client.get_open_orders(symbol):
            order_id = order.get("orderId")
            if order_id is not None:
                client.cancel_order(symbol, int(order_id))
    except Exception:
        pass

    try:
        for position in client.get_position_risk():
            if position.get("symbol") != symbol:
                continue
            amount = float(position.get("positionAmt", 0))
            if abs(amount) <= 0:
                continue

            if amount > 0:
                side = "SELL"
                position_side = "LONG"
            else:
                side = "BUY"
                position_side = "SHORT"

            client.place_order(
                {
                    "symbol": symbol,
                    "side": side,
                    "positionSide": position_side,
                    "type": "MARKET",
                    "quantity": f"{abs(amount):.12f}",
                }
            )
    except Exception:
        pass


def test_live_executor_open_positions_count_on_testnet() -> None:
    executor, _ = _build_executor()
    count = executor.open_positions_count

    assert isinstance(count, int)
    assert count >= 0


def test_live_executor_execute_trade_plan_on_testnet_quantity_guard() -> None:
    executor, symbol = _build_executor()

    # Keep this integration test non-destructive by bypassing mutating setup calls.
    executor._hedge_mode_set = True
    executor._leverage_set[symbol] = True

    now_ms = int(time.time() * 1000)
    plan = TradePlan(
        symbol=symbol,
        side="BUY",
        position_side="LONG",
        entry_price=1_000_000_000_000_000.0,
        stop_loss=900_000_000_000_000.0,
        take_profit=1_100_000_000_000_000.0,
        signal_time=now_ms,
    )

    result = executor.execute_trade_plan(plan=plan, context={"timestamp": now_ms})

    assert result.accepted is False
    assert result.reason == "quantity_too_small"


def test_live_executor_execute_trade_plan_on_testnet_places_order_and_cleans_up() -> None:
    if not _live_order_enabled():
        pytest.skip("Set RUN_LIVE_TESTNET_ORDERS=1 to place and cancel real Binance TESTNET orders")

    executor, symbol = _build_executor()
    client = executor.client

    book = client.get_book_ticker(symbol)
    bid = float(book["bidPrice"])
    ask = float(book["askPrice"])
    entry_price = (bid + ask) / 2

    equity = executor._available_equity()
    qty = executor._calc_quantity(symbol, entry_price, equity)
    if qty <= 0:
        pytest.skip("Insufficient TESTNET balance to place order through execute_trade_plan")

    now_ms = int(time.time() * 1000)
    plan = TradePlan(
        symbol=symbol,
        side="BUY",
        position_side="LONG",
        entry_price=entry_price,
        stop_loss=entry_price * 0.98,
        take_profit=entry_price * 1.02,
        signal_time=now_ms,
    )

    place_order_calls = 0
    original_place_order = client.place_order

    def counting_place_order(params):
        nonlocal place_order_calls
        place_order_calls += 1
        return original_place_order(params)

    client.place_order = counting_place_order
    try:
        result = executor.execute_trade_plan(plan=plan, context={"timestamp": now_ms})
    finally:
        client.place_order = original_place_order
        _cleanup_symbol_orders_and_positions(client, symbol)

    assert place_order_calls >= 1
    assert result.reason in {
        "entry_and_exit_orders_placed",
        "maker_entry_rejected",
        "execution_exception",
    }
