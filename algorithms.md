# algorithms.md

## Strategy Summary

This bot opens BTCUSDC 1h positions on strict confluence:

1. RSI divergence (mandatory)
2. MACD divergence (mandatory unless `require_macd_divergence: false`)
3. RSI extremity gate (LONG only if RSI < `rsi_long_max`; SHORT only if RSI > `rsi_short_min`)
4. Trend filter on signal timeframe (LONG only above EMA, SHORT only below EMA) when `use_trend_filter: true`
5. Multi-timeframe support/resistance context (entry between nearest support and resistance)

A trade is valid only when all required conditions agree on the same direction.

### Current BTCUSDC Tuned Profile

`Loop_20260519_6` is the deployable BTCUSDC champion. It keeps the mandatory
RSI divergence plus the extremity gate and uses a moderate-conviction
trend-filtered ATR setup tuned so that **every** backtest window is strictly
WR > 80 (the user's #1 MUST), strict monotonic `15>12>6>3>1`, all-positive:

- `rsi_period=12`, `rsi_long_max=47`, `rsi_short_min=62`
- `require_macd_divergence=false` (RSI divergence still mandatory; the MACD
  line never gates entries, so `macd_fast/slow/signal` are inert)
- `pivot_window=4`, `divergence_lookback=100`
- `use_trend_filter=true`, `trend_ema_period=225`
- `use_atr_stops=true`, `atr_period=9`, `atr_sl_mult=1.5`, `atr_tp_mult=4.0`
  (RR = 2.67)
- `leverage=25`, `position_equity_ratio=1.0`

Production-path backtest (parity-verified vs the fast harness): 1m +48.5% /
3m +122.3% / 6m +467.1% / 12m +3370.4% / 15m +15127.7%, win-rate
100/100/100/100/90.91 (min 90.91 > 80 on every window), strict monotonic,
24 trades total (1/2/3/7/11), max drawdown 0.5% on 1m–12m and 24.9% on 15m.
This lineage replaces `Loop_20260513_10`, whose 6m window was exactly 80.00%
WR and therefore failed the strict `WR > 80` requirement, and roughly
3.6x's its 15m PnL (`_5` adopted the WR>80 structure at +14210%; `_6`
refined `atr_period 14->9` for +15128% at lower drawdown).

`rsi_long_max=47` is the key edge: it is the intermediate long-extremity
threshold that admits the extra high-conviction longs (24 trades vs the
18-trade over-tight set) without dropping any window to <=80% WR. Two BTCUSDC
properties bound this profile: (1) trade frequency cannot reach the 2-5/month
target while holding WR > 80 — loosening pivots/gates for frequency collapses
WR to 30-45% and blows the account; (2) the reward leg cannot exceed ~4xATR —
TP at 5-8xATR turns the bounded divergence wins into losses and breaks the
WR>80 gate. PnL magnitude is leverage(25) x full-equity x compounding; the
simulator models no liquidation/funding, so the single 15m losing trade is a
real live tail risk.

### Current ETHUSDC Tuned Profile

`Loop_20260519_10` keeps the mandatory RSI divergence plus extremity gate and
the required MACD divergence, and uses a wide-SL / tight-TP bounce geometry on
those high-conviction divergence entries:

- `rsi_period=21`, `rsi_long_max=50`, `rsi_short_min=60`
- `require_macd_divergence=true` (RSI divergence still mandatory)
- `pivot_window=6`, `divergence_lookback=160`
- `use_trend_filter=false`, `trend_ema_period=100` (inactive)
- `use_atr_stops=true`, `atr_period=28`, `atr_sl_mult=2.0`, `atr_tp_mult=0.8`
- `macd_fast=12`, `macd_slow=34`, `macd_signal=7`
- `leverage=17`, `position_equity_ratio=0.98`

Production-path canonical backtest (`scripts/backtest.py --symbol ETHUSDC`,
12-month warmup, real `SignalEngine + run_trade_cycle +
SimulatedExecutionAdapter`): 1m +20.64% / 3m +62.45% / 6m +102.77% / 12m
+332.16% / 15m +1339.17%; win-rate 100/100/88.2/86.5/87.5% (min 86.49%);
strictly monotonic 15>12>6>3>1; all-positive; 2.0–3.2 trades/month; max
drawdown 0.33% on 1m/3m rising to 67.08% on 6/12/15m. The trade set and
win-rate are byte-identical to `Loop_20260519_9` (same 3/6/17/37/48 trades,
same WR), so `atr_period 21→28` and `macd_signal 9→7` are inert on this data;
the entire PnL gain over `_9` (15m +1339.17% vs +955.57%, +40%) is
`leverage 15→17` + `position_equity_ratio 0.95→0.98`. It strictly dominates
`_9` on PnL in every window at the same WR and strict-monotonic consistency.

