import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.config import load_settings
from src.runtime.backtest_runner import BacktestRunner, print_backtest_results
from src.utils.logging import configure_logging


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Load {symbol}_config.yaml (e.g. BTCUSDC). Omit to use config.yaml.",
    )
    args = parser.parse_args()

    settings = load_settings(args.symbol)
    configure_logging(settings.log_level)
    runner = BacktestRunner(settings)
    results = runner.run_windows(settings.backtest_month_windows)
    print_backtest_results(results)
