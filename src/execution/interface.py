from __future__ import annotations

from typing import Dict, Protocol

from src.models import ExecutionResult, TradePlan


class ExecutionAdapter(Protocol):
    @property
    def open_positions_count(self) -> int:
        ...

    def execute_trade_plan(self, plan: TradePlan, context: Dict[str, object] | None = None) -> ExecutionResult:
        ...
