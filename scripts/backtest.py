import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.config import load_settings
from src.runtime.backtest_runner import BacktestRunner, print_backtest_results
from src.utils.logging import configure_logging


if __name__ == "__main__":
    settings = load_settings()
    configure_logging(settings.log_level)
    runner = BacktestRunner(settings)
    results = runner.run_windows(settings.backtest_month_windows)
    print_backtest_results(results)
