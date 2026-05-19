from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pandas as pd

from src.config import Settings
from src.data.binance_api import BinanceFuturesClient
from src.data.binance_feed import klines_to_frame
from src.execution.simulator import SimulatedExecutionAdapter
from src.runtime.trade_cycle import run_trade_cycle
from src.strategy.signal_engine import SignalEngine, StrategyParams
from src.utils.logging import get_logger
from src.utils.timeframe import needs_resample, normalize_timeframe, resample_ohlcv


TRADE_HISTORY_FIELDS = [
    "symbol",
    "side",
    "opened_at_ms",
    "opened_at_utc",
    "closed_at_ms",
    "closed_at_utc",
    "entry_price",
    "stop_loss",
    "take_profit",
    "exit_price",
    "quantity",
    "pnl",
    "pnl_pct",
    "close_reason",
]


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def write_trade_history(
    history: List[Dict[str, float | int | str]],
    *,
    history_dir: str,
    loop_id: str,
    months: int,
) -> Path:
    """Write per-trade history to {history_dir}/{loop_id}/{months}m.csv.

    `history_dir` is resolved relative to the project root (where config.yaml lives) so
    files always land in the same place regardless of where the script is invoked from.
    `loop_id` must follow the `Loop_{YYYYMMDD}_{iter}` convention from AGENTS.md.
    Returns the path written. Overwrites any prior file for the same loop+window so each
    backtest rerun under the same Loop reflects the latest configuration.
    """
    base = Path(history_dir)
    if not base.is_absolute():
        base = PROJECT_ROOT / base
    out_dir = base / loop_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{months}m.csv"

    # Enrich with UTC ISO timestamps for readability.
    enriched: List[Dict[str, float | int | str]] = []
    for row in history:
        opened_ms = int(row["opened_at_ms"])
        closed_ms = int(row["closed_at_ms"])
        enriched.append(
            {
                **row,
                "opened_at_utc": pd.Timestamp(opened_ms, unit="ms", tz="UTC").isoformat(),
                "closed_at_utc": pd.Timestamp(closed_ms, unit="ms", tz="UTC").isoformat(),
            }
        )

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRADE_HISTORY_FIELDS)
        writer.writeheader()
        for row in enriched:
            writer.writerow({k: row.get(k, "") for k in TRADE_HISTORY_FIELDS})
    return path

logger = get_logger(__name__)


def _strategy_params_from_settings(settings: Settings) -> StrategyParams:
    return StrategyParams(
        rsi_period=settings.rsi_period,
        macd_fast=settings.macd_fast,
        macd_slow=settings.macd_slow,
        macd_signal=settings.macd_signal,
        divergence_lookback=settings.divergence_lookback,
        pivot_window=settings.pivot_window,
        stop_loss_buffer_bps=settings.stop_loss_buffer_bps,
        take_profit_buffer_bps=settings.take_profit_buffer_bps,
        atr_period=settings.atr_period,
        use_atr_stops=settings.use_atr_stops,
        atr_sl_mult=settings.atr_sl_mult,
        atr_tp_mult=settings.atr_tp_mult,
        use_trend_filter=settings.use_trend_filter,
        trend_ema_period=settings.trend_ema_period,
        min_rr_ratio=settings.min_rr_ratio,
        max_sl_distance_pct=settings.max_sl_distance_pct,
        rsi_long_max=settings.rsi_long_max,
        rsi_short_min=settings.rsi_short_min,
        require_macd_divergence=settings.require_macd_divergence,
    )


@dataclass(frozen=True)
class BacktestWindowResult:
    months: int
    symbol_count: int
    metrics: Dict[str, float | int]


