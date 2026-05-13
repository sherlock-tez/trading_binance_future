from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import pandas as pd

from src.models import SignalDiagnostics, TradePlan
from src.strategy.divergence import bearish_divergence, bullish_divergence
from src.strategy.indicators import macd, rsi
from src.strategy.support_resistance import (
    build_multi_timeframe_levels,
    nearest_resistance,
    nearest_support,
)


@dataclass(frozen=True)
class StrategyParams:
    rsi_period: int
    macd_fast: int
    macd_slow: int
    macd_signal: int
    divergence_lookback: int
    pivot_window: int
    stop_loss_buffer_bps: float
    take_profit_buffer_bps: float


class SignalEngine:
    def __init__(self, params: StrategyParams):
        self.params = params

    def _build_long_plan(
        self,
        symbol: str,
        signal_time: int,
        entry_price: float,
        support: float,
        resistance: float,
    ) -> Optional[TradePlan]:
        sl = support * (1 - self.params.stop_loss_buffer_bps / 10000)
        risk_distance = entry_price - sl
        if risk_distance <= 0:
            return None

        tp = entry_price + (2.0 * risk_distance)
        if tp <= entry_price:
            return None

        return TradePlan(
            symbol=symbol,
            side="BUY",
            position_side="LONG",
            entry_price=entry_price,
            stop_loss=sl,
            take_profit=tp,
            signal_time=signal_time,
            metadata={
                "direction": "long",
                "risk_reward_ratio": 2.0,
                "risk_distance": risk_distance,
                "reward_distance": tp - entry_price,
            },
        )

    def _build_short_plan(
        self,
        symbol: str,
        signal_time: int,
        entry_price: float,
        support: float,
        resistance: float,
    ) -> Optional[TradePlan]:
        sl = resistance * (1 + self.params.stop_loss_buffer_bps / 10000)
        risk_distance = sl - entry_price
        if risk_distance <= 0:
            return None

        tp = entry_price - (2.0 * risk_distance)
        if tp >= entry_price:
            return None

        return TradePlan(
            symbol=symbol,
            side="SELL",
            position_side="SHORT",
            entry_price=entry_price,
            stop_loss=sl,
            take_profit=tp,
            signal_time=signal_time,
            metadata={
                "direction": "short",
                "risk_reward_ratio": 2.0,
                "risk_distance": risk_distance,
                "reward_distance": entry_price - tp,
            },
        )

    def generate_signal(
        self,
        symbol: str,
        signal_frame_1h: pd.DataFrame,
        higher_timeframe_frames: Dict[str, pd.DataFrame],
    ) -> Tuple[Optional[TradePlan], SignalDiagnostics]:
        if signal_frame_1h.empty or len(signal_frame_1h) < 100:
            diagnostics = SignalDiagnostics(
                symbol=symbol,
                signal_time=0,
                last_price=0.0,
                rsi_value=50.0,
                bullish_rsi_divergence=False,
                bearish_rsi_divergence=False,
                bullish_macd_divergence=False,
                bearish_macd_divergence=False,
                nearest_support=None,
                nearest_resistance=None,
                decision="insufficient_data",
            )
            return None, diagnostics

        close = signal_frame_1h["close"].astype(float)
        rsi_series = rsi(close, period=self.params.rsi_period)
        macd_line, _, _ = macd(
            close,
            fast=self.params.macd_fast,
            slow=self.params.macd_slow,
            signal=self.params.macd_signal,
        )

        rsi_bull = bullish_divergence(
            close,
            rsi_series,
            pivot_window=self.params.pivot_window,
            lookback=self.params.divergence_lookback,
        )
        rsi_bear = bearish_divergence(
            close,
            rsi_series,
            pivot_window=self.params.pivot_window,
            lookback=self.params.divergence_lookback,
        )
        macd_bull = bullish_divergence(
            close,
            macd_line,
            pivot_window=self.params.pivot_window,
            lookback=self.params.divergence_lookback,
        )
        macd_bear = bearish_divergence(
            close,
            macd_line,
            pivot_window=self.params.pivot_window,
            lookback=self.params.divergence_lookback,
        )

        supports, resistances = build_multi_timeframe_levels(
            higher_timeframe_frames,
            pivot_window=self.params.pivot_window,
        )

        last_price = float(close.iloc[-1])
        current_rsi = float(rsi_series.iloc[-1])
        support = nearest_support(supports, last_price)
        resistance = nearest_resistance(resistances, last_price)
        signal_time = int(signal_frame_1h["close_time"].iloc[-1])

        long_ready = (
            bool(rsi_bull.get("is_valid"))
            and bool(macd_bull.get("is_valid"))
            and current_rsi < 50
            and support is not None
            and resistance is not None
            and last_price > support
            and resistance > last_price
        )

        short_ready = (
            bool(rsi_bear.get("is_valid"))
            and bool(macd_bear.get("is_valid"))
            and current_rsi > 50
            and support is not None
            and resistance is not None
            and last_price < resistance
            and support < last_price
        )

        decision = "no_signal"
        plan: Optional[TradePlan] = None
        if long_ready and support is not None and resistance is not None:
            plan = self._build_long_plan(
                symbol=symbol,
                signal_time=signal_time,
                entry_price=last_price,
                support=support,
                resistance=resistance,
            )
            decision = "long" if plan is not None else "invalid_risk_reward"
        elif short_ready and support is not None and resistance is not None:
            plan = self._build_short_plan(
                symbol=symbol,
                signal_time=signal_time,
                entry_price=last_price,
                support=support,
                resistance=resistance,
            )
            decision = "short" if plan is not None else "invalid_risk_reward"

        diagnostics = SignalDiagnostics(
            symbol=symbol,
            signal_time=signal_time,
            last_price=last_price,
            rsi_value=current_rsi,
            bullish_rsi_divergence=bool(rsi_bull.get("is_valid")),
            bearish_rsi_divergence=bool(rsi_bear.get("is_valid")),
            bullish_macd_divergence=bool(macd_bull.get("is_valid")),
            bearish_macd_divergence=bool(macd_bear.get("is_valid")),
            nearest_support=support,
            nearest_resistance=resistance,
            decision=decision,
        )
        return plan, diagnostics
