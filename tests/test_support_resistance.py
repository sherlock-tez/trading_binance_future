import pandas as pd

from src.strategy.support_resistance import (
    build_multi_timeframe_levels,
    nearest_resistance,
    nearest_support,
)


def test_nearest_levels_work():
    frame = pd.DataFrame(
        {
            "open_time": [1, 2, 3, 4, 5, 6, 7],
            "open": [100, 99, 101, 98, 102, 99, 103],
            "high": [101, 100, 102, 99, 103, 100, 104],
            "low": [99, 98, 100, 97, 101, 98, 102],
            "close": [100, 99, 101, 98, 102, 99, 103],
            "close_time": [1, 2, 3, 4, 5, 6, 7],
        }
    )

    supports, resistances = build_multi_timeframe_levels({"1h": frame}, pivot_window=1)
    price = 100

    sup = nearest_support(supports, price)
    res = nearest_resistance(resistances, price)

    assert sup is not None
    assert res is not None
    assert sup < price < res
