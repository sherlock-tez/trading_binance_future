# Trading Binance Futures Bot

Multi-symbol Binance Futures trading bot with:

- Real-time market data and 1h signal evaluation
- RSI + MACD divergence strategy with multi-timeframe support/resistance
- Maker-only order execution
- Telegram notifications
- Backtesting and simulation that reuse production strategy code

## Quick Start

1. Create a virtual environment and install dependencies.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` and fill credentials.

```bash
cp .env.example .env
```

3. Review non-secret settings in `config.yaml`.
	- All runtime and indicator settings now live in `config.yaml`.
	- `.env` only contains Binance and Telegram secrets.

4. Run backtest.

```bash
python scripts/backtest.py
```

	- Historical klines are cached locally under `.cache/binance_klines/`.
	- Re-running backtest reuses local cache files for covered ranges instead of refetching from Binance.

5. Run live mode.

```bash
python scripts/live.py
```

## Strategy Rules

- Evaluate trade signals on 1h closed candles.
- Strict confluence is required:
	- RSI divergence
	- MACD divergence
	- Support/resistance context
- Direction filter:
	- Long only when RSI < 50 and bullish divergence setup confirms.
	- Short only when RSI > 50 and bearish divergence setup confirms.
- Support/resistance source timeframes are configurable.
	- Current default profile uses 6h, 12h, 1d.
	- Optional 3h is supported via 1h resampling because Binance Futures does not expose native 3h candles.

### Current Tuned Defaults

- RSI period: 20
- MACD: 12 / 26 / 9
- Divergence lookback: 60
- Pivot window: 3
- Stop-loss buffer: 10 bps
- Take-profit buffer config value: 20 bps (kept aligned with 1:2 ratio intent)
- Risk:Reward: 1:2 (TP derived from entry and SL distance)

## Backtest Windows

- Required windows: 1, 3, 6, 12, 15 months.
- Configure with `backtest.month_windows` in `config.yaml`.
- Backtest uses the same signal engine and trade-plan function calls as live mode.
- Local kline cache is environment-aware (`testnet`/`mainnet`) and persisted between runs.

## Safety

- Default mode should be testnet first.
- Maker-only mode is enforced by post-only order flow.
- Max open positions is configurable and defaults to 1.
- Position sizing defaults to 0.95 equity ratio with leverage 10x.
- This software is educational and operational at your own risk.
