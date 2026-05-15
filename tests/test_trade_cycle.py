import pandas as pd

from src.models import SignalDiagnostics, TradePlan
from src.execution.simulator import SimulatedExecutionAdapter
from src.runtime.trade_cycle import run_trade_cycle


def test_trade_cycle_skips_duplicate_signal():
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

    plan = TradePlan(
        symbol="BTCUSDT",
        side="BUY",
        position_side="LONG",
        entry_price=100.0,
        stop_loss=90.0,
        take_profit=110.0,
        signal_time=3,
    )
    diagnostics = SignalDiagnostics(
        symbol="BTCUSDT",
        signal_time=3,
        last_price=100.0,
        rsi_value=45.0,
        bullish_rsi_divergence=True,
        bearish_rsi_divergence=False,
        bullish_macd_divergence=True,
        bearish_macd_divergence=False,
        nearest_support=95.0,
        nearest_resistance=105.0,
        decision="long",
    )

    class DummyEngine:
        def generate_signal(self, symbol, signal_frame_1h, higher_timeframe_frames):
            return plan, diagnostics

    class DummyExecutor:
        @property
        def open_positions_count(self) -> int:
            return 0

        def execute_trade_plan(self, plan, context=None):
            raise AssertionError("Should not execute when signal is duplicate")

    outcome = run_trade_cycle(
        symbol="BTCUSDT",
        signal_frame=frame,
        higher_frames={"3h": frame},
        execution_timestamp=3,
        engine=DummyEngine(),
        executor=DummyExecutor(),
        processed_signals={("BTCUSDT", 3)},
    )

    assert outcome.plan is not None
    assert outcome.skipped_duplicate is True
    assert outcome.execution is None


def test_trade_cycle_skips_duplicate_pivot_signal():
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

    plan = TradePlan(
        symbol="BTCUSDT",
        side="SELL",
        position_side="SHORT",
        entry_price=100.0,
        stop_loss=110.0,
        take_profit=90.0,
        signal_time=3,
        metadata={"direction": "short", "signal_pivot_time": 2},
    )
    diagnostics = SignalDiagnostics(
        symbol="BTCUSDT",
        signal_time=3,
        last_price=100.0,
        rsi_value=55.0,
        bullish_rsi_divergence=False,
        bearish_rsi_divergence=True,
        bullish_macd_divergence=False,
        bearish_macd_divergence=True,
        nearest_support=95.0,
        nearest_resistance=105.0,
        decision="short",
    )

    class DummyEngine:
        def generate_signal(self, symbol, signal_frame_1h, higher_timeframe_frames):
            return plan, diagnostics

    class DummyExecutor:
        @property
        def open_positions_count(self) -> int:
            return 0

        def execute_trade_plan(self, plan, context=None):
            raise AssertionError("Should not execute when pivot signal is duplicate")

    outcome = run_trade_cycle(
        symbol="BTCUSDT",
        signal_frame=frame,
        higher_frames={"3h": frame},
        execution_timestamp=3,
        engine=DummyEngine(),
        executor=DummyExecutor(),
        processed_signals={("BTCUSDT:short", 2)},
    )

    assert outcome.signal_key == ("BTCUSDT:short", 2)
    assert outcome.skipped_duplicate is True
    assert outcome.execution is None


def test_simulator_rejects_when_max_open_positions_reached():
    from src.config import load_settings

    settings = load_settings()
    simulator = SimulatedExecutionAdapter(settings)

    simulator.position = type("P", (), {"symbol": "BTCUSDT"})()  # lightweight occupied marker

    plan = TradePlan(
        symbol="BTCUSDT",
        side="BUY",
        position_side="LONG",
        entry_price=100.0,
        stop_loss=90.0,
        take_profit=110.0,
        signal_time=1,
    )

    result = simulator.execute_trade_plan(
        plan=plan,
        context={"timestamp": 1},
    )

    assert result.accepted is False
    assert result.reason == "max_open_positions_reached"
