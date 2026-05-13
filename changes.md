# changes.md

## 2026-05-11 - BTCUSDC Optimization Loop v5 (RR 1:2, Strict Aggregate Promotion)

### Summary
Ran a focused BTCUSDC optimization loop under fixed `1:2` risk:reward behavior and promoted a new profile that strictly improves aggregate return, aggregate win rate, and aggregate trade count versus the current active profile.

### Affected Files
- `config.yaml`
- `algorithms.md`
- `README.md`
- `changes.md`

### Reason
Continue iterative BTCUSDC improvement while preserving required rules:
- no new config keys,
- fixed risk:reward `1:2`,
- long only when `RSI < 50`, short only when `RSI > 50`.

### Backtest Result
- Command/method:
	- `PYTHONWARNINGS=ignore PYTHONPATH=. "/Users/phanngt/Phan BOT/trading_binance_future/.venv/bin/python" /tmp/run_rr12_micro.py`
	- `PYTHONWARNINGS=ignore PYTHONPATH=. "/Users/phanngt/Phan BOT/trading_binance_future/.venv/bin/python" - <<'PY' ... BacktestRunner(load_settings->replace(symbols=["BTCUSDC"], backtest_month_windows=[1,3,6,12,15])) ... PY`
- Dataset/time range:
	- Binance Futures public klines (testnet profile), symbol: `BTCUSDC`, windows: `1m`, `3m`, `6m`, `12m`, `15m`.
- Promoted config values:
	- `trading.sup_res_timeframes`: `6h, 12h, 1d`
	- `strategy.rsi_period`: `20`
	- `strategy.divergence_lookback`: `60`
	- `strategy.pivot_window`: `3`
	- `execution.stop_loss_buffer_bps`: `10`
	- `execution.take_profit_buffer_bps`: `20`
- Key metrics (previous current -> promoted loop v5):
	- Aggregate:
		- `avg_return_pct`: `-48.91` -> `-45.86` (`+3.05`)
		- `avg_win_rate_pct`: `32.61` -> `36.81` (`+4.20`)
		- `total_trades`: `189` -> `285` (`+96`)
		- `avg_max_drawdown_pct`: `76.63` -> `71.86` (`-4.77`)
		- `min_window_win_rate_pct`: `0.00` -> `28.87` (`+28.87`)
	- Promoted profile by window:
		- `1m`: return `9.02`, win rate `50.00`, trades `4`, max drawdown `6.45`
		- `3m`: return `35.07`, win rate `40.00`, trades `15`, max drawdown `63.12`
		- `6m`: return `-96.65`, win rate `31.58`, trades `38`, max drawdown `98.31`
		- `12m`: return `-99.03`, win rate `28.87`, trades `97`, max drawdown `99.52`
		- `15m`: return `-77.73`, win rate `33.59`, trades `131`, max drawdown `91.91`
- Comparison with previous version:
	- Strict aggregate promotion criteria passed (return, win rate, trades all increased).
	- The per-window `>=60%` win-rate target is still not met (`win60_count=0` in this run).
- Limitations:
	- Despite aggregate improvement, returns remain negative across aggregate and several windows.
	- Long-running broad random sweeps were unstable/time-consuming in this environment, so this promotion used a focused micro-sweep around high-probability parameter neighborhoods.

### Documentation Updated
- `algorithms.md`
- `README.md`
- `changes.md`

## 2026-05-11 - Enforce Fixed 1:2 Risk-Reward Ratio

### Summary
Implemented fixed risk:reward behavior of `1:2` in trade-plan generation. Stop-loss remains anchored to support/resistance with the configured stop-loss buffer, and take-profit is now derived from entry price and risk distance (`TP = entry +/- 2R`).

### Affected Files
- `src/strategy/signal_engine.py`
- `tests/test_signal_engine.py`
- `algorithms.md`
- `architecture.md`
- `README.md`
- `changes.md`

### Reason
Apply requested strategy behavior where each trade targets reward twice the defined risk.

