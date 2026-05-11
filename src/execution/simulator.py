from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Dict, List, Optional

from src.config import Settings
from src.models import ExecutionResult, TradePlan


@dataclass
class SimPosition:
    symbol: str
    side: str
    entry_price: float
    stop_loss: float
    take_profit: float
    quantity: float
    opened_at: int


@dataclass
class ClosedTrade:
    symbol: str
    side: str
    opened_at: int
    closed_at: int
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    pnl_pct: float
    close_reason: str


class SimulatedExecutionAdapter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.equity = settings.initial_balance
        self.position: Optional[SimPosition] = None
        self.closed_trades: List[ClosedTrade] = []
        self.equity_curve: List[float] = [settings.initial_balance]
        self.entry_attempts = 0
        self.entry_fills = 0

    @property
    def open_positions_count(self) -> int:
        return 1 if self.position is not None else 0

    def _notional(self) -> float:
        return self.equity * self.settings.position_equity_ratio * self.settings.leverage

    def _quantity(self, entry_price: float) -> float:
        return self._notional() / max(entry_price, 1e-12)

    def execute_trade_plan(
        self,
        plan: TradePlan,
        context: Dict[str, object] | None = None,
    ) -> ExecutionResult:
        self.entry_attempts += 1
        current_positions = self.open_positions_count
        if current_positions >= self.settings.max_open_positions:
            return ExecutionResult(
                accepted=False,
                reason="max_open_positions_reached",
                details={"current_positions": current_positions},
            )

        signal_time = int((context or {}).get("timestamp", plan.signal_time))
        qty = self._quantity(plan.entry_price)
        if qty <= 0:
            return ExecutionResult(
                accepted=False,
                reason="quantity_too_small",
                details={"entry_price": plan.entry_price, "equity": self.equity},
            )

        self.position = SimPosition(
            symbol=plan.symbol,
            side=plan.side,
            entry_price=plan.entry_price,
            stop_loss=plan.stop_loss,
            take_profit=plan.take_profit,
            quantity=qty,
            opened_at=signal_time,
        )

        entry_fee = plan.entry_price * qty * self.settings.maker_fee_rate
        self.equity -= entry_fee
        self.entry_fills += 1
        self.equity_curve.append(self.equity)

        return ExecutionResult(
            accepted=True,
            reason="sim_entry_opened",
            details={"quantity": qty, "entry_fee": entry_fee},
        )

    def on_bar(self, symbol: str, timestamp: int, high: float, low: float, close: float) -> None:
        if self.position is None:
            return
        if self.position.symbol != symbol:
            return

        exit_price: Optional[float] = None
        close_reason: Optional[str] = None

        if self.position.side == "BUY":
            # Conservative assumption: if both touched, stop loss triggers first.
            if low <= self.position.stop_loss:
                exit_price = self.position.stop_loss
                close_reason = "stop_loss"
            elif high >= self.position.take_profit:
                exit_price = self.position.take_profit
                close_reason = "take_profit"
        else:
            if high >= self.position.stop_loss:
                exit_price = self.position.stop_loss
                close_reason = "stop_loss"
            elif low <= self.position.take_profit:
                exit_price = self.position.take_profit
                close_reason = "take_profit"

        if exit_price is None:
            return

        self._close_position(timestamp, exit_price, close_reason)

    def force_close_all(self, timestamp: int, mark_prices: Dict[str, float]) -> None:
        if self.position is None:
            return
        price = mark_prices.get(self.position.symbol, self.position.entry_price)
        self._close_position(timestamp, price, "end_of_window")

    def _close_position(self, timestamp: int, exit_price: float, close_reason: str) -> None:
        assert self.position is not None
        pos = self.position

        if pos.side == "BUY":
            gross_pnl = (exit_price - pos.entry_price) * pos.quantity
        else:
            gross_pnl = (pos.entry_price - exit_price) * pos.quantity

        exit_fee = exit_price * pos.quantity * self.settings.maker_fee_rate
        net_pnl = gross_pnl - exit_fee

        self.equity += net_pnl
        self.equity_curve.append(self.equity)

        trade_notional = pos.entry_price * pos.quantity
        pnl_pct = net_pnl / max(trade_notional, 1e-12) * 100

        self.closed_trades.append(
            ClosedTrade(
                symbol=pos.symbol,
                side=pos.side,
                opened_at=pos.opened_at,
                closed_at=timestamp,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                quantity=pos.quantity,
                pnl=net_pnl,
                pnl_pct=pnl_pct,
                close_reason=close_reason,
            )
        )
        self.position = None

    def metrics(self) -> Dict[str, float | int]:
        trade_count = len(self.closed_trades)
        wins = sum(1 for trade in self.closed_trades if trade.pnl > 0)
        win_rate = (wins / trade_count * 100) if trade_count else 0.0

        total_return = (self.equity - self.settings.initial_balance) / self.settings.initial_balance * 100

        drawdown = 0.0
        peak = self.equity_curve[0] if self.equity_curve else self.settings.initial_balance
        for value in self.equity_curve:
            peak = max(peak, value)
            dd = (peak - value) / max(peak, 1e-12)
            drawdown = max(drawdown, dd)

        pnl_pcts = [trade.pnl_pct for trade in self.closed_trades]
        sharpe = 0.0
        if len(pnl_pcts) >= 2:
            avg = mean(pnl_pcts)
            std = pstdev(pnl_pcts)
            if std > 0:
                sharpe = (avg / std) * math.sqrt(len(pnl_pcts))

        maker_fill_ratio = (
            self.entry_fills / self.entry_attempts if self.entry_attempts > 0 else 0.0
        )

        return {
            "final_equity": round(self.equity, 2),
            "total_return_pct": round(total_return, 2),
            "max_drawdown_pct": round(drawdown * 100, 2),
            "win_rate_pct": round(win_rate, 2),
            "sharpe": round(sharpe, 3),
            "trade_count": trade_count,
            "maker_fill_ratio": round(maker_fill_ratio, 3),
            "maker_reject_count": int(max(self.entry_attempts - self.entry_fills, 0)),
        }
