import pandas as pd

from src.strategy.divergence import bearish_divergence, bullish_divergence


def test_bullish_divergence_detected():
    price = pd.Series([12, 11, 12, 10, 13, 14, 15, 16, 17, 18])
    indicator = pd.Series([35, 30, 34, 31, 36, 37, 38, 39, 40, 41])

    result = bullish_divergence(price, indicator, pivot_window=1, lookback=10)
    assert result["is_valid"] is True


def test_bearish_divergence_detected():
    price = pd.Series([10, 12, 11, 13, 10, 9, 8, 7, 6, 5])
    indicator = pd.Series([40, 45, 43, 44, 42, 41, 40, 39, 38, 37])

    result = bearish_divergence(price, indicator, pivot_window=1, lookback=10)
    assert result["is_valid"] is True