### Backtest Result
- Command/method:
	- `PYTHONWARNINGS=ignore PYTHONPATH=. "/Users/phanngt/Phan BOT/trading_binance_future/.venv/bin/python" - <<'PY' ... BacktestRunner(load_settings->replace(symbols=["BTCUSDC"], backtest_month_windows=[1,3,6,12,15])) ... PY` (run before and after implementation)
	- `PYTHONPATH=. "/Users/phanngt/Phan BOT/trading_binance_future/.venv/bin/python" -m pytest -q tests/test_signal_engine.py tests/test_trade_cycle.py`
- Dataset/time range:
	- Binance Futures public klines (testnet profile), symbol: `BTCUSDC`, windows: `1m`, `3m`, `6m`, `12m`, `15m`.
- Key metrics:
	- Before change (same config, pre-implementation):
		- `avg_return_pct`: `130.41`
		- `avg_win_rate_pct`: `49.15`
		- `total_trades`: `394`
		- `avg_max_drawdown_pct`: `52.00`
	- After fixed `1:2` RR implementation:
		- `avg_return_pct`: `-48.91`
		- `avg_win_rate_pct`: `32.61`
		- `total_trades`: `189`
		- `avg_max_drawdown_pct`: `76.63`
	- Delta (after - before):
		- `avg_return_pct`: `-179.32`
		- `avg_win_rate_pct`: `-16.54`
		- `total_trades`: `-205`
		- `avg_max_drawdown_pct`: `+24.63`
	- Post-change by window:
		- `1m`: return `9.34`, win rate `50.00`, trades `4`, max drawdown `6.47`
		- `3m`: return `48.99`, win rate `42.86`, trades `14`, max drawdown `59.47`
		- `6m`: return `-94.98`, win rate `37.84`, trades `37`, max drawdown `97.47`
		- `12m`: return `-127.44`, win rate `0.00`, trades `1`, max drawdown `127.44`
		- `15m`: return `-80.45`, win rate `32.33`, trades `133`, max drawdown `92.30`
- Comparison with previous version:
	- Fixed `1:2` RR worsened aggregate return, win rate, and trade count, and increased average drawdown.
	- Behavior change is active and validated by tests, but current parameter set is no longer well-tuned under the new RR rule.
- Limitations:
	- The current settings were optimized for prior TP logic; enforcing fixed 1:2 RR changes trade geometry and likely requires a fresh optimization loop.
	- Backtest remains simulation-based and does not model exchange queue priority or partial fills.

### Documentation Updated
- `algorithms.md`
- `architecture.md`
- `README.md`
- `changes.md`

## 2026-05-11 - BTCUSDC Optimization Loop v4 (Search Run, No Promotion)

### Summary
Ran additional Loop v4 search cycles to find a configuration that strictly improves all three aggregate targets (PnL, win rate, trade count) versus the current Loop v3 profile. No candidate met all three conditions simultaneously, so Loop v3 config remains active.

### Affected Files
- `changes.md`

### Reason
Continue optimization/backtest reruns while preventing regression in aggregate win rate.

### Backtest Result
- Command/method:
	- `PYTHONWARNINGS=ignore PYTHONPATH=. "/Users/phanngt/Phan BOT/trading_binance_future/.venv/bin/python" - <<'PY' ... targeted loop4 search ... PY`
	- `PYTHONWARNINGS=ignore PYTHONPATH=. "/Users/phanngt/Phan BOT/trading_binance_future/.venv/bin/python" - <<'PY' ... current vs candidate_sl9_tp3/sl9_tp4/sl9_tp2 compare ... PY`
	- `PYTHONWARNINGS=ignore PYTHONPATH=. "/Users/phanngt/Phan BOT/trading_binance_future/.venv/bin/python" - <<'PY' ... broader random loop4 sweep (32 candidates) ... PY`
	- `PYTHONWARNINGS=ignore PYTHONPATH=. "/Users/phanngt/Phan BOT/trading_binance_future/.venv/bin/python" - <<'PY' ... final six-profile shortlist compare ... PY`
- Dataset/time range:
	- Binance Futures public klines (testnet profile), symbol: `BTCUSDC`, windows: `1m`, `3m`, `6m`, `12m`, `15m`.
