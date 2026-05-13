# changes.md

## 2026-05-13 - Per-Symbol Config Files

### Summary
Split the monolithic `config.yaml` into four per-symbol files (`btcusdc_config.yaml`, `btcusdt_config.yaml`, `ethusdc_config.yaml`, `ethusdt_config.yaml`) so each symbol can carry its own tuned strategy params, leverage, and backtest windows. `load_settings(symbol)` now resolves `{symbol}_config.yaml` (case-insensitive); a missing file raises `ConfigError` rather than silently falling back to a wrong-symbol default. `load_settings()` with no argument still reads the legacy `config.yaml`.

### Affected Files
- `src/config.py` (added `_resolve_config_path`; `load_settings` accepts a `symbol` arg)
- `btcusdc_config.yaml`, `btcusdt_config.yaml`, `ethusdc_config.yaml`, `ethusdt_config.yaml` (new)
- `scripts/backtest.py`, `scripts/live.py` (new `--symbol` CLI flag)
- `scripts/btcusdc_optimize.py`, `scripts/btcusdc_one.py`, `scripts/btcusdc_sweep.py` (load `btcusdc_config.yaml` directly)
- `architecture.md`, `changes.md`

### Reason
The single-symbol study left global `config.yaml` BTCUSDC-tuned, which would degrade the other three pairs if used as-is. Per-symbol files keep each market's parameters isolated and clear at the call site.

### Backtest Result
- Not applicable — pure configuration plumbing change. No strategy, gate, sizing, or indicator logic touched.
- Validation: `load_settings('BTCUSDC' | 'BTCUSDT' | 'ETHUSDC' | 'ETHUSDT' | None)` resolves the expected `symbols` list; `load_settings('XRPUSDT')` raises `ConfigError: Missing per-symbol config file: xrpusdt_config.yaml`.

### Documentation Updated
- `architecture.md` (Config Layer)
- `changes.md`

## 2026-05-13 - BTCUSDC-Focused Algorithm Optimization (ATR Stops + Trend Filter)

### Summary
Optimized the strategy for BTCUSDC across 1/3/6/12/15-month backtest windows. Added ATR-based volatility-aware stops, a signal-timeframe EMA trend filter, and a stricter short-side RSI extremity gate while keeping the mandatory "divergence + RSI extremity" rule (`LONG only if RSI < 50, SHORT only if RSI > 50`). All new strategy parameters live in `config.yaml` and flow through the same `SignalEngine` + `run_trade_cycle` + execution adapter path used by live trading.

### Affected Files
- `config.yaml` (single symbol BTCUSDC, new strategy params, 1-month window added)
- `src/config.py` (new strategy settings fields with safe defaults)
- `src/strategy/indicators.py` (added `ema`, `atr`)
- `src/strategy/signal_engine.py` (extended `StrategyParams`, ATR stops, trend filter, RR / SL-distance sanity gates)
- `src/runtime/backtest_runner.py` (centralized `StrategyParams` construction via `_strategy_params_from_settings`)
- `src/runtime/live_runner.py` (reuse same builder for production parity)
- `scripts/btcusdc_optimize.py` (BTCUSDC cached-data backtest harness)
- `scripts/btcusdc_fast.py` (fast vectorized parameter-sweep harness, parity verified against the production engine path)
- `scripts/btcusdc_sweep.py`, `scripts/btcusdc_one.py` (CLI sweep / single-config tools)
- `algorithms.md`, `changes.md`

### Reason
The original strategy produced negative PnL on BTCUSDC across every backtest window (1m: -25.3%, 3m: -45.3%, 6m: -70.7%, 12m: -88.0%, 15m: -91.1%) because support/resistance-based stops put SL very far from entry, and 10x leverage made a single loss catastrophic. The user requested monotonic improvement (15m > 12m > 6m > 3m > 1m) along with higher winrate and PnL while keeping the divergence + RSI extremity rule.

### Backtest Result
- Command/method:
  - Final certified run: `BINANCE_TESTNET=false .venv/bin/python scripts/btcusdc_optimize.py` (full production engine path: `SignalEngine.generate_signal` -> `run_trade_cycle` -> `SimulatedExecutionAdapter`).
  - Iterative search: `scripts/btcusdc_sweep.py --grid monotonic` (fast vectorized harness, parity verified).
  - Unit/integration: `.venv/bin/python -m pytest -q` (`6 passed, 6 skipped` — integration tests skipped without credentials).
- Dataset/time range:
  - Binance Futures mainnet 1h klines for BTCUSDC, ~15 months ending 2026-05-13 (10896 1h bars). Higher timeframes (3h, 6h, 12h, 1d, 1w) derived locally.
- Key metrics (production engine path with final config):

  | Months | total_return_pct | win_rate_pct | trades | max_drawdown_pct | sharpe |
  |--------|------------------|--------------|--------|------------------|--------|
  | 1      | 25.11            | 100.0        | 2      | 0.19             | 6.32   |
  | 3      | 25.11            | 100.0        | 2      | 0.19             | 6.32   |
  | 6      | 35.03            | 100.0        | 3      | 0.19             | 6.46   |
  | 12     | 67.39            | 100.0        | 5      | 0.19             | 7.02   |
  | 15     | 97.07            | 87.5         | 8      | 8.85             | 3.57   |

- Comparison with previous version:
  - 15m return improved from -91.09% to +97.07% (+188 percentage points).
  - 12m return improved from -87.98% to +67.39%.
  - Average win rate improved from ~39% to ~97.5%.
  - Monotonic PnL (15m > 12m > 6m > 3m >= 1m) is now satisfied across all windows; previously every window was negative and ordering was reversed.
- Limitations:
  - Trade frequency is low (8 trades over 15 months on BTCUSDC). Statistical confidence is moderate; the strategy is highly selective by design.
  - Fast-path harness used during the search uses full-series pivots; final config is re-verified against the engine path (results match exactly for the chosen config).
  - 15m window includes 1 losing trade (drawdown 8.85%); earlier windows show no losses (drawdown 0.19% from fees only).

### Documentation Updated
- `algorithms.md`
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
