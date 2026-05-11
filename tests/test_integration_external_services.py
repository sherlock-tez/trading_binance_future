import os
import time
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv

from src.data.binance_api import BinanceFuturesClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=True)


def _integration_enabled() -> bool:
    value = os.getenv("RUN_INTEGRATION_TESTS", "")
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


def test_binance_testnet_public_exchange_info_contains_core_symbols() -> None:
    client = BinanceFuturesClient(api_key="", api_secret="", testnet=True, timeout=15)
    exchange_info = client.get_exchange_info()

    symbols = {item.get("symbol") for item in exchange_info.get("symbols", [])}
    assert "BTCUSDT" in symbols
    assert "ETHUSDT" in symbols


def test_binance_testnet_signed_account_endpoints() -> None:
    credentials = _testnet_credentials()
    if credentials is None:
        pytest.skip("Missing BINANCE_TESTNET_FUTURE_API_KEY or BINANCE_TESTNET_FUTURE_API_SECRET")

    client = BinanceFuturesClient(
        api_key=credentials[0],
        api_secret=credentials[1],
        testnet=True,
        timeout=15,
    )

    balances = client.get_balances()
    positions = client.get_position_risk()

    assert isinstance(balances, list)
    assert isinstance(positions, list)


def test_telegram_bot_send_message() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        pytest.skip("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": f"integration test ping {int(time.time())}",
        "disable_web_page_preview": True,
    }
    response = requests.post(url, json=payload, timeout=15)
    response.raise_for_status()

    data = response.json()
    assert data.get("ok") is True