- Baseline (current Loop v3 in this run):
	- `avg_return`: `130.41`
	- `avg_win_rate`: `49.15`
	- `total_trades`: `394`
- Best nearby candidates (vs current):
	- `sl9/tp3`: `avg_return +21.62`, `avg_win_rate -0.25`, `total_trades +7`
	- `sl9/tp2`: `avg_return +31.04`, `avg_win_rate -0.76`, `total_trades +2`
	- `sl9/tp4`: `avg_return +8.17`, `avg_win_rate -1.13`, `total_trades +25`
	- Broad random sweep top (`rsi=21`, `lookback=70`, `pivot=4`, `sl=8`, `tp=4`): `avg_return +1160.08`, `avg_win_rate -5.61`, `total_trades +11` (fails strict win-rate target).
- Comparison with previous version:
	- No strict candidate found with all-three aggregate improvements at once.
	- Current Loop v3 retained to preserve higher aggregate win rate.
	- Follow-up broad/random and shortlist reruns also produced no strict winner (`strict_count=0`).
- Limitations:
	- Moving-window backtests can shift aggregate values over time as new candles appear.
	- Simulator assumptions remain (no queue-priority/partial-fill realism).

### Documentation Updated
- `changes.md`

## 2026-05-11 - BTCUSDC Optimization Loop v3 (Aggregate Improvement vs Loop v2)

### Summary
Continued BTCUSDC optimization loops and selected a new config update that improves aggregate PnL, aggregate win rate, and aggregate trade count versus the prior `BTCUSDC Optimization Loop v2` baseline across required windows `1m/3m/6m/12m/15m`.

### Affected Files
- `config.yaml`
- `algorithms.md`
- `README.md`
- `changes.md`

### Reason
Follow iterative tuning request while keeping schema unchanged and updating only existing `config.yaml` values.

### Backtest Result
- Command/method:
	- `PYTHONWARNINGS=ignore PYTHONPATH=. "/Users/phanngt/Phan BOT/trading_binance_future/.venv/bin/python" - <<'PY' ... candidate sweep around Loop v2 ... PY`
	- `PYTHONWARNINGS=ignore PYTHONPATH=. "/Users/phanngt/Phan BOT/trading_binance_future/.venv/bin/python" - <<'PY' ... old_loop_v2 vs new_loop_v3 direct compare ... PY`
- Dataset/time range:
	- Binance Futures public klines (testnet profile), symbol: `BTCUSDC`, windows: `1m`, `3m`, `6m`, `12m`, `15m`.
- Selected config values:
	- `trading.sup_res_timeframes`: `6h, 12h, 1d`
	- `strategy.rsi_period`: `18`
	- `strategy.divergence_lookback`: `50`
	- `strategy.pivot_window`: `3`
	- `execution.stop_loss_buffer_bps`: `12`
	- `execution.take_profit_buffer_bps`: `3`
- Key metrics (`Loop v2` -> `Loop v3`):
	- 1m:
		- `total_return_pct`: `12.23` -> `-3.59` (`-15.82`)
		- `win_rate_pct`: `42.86` -> `50.00` (`+7.14`)
		- `trade_count`: `7` -> `4` (`-3`)
	- 3m:
		- `total_return_pct`: `41.16` -> `17.55` (`-23.61`)
		- `win_rate_pct`: `54.17` -> `57.69` (`+3.52`)
		- `trade_count`: `24` -> `26` (`+2`)
	- 6m:
		- `total_return_pct`: `-0.14` -> `489.69` (`+489.83`)
		- `win_rate_pct`: `56.25` -> `53.85` (`-2.40`)
		- `trade_count`: `48` -> `52` (`+4`)
	- 12m:
		- `total_return_pct`: `-110.43` -> `369.33` (`+479.76`)
		- `win_rate_pct`: `40.34` -> `45.59` (`+5.25`)
		- `trade_count`: `119` -> `136` (`+17`)
	- 15m:
		- `total_return_pct`: `495.86` -> `55.02` (`-440.84`)
		- `win_rate_pct`: `42.35` -> `39.55` (`-2.80`)
		- `trade_count`: `196` -> `177` (`-19`)
	- Aggregate (mean over windows + total trades):
		- `avg_return`: `87.74` -> `185.60` (`+97.86`)
		- `avg_win_rate`: `47.19` -> `49.34` (`+2.15`)
		- `total_trades`: `394` -> `395` (`+1`)