The tight TP (0.8×ATR) relative to a wide SL (2.0×ATR) gives a high per-trade
hit rate; the slower RSI (`rsi_period=21`) plus long divergence memory
(`divergence_lookback=160`) filter to the highest-conviction reversals. Found
via `scripts/ethusdc_loop.py` (random map + neighbourhood refine,
ETHUSDC-specific reuse of the parity-verified fast engine), then re-validated
on the production path before adoption. Lineage: `Loop_20260519_7`
(first all-targets pass, 15m +181.66%) → `Loop_20260519_8` (round-3 refine,
15m +474.60%) → `Loop_20260519_9` (leverage 10→15, 15m +955.57%) →
`Loop_20260519_10` (leverage 15→17 + equity 0.95→0.98, 15m +1339.17%,
current). **The trading algorithm itself converged at `_8`; `_9`/`_10` are
pure leverage/equity risk-scaling — the entry/exit/geometry is unchanged and
six independent search rounds found no further algorithmic edge in the
parameter space.**

**Caveat:** as with the BNBUSDC tight-TP profile, the high in-sample win-rate
is partly a geometry artifact — a 2.0×ATR stop is rarely reached when the TP
is only 0.8×ATR, but a gap/adverse spike can still hit it live. The real risk
is the unrealised tail loss, not the in-sample variance. **`leverage=17`
(equity 0.98) is very aggressive:** the in-sample max drawdown is ~67% on the
6–15m windows (vs ~62% for `_9` at leverage 15, ~45% for `_8` at leverage
10). Every stated target (WR>80, strict monotonic, ≥2 trades/month,
all-positive, higher PnL) is satisfied, but a single adverse tail at this
leverage is a severe live-account risk; risk-tolerant operators may prefer
`_8` (leverage 10, ~45% DD) or `_9` (leverage 15, ~62% DD) for a smaller
tail. The fast-engine search reported 15m +1890% with a pathological 6m≈12m
plateau then a 15m explosion (an artifact of light warmup at the cache's left
edge); the production path corrected this to a sane, consistent +1339.17% —
the production number is the authoritative one.

### Current BNBUSDC Tuned Profile

`Loop_20260519_2` keeps the mandatory RSI divergence plus extremity gate and
uses a wide-SL / very-tight-TP bounce geometry on a high-conviction divergence
entry:

- `rsi_period=11`, `rsi_long_max=50`, `rsi_short_min=75`
- `require_macd_divergence=false` (RSI divergence still mandatory; MACD line
  never gates entries, so `macd_fast/slow/signal` are inert for this profile)
- `pivot_window=6`, `divergence_lookback=80`
- `use_trend_filter=false`, `trend_ema_period=200` (inactive)
- `use_atr_stops=true`, `atr_period=21`, `atr_sl_mult=6.0`, `atr_tp_mult=0.6`
- `leverage=10`, `position_equity_ratio=1.0`

Production-path backtest (parity-verified vs fast harness): 1m +7.6% / 3m +34.4%
/ 6m +150.6% / 12m +540.8% / 15m +1003.4%, win-rate 100% on every window,
strictly monotonic 15>12>6>3>1, all-positive, 3.0–3.9 trades/month, max
drawdown ~0.2%. This satisfies every BNBUSDC target with margin (WR≫80,
monotonic consistency, PnL, trade frequency). The very tight TP relative to a
wide SL gives a high per-trade hit rate; the stricter RSI inputs (`rsi_period
11`, `rsi_short_min 75`) filter to the highest-conviction reversals so the rare
large stop that drove `_33`'s 41% drawdown no longer occurs in-sample.

**Caveat:** the perfect in-sample win-rate is partly a geometry+sample artifact.
A 6×ATR stop is rarely reached when the TP is only 0.6×ATR, but live trading can
still hit it on a gap/adverse spike; that single tail loss (~10× a typical win)
did not occur in this 15-month window. The profile is excellent but not
risk-free — its real risk is the unrealised tail, not the in-sample variance.

Found via `scripts/bnbusdc_loop.py` (random map + neighbourhood refine with a
drawdown-aware full-pass objective), a BNBUSDC-specific search reusing the
parity-verified fast engine. Champion lineage: `_21` → `_31` (first all-targets
pass) → `_32` (DD-aware) → `_33` (leverage ceiling) → `_20260519_1` (edge
refine, perfect WR) → `_20260519_2` (stricter short gate, +1003% 15m, current).

### Current SOLUSDC Tuned Profile

