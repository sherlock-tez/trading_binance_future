from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

import pandas as pd

from src.models import SignalDiagnostics, TradePlan
from src.strategy.divergence import bearish_divergence, bullish_divergence
from src.strategy.indicators import atr, ema, macd, rsi
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
    # New params (all optional w/ defaults to keep production parity).
    atr_period: int = 14
    use_atr_stops: bool = False
    atr_sl_mult: float = 1.5
    atr_tp_mult: float = 3.0
    use_trend_filter: bool = False
    trend_ema_period: int = 200
    min_rr_ratio: float = 0.0
    max_sl_distance_pct: float = 0.0  # 0 disables
    rsi_long_max: float = 50.0
    rsi_short_min: float = 50.0
    require_macd_divergence: bool = True


class SignalEngine:
    def __init__(self, params: StrategyParams):
        self.params = params

    def _resolve_long_levels(
        self,
        entry_price: float,
        support: Optional[float],
        resistance: Optional[float],
        atr_value: float,
    ) -> Tuple[Optional[float], Optional[float]]:
        p = self.params
        if p.use_atr_stops and atr_value > 0:
            sl = entry_price - p.atr_sl_mult * atr_value
            tp = entry_price + p.atr_tp_mult * atr_value
            return sl, tp
        if support is None or resistance is None:
            return None, None
        sl = support * (1 - p.stop_loss_buffer_bps / 10000)
        tp = resistance * (1 - p.take_profit_buffer_bps / 10000)
        return sl, tp

    def _resolve_short_levels(
        self,
        entry_price: float,
        support: Optional[float],
        resistance: Optional[float],
        atr_value: float,
    ) -> Tuple[Optional[float], Optional[float]]:
        p = self.params
        if p.use_atr_stops and atr_value > 0:
            sl = entry_price + p.atr_sl_mult * atr_value
            tp = entry_price - p.atr_tp_mult * atr_value
            return sl, tp
        if support is None or resistance is None:
            return None, None
        sl = resistance * (1 + p.stop_loss_buffer_bps / 10000)
        tp = support * (1 + p.take_profit_buffer_bps / 10000)
        return sl, tp

    def _build_long_plan(
        self,
        symbol: str,
        signal_time: int,
        entry_price: float,
        sl: float,
        tp: float,
    ) -> TradePlan:
        return TradePlan(
            symbol=symbol,
            side="BUY",
            position_side="LONG",
            entry_price=entry_price,
            stop_loss=sl,
            take_profit=tp,
            signal_time=signal_time,
            metadata={"direction": "long"},
        )

    def _build_short_plan(
        self,
        symbol: str,
        signal_time: int,
        entry_price: float,
        sl: float,
        tp: float,
    ) -> TradePlan:
        return TradePlan(
            symbol=symbol,
            side="SELL",
            position_side="SHORT",
            entry_price=entry_price,
            stop_loss=sl,
            take_profit=tp,
            signal_time=signal_time,
            metadata={"direction": "short"},
        )

    def generate_signal(
        self,
        symbol: str,
        signal_frame_1h: pd.DataFrame,
        higher_timeframe_frames: Dict[str, pd.DataFrame],
    ) -> Tuple[Optional[TradePlan], SignalDiagnostics]:
        p = self.params
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
        high = signal_frame_1h["high"].astype(float)
        low = signal_frame_1h["low"].astype(float)
        rsi_series = rsi(close, period=p.rsi_period)
        macd_line, _, _ = macd(close, fast=p.macd_fast, slow=p.macd_slow, signal=p.macd_signal)
        atr_series = atr(high, low, close, period=p.atr_period)
        trend_ema = ema(close, period=p.trend_ema_period) if p.use_trend_filter else None

        rsi_bull = bullish_divergence(close, rsi_series, pivot_window=p.pivot_window, lookback=p.divergence_lookback)
        rsi_bear = bearish_divergence(close, rsi_series, pivot_window=p.pivot_window, lookback=p.divergence_lookback)
        macd_bull = bullish_divergence(close, macd_line, pivot_window=p.pivot_window, lookback=p.divergence_lookback)
        macd_bear = bearish_divergence(close, macd_line, pivot_window=p.pivot_window, lookback=p.divergence_lookback)

        supports, resistances = build_multi_timeframe_levels(
            higher_timeframe_frames,
            pivot_window=p.pivot_window,
        )

        last_price = float(close.iloc[-1])
        current_rsi = float(rsi_series.iloc[-1])
        atr_val = float(atr_series.iloc[-1]) if not atr_series.isna().iloc[-1] else 0.0
        support = nearest_support(supports, last_price)
        resistance = nearest_resistance(resistances, last_price)
        signal_time = int(signal_frame_1h["close_time"].iloc[-1])

        trend_up = True
        trend_down = True
        if trend_ema is not None:
            ema_val = float(trend_ema.iloc[-1])
            trend_up = last_price > ema_val
            trend_down = last_price < ema_val

        # Core gate (keep divergence + RSI extremity rule).
        long_div_ok = bool(rsi_bull.get("is_valid")) and (not p.require_macd_divergence or bool(macd_bull.get("is_valid")))
        short_div_ok = bool(rsi_bear.get("is_valid")) and (not p.require_macd_divergence or bool(macd_bear.get("is_valid")))

        long_rsi_ok = current_rsi < p.rsi_long_max
        short_rsi_ok = current_rsi > p.rsi_short_min

        long_sr_ok = support is not None and resistance is not None and last_price > support and resistance > last_price
        short_sr_ok = support is not None and resistance is not None and last_price < resistance and support < last_price

        long_ready = long_div_ok and long_rsi_ok and long_sr_ok and trend_up
        short_ready = short_div_ok and short_rsi_ok and short_sr_ok and trend_down

        decision = "no_signal"
        plan: Optional[TradePlan] = None

        if long_ready:
            sl, tp = self._resolve_long_levels(last_price, support, resistance, atr_val)
            if sl is not None and tp is not None and self._levels_valid_long(last_price, sl, tp):
                plan = self._build_long_plan(symbol=symbol, signal_time=signal_time, entry_price=last_price, sl=sl, tp=tp)
                decision = "long"
        elif short_ready:
            sl, tp = self._resolve_short_levels(last_price, support, resistance, atr_val)
            if sl is not None and tp is not None and self._levels_valid_short(last_price, sl, tp):
                plan = self._build_short_plan(symbol=symbol, signal_time=signal_time, entry_price=last_price, sl=sl, tp=tp)
                decision = "short"

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

    def _levels_valid_long(self, entry: float, sl: float, tp: float) -> bool:
        p = self.params
        if not (sl < entry < tp):
            return False
        risk = entry - sl
        reward = tp - entry
        if risk <= 0 or reward <= 0:
            return False
        if p.min_rr_ratio > 0 and (reward / risk) < p.min_rr_ratio:
            return False
        if p.max_sl_distance_pct > 0 and (risk / entry) > p.max_sl_distance_pct:
            return False
        return True

    def _levels_valid_short(self, entry: float, sl: float, tp: float) -> bool:
        p = self.params
        if not (tp < entry < sl):
            return False
        risk = sl - entry
        reward = entry - tp
        if risk <= 0 or reward <= 0:
            return False
        if p.min_rr_ratio > 0 and (reward / risk) < p.min_rr_ratio:
            return False
        if p.max_sl_distance_pct > 0 and (risk / entry) > p.max_sl_distance_pct:
            return False
        return True