- Comparison with previous version:
	- Aggregate targets improved (`PnL`, `win_rate`, `trade_count`).
	- Window-by-window behavior is mixed: large gains in `6m/12m`, weaker `1m/3m/15m` returns.
	- Win rate improved in `1m/3m/12m` but declined in `6m/15m`.
- Limitations:
	- Distribution of returns remains highly non-uniform across windows.
	- Results are simulation-based and still exclude exchange queue priority/partial-fill realism.
	- Candidate selection optimized for aggregate objective and may not be ideal for per-window stability.

### Documentation Updated
- `algorithms.md`
- `README.md`
- `changes.md`

## 2026-05-11 - BTCUSDC Optimization Loop v2 (Balanced Winrate/PnL/Trades)

### Summary
Ran additional BTCUSDC optimization loops across 1m/3m/6m/12m/15m and selected a balanced profile that improves aggregate win rate, aggregate PnL, and aggregate trade count versus the previous `current_performance` baseline.

### Affected Files
- `config.yaml`
- `algorithms.md`
- `architecture.md`
- `README.md`
- `changes.md`

### Reason
Continue iterative algorithm tuning as requested, while keeping config schema unchanged and updating only existing values.

### Backtest Result
- Command/method:
	- `PYTHONPATH=. "/Users/phanngt/Phan BOT/trading_binance_future/.venv/bin/python" - <<'PY' ... benchmarked candidate sets with BacktestRunner ... PY`
	- `PYTHONPATH=. "/Users/phanngt/Phan BOT/trading_binance_future/.venv/bin/python" - <<'PY' ... rerun BTCUSDC windows [1,3,6,12,15] ... PY`
- Dataset/time range:
	- Binance Futures public klines (testnet profile), symbol: `BTCUSDC`, windows: `1m`, `3m`, `6m`, `12m`, `15m`.
- Selected config values:
	- `trading.sup_res_timeframes`: `6h, 12h, 1d`
	- `strategy.rsi_period`: `18`
	- `strategy.divergence_lookback`: `50`
	- `strategy.pivot_window`: `4`
	- `execution.stop_loss_buffer_bps`: `12`
	- `execution.take_profit_buffer_bps`: `4`
- Key metrics (previous `current_performance` baseline -> selected profile):
	- 1m:
		- `total_return_pct`: `-35.11` -> `12.23` (`+47.34`)
		- `win_rate_pct`: `14.29` -> `42.86` (`+28.57`)
		- `trade_count`: `7` -> `7` (`+0`)
	- 3m:
		- `total_return_pct`: `-24.83` -> `41.16` (`+65.99`)
		- `win_rate_pct`: `40.00` -> `54.17` (`+14.17`)
		- `trade_count`: `30` -> `24` (`-6`)
	- 6m:
		- `total_return_pct`: `-14.24` -> `-0.14` (`+14.10`)
		- `win_rate_pct`: `43.24` -> `56.25` (`+13.01`)
		- `trade_count`: `74` -> `48` (`-26`)
	- 12m:
		- `total_return_pct`: `-173.81` -> `-110.43` (`+63.38`)
		- `win_rate_pct`: `66.67` -> `40.34` (`-26.33`)
		- `trade_count`: `3` -> `119` (`+116`)
	- 15m:
		- `total_return_pct`: `-82.54` -> `495.86` (`+578.40`)
		- `win_rate_pct`: `39.55` -> `42.35` (`+2.80`)
		- `trade_count`: `177` -> `196` (`+19`)
	- Aggregate (mean over windows + total trades):
		- `avg_return`: `-66.11` -> `87.74` (`+153.85`)
		- `avg_win_rate`: `40.75` -> `47.19` (`+6.44`)
		- `total_trades`: `291` -> `394` (`+103`)
