from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Set, Tuple

from src.config import Settings
from src.data.binance_api import BinanceFuturesClient
from src.data.binance_feed import BinanceMarketDataService, CandleEvent
from src.execution.binance_futures import BinanceFuturesExecutor
from src.notify.telegram import TelegramNotifier, escape_html
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


def _pnl_icon(value: float) -> str:
    if value > 0:
        return "🟢"
    if value < 0:
        return "🔴"
    return "⚪"


def _direction_icon(direction: str) -> str:
    normalized = direction.strip().upper()
    if normalized in {"LONG", "BUY"}:
        return "🟢⬆️"
    if normalized in {"SHORT", "SELL"}:
        return "🔴⬇️"
    return "⚪"


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
        event_icon = {"STARTING": "🟢", "STOPPED": "🔴"}.get(event.upper(), "ℹ️")
        mode_icon = "🧪" if self.settings.binance_testnet else "🌐"
        lines = [
            f"{event_icon} <b>Bot {escape_html(event)}</b>",
            f"{mode_icon} Mode: <b>{mode}</b>",
            f"📈 Symbols: <b>{escape_html(', '.join(self.settings.symbols))}</b>",
            f"⏱ Signal timeframe: <b>{escape_html(self.settings.signal_timeframe)}</b>",
            f"⚙️ Leverage: <b>{self.settings.leverage}x</b>",
            f"📐 Position equity ratio: <code>{self.settings.position_equity_ratio:.4f}</code>",
            f"🔢 Max open positions: <b>{self.settings.max_open_positions}</b>",
        ]

        if error:
            lines.append(f"⚠️ Account snapshot: <i>unavailable ({escape_html(error)})</i>")
            return "\n".join(lines)

        if snapshot is None:
            lines.append("⚠️ Account snapshot: <i>unavailable</i>")
            return "\n".join(lines)

        unrealized = float(snapshot["unrealized_pnl"])
        lines.extend(
            [
                "",
                "💰 <b>Account</b>",
                f"  💵 Wallet: <code>{_format_amount(float(snapshot['wallet_balance']))}</code>",
                f"  🟦 Available: <code>{_format_amount(float(snapshot['available_balance']))}</code>",
                f"  {_pnl_icon(unrealized)} Unrealized PnL: <code>{_format_amount(unrealized)}</code>",
                f"  🏦 Equity: <code>{_format_amount(float(snapshot['equity']))}</code>",
            ]
        )

        assets = snapshot.get("assets", [])
        if isinstance(assets, list) and assets:
            lines.append("📦 <b>Assets</b>")
            for row in assets:
                if not isinstance(row, dict):
                    continue
                row_pnl = float(row["unrealized_pnl"])
                lines.append(
                    f"  • <b>{escape_html(row['asset'])}</b> — "
                    f"wallet=<code>{_format_amount(float(row['wallet_balance']))}</code>, "
                    f"avail=<code>{_format_amount(float(row['available_balance']))}</code>, "
                    f"{_pnl_icon(row_pnl)} uPnL=<code>{_format_amount(row_pnl)}</code>, "
                    f"eq=<code>{_format_amount(float(row['equity']))}</code>"
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

    def _alert_stream(self, message: str) -> None:
        logger.warning("stream alert: %s", message)
        try:
            self.notifier.send(
                f"📡 <b>Market data stream</b>\n⚠️ <i>{escape_html(message)}</i>"
            )
        except Exception:
            logger.exception("failed to send stream alert")

    async def _on_closed_candle(self, event: CandleEvent) -> None:
        logger.info(
            "[live] closed candle %s %s | open_time=%s close_time=%s close=%s",
            event.symbol,
            event.timeframe,
            event.open_time,
            event.close_time,
            event.close_price,
        )
        try:
            await self._process_closed_candle(event)
        except Exception as exc:
            logger.exception(
                "Closed-candle processing failed for %s: %s", event.symbol, exc
            )
            self.notifier.send(
                "\n".join(
                    [
                        f"⚠️ <b>Signal cycle error</b> — <b>{escape_html(event.symbol)}</b>",
                        f"🕒 Candle close: <code>{event.close_time}</code>",
                        f"❗ <i>{escape_html(type(exc).__name__)}: {escape_html(exc)}</i>",
                    ]
                )
            )

    async def _process_closed_candle(self, event: CandleEvent) -> None:
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
                    f"🔍 <b>Signal Scan</b> — <b>{escape_html(symbol)}</b>",
                    f"🧭 Decision: <b>{escape_html(outcome.diagnostics.decision)}</b>",
                    f"📊 RSI: <code>{outcome.diagnostics.rsi_value:.2f}</code>",
                    f"🟩 Support: <code>{escape_html(outcome.diagnostics.nearest_support)}</code>",
                    f"🟥 Resistance: <code>{escape_html(outcome.diagnostics.nearest_resistance)}</code>",
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

        direction = str(outcome.plan.metadata.get("direction", ""))
        accepted = outcome.execution.accepted
        accept_icon = "✅" if accepted else "❌"
        self.notifier.send(
            "\n".join(
                [
                    f"🚨 <b>Trade Signal</b> — <b>{escape_html(symbol)}</b> "
                    f"{_direction_icon(direction)} <b>{escape_html(direction)}</b>",
                    f"{accept_icon} Accepted: <b>{accepted}</b>",
                    f"📝 Reason: <i>{escape_html(outcome.execution.reason)}</i>",
                    f"🎯 Entry: <code>{outcome.plan.entry_price:.4f}</code>",
                    f"🛑 SL: <code>{outcome.plan.stop_loss:.4f}</code>",
                    f"🏁 TP: <code>{outcome.plan.take_profit:.4f}</code>",
                ]
            )
        )

    async def start(self) -> None:
        all_timeframes = [self.settings.signal_timeframe] + self.settings.sup_res_timeframes

        self._send_lifecycle_status("STARTING")
        try:
            self.data_service.warmup(self.settings.symbols, all_timeframes, limit=600)

            self.notifier.send("🚀 <i>Live runner started in maker-only mode</i>")
            await self.data_service.stream_closed_klines(
                self.settings.symbols,
                self.settings.signal_timeframe,
                self._on_closed_candle,
                on_error=self._alert_stream,
                mode=self.settings.market_data_mode,
                staleness_timeout=self.settings.ws_staleness_timeout,
                stream_path_mode=self.settings.ws_stream_path_mode,
                rest_fallback_after=self.settings.ws_rest_fallback_after,
                rest_poll_seconds=self.settings.ws_rest_poll_seconds,
                recover_probe_seconds=self.settings.ws_recover_probe_seconds,
            )
        finally:
            self._send_lifecycle_status("STOPPED")


def run_live(settings: Settings) -> None:
    runner = LiveTradingRunner(settings)
    asyncio.run(runner.start())
