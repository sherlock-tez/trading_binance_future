from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Set, Tuple

import pandas as pd

from src.execution.interface import ExecutionAdapter
from src.models import ExecutionResult, SignalDiagnostics, TradePlan
from src.strategy.signal_engine import SignalEngine


@dataclass(frozen=True)
class TradeCycleOutcome:
    symbol: str
    diagnostics: SignalDiagnostics
    plan: Optional[TradePlan]
    execution: Optional[ExecutionResult]
    signal_key: Optional[Tuple[str, int]]
    skipped_duplicate: bool


def run_trade_cycle(
    *,
    symbol: str,
    signal_frame: pd.DataFrame,
    higher_frames: Dict[str, pd.DataFrame],
    execution_timestamp: int,
    engine: SignalEngine,
    executor: ExecutionAdapter,
    processed_signals: Optional[Set[Tuple[str, int]]] = None,
) -> TradeCycleOutcome:
    plan, diagnostics = engine.generate_signal(symbol, signal_frame, higher_frames)

    if plan is None:
        return TradeCycleOutcome(
            symbol=symbol,
            diagnostics=diagnostics,
            plan=None,
            execution=None,
            signal_key=None,
            skipped_duplicate=False,
        )

    signal_key = (symbol, diagnostics.signal_time)
    if processed_signals is not None and signal_key in processed_signals:
        return TradeCycleOutcome(
            symbol=symbol,
            diagnostics=diagnostics,
            plan=plan,
            execution=None,
            signal_key=signal_key,
            skipped_duplicate=True,
        )

    if processed_signals is not None:
        processed_signals.add(signal_key)

    execution = executor.execute_trade_plan(plan, context={"timestamp": execution_timestamp})
    return TradeCycleOutcome(
        symbol=symbol,
        diagnostics=diagnostics,
        plan=plan,
        execution=execution,
        signal_key=signal_key,
        skipped_duplicate=False,
    )