- Comparison with previous version:
	- Improved aggregate PnL, aggregate win rate, and aggregate trade count.
	- Return improved in all 5 required windows.
	- Trade count increased overall but decreased in 3m and 6m windows.
	- Win rate improved in 4/5 windows; 12m win rate decreased while return and trade count improved.
- Limitations:
	- High variability across windows remains (especially long-window sensitivity).
	- 12m drawdown and win-rate profile are still weak relative to shorter windows.
	- Results remain simulation-based and do not model exchange queue priority/partial fills.

### Documentation Updated
- `algorithms.md`
- `architecture.md`
- `README.md`
- `changes.md`

## 2026-05-11 - Backtest Local Kline Cache Persistence

### Summary
Implemented local on-disk kline caching for backtest data loading so downloaded Binance candles are saved and reused across backtest reruns.

### Affected Files
- `src/runtime/backtest_runner.py`
- `README.md`
- `.gitignore`
- `changes.md`

### Reason
Reduce repeated network fetches and speed up iterative backtest cycles by reusing previously downloaded candle data.

### Backtest Result
- Command/method:
	- `PYTHONPATH=. "/Users/phanngt/Phan BOT/trading_binance_future/.venv/bin/python" - <<'PY' ... runner._prepare_data(1) twice ... PY`
	- `ls -1 .cache/binance_klines | head`
- Dataset/time range:
	- Binance Futures public klines for `BTCUSDC` testnet profile, including `1h`, `6h`, `12h`, `1d`.
- Key metrics:
	- Cache smoke check completed: `cache_ok`.
	- Local cache files confirmed: `testnet_BTCUSDC_1h.csv`, `testnet_BTCUSDC_6h.csv`, `testnet_BTCUSDC_12h.csv`, `testnet_BTCUSDC_1d.csv`.
- Comparison with previous version:
	- Previous behavior fetched candles from Binance on every backtest run.
	- New behavior reuses local cache files when requested history is already covered; remote fetch is only used to fill missing history.
- Limitations:
	- Cache refresh is append/fill oriented and prioritizes reusing local history over always fetching newest candles.
	- Cache is stored as CSV and may grow over time.

### Documentation Updated
- `README.md`
- `changes.md`

## 2026-05-11 - BTCUSDC Config Optimization (1m/3m/6m/12m/15m)

### Summary
Updated existing strategy and execution values in `config.yaml` to the best-ranked BTCUSDC candidate from iterative backtest sweeps, then re-ran baseline-vs-new comparisons for 1m, 3m, 6m, 12m, and 15m windows.

### Affected Files
- `config.yaml`
- `algorithms.md`
- `architecture.md`
- `README.md`
- `changes.md`

### Reason
Targeted better BTCUSDC performance using existing config keys only (no new config fields), with emphasis on improving return and win rate across required windows.

### Backtest Result
- Command/method:
	- `PYTHONPATH=. "/Users/phanngt/Phan BOT/trading_binance_future/.venv/bin/python" scripts/tmp_optimize_btcusdc.py`
	- `PYTHONPATH=. "/Users/phanngt/Phan BOT/trading_binance_future/.venv/bin/python" scripts/tmp_compare_btcusdc.py`
- Dataset/time range:
	- Binance Futures public candles (mainnet endpoint), symbol: `BTCUSDC`.
	- Windows: `1m`, `3m`, `6m`, `12m`, `15m`.
- Key config changes:
	- `trading.sup_res_timeframes`: `6h, 12h, 1d` (removed `3h`, `1w`)
	- `strategy.rsi_period`: `16`
	- `strategy.divergence_lookback`: `100`
	- `strategy.pivot_window`: `2`
	- `execution.stop_loss_buffer_bps`: `8`
	- `execution.take_profit_buffer_bps`: `4`
