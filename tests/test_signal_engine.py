import pandas as pd
import pytest

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


def _params() -> StrategyParams:
    return StrategyParams(
        rsi_period=14,
        macd_fast=12,
        macd_slow=26,
        macd_signal=9,
        divergence_lookback=80,
        pivot_window=3,
        stop_loss_buffer_bps=12,
        take_profit_buffer_bps=3,
    )


def test_build_long_plan_enforces_exact_two_reward_to_one_risk():
    engine = SignalEngine(_params())

    plan = engine._build_long_plan(
        symbol="BTCUSDT",
        signal_time=1,
        entry_price=100.0,
        support=99.0,
        resistance=110.0,
    )

    assert plan is not None
    risk = plan.entry_price - plan.stop_loss
    reward = plan.take_profit - plan.entry_price
    assert risk > 0
    assert reward > 0
    assert reward / risk == pytest.approx(2.0, rel=1e-9)
    assert plan.metadata.get("risk_reward_ratio") == 2.0


def test_build_short_plan_enforces_exact_two_reward_to_one_risk():
    engine = SignalEngine(_params())

    plan = engine._build_short_plan(
        symbol="BTCUSDT",
        signal_time=1,
        entry_price=100.0,
        support=90.0,
        resistance=101.0,
    )

    assert plan is not None
    risk = plan.stop_loss - plan.entry_price
    reward = plan.entry_price - plan.take_profit
    assert risk > 0
    assert reward > 0
    assert reward / risk == pytest.approx(2.0, rel=1e-9)
    assert plan.metadata.get("risk_reward_ratio") == 2.0


def test_build_long_plan_rejects_non_positive_risk():
    engine = SignalEngine(_params())

    plan = engine._build_long_plan(
        symbol="BTCUSDT",
        signal_time=1,
        entry_price=100.0,
        support=100.5,
        resistance=110.0,
    )

    assert plan is None
