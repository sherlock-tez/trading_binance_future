"""One-shot ETHUSDC production-path backtest summary."""
from src.config import load_settings
from src.runtime.backtest_runner import BacktestRunner
from src.utils.logging import configure_logging

s = load_settings("ETHUSDC")
configure_logging("WARNING")
res = BacktestRunner(s).run_windows(s.backtest_month_windows)
print(f"ETHUSDC {s.loop_id}")
print(f"{'mo':>3} | {'ret%':>10} | {'WR%':>6} | {'trades':>6} | {'DD%':>6} | {'tpm':>5}")
for w in res:
    m = w.metrics
    tpm = m["trade_count"] / w.months if w.months else 0.0
    print(
        f"{w.months:>3} | {m['total_return_pct']:>10.2f} | "
        f"{m['win_rate_pct']:>6.1f} | {m['trade_count']:>6} | "
        f"{m['max_drawdown_pct']:>6.2f} | {tpm:>5.2f}"
    )