- Comparison with previous version (baseline -> new):
	- 1m:
		- `total_return_pct`: `-25.03` -> `0.00` (`+25.03`)
		- `win_rate_pct`: `0.00` -> `0.00` (`+0.00`)
		- `trade_count`: `4` -> `0` (`-4`)
	- 3m:
		- `total_return_pct`: `-42.20` -> `23.18` (`+65.38`)
		- `win_rate_pct`: `40.32` -> `55.56` (`+15.24`)
		- `trade_count`: `62` -> `27` (`-35`)
	- 6m:
		- `total_return_pct`: `-48.68` -> `400.15` (`+448.83`)
		- `win_rate_pct`: `42.73` -> `57.41` (`+14.68`)
		- `trade_count`: `110` -> `54` (`-56`)
	- 12m:
		- `total_return_pct`: `-80.32` -> `329.61` (`+409.93`)
		- `win_rate_pct`: `39.78` -> `48.23` (`+8.45`)
		- `trade_count`: `269` -> `141` (`-128`)
	- 15m:
		- `total_return_pct`: `-91.11` -> `177.07` (`+268.18`)
		- `win_rate_pct`: `38.12` -> `46.94` (`+8.82`)
		- `trade_count`: `320` -> `196` (`-124`)
- Limitations:
	- Trade frequency decreased in all windows despite higher return and win rate in most windows.
	- Results are simulation-based and still inherit simulator assumptions (no exchange queue/partial-fill realism).

### Documentation Updated
- `algorithms.md`
- `architecture.md`
- `README.md`
- `changes.md`

## 2026-05-10 - Real TESTNET Order Call Integration Test

### Summary
Added an opt-in integration test that calls `execute_trade_plan` and verifies at least one real Binance TESTNET order API call is made, then performs cleanup by canceling open orders and closing leftover positions on the test symbol.

### Affected Files
- `tests/test_integration_live_executor.py`
- `changes.md`

### Reason
Support explicit validation mode where integration tests place/call real TESTNET orders instead of only exercising non-destructive guard branches.

### Backtest Result
- Command/method:
	- `RUN_INTEGRATION_TESTS=1 RUN_LIVE_TESTNET_ORDERS=1 .venv/bin/python -m pytest -q -rs tests/test_integration_live_executor.py -k places_order_and_cleans_up`
- Dataset/time range:
	- N/A (live external integration test against Binance Futures TESTNET API).
- Key metrics:
	- Test result: `1 passed`.
- Comparison with previous version:
	- Previous integration coverage validated only non-order branch (`quantity_too_small`); this change validates real order-call path.
- Limitations:
	- Requires valid TESTNET credentials and sufficient TESTNET balance.
	- Test is opt-in via `RUN_LIVE_TESTNET_ORDERS=1` to avoid accidental order placement.

### Documentation Updated
- `changes.md`

## 2026-05-10 - Live Executor TESTNET Integration Tests

### Summary
Added TESTNET integration tests for live execution adapter methods, including `open_positions_count` and `execute_trade_plan`.

### Affected Files
- `tests/test_integration_live_executor.py`
- `changes.md`

### Reason
Provide explicit integration coverage for the production execution adapter contract using real Binance Futures TESTNET API responses.

### Backtest Result
- Command/method:
	- `RUN_INTEGRATION_TESTS=1 .venv/bin/python -m pytest -q -rs tests/test_integration_live_executor.py`
- Dataset/time range:
	- N/A (external service integration checks only).
- Key metrics:
	- Test result: `2 passed`.
- Comparison with previous version:
	- Adds new coverage for live adapter protocol methods not previously validated by integration tests.
- Limitations:
	- `execute_trade_plan` integration test is intentionally non-destructive; it validates the live path up to quantity guard rejection and does not place real TESTNET orders.

### Documentation Updated
- `changes.md`

## 2026-05-10 - External Service Integration Tests

### Summary
Added integration tests for Binance Futures testnet and Telegram Bot API connectivity, guarded behind an explicit test flag so default unit test runs remain isolated from live network dependencies.

### Affected Files
- `tests/test_integration_external_services.py`
- `pytest.ini`
- `changes.md`

### Reason
Validate that configured testnet Binance credentials and Telegram bot settings can successfully call real endpoints.

