from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional

from src.config import Settings
from src.data.binance_api import BinanceAPIError, BinanceFuturesClient
from src.models import ExecutionResult, TradePlan
from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class SymbolFilters:
    tick_size: float
    step_size: float
    min_qty: float


class BinanceFuturesExecutor:
    def __init__(self, client: BinanceFuturesClient, settings: Settings):
        self.client = client
        self.settings = settings
        self._filters = self._load_filters()
        self._hedge_mode_set = False
        self._leverage_set: Dict[str, bool] = {}

    @property
    def open_positions_count(self) -> int:
        try:
            return self._open_position_count()
        except Exception:
            return 0

    @staticmethod
    def _floor_to_step(value: float, step: float) -> float:
        if step <= 0:
            return value
        return math.floor(value / step) * step

    @staticmethod
    def _round_to_tick(value: float, tick: float) -> float:
        if tick <= 0:
            return value
        return round(round(value / tick) * tick, 12)

    def _load_filters(self) -> Dict[str, SymbolFilters]:
        info = self.client.get_exchange_info()
        out: Dict[str, SymbolFilters] = {}
        for symbol in info.get("symbols", []):
            sym = symbol.get("symbol")
            if sym not in self.settings.symbols:
                continue
            tick_size = 0.0
            step_size = 0.0
            min_qty = 0.0
            for f in symbol.get("filters", []):
                f_type = f.get("filterType")
                if f_type == "PRICE_FILTER":
                    tick_size = float(f.get("tickSize", 0))
                elif f_type == "LOT_SIZE":
                    step_size = float(f.get("stepSize", 0))
                    min_qty = float(f.get("minQty", 0))
            out[sym] = SymbolFilters(tick_size=tick_size, step_size=step_size, min_qty=min_qty)
        return out

    def _ensure_hedge_mode(self) -> None:
        if self._hedge_mode_set:
            return
        try:
            self.client.set_hedge_mode(True)
        except BinanceAPIError as exc:
            # Binance may return an error if mode already set; treat as non-fatal.
            logger.warning("Unable to set hedge mode explicitly: %s", exc)
        self._hedge_mode_set = True

    def _ensure_leverage(self, symbol: str) -> None:
        if self._leverage_set.get(symbol):
            return
        self.client.set_leverage(symbol, self.settings.leverage)
        self._leverage_set[symbol] = True

    def _available_equity(self) -> float:
        balances = self.client.get_balances()
        target_assets = {"USDT", "USDC"}
        return sum(
            float(item.get("availableBalance", 0))
            for item in balances
            if item.get("asset") in target_assets
        )

    def _open_position_count(self) -> int:
        positions = self.client.get_position_risk()
        count = 0
        for pos in positions:
            amt = float(pos.get("positionAmt", 0))
            if abs(amt) > 0:
                count += 1
        return count

    def _position_notional(self, equity: float) -> float:
        return equity * self.settings.position_equity_ratio * self.settings.leverage

    def _calc_quantity(self, symbol: str, entry_price: float, equity: float) -> float:
        filters = self._filters.get(symbol)
        if filters is None:
            raise BinanceAPIError(f"Symbol filters not found for {symbol}")

        notional = self._position_notional(equity)
        raw_qty = notional / max(entry_price, 1e-12)
        floored = self._floor_to_step(raw_qty, filters.step_size)
        if floored < filters.min_qty:
            return 0.0
        return floored

    def _place_maker_entry(self, plan: TradePlan, quantity: float) -> Dict[str, object]:
        for retry in range(self.settings.order_reprice_max_retries):
            book = self.client.get_book_ticker(plan.symbol)
            bid = float(book["bidPrice"])
            ask = float(book["askPrice"])
            bps = self.settings.order_reprice_bps * (retry + 1)

            if plan.side == "BUY":
                raw_price = bid * (1 - bps / 10000)
            else:
                raw_price = ask * (1 + bps / 10000)

            symbol_filters = self._filters[plan.symbol]
            order_price = self._round_to_tick(raw_price, symbol_filters.tick_size)

            params = {
                "symbol": plan.symbol,
                "side": plan.side,
                "positionSide": plan.position_side,
                "type": "LIMIT",
                "timeInForce": "GTX",
                "quantity": f"{quantity:.12f}",
                "price": f"{order_price:.12f}",
                "newOrderRespType": "RESULT",
            }

            try:
                order = self.client.place_order(params)
                return {
                    "accepted": True,
                    "order": order,
                    "retries": retry,
                    "entry_price": order_price,
                }
            except BinanceAPIError as exc:
                message = str(exc).lower()
                is_post_only_reject = (
                    "immediately" in message
                    or "post" in message
                    or "-5022" in message
                )
                if is_post_only_reject:
                    logger.warning("Post-only reject on %s retry=%s", plan.symbol, retry + 1)
                    continue
                return {"accepted": False, "error": str(exc), "retries": retry}

        return {
            "accepted": False,
            "error": "maker_order_reprice_exhausted",
            "retries": self.settings.order_reprice_max_retries,
        }

    def _place_exit_orders(self, plan: TradePlan, quantity: float) -> Dict[str, object]:
        sl_side = "SELL" if plan.position_side == "LONG" else "BUY"
        tp_side = sl_side

        stop_order = self.client.place_order(
            {
                "symbol": plan.symbol,
                "side": sl_side,
                "positionSide": plan.position_side,
                "type": "STOP_MARKET",
                "stopPrice": f"{plan.stop_loss:.12f}",
                "closePosition": "true",
                "workingType": "MARK_PRICE",
                "priceProtect": "true",
            }
        )

        take_order = self.client.place_order(
            {
                "symbol": plan.symbol,
                "side": tp_side,
                "positionSide": plan.position_side,
                "type": "TAKE_PROFIT_MARKET",
                "stopPrice": f"{plan.take_profit:.12f}",
                "closePosition": "true",
                "workingType": "MARK_PRICE",
                "priceProtect": "true",
            }
        )

        return {"stop_order": stop_order, "take_order": take_order, "qty": quantity}

    def execute_trade_plan(
        self,
        plan: TradePlan,
        context: Optional[Dict[str, object]] = None,
    ) -> ExecutionResult:
        try:
            self._ensure_hedge_mode()
            self._ensure_leverage(plan.symbol)

            current_positions = self._open_position_count()
            if current_positions >= self.settings.max_open_positions:
                return ExecutionResult(
                    accepted=False,
                    reason="max_open_positions_reached",
                    details={"current_positions": current_positions},
                )

            equity = self._available_equity()
            qty = self._calc_quantity(plan.symbol, plan.entry_price, equity)
            if qty <= 0:
                return ExecutionResult(
                    accepted=False,
                    reason="quantity_too_small",
                    details={"equity": equity, "entry_price": plan.entry_price},
                )

            maker_result = self._place_maker_entry(plan, qty)
            if not bool(maker_result.get("accepted")):
                return ExecutionResult(
                    accepted=False,
                    reason="maker_entry_rejected",
                    details=maker_result,
                )

            exit_orders = self._place_exit_orders(plan, qty)
            return ExecutionResult(
                accepted=True,
                reason="entry_and_exit_orders_placed",
                order_id=str(maker_result["order"].get("orderId")),
                details={
                    "entry": maker_result,
                    "exits": exit_orders,
                },
            )
        except Exception as exc:
            return ExecutionResult(
                accepted=False,
                reason="execution_exception",
                details={"error": str(exc)},
            )
