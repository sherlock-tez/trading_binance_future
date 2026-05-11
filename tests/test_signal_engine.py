import pandas as pd

from src.strategy.signal_engine import SignalEngine, StrategyParams


def test_signal_engine_handles_insufficient_data():
    frame = pd.DataFrame(
        {
            "open_time": [1, 2, 3],
            "open": [100, 101, 102],
            "high": [101, 102, 103],
            "low": [99, 100, 101],
            "close": [100, 101, 102],
            "volume": [1, 1, 1],
            "close_time": [1, 2, 3],
        }
    )

    engine = SignalEngine(
        StrategyParams(
            rsi_period=14,
            macd_fast=12,
            macd_slow=26,
            macd_signal=9,
            divergence_lookback=80,
            pivot_window=3,
            stop_loss_buffer_bps=8,
            take_profit_buffer_bps=8,
        )
    )

    plan, diagnostics = engine.generate_signal("BTCUSDT", frame, {"3h": frame})
    assert plan is None
    assert diagnostics.decision == "insufficient_data"
