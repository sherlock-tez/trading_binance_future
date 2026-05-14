from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional

from binance.client import Client as BinanceSDKClient

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
    def __init__(
        self,
        client: BinanceFuturesClient,
        settings: Settings,
        sdk_client: Optional[BinanceSDKClient] = None,
    ):
        self.client = client
        self.settings = settings
        self._filters = self._load_filters()
        self._hedge_mode_set = False
        self._leverage_set: Dict[str, bool] = {}
        # python-binance SDK client used for conditional orders (SL/TP).
        # Binance migrated STOP/TAKE_PROFIT/STOP_MARKET/TAKE_PROFIT_MARKET to
        # the Algo Order endpoint on 2025-12-09 (-4120 on legacy endpoint), and
        # python-binance's futures_create_order auto-routes those types. We
        # accept an injected client for tests; otherwise build one from the
        # same credentials the raw client uses (or from settings when those
        # aren't exposed).
        self._sdk_client = sdk_client or self._build_sdk_client(client, settings)

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

    @staticmethod
    def _decimals_from_step(step: float) -> int:
        """Derive the decimal-place count Binance expects for this filter step.

        BTCUSDC tick=0.1 → 1 decimal; step=0.001 → 3 decimals. Sending more
        decimals than the symbol's precision triggers `-1111 Precision is over
        the maximum defined for this asset`.
        """
        if step <= 0:
            return 0
        s = f"{step:.12f}".rstrip("0")
        if "." not in s:
            return 0
        return max(0, len(s.split(".", 1)[1]))

    def _format_qty(self, symbol: str, qty: float) -> str:
        f = self._filters.get(symbol)
        decimals = self._decimals_from_step(f.step_size) if f else 8
        return f"{qty:.{decimals}f}"

    def _format_price(self, symbol: str, price: float) -> str:
        f = self._filters.get(symbol)
        decimals = self._decimals_from_step(f.tick_size) if f else 8
        # Snap to tick first, then format — guards against floating-point dust.
        if f and f.tick_size > 0:
            price = self._round_to_tick(price, f.tick_size)
        return f"{price:.{decimals}f}"

    @staticmethod
    def _build_sdk_client(
        raw_client: BinanceFuturesClient,
        settings: Settings,
    ) -> BinanceSDKClient:
        # python-binance accepts the raw secret string; our raw client stores
        # it as bytes after __init__. Decode back so the SDK can re-sign.
        secret = raw_client.api_secret
        if isinstance(secret, (bytes, bytearray)):
            secret = secret.decode("utf-8")
        return BinanceSDKClient(
            api_key=raw_client.api_key,
            api_secret=secret,
            testnet=settings.binance_testnet,
        )

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

    def _ensure_position_mode(self) -> None:
        """Force the account into ONE-WAY (net-position) mode.

        We deliberately run in one-way mode rather than hedge mode because:
          1. ``reduceOnly=True`` on SL/TP orders is rejected with -1106 in
             hedge mode (Binance treats ``positionSide`` as the exclusive
             reduce-only signal there). One-way lets us send the parameter
             explicitly — clearer intent + defence-in-depth.
          2. ``max_open_positions=1`` makes the hedge feature (LONG and SHORT
             on the same symbol simultaneously) unused anyway.
          3. Order payloads are simpler — no ``positionSide`` field on any
             order, and the same code path serves both LONG and SHORT
             positions.
        """
        if self._hedge_mode_set:
            return
        try:
            self.client.set_hedge_mode(False)
        except BinanceAPIError as exc:
            # Binance returns -4059 "No need to change position side" when the
            # account is already in the requested mode. Treat as non-fatal.
            logger.warning("Unable to set position mode explicitly: %s", exc)
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

            # One-way mode: no positionSide. Entry must NOT be reduceOnly —
            # this opens a new position.
            params = {
                "symbol": plan.symbol,
                "side": plan.side,
                "type": "LIMIT",
                "timeInForce": "GTX",
                "quantity": self._format_qty(plan.symbol, quantity),
                "price": self._format_price(plan.symbol, order_price),
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

    def _exit_limit_price(self, side: str, trigger_price: float, buffer_bps: float) -> float:
        """Derive the post-trigger limit price for a maker-mode bracket exit.

        For a SELL exit (closing a LONG), the post-only LIMIT must rest ABOVE
        the current bid once the stop fires, otherwise GTX rejects it as
        immediately marketable — so the limit sits `buffer_bps` above the
        trigger. For a BUY exit (closing a SHORT) the logic mirrors: limit
        rests `buffer_bps` below trigger to avoid crossing the ask.
        """
        offset = trigger_price * (buffer_bps / 10000.0)
        return trigger_price + offset if side == "SELL" else trigger_price - offset

    def _place_exit_orders(self, plan: TradePlan, quantity: float) -> Dict[str, object]:
        """Place SL + TP bracket as post-only stop-limit orders via python-binance.

        Binance migrated conditional orders (STOP/STOP_MARKET/TAKE_PROFIT/etc.)
        to the Algo Order endpoint on 2025-12-09; python-binance's
        ``futures_create_order`` auto-routes these types to ``/fapi/v1/algoOrder``,
        so we hand off the request without manually choosing the endpoint.

        Mode: ONE-WAY (net-position). The bot calls ``_ensure_position_mode``
        to force ``dualSidePosition=false``. Therefore we DO NOT pass
        ``positionSide`` here — that field is hedge-mode-only and Binance
        rejects it (or rejects explicit ``reduceOnly``) when both are sent.

        Why STOP + GTX instead of STOP_MARKET:
          * STOP_MARKET fires a TAKER market order on trigger — costs taker
            fees and accepts slippage during fast moves.
          * STOP with timeInForce=GTX places a post-only LIMIT on trigger —
            earns the maker rebate and avoids slippage, at the cost of "no
            fill if the market gaps through the limit." This matches the
            bot's ``maker_only: true`` posture (entries are also GTX).

        Why reduceOnly=True and explicit quantity:
          * ``reduceOnly=True`` is the one-way-mode signal that this order
            can only CLOSE an existing position, never open a new one. Acts
            as defence-in-depth against accidentally flipping the position.
          * ``closePosition=true`` requires an existing open position; with a
            resting GTX entry it returns -4509 (TIF GTE).
          * The entry is post-only and fills entirely or not at all under
            normal book conditions, so matching ``quantity`` to the entry
            size is safe.
        """
        # Direction of the SL/TP exit is opposite to the entry side. In one-way
        # mode, plan.position_side is just informational — what matters is the
        # entry side (BUY → close with SELL; SELL → close with BUY).
        exit_side = "SELL" if plan.side == "BUY" else "BUY"
        qty_str = self._format_qty(plan.symbol, quantity)

        sl_trigger = plan.stop_loss
        sl_limit = self._exit_limit_price(
            exit_side, sl_trigger, self.settings.stop_loss_buffer_bps
        )
        tp_trigger = plan.take_profit
        tp_limit = self._exit_limit_price(
            exit_side, tp_trigger, self.settings.take_profit_buffer_bps
        )

        stop_order = self._sdk_client.futures_create_order(
            symbol=plan.symbol,
            side=exit_side,
            type="STOP",
            timeInForce="GTX",
            quantity=qty_str,
            price=self._format_price(plan.symbol, sl_limit),
            stopPrice=self._format_price(plan.symbol, sl_trigger),
            reduceOnly=True,
            workingType="MARK_PRICE",
        )

        take_order = self._sdk_client.futures_create_order(
            symbol=plan.symbol,
            side=exit_side,
            type="TAKE_PROFIT",
            timeInForce="GTX",
            quantity=qty_str,
            price=self._format_price(plan.symbol, tp_limit),
            stopPrice=self._format_price(plan.symbol, tp_trigger),
            reduceOnly=True,
            workingType="MARK_PRICE",
        )

        return {"stop_order": stop_order, "take_order": take_order, "qty": quantity}

    def execute_trade_plan(
        self,
        plan: TradePlan,
        context: Optional[Dict[str, object]] = None,
    ) -> ExecutionResult:
        try:
            self._ensure_position_mode()
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