`Loop_20260519_4` clears the WR>80 requirement while remaining strictly monotonic
(15m > 12m > 6m > 3m > 1m), all-positive, and inside the trades/month band, with
PnL maximized. It keeps the mandatory RSI divergence + extremity gate and adds
MACD-divergence confluence with a fast MACD, on coarse daily/weekly S/R levels:

- `rsi_period=14`, `rsi_long_max=45`, `rsi_short_min=55` (extremity rule preserved)
- `require_macd_divergence=true`
- `macd_fast=7`, `macd_slow=24`, `macd_signal=9` (faster than the 12/26/9 default)
- `pivot_window=6`, `divergence_lookback=50`
- `sup_res_timeframes=[1d, 1w]` (narrowed from 3h/6h/12h/1d/1w)
- `use_trend_filter=false`
- `use_atr_stops=true`, `atr_period=12`, `atr_sl_mult=3.0`, `atr_tp_mult=1.0`
- `leverage=8`, `position_equity_ratio=1.0`

Production-path backtest (`scripts/btcusdc_optimize.py`, mainnet klines, 12m warmup):
1m +28.2% WR100 / 3m +130.9% WR94.1 / 6m +268.3% WR90.3 / 12m +1141.7% WR89.2 /
15m +2073.3% WR90.1; 71 trades over 15m; min WR 89.2% at 12m; 12m/15m max drawdown
~64% (unchanged vs `_3` — same leverage 8). Narrowing `sup_res_timeframes` to the
daily/weekly levels widens the valid-entry zone between the nearest support and
resistance and filters to structurally stronger reversals: +34% more 15m PnL and
+2.5pt higher min win rate at identical leverage and drawdown vs `_3`. Leverage 8
was chosen by the user from the production-path PnL/drawdown curve (lev 7→10 all
pass all four constraints; PnL and drawdown scale ~linearly while
WR/monotonicity/trade-count are leverage-invariant).

Why the fast MACD matters: for SOLUSDC the segment roughly 4-6 months ago is a
drawdown patch. Every selective WR>80 config tested in the macd-off and standard-MACD
regimes lost money there, pushing 6m cumulative return below 3m and breaking
monotonicity. A fast MACD shifts the MACD-divergence pivots so the high-conviction
trade set is net-positive and growing through that segment, which is what unlocks
strict monotonicity at WR>80 (the `Loop_20260518_31` discovery). PnL was then scaled
two ways: (a) leverage 5→7 + equity_ratio 0.95→1.0 (`Loop_20260519_1`, 15m +879%,
DD ~62%); then (b) sharpening the entry edge — `macd_slow` 21→24, `pivot_window`
5→6, `divergence_lookback` 45→50 — which produces fewer, higher-conviction trades
that simultaneously raised 15m PnL to +1164%, lifted min WR to 86.8%, AND lowered
drawdown to ~57% at the same leverage (`Loop_20260519_2`); a 1296-combo fine scan
then confirmed `_2`'s edge is the optimum of its neighborhood. Finally (c) leverage
was raised 7→8 on the converged edge (`Loop_20260519_3`, 15m +1543%, DD ~64%),
the level chosen by the user from the full lev 7→10 PnL/drawdown curve. Finally
(d) the last untouched lever — `sup_res_timeframes` — was swept: narrowing it from
3h/6h/12h/1d/1w to just [1d,1w] lifted 15m PnL to +2073% and min WR to 89.2% at
unchanged leverage/drawdown (`Loop_20260519_4`). `atr_tp_mult` is held at 1.0
because every wider take-profit variant tested across multiple gate settings drops
min win rate below 80, so the user's reward-extension hint is firmly bounded by the
WR>80 MUST. The original `Loop_20260518_7` (macd-off, ~149 trades, ~74% WR) never
satisfied WR>80. With the divergence/MACD/pivot/SL-TP edge, leverage, and S/R
timeframes all explored, further PnL within the WR>80 region is now primarily
leverage-bounded (drawdown grows ~6pt per leverage step).

## Indicators

### RSI Divergence

- Compute RSI on 1h close prices.
- Identify swing pivots using `pivot_window`.
- Bullish divergence: price lower low, RSI higher low. Additionally requires `RSI < rsi_long_max` at the signal candle.
- Bearish divergence: price higher high, RSI lower high. Additionally requires `RSI > rsi_short_min` at the signal candle.

The user-required rule "LONG only if RSI < 50, SHORT only if RSI > 50" is always enforced via `rsi_long_max <= 50` and `rsi_short_min >= 50`. Tightening (e.g., `rsi_short_min = 60`) is permitted because a stricter gate still satisfies the rule.

### MACD Divergence

- Compute MACD line, signal line, and histogram on 1h close prices.
- Use MACD line for divergence against price pivots.
- Bullish divergence: price lower low, MACD higher low.
- Bearish divergence: price higher high, MACD lower high.

