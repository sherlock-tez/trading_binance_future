# architecture.md

## Overview

The system is a modular Python application that separates:

- Data ingestion
- Strategy computation
- Execution adapters
- Notifications
- Runtime orchestrators

A shared strategy core is used by both live trading and backtest simulation.

## Components

### Config Layer

- Loads secrets from `.env` (`BINANCE_FUTURES_API_KEY`, `BINANCE_FUTURES_API_SECRET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).
- Loads non-secret runtime and strategy parameters from YAML in the project root.
- Per-symbol files: `btcusdc_config.yaml`, `btcusdt_config.yaml`, `ethusdc_config.yaml`, `ethusdt_config.yaml`. Each carries its own `symbols`, strategy params, and backtest settings so symbols can be tuned independently.
- `load_settings(symbol)` resolves `{symbol}_config.yaml` (case-insensitive). Missing per-symbol files raise `ConfigError` instead of silently falling back. `load_settings()` with no symbol reads the legacy `config.yaml`.
- Entry-point scripts (`scripts/backtest.py`, `scripts/live.py`) accept `--symbol`. Symbol-specific harnesses (`scripts/btcusdc_*.py`) are symbol-parametric via the `SWEEP_SYMBOL` env var. Per-symbol search drivers (`scripts/bnbusdc_loop.py`, `scripts/ethusdc_loop.py`) reuse the parity-verified fast engine for random + neighbourhood-refine parameter search against that symbol's targets; any winning config is always re-validated on the production path before adoption.
- Validates leverage, maker mode, windows, and that `symbols` is non-empty.

### Data Layer

- Historical warm-up via Binance Futures REST klines.
- Real-time 1h updates via Binance Futures WebSocket kline streams.
- Multi-timeframe cache used by support/resistance module.
- Timeframe adaptation:
  - Unsupported intervals are resampled from supported base intervals.
  - 3h candles are generated from 1h candles.
  - When a resampled timeframe needs more base candles than Binance allows in one kline request, the data service paginates REST calls and then resamples the combined base series.

### Strategy Layer

- `indicators.py`: RSI and MACD calculations.
- `divergence.py`: pivot detection and divergence conditions.
- `support_resistance.py`: level extraction and clustering.
- `signal_engine.py`: strict confluence, SL/TP generation, trade plan creation.

### Execution Layer

- Live adapter:
  - Binance Futures signed API calls
  - Leverage setup
  - Maker-only post-only order flow with cancel/reprice retries
- Simulation adapter:
  - Simulated post-only fills
  - Fee, PnL, and balance accounting
- Shared execution interface:
  - Both live and simulation paths implement the same `execute_trade_plan` contract.
  - Backtest and live orchestration call the same trade-plan function signatures.

### Notification Layer

- Telegram outbound messages for signal, order, startup, and shutdown events.
- Live startup/shutdown notifications include the current account wallet balance, available balance, unrealized PnL, derived equity, configured leverage, position equity ratio, max open positions, symbols, and testnet/mainnet mode.

### Runtime Layer

- Live runner:
  - 1h decision loop across symbols
  - Calls shared runtime trade-cycle function
  - Sends a read-only account/config snapshot to Telegram when the runner starts and when it stops.
- Backtest runner:
  - Loads historical windows (3m, 6m, 12m, 15m)
  - Calls the same shared runtime trade-cycle function as live runner

### Shared Trade Cycle

- `runtime/trade_cycle.py` centralizes one cycle of:
  - signal generation
  - duplicate signal filtering
  - execution adapter call
- Both live and backtest runners call this exact function.
- Duplicate signal filtering uses the signal direction plus the underlying RSI divergence pivot timestamp when available, falling back to candle signal time for older plans/tests. This keeps live and backtest behavior aligned and avoids repeated execution attempts from one unchanged divergence setup.
- Adapter swap happens only at execution layer:
  - live: `BinanceFuturesExecutor`
  - backtest: `SimulatedExecutionAdapter`

## Data Flow

1. Ingest and update candles.
2. Compute indicators and levels.
3. Generate candidate trade plan.
4. Route trade plan to execution adapter.
5. Publish events to Telegram.
6. Persist metrics/logs for analysis.

## Safety Controls

- Testnet-first configuration.
- Maker-only enforcement.
- Max open positions.
- Retry cap for reprice loops.

## External Integrations

- Binance Futures REST + WebSocket APIs.
- Telegram Bot API.