### Backtest Result
- Command/method:
	- `RUN_INTEGRATION_TESTS=1 .venv/bin/python -m pytest -q tests/test_integration_external_services.py`
- Dataset/time range:
	- N/A (external service integration checks only).
- Key metrics:
	- Test result in this workspace: `1 passed, 2 skipped`.
	- Skip reasons: missing `BINANCE_TESTNET_FUTURE_API_KEY`/`BINANCE_TESTNET_FUTURE_API_SECRET` and missing `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` from process environment.
- Comparison with previous version:
	- No strategy/backtest algorithm change.
- Limitations:
	- Requires internet access and valid external credentials.
	- Sends a real Telegram message to the configured chat.
	- Local on-disk `.env` is currently empty, so credentialed checks were skipped in this run.

### Documentation Updated
- `changes.md`

## 2026-05-10 - Shared Trade-Cycle Parity Refactor

### Summary
Refactored live and backtest orchestration to use one shared trade-cycle function for signal evaluation, duplicate-signal guard, and execution adapter invocation. Updated simulation rejection semantics to match production executor reason codes for position limit and quantity sizing guards.

### Affected Files
- `src/runtime/trade_cycle.py`
- `src/runtime/live_runner.py`
- `src/runtime/backtest_runner.py`
- `src/execution/interface.py`
- `src/execution/binance_futures.py`
- `src/execution/simulator.py`
- `tests/test_trade_cycle.py`
- `algorithms.md`
- `architecture.md`
- `changes.md`

### Reason
Implement true production-path parity in backtest mode by eliminating duplicated orchestration paths and ensuring adapter-only execution differences.

### Backtest Result
- Command/method:
	- `pytest -q`
	- `python - <<'PY' ... BacktestRunner(..., backtest_month_windows=[3]) ... PY`
- Dataset/time range:
	- Binance Futures public candles (mainnet endpoint), symbols: `BTCUSDT`, `BTCUSDC`, `ETHUSDT`, `ETHUSDC`, 3-month window.
- Key metrics:
	- `final_equity=22727.91`
	- `total_return_pct=127.28`
	- `max_drawdown_pct=56.32`
	- `win_rate_pct=39.19`
	- `sharpe=1.183`
	- `trade_count=74`
	- `maker_fill_ratio=0.196`
	- `maker_reject_count=303`
- Comparison with previous version:
	- Return and trade count remained unchanged for 3-month smoke test.
	- Maker parity metrics changed from optimistic (`maker_fill_ratio=1.0`, `maker_reject_count=0`) to stricter adapter-parity values (`0.196`, `303`) because all symbol-cycle attempts now follow unified rejection handling.
- Limitations:
	- Backtest still uses simulated fills and does not model exchange queue priority or partial fills.
	- Full 3/6/12/15-month rerun was not executed in this refactor pass; 3-month smoke run used for parity validation.

### Documentation Updated
- `algorithms.md`
- `architecture.md`
- `changes.md`

## 2026-05-10 - Project Bootstrap

### Summary
Initialized project structure and created baseline algorithm and architecture documentation for a new multi-symbol Binance Futures trading bot.

### Affected Files
- `README.md`
- `.env.example`
- `requirements.txt`
- `algorithms.md`
- `architecture.md`
- `changes.md`

### Reason
Set up the foundational structure and source-of-truth docs required before implementation.

### Backtest Result
- Command/method: Not run yet at bootstrap stage.
- Dataset/time range: N/A.
- Key metrics: N/A.
- Comparison with previous version: N/A.
- Limitations: No algorithm implementation existed yet.

### Documentation Updated
- `algorithms.md`
- `architecture.md`
- `changes.md`

## 2026-05-10 - Split Secrets and Runtime Config

### Summary
Refactored configuration management so `.env` stores only Binance and Telegram secrets, while all non-secret trading, indicator, execution, and backtest settings are read from `config.yaml`.

### Affected Files
- `.env.example`
- `config.yaml`
- `src/config.py`
- `requirements.txt`
- `README.md`
- `architecture.md`
- `changes.md`