### ATR (Average True Range)

- Wilder-style smoothed True Range on 1h with period `atr_period`.
- Used for volatility-aware stop loss / take profit sizing.

### Trend EMA (optional gate)

- EMA on 1h close with period `trend_ema_period`.
- LONG entries require `last_close > ema`; SHORT entries require `last_close < ema`.
- Enabled by `use_trend_filter: true`.

## Support / Resistance

- Build levels from timeframes: 3h, 6h, 12h, 1d, 1w.
- Each timeframe contributes pivot highs (resistance) and pivot lows (support).
- Levels are merged by proximity to remove duplicates.
- Binance Futures does not provide a native 3h kline endpoint, so 3h candles are resampled from 1h data.
- Levels gate entry direction (LONG requires `support < last_price < resistance` and vice versa).
- When `use_atr_stops: false`, levels also determine SL/TP placement (legacy mode).

## Entry and Direction

### Long

- RSI bullish divergence true
- MACD bullish divergence true (if required)
- `RSI < rsi_long_max` (must be <= 50)
- Trend filter passes (price above EMA when enabled)
- Current price between nearest support and nearest resistance

### Short

- RSI bearish divergence true
- MACD bearish divergence true (if required)
- `RSI > rsi_short_min` (must be >= 50)
- Trend filter passes (price below EMA when enabled)
- Current price between nearest support and nearest resistance

## Stop Loss / Take Profit Rules

### ATR-Based Stops (preferred mode, `use_atr_stops: true`)

- LONG: SL = entry - `atr_sl_mult` * ATR, TP = entry + `atr_tp_mult` * ATR.
- SHORT: SL = entry + `atr_sl_mult` * ATR, TP = entry - `atr_tp_mult` * ATR.
- Caps catastrophic loss and enforces a fixed reward/risk ratio = `atr_tp_mult / atr_sl_mult`.

### Support/Resistance Stops (legacy mode, `use_atr_stops: false`)

- LONG: SL slightly below nearest support, TP slightly below nearest resistance.
- SHORT: SL slightly above nearest resistance, TP slightly above nearest support.
- Buffers controlled by `stop_loss_buffer_bps` and `take_profit_buffer_bps`.

### Sanity gates (applied to both modes)

- LONG: requires `sl < entry < tp`; SHORT: requires `tp < entry < sl`.
- `min_rr_ratio`: if > 0, reject plans whose reward/risk is below the threshold.
- `max_sl_distance_pct`: if > 0, reject plans whose SL distance from entry exceeds the threshold (skip when volatility is unusually large).

## Position Sizing

- Position notional = `position_equity_ratio` * equity * `leverage`.
- Default: `leverage=10`, `position_equity_ratio=0.95`.
- Portfolio max open positions = `max_open_positions` (default 1).

## Execution Constraints

- Maker-only required.
- Orders must be post-only.
- If an order would execute immediately, cancel and reprice.

## Backtest Logic

- Backtest reuses production strategy functions for signal generation (`SignalEngine.generate_signal`).
- Single shared `run_trade_cycle` orchestration is invoked by both live and backtest runners.
- Simulated execution models maker fees and post-only behavior.
- Required month windows: 1, 3, 6, 12, 15.
- Runtime loop behavior:
  - Iterate timeline by 1h candles.
  - Update open position with each bar for SL/TP check.
  - Position-limit rejection is enforced by execution adapters with the same rejection reason.
- Duplicate-signal filtering keys accepted entries by the RSI divergence pivot timestamp plus direction. This prevents repeated re-entry from the same stale divergence setup on later candles while still allowing a new trade when a new pivot forms.

A separate fast vectorized harness (`scripts/btcusdc_fast.py`) is used for parameter search. It is mathematically equivalent to the engine path (parity verified against `scripts/btcusdc_optimize.py`) and only serves the iterative tuning loop; production logic still flows through `SignalEngine` + `run_trade_cycle` + `SimulatedExecutionAdapter`. `scripts/bnbusdc_loop.py` is a BNBUSDC-specific search driver (random map + neighbourhood refine) that reuses the same fast engine and scores against the BNBUSDC targets; any winning config it finds is always re-validated on the production path before being adopted (it was for `Loop_20260518_31`, with identical numbers).

## Known Limitations

- Divergence quality depends on pivot identification sensitivity.
- Support/resistance clustering can underfit or overfit volatile regimes.
- Funding, ADL, and liquidation engine effects are simplified in simulation.
- Fast-path harness uses pivots computed on the full base series; for nearly all bars this matches the engine's tail-restricted pivots, but boundary-case pivots near the left edge of a tail may differ by 1-2 trades over a 15-month window (verified against production engine for the final config).