class BacktestRunner:
    # Number of months of additional history to download BEFORE the requested
    # window starts. Ensures slow indicators (200-EMA, divergence lookback, ATR)
    # are fully warmed at every timestamp inside the window — matching live
    # production where the bot has been running continuously.
    WARMUP_MONTHS = 12

    def __init__(self, settings: Settings):
        self.settings = settings
        # Historical klines must come from mainnet — testnet kline history is
        # sparse/synthetic and produces meaningless backtest results. The
        # binance.testnet flag still controls live order placement elsewhere.
        self.client = BinanceFuturesClient(
            api_key=settings.binance_futures_api_key,
            api_secret=settings.binance_futures_api_secret,
            testnet=False,
        )
        self.engine = SignalEngine(_strategy_params_from_settings(settings))

    def _months_ago_ms(self, months: int) -> int:
        now = pd.Timestamp.utcnow().tz_localize(None)
        start = now - pd.DateOffset(months=months)
        return int(start.timestamp() * 1000)

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    def _download_klines_range(
        self,
        symbol: str,
        timeframe: str,
        start_time: int,
        end_time: int,
    ) -> pd.DataFrame:
        tf = normalize_timeframe(timeframe)
        if needs_resample(tf):
            base = self._download_klines_range(symbol, "1h", start_time, end_time)
            return resample_ohlcv(base, tf)

        all_rows: List[List[object]] = []
        cursor = start_time

        while True:
            chunk = self.client.get_klines(
                symbol,
                tf,
                start_time=cursor,
                end_time=end_time,
                limit=1000,
            )
            if not chunk:
                break
            all_rows.extend(chunk)
            last_open = int(chunk[-1][0])
            if last_open >= end_time or len(chunk) < 1000:
                break
            cursor = last_open + 1

        frame = klines_to_frame(all_rows)
        if not frame.empty:
            frame = frame.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
        return frame

    def _prepare_data(self, months: int) -> Dict[str, Dict[str, pd.DataFrame]]:
        # Download an additional WARMUP_MONTHS of pre-window history so indicators
        # are fully primed before the first in-window timestamp. Trade-cycle calls
        # in _run_window are gated to fire only at t >= window_start_ms.
        start_ms = self._months_ago_ms(months + self.WARMUP_MONTHS)
        end_ms = self._now_ms()

        data: Dict[str, Dict[str, pd.DataFrame]] = {}
        all_tfs = [self.settings.signal_timeframe] + self.settings.sup_res_timeframes

        for symbol in self.settings.symbols:
            data[symbol] = {}
            for timeframe in all_tfs:
                frame = self._download_klines_range(symbol, timeframe, start_ms, end_ms)
                data[symbol][timeframe] = frame
                logger.info(
                    "Loaded %s %s rows=%s for %s months",
                    symbol,
                    timeframe,
                    len(frame),
                    months,
                )
        return data

    @staticmethod
    def _row_lookup(frame: pd.DataFrame) -> Dict[int, Dict[str, float | int]]:
        out: Dict[int, Dict[str, float | int]] = {}
        for _, row in frame.iterrows():
            key = int(row["open_time"])
            out[key] = {
                "open_time": key,
                "close_time": int(row["close_time"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
        return out

    def _run_window(self, months: int) -> BacktestWindowResult:
        data = self._prepare_data(months)
        window_start_ms = self._months_ago_ms(months)
        simulator = SimulatedExecutionAdapter(self.settings)
        processed_signals: Set[Tuple[str, int]] = set()

        signal_rows_by_symbol: Dict[str, Dict[int, Dict[str, float | int]]] = {}
        timeline_set = set()

        for symbol in self.settings.symbols:
            signal_frame = data[symbol][self.settings.signal_timeframe]
            lookup = self._row_lookup(signal_frame)
            signal_rows_by_symbol[symbol] = lookup
            timeline_set.update(lookup.keys())

        timeline = sorted(timeline_set)

        for t in timeline:
            for symbol in self.settings.symbols:
                row = signal_rows_by_symbol[symbol].get(t)
                if row:
                    simulator.on_bar(
                        symbol,
                        t,
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                    )

            # Warmup region: let indicators build history but do not generate
            # new entry signals or open positions until we cross window_start_ms.
            if t < window_start_ms:
                continue

            for symbol in self.settings.symbols:
                row = signal_rows_by_symbol[symbol].get(t)
                if not row:
                    continue

                signal_frame = data[symbol][self.settings.signal_timeframe]
                # Trailing window must match live exactly: live re-fetches only
                # the last `frame_lookback` candles per timeframe each cycle, so
                # the backtest slices to the same tail or its S/R (windowed
                # extremes) would diverge from production.
                lookback = self.settings.frame_lookback
                signal_slice = signal_frame[signal_frame["open_time"] <= t].tail(lookback)
                if len(signal_slice) < 100:
                    continue

                higher_slices = {}
                for timeframe in self.settings.sup_res_timeframes:
                    frame = data[symbol][timeframe]
                    higher_slices[timeframe] = (
                        frame[frame["open_time"] <= t].tail(lookback)
                    )

                outcome = run_trade_cycle(
                    symbol=symbol,
                    signal_frame=signal_slice,
                    higher_frames=higher_slices,
                    execution_timestamp=int(row["close_time"]),
                    engine=self.engine,
                    executor=simulator,
                    processed_signals=processed_signals,
                )
                if outcome.plan is None or outcome.skipped_duplicate:
                    continue

        last_prices = {}
        if timeline:
            final_time = timeline[-1]
            for symbol in self.settings.symbols:
                row = signal_rows_by_symbol[symbol].get(final_time)
                if row:
                    last_prices[symbol] = float(row["close"])
            simulator.force_close_all(final_time, last_prices)

        metrics = simulator.metrics()
        history = simulator.trade_history()
        history_path = write_trade_history(
            history,
            history_dir=self.settings.backtest_history_dir,
            loop_id=self.settings.loop_id,
            months=months,
        )
        logger.info("Wrote %s trades to %s", len(history), history_path)
        return BacktestWindowResult(months=months, symbol_count=len(self.settings.symbols), metrics=metrics)

    def run_windows(self, month_windows: List[int]) -> List[BacktestWindowResult]:
        results: List[BacktestWindowResult] = []
        for months in month_windows:
            logger.info("Running backtest for %s months", months)
            results.append(self._run_window(months))
        return results


def print_backtest_results(results: List[BacktestWindowResult]) -> None:
    serializable = [
        {
            "months": item.months,
            "symbol_count": item.symbol_count,
            "metrics": item.metrics,
        }
        for item in results
    ]
    print(json.dumps(serializable, indent=2))
