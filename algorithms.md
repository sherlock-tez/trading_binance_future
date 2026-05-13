# algorithms.md

## Strategy Summary

This bot uses strict confluence across 3 pillars before opening a position on 1h:

1. RSI divergence
2. MACD divergence
3. Multi-timeframe support/resistance context

A trade is valid only when all required conditions agree on the same direction.

## Indicators

### RSI Divergence

- Compute RSI on 1h close prices.
- Identify swing pivots using `PIVOT_WINDOW`.
- Bullish divergence:
  - Price makes lower low
  - RSI makes higher low
  - RSI on signal candle < 50
- Bearish divergence:
  - Price makes higher high
  - RSI makes lower high
  - RSI on signal candle > 50

### MACD Divergence

- Compute MACD line, signal line, and histogram on 1h close prices.
- Use MACD line for divergence against price pivots.
- Bullish divergence:
  - Price lower low
  - MACD higher low
- Bearish divergence:
  - Price higher high
  - MACD lower high

## Support / Resistance

- Build levels from configured higher timeframes.
- Current tuned default: 6h, 12h, 1d.
- Each timeframe contributes pivot highs (resistance) and pivot lows (support).
- Levels are merged by proximity to remove duplicates.
- Optional 3h mode is supported by resampling from 1h, because Binance Futures has no native 3h kline endpoint.

## Entry and Direction

### Long

- RSI bullish divergence true
- MACD bullish divergence true
- Current price is above nearest support

### Short

- RSI bearish divergence true
- MACD bearish divergence true
- Current price is below nearest resistance

## Stop Loss / Take Profit Rules

For 1h trade setup:

- Long:
  - SL slightly below nearest support
  - TP = entry + 2 * (entry - SL)
- Short:
  - SL slightly above nearest resistance
  - TP = entry - 2 * (SL - entry)

This enforces a fixed risk:reward ratio of 1:2 from the entry price.

Buffers are controlled by basis-point config values for stop-loss anchoring.

### Current Tuned Defaults

- RSI period: 20
- MACD: 12 / 26 / 9
- Divergence lookback: 60
- Pivot window: 3
- Stop-loss buffer: 10 bps
- Take-profit buffer config value: 20 bps (kept as a 2x counterpart for consistency)
- Risk:Reward: 1:2 (TP derived from SL distance)

## Position Sizing

- Position notional = 0.95 * available equity.
- Leverage default is 10x.
- Portfolio max open positions = 1.

## Execution Constraints

- Maker-only required.
- Orders must be post-only.
- If an order would execute immediately, cancel and reprice.

## Backtest Logic

- Backtest reuses production strategy functions for signal generation.
- Simulated execution models maker fees and post-only behavior.
- Required month windows: 1, 3, 6, 12, 15.
- Backtest and live now share the same trade-cycle function path for:
  - signal evaluation
  - duplicate-signal guard
  - execution adapter invocation
- Runtime loop behavior:
  - Iterate timeline by 1h candles.
  - Update open position with each bar for SL/TP check.
  - Evaluate all configured symbols at each 1h step.
  - Position-limit rejection is enforced by execution adapters (live and simulator) with the same rejection reason.

## Known Limitations

- Divergence quality depends on pivot identification sensitivity.
- Support/resistance clustering can underfit or overfit volatile regimes.
- Funding, ADL, and liquidation engine effects are simplified in simulation.
