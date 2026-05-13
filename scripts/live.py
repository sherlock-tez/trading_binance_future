import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.config import load_settings
from src.runtime.live_runner import run_live
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
    run_live(settings)
