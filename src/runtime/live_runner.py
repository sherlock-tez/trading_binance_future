from __future__ import annotations

import asyncio
from typing import Set, Tuple

from src.config import Settings
from src.data.binance_api import BinanceFuturesClient
from src.data.binance_feed import BinanceMarketDataService, CandleEvent
from src.execution.binance_futures import BinanceFuturesExecutor
from src.notify.telegram import TelegramNotifier
from src.runtime.trade_cycle import run_trade_cycle
from src.strategy.signal_engine import SignalEngine, StrategyParams
from src.utils.logging import get_logger

logger = get_logger(__name__)


class LiveTradingRunner:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = BinanceFuturesClient(
            api_key=settings.binance_futures_api_key,
            api_secret=settings.binance_futures_api_secret,
            testnet=settings.binance_testnet,
        )
        self.data_service = BinanceMarketDataService(self.client)
        self.executor = BinanceFuturesExecutor(self.client, settings)
        self.notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
        self.engine = SignalEngine(
            StrategyParams(
                rsi_period=settings.rsi_period,
                macd_fast=settings.macd_fast,
                macd_slow=settings.macd_slow,
                macd_signal=settings.macd_signal,
                divergence_lookback=settings.divergence_lookback,
                pivot_window=settings.pivot_window,
                stop_loss_buffer_bps=settings.stop_loss_buffer_bps,
                take_profit_buffer_bps=settings.take_profit_buffer_bps,
            )
        )
        self._processed_signals: Set[Tuple[str, int]] = set()

    async def _on_closed_candle(self, event: CandleEvent) -> None:
        symbol = event.symbol
        all_timeframes = [self.settings.signal_timeframe] + self.settings.sup_res_timeframes
        frames = self.data_service.refresh_symbol_timeframes(symbol, all_timeframes, limit=600)

        signal_frame = frames[self.settings.signal_timeframe]
        higher = {
            tf: frames[tf]
            for tf in self.settings.sup_res_timeframes
            if tf in frames
        }

        outcome = run_trade_cycle(
            symbol=symbol,
            signal_frame=signal_frame,
            higher_frames=higher,
            execution_timestamp=event.close_time,
            engine=self.engine,
            executor=self.executor,
            processed_signals=self._processed_signals,
        )

        self.notifier.send(
            "\n".join(
                [
                    f"Signal scan {symbol}",
                    f"Decision: {outcome.diagnostics.decision}",
                    f"RSI: {outcome.diagnostics.rsi_value:.2f}",
                    f"Support: {outcome.diagnostics.nearest_support}",
                    f"Resistance: {outcome.diagnostics.nearest_resistance}",
                ]
            )
        )

        if outcome.plan is None:
            return

        if outcome.skipped_duplicate:
            logger.info("Duplicate signal skipped %s", outcome.signal_key)
            return

        if outcome.execution is None:
            return

        self.notifier.send(
            "\n".join(
                [
                    f"Trade signal {symbol} {outcome.plan.metadata.get('direction', '')}",
                    f"Accepted: {outcome.execution.accepted}",
                    f"Reason: {outcome.execution.reason}",
                    f"Entry: {outcome.plan.entry_price:.4f}",
                    f"SL: {outcome.plan.stop_loss:.4f}",
                    f"TP: {outcome.plan.take_profit:.4f}",
                ]
            )
        )

    async def start(self) -> None:
        all_timeframes = [self.settings.signal_timeframe] + self.settings.sup_res_timeframes
        self.data_service.warmup(self.settings.symbols, all_timeframes, limit=600)

        self.notifier.send("Live runner started in maker-only mode")
        await self.data_service.stream_closed_klines(
            self.settings.symbols,
            self.settings.signal_timeframe,
            self._on_closed_candle,
        )


def run_live(settings: Settings) -> None:
    runner = LiveTradingRunner(settings)
    asyncio.run(runner.start())
