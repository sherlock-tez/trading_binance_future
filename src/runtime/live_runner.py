from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Set, Tuple

from src.config import Settings
from src.data.binance_api import BinanceFuturesClient
from src.data.binance_feed import BinanceMarketDataService, CandleEvent
from src.execution.binance_futures import BinanceFuturesExecutor
from src.notify.telegram import TelegramNotifier
from src.runtime.backtest_runner import _strategy_params_from_settings
from src.runtime.trade_cycle import run_trade_cycle
from src.strategy.signal_engine import SignalEngine
from src.utils.logging import get_logger

logger = get_logger(__name__)


BALANCE_ASSETS = {"USDC", "USDT"}


def _float_field(item: Dict[str, Any], key: str) -> float:
    try:
        return float(item.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _format_amount(value: float) -> str:
    return f"{value:,.4f}"


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
        self.engine = SignalEngine(_strategy_params_from_settings(settings))
        self._processed_signals: Set[Tuple[str, int]] = set()

    def _account_snapshot(self) -> Dict[str, object]:
        balances = self.client.get_balances()
        asset_rows: List[Dict[str, float | str]] = []

        for item in balances:
            asset = str(item.get("asset", "")).upper()
            if asset not in BALANCE_ASSETS:
                continue

            wallet_balance = _float_field(item, "balance")
            available_balance = _float_field(item, "availableBalance")
            unrealized_pnl = _float_field(item, "crossUnPnl")
            if wallet_balance == 0 and available_balance == 0 and unrealized_pnl == 0:
                continue

            asset_rows.append(
                {
                    "asset": asset,
                    "wallet_balance": wallet_balance,
                    "available_balance": available_balance,
                    "unrealized_pnl": unrealized_pnl,
                    "equity": wallet_balance + unrealized_pnl,
                }
            )

        wallet_balance = sum(float(row["wallet_balance"]) for row in asset_rows)
        available_balance = sum(float(row["available_balance"]) for row in asset_rows)
        unrealized_pnl = sum(float(row["unrealized_pnl"]) for row in asset_rows)

        return {
            "wallet_balance": wallet_balance,
            "available_balance": available_balance,
            "unrealized_pnl": unrealized_pnl,
            "equity": wallet_balance + unrealized_pnl,
            "assets": sorted(asset_rows, key=lambda row: str(row["asset"])),
        }

    def _format_lifecycle_message(
        self,
        event: str,
        snapshot: Dict[str, object] | None = None,
        error: str | None = None,
    ) -> str:
        mode = "TESTNET" if self.settings.binance_testnet else "MAINNET"
        lines = [
            f"Bot {event}",
            f"Mode: {mode}",
            f"Symbols: {', '.join(self.settings.symbols)}",
            f"Signal timeframe: {self.settings.signal_timeframe}",
            f"Leverage config: {self.settings.leverage}x",
            f"Position equity ratio: {self.settings.position_equity_ratio:.4f}",
            f"Max open positions: {self.settings.max_open_positions}",
        ]

        if error:
            lines.append(f"Account snapshot: unavailable ({error})")
            return "\n".join(lines)

        if snapshot is None:
            lines.append("Account snapshot: unavailable")
            return "\n".join(lines)

        lines.extend(
            [
                f"Wallet balance: {_format_amount(float(snapshot['wallet_balance']))}",
                f"Available balance: {_format_amount(float(snapshot['available_balance']))}",
                f"Unrealized PnL: {_format_amount(float(snapshot['unrealized_pnl']))}",
                f"Equity: {_format_amount(float(snapshot['equity']))}",
            ]
        )

        assets = snapshot.get("assets", [])
        if isinstance(assets, list) and assets:
            lines.append("Assets:")
            for row in assets:
                if not isinstance(row, dict):
                    continue
                lines.append(
                    " - "
                    f"{row['asset']}: "
                    f"wallet={_format_amount(float(row['wallet_balance']))}, "
                    f"available={_format_amount(float(row['available_balance']))}, "
                    f"unrealized={_format_amount(float(row['unrealized_pnl']))}, "
                    f"equity={_format_amount(float(row['equity']))}"
                )

        return "\n".join(lines)

    def _send_lifecycle_status(self, event: str) -> None:
        try:
            snapshot = self._account_snapshot()
        except Exception as exc:
            logger.warning("Unable to read account snapshot for %s notification: %s", event, exc)
            self.notifier.send(self._format_lifecycle_message(event, error=str(exc)))
            return

        self.notifier.send(self._format_lifecycle_message(event, snapshot=snapshot))

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

        self._send_lifecycle_status("STARTING")
        try:
            self.data_service.warmup(self.settings.symbols, all_timeframes, limit=600)

            self.notifier.send("Live runner started in maker-only mode")
            await self.data_service.stream_closed_klines(
                self.settings.symbols,
                self.settings.signal_timeframe,
                self._on_closed_candle,
            )
        finally:
            self._send_lifecycle_status("STOPPED")


def run_live(settings: Settings) -> None:
    runner = LiveTradingRunner(settings)
    asyncio.run(runner.start())
