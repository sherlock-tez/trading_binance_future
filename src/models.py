from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass(frozen=True)
class TradePlan:
    symbol: str
    side: str
    position_side: str
    entry_price: float
    stop_loss: float
    take_profit: float
    signal_time: int
    metadata: Dict[str, float | int | str | bool] = field(default_factory=dict)


@dataclass(frozen=True)
class SignalDiagnostics:
    symbol: str
    signal_time: int
    last_price: float
    rsi_value: float
    bullish_rsi_divergence: bool
    bearish_rsi_divergence: bool
    bullish_macd_divergence: bool
    bearish_macd_divergence: bool
    nearest_support: Optional[float]
    nearest_resistance: Optional[float]
    decision: str


@dataclass(frozen=True)
class ExecutionResult:
    accepted: bool
    reason: str
    order_id: Optional[str] = None
    details: Dict[str, float | int | str | bool] = field(default_factory=dict)