### Reason
Align configuration model with the requested security and maintainability split: secrets in environment file, operational parameters in versioned YAML config.

### Backtest Result
- Command/method: `pytest -q` and config smoke-run via `python scripts/backtest.py` (single-window check after migration).
- Dataset/time range: Binance Futures public candles, 3-month smoke check.
- Key metrics: Existing strategy metrics unchanged for 3-month check (`total_return_pct=127.28`, `trade_count=74`).
- Comparison with previous version: No algorithm logic changes; configuration source changed only.
- Limitations: Full multi-window rerun was not repeated for this config-only change.

### Documentation Updated
- `architecture.md`
- `changes.md`

## 2026-05-10 - Live/Backtest Trading Bot Implementation

### Summary
Implemented a full Python multi-symbol Binance Futures bot with real-time 1h signal generation, strict-confluence strategy logic (RSI divergence + MACD divergence + multi-timeframe support/resistance), maker-only execution flow, Telegram notifications, and backtest/simulation runtime that reuses production strategy calls.

### Affected Files
- `src/config.py`
- `src/models.py`
- `src/data/binance_api.py`
- `src/data/binance_feed.py`
- `src/strategy/indicators.py`
- `src/strategy/divergence.py`
- `src/strategy/support_resistance.py`
- `src/strategy/signal_engine.py`
- `src/execution/interface.py`
- `src/execution/binance_futures.py`
- `src/execution/simulator.py`
- `src/notify/telegram.py`
- `src/runtime/live_runner.py`
- `src/runtime/backtest_runner.py`
- `src/utils/timeframe.py`
- `src/utils/logging.py`
- `scripts/live.py`
- `scripts/backtest.py`
- `tests/test_divergence.py`
- `tests/test_support_resistance.py`
- `tests/test_signal_engine.py`
- `.env.example`
- `requirements.txt`
- `README.md`
- `algorithms.md`
- `architecture.md`
- `changes.md`

### Reason
Delivered end-to-end implementation for the requested bot behavior: multi-symbol support, 1h decision loop, maker-only order handling, Telegram integration, and production-parity backtest engine for 3/6/12/15 month windows.

### Backtest Result
- Command/method:
	- `BINANCE_TESTNET=false /Users/phanngt/Phan BOT/trading_binance_future/.venv/bin/python scripts/backtest.py`
	- Strategy function reuse: live and backtest both call `SignalEngine.generate_signal(...)`; execution path differs by adapter (`BinanceFuturesExecutor` vs `SimulatedExecutionAdapter`).
- Dataset/time range:
	- Binance Futures public klines (mainnet endpoint), symbols: `BTCUSDT`, `BTCUSDC`, `ETHUSDT`, `ETHUSDC`.
	- Timeframes for context: `1h`, `3h` (resampled from `1h`), `6h`, `12h`, `1d`, `1w`.
	- Windows: `3m`, `6m`, `12m`, `15m` (months).
- Key metrics:
	- 3 months: return `127.28%`, max drawdown `56.32%`, win rate `39.19%`, Sharpe `1.183`, trades `74`, maker fill ratio `1.0`.
	- 6 months: return `-83.15%`, max drawdown `83.72%`, win rate `39.66%`, Sharpe `-0.697`, trades `179`, maker fill ratio `1.0`.
	- 12 months: return `-99.57%`, max drawdown `99.73%`, win rate `35.75%`, Sharpe `-1.792`, trades `414`, maker fill ratio `1.0`.
	- 15 months: return `-99.51%`, max drawdown `99.86%`, win rate `36.46%`, Sharpe `-1.224`, trades `491`, maker fill ratio `1.0`.
- Comparison with previous version:
	- Previous version had no executable strategy implementation, so there is no numeric baseline for direct performance delta.
- Limitations:
	- Simulation assumes deterministic maker fill behavior and does not model order queue priority or partial fills.
	- Funding, ADL, and liquidation dynamics are simplified.
	- One-position portfolio cap can underrepresent concurrent symbol opportunities.

### Documentation Updated
- `algorithms.md`
- `architecture.md`
- `changes.md`
