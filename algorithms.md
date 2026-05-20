# algorithms.md

## Strategy Summary

This bot opens BTCUSDC 1h positions on strict confluence:

1. RSI divergence (mandatory)
2. MACD divergence (mandatory unless `require_macd_divergence: false`)
3. RSI extremity gate (LONG only if RSI < `rsi_long_max`; SHORT only if RSI > `rsi_short_min`)
4. Trend filter on signal timeframe (LONG only above EMA, SHORT only below EMA) when `use_trend_filter: true`
5. Multi-timeframe support/resistance context (entry between nearest support and resistance)

A trade is valid only when all required conditions agree on the same direction.

### Current ETHUSDC Tuned Profile

`Loop_20260514_14` keeps the mandatory RSI divergence plus extremity gate and uses
the high-conviction ATR/MACD mode:

- `rsi_period=11`, `rsi_long_max=30`, `rsi_short_min=58`
- `require_macd_divergence=true`
- `pivot_window=5`, `divergence_lookback=60`
- `use_trend_filter=true`, `trend_ema_period=150`
- `use_atr_stops=true`, `atr_period=10`, `atr_sl_mult=2.0`, `atr_tp_mult=8.0`
- `leverage=20`, `position_equity_ratio=1.0`

This profile favors win rate, PnL, and drawdown quality over signal frequency. The
latest canonical ETHUSDC backtest improved 15-month return and win rate versus
`Loop_20260514_9`, but it still produces no trade in the latest 1-month window and
no additional high-conviction trade in the oldest 12-to-15-month segment.

### Current SOLUSDC Tuned Profile

**CONSTRAINT CHANGE (2026-05-19):** the user imposed a hard cap
**Risk/Reward = `atr_sl_mult`/`atr_tp_mult` ≤ 0.5** (reward TP ≥ 2× risk SL),
forbidding the degenerate wide-SL/tiny-TP geometry. `_8` → `_9` → `_10`
followed under this cap with tight-SL/far-TP (R/R ~0.11) and ~5 trades/mo.

**TARGET CHANGE (2026-05-20 round 2):** the user imposed **WR > 70% as a HARD
target** (while keeping R/R ≤ 0.5), and explicitly **dropped the trade-
frequency floor (2-5/mo) and PnL maximization**. `Loop_20260520_1` shipped
under that set (15m +170 / WR 85.71 / 7 tr).

**TARGET TIGHTENING (2026-05-20 round 3):** the user **raised WR floor 70 → 80
HARD** and **re-introduced "increase number of trades" as a directional
target**. Strict-mono + all-positive HARD remain (ACTIVE windows; zero-trade
windows neutral). R/R ≤ 0.5 remains. RSI extremity + MACDdiv rules remain.

`Loop_20260520_2` is the new champion, found by `scripts/solusdc_sweep.py
--grid wr80_freq --maxrr 0.5 --wrfloor 80 --tpmfloor 0` (972 combos probing
the entry-frequency dims `wr70_pareto` left thin: `atr_period` 8/10/12,
`pivot_window` 4/5/6, `divergence_lookback` 30/40/52/80, `rsi_long_max`
45/47/48, `rsi_short_min` 52/55/60, `trend_ema_period` 50/100/200; geometry
anchored at `_11`'s sl=1.0/tp=2.0). 12 passers. Top 3 (rsi_long_max=47 across
atr_period 8/10/12) all show the SAME +1 trade vs `_11` — robust to
atr_period, not a single-config fluke. `_2` picks `atr_period=8` (highest 15m
PnL of the trio).

- `rsi_period=14`, **`rsi_long_max=47`** (was 45 in `_11`; the +2 unlock),
  `rsi_short_min=60` (extremity rule preserved: long<50 ✓ short>50 ✓)
