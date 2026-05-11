from __future__ import annotations

import json
import time
from dataclasses import dataclass
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

logger = get_logger(__name__)


@dataclass(frozen=True)
class BacktestWindowResult:
    months: int
    symbol_count: int
    metrics: Dict[str, float | int]


class BacktestRunner:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = BinanceFuturesClient(
            api_key=settings.binance_futures_api_key,
            api_secret=settings.binance_futures_api_secret,
            testnet=settings.binance_testnet,
        )
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
        start_ms = self._months_ago_ms(months)
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

            for symbol in self.settings.symbols:
                row = signal_rows_by_symbol[symbol].get(t)
                if not row:
                    continue

                signal_frame = data[symbol][self.settings.signal_timeframe]
                signal_slice = signal_frame[signal_frame["open_time"] <= t].tail(600)
                if len(signal_slice) < 100:
                    continue

                higher_slices = {}
                for timeframe in self.settings.sup_res_timeframes:
                    frame = data[symbol][timeframe]
                    higher_slices[timeframe] = frame[frame["open_time"] <= t].tail(600)

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
