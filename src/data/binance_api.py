from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests


class BinanceAPIError(RuntimeError):
    pass


class BinanceFuturesClient:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True, timeout: int = 20):
        self.api_key = api_key
        self.api_secret = api_secret.encode("utf-8") if api_secret else b""
        self.testnet = testnet
        self.timeout = timeout
        self.base_url = (
            "https://testnet.binancefuture.com" if testnet else "https://fapi.binance.com"
        )
        self.ws_base_url = (
            "wss://stream.binancefuture.com" if testnet else "wss://fstream.binance.com"
        )

    @staticmethod
    def _timestamp_ms() -> int:
        return int(time.time() * 1000)

    def _sign(self, params: Dict[str, Any]) -> str:
        if not self.api_secret:
            raise BinanceAPIError("Missing API secret for signed request")
        query = urlencode(params, doseq=True)
        return hmac.new(self.api_secret, query.encode("utf-8"), hashlib.sha256).hexdigest()

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        signed: bool = False,
    ) -> Any:
        params = dict(params or {})
        headers = {}

        if signed:
            if not self.api_key:
                raise BinanceAPIError("Missing API key for signed request")
            params.setdefault("timestamp", self._timestamp_ms())
            params.setdefault("recvWindow", 10000)
            params["signature"] = self._sign(params)
            headers["X-MBX-APIKEY"] = self.api_key

        url = f"{self.base_url}{path}"
        response = requests.request(
            method,
            url,
            params=params,
            headers=headers,
            timeout=self.timeout,
        )

        if response.status_code >= 400:
            raise BinanceAPIError(
                f"Binance request failed ({response.status_code}): {response.text}"
            )

        return response.json()

    def get_klines(
        self,
        symbol: str,
        interval: str,
        *,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 1000,
    ) -> List[List[Any]]:
        params: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": limit,
        }
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        return self._request("GET", "/fapi/v1/klines", params=params, signed=False)

    def get_exchange_info(self) -> Dict[str, Any]:
        return self._request("GET", "/fapi/v1/exchangeInfo", signed=False)

    def get_book_ticker(self, symbol: str) -> Dict[str, Any]:
        return self._request("GET", "/fapi/v1/ticker/bookTicker", params={"symbol": symbol}, signed=False)

    def get_balances(self) -> List[Dict[str, Any]]:
        return self._request("GET", "/fapi/v2/balance", signed=True)

    def get_position_risk(self) -> List[Dict[str, Any]]:
        return self._request("GET", "/fapi/v2/positionRisk", signed=True)

    def set_hedge_mode(self, enabled: bool = True) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/fapi/v1/positionSide/dual",
            params={"dualSidePosition": "true" if enabled else "false"},
            signed=True,
        )

    def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        return self._request(
            "POST",
            "/fapi/v1/leverage",
            params={"symbol": symbol, "leverage": leverage},
            signed=True,
        )

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        return self._request("GET", "/fapi/v1/openOrders", params=params, signed=True)

    def cancel_order(self, symbol: str, order_id: int) -> Dict[str, Any]:
        return self._request(
            "DELETE",
            "/fapi/v1/order",
            params={"symbol": symbol, "orderId": order_id},
            signed=True,
        )

    def place_order(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", "/fapi/v1/order", params=params, signed=True)

    def place_batch_orders(self, batch_orders: List[Dict[str, Any]]) -> Dict[str, Any]:
        payload = {"batchOrders": str(batch_orders).replace("'", '"')}
        return self._request("POST", "/fapi/v1/batchOrders", params=payload, signed=True)

    def place_algo_order(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Place a conditional algo order (STOP_MARKET / TAKE_PROFIT_MARKET / etc.).

        As of Binance USDS-M Futures 2025-12-09, conditional orders MUST use
        `/fapi/v1/algoOrder` — the legacy `/fapi/v1/order` endpoint now returns
        -4120 for these order types. Required params: `algoType=CONDITIONAL`,
        `symbol`, `side`, `type`, `triggerPrice` (replaces legacy `stopPrice`).
        Returns include `algoId` (not `orderId`).
        """
        return self._request("POST", "/fapi/v1/algoOrder", params=params, signed=True)

    def cancel_algo_order(self, algo_id: int) -> Dict[str, Any]:
        return self._request(
            "DELETE",
            "/fapi/v1/algoOrder",
            params={"algoId": algo_id},
            signed=True,
        )

    def get_open_algo_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        return self._request("GET", "/fapi/v1/openAlgoOrders", params=params, signed=True)