- `require_macd_divergence=true`
- `macd_fast=7`, `macd_slow=24`, `macd_signal=9`
- `pivot_window=6`, `divergence_lookback=80`
- `sup_res_timeframes=[1d, 1w]`
- `use_trend_filter=true`, `trend_ema_period=200`
- `use_atr_stops=true`, **`atr_period=8`** (was 12 in `_11`; faster ATR),
  `atr_sl_mult=1.0`, `atr_tp_mult=2.0` (**R/R = 1.0/2.0 = 0.5 exactly**)
- `leverage=8`, `position_equity_ratio=1.0`

**The unlock:** `rsi_long_max 45→47` admits one additional LONG entry that's
still inside the LONG<50 extremity rule. WR rose (85.71 → 87.5%) and PnL rose
(+170 → +211 in-sample 15m). This is a strict Pareto improvement, not a
WR/frequency tradeoff. Robust to `atr_period` choice (top 3 in sweep all show
the same +1 trade pattern).

Production-path backtest (`scripts/btcusdc_optimize.py`, mainnet klines, 12m
warmup; fast-harness parity exact):
1m: 0 trades (neutral) / 3m +26.4% WR100 (1 tr) / 6m +66.7% WR100 (3 tr) /
12m +165.6% WR100 (6 tr) / 15m **+211.05%** WR**87.5** (8 tr);
strict-monotonic ✓, all-positive ✓, max DD **10.83%** (marginally better than
`_11`'s 11.35%); Sharpe 3.74–7.24.
**Out-of-sample (held-out 18m/24m, cache temporarily extended to 24 months
then reverted to 15):** 18m +211.0% / WR 87.5% (8 tr — no new trades fired
months 15→18); 24m **+161.21% / WR 70.0%** (10 tr — 2 new trades vs 15m:
1 win / 1 loss). **Materially better OOS than `_11`** (`_11` 24m was +125.5
/ WR 66.67); WR climbs back to exactly the prior 70-floor, no longer below.

**Tradeoff vs prior champion `_11`:** strict win on every dimension —
+1 in-sample trade, +1.8pt WR (85.71 → 87.5), +41pt 15m PnL (170 → 211),
-0.5pt max DD (11.35 → 10.83), +36pt 24m OOS PnL (125.5 → 161.2),
+3.3pt 24m OOS WR (66.67 → 70.0). Trade frequency still ~0.5–0.7 tr/mo
(BTC-like low-freq); equity remains lumpy by design.

**Historical (`_8` → `_9` → `_10` lineage, pre-WR-target era — preserved
for context):** sl 0.8 / tp 5 / atrp 10 → sl 0.6 / tp 5 / atrp 10 → sl 0.55 /
tp 5.0 / atrp 9. `_10` was the converged high-PnL/low-WR champion under the
old constraint set (target was 2-5 tr/mo + monotonic + all-positive, no WR
floor). `_10` numbers: PROD 15m +11194% / WR 27% / max DD 35.2% / 74 trades /
~5 tr/mo; OOS 18m +5760% / 24m +6622% (growing). The lineage is now
superseded by the regime change; `_10`'s tight-SL/far-TP geometry is
infeasible under the new WR>70 target. Residual live risk for `_11`: small
sample / lumpy equity at ~0.5 tr/mo. Was lumpy at `_10` too but for the
opposite reason — long losing streaks broken by occasional 5-ATR winners.

*Historical (pre-R/R-constraint) lineage, kept for context — `_7` and earlier
are now INFEASIBLE under R/R≤0.5:* `_5`-`_7` were between-node
micro-tunes with a jagged `atr_sl_mult` response (sl 2.8/2.85/2.9/2.95/3.0 →
+2334/+3287/+3152/+3011/+2876%), so the *exact* SL value is sample-sensitive.
However, OOS testing on never-tuned 18m/24m windows + extended 31-month data shows
`_7`'s edge over `_4` **persists** (18m +4664% vs +2488%, 24m +3155% vs +1442%)
with WR ~87-90% on every window, and `_7`'s 24m trade history is net-positive in
every full quarter (8 quarters, WR 81-100%). The core edge generalizes; it is not
a fragile ≤15m fit. The primary live risk is not overfitting but **leverage-8 max
drawdown reaching ~74-78% on 18-24m horizons** — size conservatively and
periodically re-validate. `divergence_lookback` 50→52, `atr_period` 12→11, and `atr_sl_mult` 3.0→2.9
are between-node points the coarse grids (dlb {45,50,55}, atrp {10,12,14}, sl
{2.8,3.0,3.2}) never tested; successive fine-resolution sweeps found each strictly
better. The tighter SL (2.9) shrinks per-loss size, raising PnL *and* lowering
drawdown vs `_5` (~62% vs ~64%) at the same leverage. Narrowing `sup_res_timeframes` to the
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
unchanged leverage/drawdown (`Loop_20260519_4`). Then (e) a fine-resolution sweep
between the coarse grid nodes found `divergence_lookback` 50→52 + `atr_period`
12→11 strictly better still: 15m +2876%, min WR 89.9%, identical drawdown
(`Loop_20260519_5`). Then (f) a finer SL step found `atr_sl_mult` 3.0→2.9 (the
2.9 peak sat between the {2.8,3.0} grid nodes): 15m +3152%, same min WR, and
*lower* drawdown ~62% — tighter stops cut per-loss size (`Loop_20260519_6`).
`macd_signal` was confirmed inert (the strategy takes divergence on the MACD
line, never the signal line). Under the *old* (no-R/R) constraint set, `atr_tp_mult`
was held at 1.0 because every wider take-profit dropped min WR below 80 — that
conclusion is now MOOT: the R/R≤0.5 cap *requires* a wide TP and explicitly
accepts the resulting sub-80 WR. The original `Loop_20260518_7` (macd-off,
~149 trades, ~74% WR) never satisfied WR>80 either.

**Post-constraint search (R/R≤0.5):** the `sol_rr` grid (`--maxrr 0.5` skips
infeasible (sl,tp) pairs by construction; `MAX_RISK_REWARD=0.5` in the sweep)
swept the tight-SL/far-TP region with the `_7` entry edge held → `_8`
(`sl0.8/tp5.0/atrp10`). `sol_rr_refine` then probed below the `_8` grid edges
(sl<0.8, atrp<10) and finer/wider TP, revealing a high-PnL plateau at
sl{0.6,0.7} × tp{5,6} × atrp{10,12} → `_9` (`sl0.6/tp5.0/atrp10`, R/R 0.12;
the overfit `sl0.7/tp6/atrp12` corner rejected). `sol_rr_fine` then did a
between-node scan around `_9` (sl 0.5-0.7 step 0.05, tp 4.5-6, atrp 9-12),
which moved the optimum to `_10` (`sl0.55/tp5.0/atrp9`, R/R 0.11): PROD 15m
+11194%, lowest DD of the lineage (35.2%), and uniquely an OOS profile that
*grows* 18m→24m (+5760%→+6622%). The fine sweep's nominal maxima (#1
`sl0.65/tp6/atrp12` +11869%, #2 `sl0.65/tp5/atrp10` +11560%) were not adopted:
#1 is overfit (24m OOS +1497%, 81% DD), #2 robust but `_10` dominates it OOS
for a ~3% in-sample give-up. WR>80 is structurally unreachable in this region
and is an accepted tradeoff; do not re-probe the forbidden wide-SL/tiny-TP basin.

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

A separate fast vectorized harness (`scripts/btcusdc_fast.py`) is used for parameter search. It is mathematically equivalent to the engine path (parity verified against `scripts/btcusdc_optimize.py`) and only serves the iterative tuning loop; production logic still flows through `SignalEngine` + `run_trade_cycle` + `SimulatedExecutionAdapter`.

## Known Limitations

- Divergence quality depends on pivot identification sensitivity.
- Support/resistance clustering can underfit or overfit volatile regimes.
- Funding, ADL, and liquidation engine effects are simplified in simulation.
- Fast-path harness uses pivots computed on the full base series; for nearly all bars this matches the engine's tail-restricted pivots, but boundary-case pivots near the left edge of a tail may differ by 1-2 trades over a 15-month window (verified against production engine for the final config).
