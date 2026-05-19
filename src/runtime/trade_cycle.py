from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Set, Tuple

import pandas as pd

from src.execution.interface import ExecutionAdapter
from src.models import ExecutionResult, SignalDiagnostics, TradePlan
from src.strategy.signal_engine import SignalEngine
from src.utils.logging import get_logger

logger = get_logger(__name__)


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
    logger.info(
        "[trade_cycle] %s start | exec_ts=%s | signal_rows=%d | higher_frames=%s",
        symbol,
        execution_timestamp,
        len(signal_frame),
        {tf: len(df) for tf, df in higher_frames.items()},
    )

    plan, diagnostics = engine.generate_signal(symbol, signal_frame, higher_frames)

    logger.info(
        "[trade_cycle] %s diagnostics | decision=%s rsi=%.2f last_price=%s "
        "support=%s resistance=%s | rsi_div(bull=%s,bear=%s) macd_div(bull=%s,bear=%s) "
        "signal_time=%s",
        symbol,
        diagnostics.decision,
        diagnostics.rsi_value,
        diagnostics.last_price,
        diagnostics.nearest_support,
        diagnostics.nearest_resistance,
        diagnostics.bullish_rsi_divergence,
        diagnostics.bearish_rsi_divergence,
        diagnostics.bullish_macd_divergence,
        diagnostics.bearish_macd_divergence,
        diagnostics.signal_time,
    )

    if plan is None:
        logger.info("[trade_cycle] %s outcome=NO_PLAN (no actionable signal)", symbol)
        return TradeCycleOutcome(
            symbol=symbol,
            diagnostics=diagnostics,
            plan=None,
            execution=None,
            signal_key=None,
            skipped_duplicate=False,
        )

    logger.info(
        "[trade_cycle] %s PLAN | side=%s position_side=%s entry=%s sl=%s tp=%s metadata=%s",
        symbol,
        plan.side,
        plan.position_side,
        plan.entry_price,
        plan.stop_loss,
        plan.take_profit,
        plan.metadata,
    )

    signal_key_name = symbol
    signal_key_time = diagnostics.signal_time
    if "signal_pivot_time" in plan.metadata:
        signal_key_name = f"{symbol}:{plan.metadata.get('direction', '')}"
        signal_key_time = int(plan.metadata["signal_pivot_time"])
    signal_key = (signal_key_name, signal_key_time)
    if processed_signals is not None and signal_key in processed_signals:
        logger.info(
            "[trade_cycle] %s outcome=SKIPPED_DUPLICATE | signal_key=%s",
            symbol,
            signal_key,
        )
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

    logger.info(
        "[trade_cycle] %s executing trade plan | signal_key=%s", symbol, signal_key
    )
    execution = executor.execute_trade_plan(plan, context={"timestamp": execution_timestamp})
    logger.info(
        "[trade_cycle] %s outcome=EXECUTED | accepted=%s reason=%s order_id=%s",
        symbol,
        execution.accepted,
        execution.reason,
        execution.order_id,
    )
    return TradeCycleOutcome(
        symbol=symbol,
        diagnostics=diagnostics,
        plan=plan,
        execution=execution,
        signal_key=signal_key,
        skipped_duplicate=False,
    )
