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

`Loop_20260524_3` is the current ETHUSDC baseline. It explicitly rejects
`Loop_20260524_2` because `_2` improved returns only by changing
`position_equity_ratio 0.98→1.0`, which is sizing rather than algorithmic edge.
ETHUSDC now treats `position_equity_ratio=0.98` as pinned alongside leverage
17. The strict-monotonic MUST is still not fully solved: the high-WR signal
fires once in month 1 and no second qualifying entry appears until the 4-6m
window, so `1m == 3m` remains the structural ETHUSDC floor. Every later pair
(`3m<6m<12m<15m`) holds strictly.

- `rsi_period=24`, `rsi_long_max=45`, `rsi_short_min=50`
- `require_macd_divergence=true` (RSI divergence still mandatory)
- `pivot_window=5`, `divergence_lookback=120`
- `use_trend_filter=true`, `trend_ema_period=200`
- `use_atr_stops=true`, `atr_period=9`, `atr_sl_mult=1.0`, `atr_tp_mult=2.0`
  (**R/R = 0.5 exactly — at the constraint cap**)
- `macd_fast=8`, `macd_slow=21`, `macd_signal=7`
- `leverage=17` and `position_equity_ratio=0.98` (both pinned; not tuning levers)

Production-path canonical backtest (`SWEEP_SYMBOL=ETHUSDC .venv/bin/python
scripts/btcusdc_optimize.py --windows 1,3,6,12,15`, real `SignalEngine +
run_trade_cycle + SimulatedExecutionAdapter`):
1m +10.50% / 3m +10.50% / 6m +86.51% / 12m +195.04% / 15m **+276.75%**;
**win-rate 100/100/100/100/100% (min 100% across every window)**;
all-positive; **max drawdown 0.33% across all windows**; Sharpe rises
4.27→6.74→8.16 on 6m→12m→15m. 6 trades total over 15 months (1/1/3/5/6 by
window). Strict-monotonic holds for 3m→6m→12m→15m; 1m=3m tie remains
unresolved.

The favorable R:R geometry (tp 2.0×ATR vs sl 1.0×ATR) inverts the prior
champion's per-trade payoff: each win pays ~2× the risk per trade, so even
with the much lower expected hit rate of "near-tp first," the wide reward
distance plus the trend-filter + RSI-extremity + MACD-divergence triple gate
filters to high-conviction entries that hit TP all 5 times in the cache.
Trade-frequency cost: the strict signal stack only fires every ~3 months on
average, so live there can be long inactive periods. The 100% WR is
small-sample (n=5) and almost certainly does not generalize — a more
realistic forward-WR estimate is the BTC analogue's ~90.9% at n=11.

Search history this regime: `scripts/ethusdc_loop.py` refine, S/R timeframe
sweep, MACD-geometry probe, ATR-only probe, duplicate-policy probe, ETHUSDT-
shape probe, and targeted RSI-gate probe all kept R/R ≤ 0.5 and leverage
unchanged. Configs that admitted the missing April 2026 3m trade also admitted
older losing clusters and collapsed 6m/12m/15m WR, typically below 70%. True
Tier A (`all_positive + strict_monotonic + WR>80`) still has not surfaced for
ETHUSDC across the ~49k prior evals plus the 2026-05-24 probes. Future searches
must not tune `position_equity_ratio`; it is pinned with leverage.

**Caveats:**
1. Sample size is tiny (6 trades over 15 months). Treat the 100% in-sample
   WR as the upper bound, not the expectation.
2. Long inactivity periods are expected. Live operators should monitor that
   the trend filter + divergence stack isn't permanently quiet.
3. `leverage=17` with `position_equity_ratio=0.98` is still aggressive. With
   DD ~0.33% in-sample the leverage choice looks safe, but only 6 trades have stressed it. A
   single adverse tail at this leverage remains a live-account risk.

### Current BNBUSDC Tuned Profile

`Loop_20260524_1` is the current BNBUSDC champion under the user's hard
WR>80 + strict monotonic + R/R<=0.5 regime. It is the first verified BNBUSDC
profile in this regime that improves over the prior champion on both PnL and
trade count for every requested window (1m/3m/6m/12m/15m), while preserving
leverage 10 and the mandatory RSI-divergence + extremity rule.

- `rsi_period=5`, `rsi_long_max=46`, `rsi_short_min=70`
  (mandatory extremity gate preserved: LONG only RSI<46, SHORT only RSI>70)
- `require_macd_divergence=false` -- RSI-divergence ONLY; MACD params are inert
  for entry gating (`macd_fast=10`, `macd_slow=40`, `macd_signal=9`)
- `pivot_window=20`, `divergence_lookback=220`
- `sup_res_timeframes=[3h, 6h, 12h, 1d, 1w]`
- `use_trend_filter=true`, `trend_ema_period=150`
- `use_atr_stops=true`, `atr_period=34`, `atr_sl_mult=2.0`,
  `atr_tp_mult=4.1` -> **Risk/Reward = 2.0/4.1 = 0.488**
- `leverage=10` (PINNED, user requirement), `position_equity_ratio=1.0`

Production-path backtest (`SWEEP_SYMBOL=BNBUSDC .venv/bin/python
scripts/btcusdc_optimize.py --windows 1,3,6,12,15`, refreshed BNBUSDC 1h
cache rows=10896, last bar from the current 15m cache): 1m +43.91% / 3m
+132.46% / 6m +422.09% / 12m +831.28% / **15m +2013.27%**. WR is **100 /
83.33 / 88.89 / 84.62 / 84.21%**, so every window is strictly above 80.
Performance is strictly
monotonic (`15m > 12m > 6m > 3m > 1m`), all windows are positive, max
drawdown is 19.39%, 15m Sharpe is 4.597, and trade counts are 2 / 6 / 9 / 13 /
19.

Why the new edge works: `Loop_20260523_2` was a sparse high-WR quality stack
that satisfied WR/order/R/R but could not increase 1m trade count. `_1` moves
to a low-RSI-period, wider-pivot, longer-lookback family that admits the second
1m trade and many more 15m signals, then restores WR discipline with a deeper
SHORT extremity gate (`rsi_short_min=70`), broader 3h-1w S/R context, a faster
150-EMA trend filter, and a small stop-distance cap (`max_sl_distance_pct=0.022`).
The final edge was the reward leg: `atr_tp_mult 4.0 -> 4.1` kept R/R below the
0.5 cap while lifting the 6m/12m compounded return enough to beat `_2` on every
requested PnL window.

Full production comparison vs `Loop_20260523_2`: returns improve on every
requested window (+16.91/+101.07/+403.67/+767.48/+810.43 -> +43.91/+132.46/
+422.09/+831.28/+2013.27), and trade counts improve on every requested window
(1/4/8/10/12 -> 2/6/9/13/19). The tradeoff is higher max drawdown
(13.32% -> 19.39%) and lower 3m/12m/15m WR than `_2`, but every WR remains
strictly above the user's 80% floor.

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

`Loop_20260521_3` is the new champion. The optimization went through four
strict-Pareto steps in this session (each step strictly better than the prior
on every dimension):

1. `_2` (`Loop_20260521_1`, atr_period=8, rsi_long_max=47): wr80_freq sweep
   winner — 972 combos. The `rsi_long_max 45→47` unlock added +1 LONG entry
   inside the LONG<50 extremity rule.
2. `_3` (`Loop_20260521_2`, atr_period=3): wr80_atr_fine probe found a smooth
   monotonic atr_period gradient from 9→3 (15m PnL: 207.78→402.22) that
   sharply breaks at atr_p=2 (3m -19%/WR0, 6m -12%/WR33). atr_p=3 is the peak.
3. `_4` (`Loop_20260521_3`, **pivot_window=5**, **rsi_short_min=65**):
   `wr80_atr3_cross` sweep at the atr_p=3 anchor (864 combos, 66 passers)
   found the (pivot=5, rsi_short=65) combo unlocks +1 LONG trade at 15m
   (8→9) and +1 at 12m (6→7) while keeping all 9 in-sample trades winning.

`Loop_20260521_3` params (only `pivot_window` and `rsi_short_min` changed
vs `_3`):
- `rsi_period=14`, `rsi_long_max=47`, **`rsi_short_min=65`** (was 60 in `_3`;
  extremity rule preserved: long<50 ✓ short>50 ✓)
- `require_macd_divergence=true`
- `macd_fast=7`, `macd_slow=24`, `macd_signal=9`
- **`pivot_window=5`** (was 6 in `_3`), `divergence_lookback=80`
- `sup_res_timeframes=[1d, 1w]` (invariant: [6h,12h,1d,1w] gives identical
  trade path in the sweep)
- `use_trend_filter=true`, `trend_ema_period=200`
- `use_atr_stops=true`, `atr_period=3` (from `_3`),
  `atr_sl_mult=1.0`, `atr_tp_mult=2.0` (**R/R = 1.0/2.0 = 0.5 exactly**)
- `leverage=8`, `position_equity_ratio=1.0`

**Why pivot_window=5 + rsi_short_min=65 work together:** A shorter pivot
window detects more local extrema (more candidate S/R levels). On the SHORT
side, a higher `rsi_short_min` (65 vs 60) means SHORT entries require the
RSI to be deeper into the overbought zone — combined with the new (shorter)
pivot S/R levels, this gates SHORTs more strictly. The net is +1 LONG trade
in the 6-12m window (the prior pivot=6 missed a pivot level that pivot=5
catches, and at the same instant the LONG-side `rsi_long_max=47` was
already permissive). The rsi_short_min=65 by itself with pivot=6 doesn't
change anything (sweep confirmed); the combination is what unlocks.

Production-path backtest (`scripts/btcusdc_optimize.py`, mainnet klines, 12m
warmup; fast-harness parity exact):
1m: 0 trades (neutral) / 3m +33.4% WR100 (1 tr) / 6m +85.5% WR100 (3 tr) /
12m +269.7% WR100 (7 tr) / 15m **+533.77%** WR **100%** (9 tr);
strict-monotonic ✓, all-positive ✓, max DD **0.16%** (no SL hit in-sample);
Sharpe 6.83.
**Out-of-sample (held-out 18m/24m, cache temporarily extended to 24m then
reverted to 15):** 18m **+533.77% / WR 100%** (9 tr — no new trades fired
15→18); 24m **+545.54% / WR 83.33%** (12 tr — 3 new vs 15m: 1W/2L). 24m
OOS PnL exceeds in-sample 15m (+545.54 > +533.77) because the OOS winner
on compounded equity outweighs the 2 losers. 24m OOS WR 83.33% has
comfortable headroom over the >80 hard floor. 24m max DD halves vs `_3`
(15.17 → 8.73%).

**Tradeoff vs prior champion `_3`:** strict win on every dimension —
+131pt 15m PnL (402→534), +1 trade in-sample (8→9), +0 WR delta but +1
trade at 100% in-sample, +219pt 24m OOS PnL (327→546), +3.3pt 24m OOS WR
(80.0→83.33), -6.4pt 24m OOS DD (15.17→8.73). No tradeoff dial.

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

### Current XRPUSDC Tuned Profile

`Loop_20260520_8` is the deployable XRPUSDC champion under the operator MUST
**Risk/Reward ≤ 0.5** (reward ≥ 2× risk), enforced via the existing
`min_rr_ratio=2.0` config key on both the production path and the fast engine.
The reward-heavy geometry inverts the prior tight-TP/wide-SL family used by
the other symbols: SL is now close (3×ATR) and TP is far (10×ATR), and the
configuration is trend-filtered on a fast RSI with mandatory MACD-divergence
confluence. Mandatory RSI divergence + extremity gate preserved
(`rsi_long_max=40` → LONG only if RSI<50; `rsi_short_min=55` → SHORT only if
RSI>50):

- `rsi_period=7`, `rsi_long_max=40`, `rsi_short_min=60` (extremity rule preserved)
- `require_macd_divergence=true` (RSI divergence still mandatory; MACD-div
  confluence is the additional filter that admits only the highest-conviction
  reversals)
- `pivot_window=6`, `divergence_lookback=60`
- `use_trend_filter=true`, `trend_ema_period=100`
- `use_atr_stops=true`, `atr_period=7`, `atr_sl_mult=3.0`, `atr_tp_mult=10.0`
  (reward/risk = 3.33, risk/reward = 0.30 ≤ 0.5 ✓)
- `macd_fast=7`, `macd_slow=24`, `macd_signal=9`
- `min_rr_ratio=2.0` (the operator MUST; rejects any plan with reward/risk < 2)
- `leverage=25` (operator-pinned), `position_equity_ratio=0.9` (also pinned: composes with leverage into effective exposure)

Production-path backtest (`scripts/btcusdc_optimize.py --symbol XRPUSDC`, real
`SignalEngine + run_trade_cycle + SimulatedExecutionAdapter`, 12-month warmup,
mainnet klines), exact parity vs the fast harness: 1m +319.87% / 3m +4052.85%
/ 6m +4052.85% / 12m +84537.00% / 15m +84537.00%; win-rate 100/100/100/100/100;
all-positive; max drawdown 0.45% on every window; Sharpe 4.2–7.2; 6 trades
over 15m (2/4/4/6/6).

**Important: strict-monotonicity is intentionally relaxed for this profile.**
The 3m and 6m windows are equal (both +1537.29%) and the 12m and 15m windows
are equal (both +33268.68%) — the reward-heavy geometry yields only ~0.3
trades/month so consecutive windows that contain the same set of closed
trades produce identical cumulative returns. ~18,000 evaluations across two
independent campaigns confirmed that the constraint set {WR>80 +
strict-monotonic + ≥2 trades/mo + RR≤0.5 + leverage 25 + divergence +
extremity gate} is mutually infeasible for XRPUSDC: a far take-profit
(≥2×SL) is hit so rarely that the only WR-100 configs are too sparse to
build a strictly increasing 1<3<6<12<15 curve. The operator chose
{WR>80, RR≤0.5, all-positive, +PnL} as the binding constraints and
explicitly accepted dropping strict-monotonicity for this symbol. The
trades/month target (2–5/mo) is also missed by ~6–10× for the same
structural reason.

This profile is a hard departure from `Loop_20260519_10` (SL 5.0 / TP 1.0,
risk/reward 5.0 — violated the new RR MUST). Lineage: shipped starter
(broken) → `Loop_20260519_9` (first WR-100 strict-monotonic champion,
15m +8035%, RR 5.0) → `Loop_20260519_10` (PnL refine, 15m +11207%, RR 5.0,
strict-monotonic) → `Loop_20260520_6` (RR≤0.5 MUST added; 15m +33269% with
WR-100 all-positive but strict-monotonic dropped) → `Loop_20260520_7` (pure
algorithmic refine under pinned exposure: `atr_tp_mult 10→8`, `rsi_period
9→7`, `macd_slow 26→24`, `rsi_short_min 55→60`; 4 of 5 windows improved,
15m PnL +9% to +36232%, +1 trade, 1m regressed +314→+238% as the cost)
→ `Loop_20260520_8` (algorithmic refine reversing the TP tightening:
`atr_tp_mult 8→10`, `macd_signal 7→9`; strict Pareto improvement on **all
5 windows**, 15m PnL +133% to +84537%, 1m recovers to +320%, risk/reward
0.30 ≤ 0.5 ✓; current).

**Caveats:** (1) The 100% in-sample WR over 5 trades is a tiny sample and
partly luck — the reward-heavy geometry would normally have a *lower* hit
rate than a tight-TP design because the close 3×ATR stop is reached more
easily than the far 10×ATR take-profit; in-sample none of the 5 trades were
stopped, but live the WR distribution will look different. (2) Trade
frequency is very low (~0.3/mo on the 15m window); the strategy is
effectively a high-conviction divergence reversal sniper at this RR. (3) At
leverage 25 the simulator (no funding/liquidation) still doesn't model the
live tail; a single live stop at this leverage is large but not
account-wiping since SL is only 3×ATR (`5×ATR*25` would be ~125% loss in the
old geometry; `3×ATR*25` is ~75% loss — still severe).

The wide SL (5.0×ATR) relative to a tight TP (1.2×ATR) gives a very high
per-trade hit rate; the fast RSI (`rsi_period=7`) plus the short trend EMA
(`trend_ema_period=50`) filter to high-conviction reversals aligned with the
local trend, and PnL magnitude is leverage(25) × full-equity × compounding.
Found via `scripts/xrpusdc_loop.py` (random map + neighbourhood refine reusing
the parity-verified fast engine, scored against the XRPUSDC targets with PnL
as the dominant objective), then re-validated on the production path before
adoption (identical numbers).

**Caveat:** as with the BNBUSDC/ETHUSDC/SOLUSDC tight-TP profiles, the perfect
in-sample win-rate is partly a geometry artifact — a 5.0×ATR stop is rarely
reached when the TP is only 1.2×ATR, but a gap/adverse spike can still hit it
live. The real risk is the unrealised tail loss, not the in-sample variance;
the simulator models no liquidation/funding, so at leverage 25 a single
adverse 15m tail is a real live risk. Trade frequency (≈1.0–1.13 trades/month)
is below the 2–5/month target: every higher-frequency variant tested dropped a
window below WR 80 or broke strict monotonicity, and per the operator's
standing preference PnL is maximized over forcing the trade-count band when
they conflict. The 15-month window also carries the documented left-edge
warmup handicap (fewer early divergence pivots than a 12-month-warmup span),
which structurally caps 15m trade count.

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

A separate fast vectorized harness (`scripts/btcusdc_fast.py`) is used for parameter search. It is mathematically equivalent to the engine path (parity verified against `scripts/btcusdc_optimize.py`) and only serves the iterative tuning loop; production logic still flows through `SignalEngine` + `run_trade_cycle` + `SimulatedExecutionAdapter`. `scripts/bnbusdc_loop.py` is a BNBUSDC-specific search driver (random map + neighbourhood refine) that reuses the same fast engine and scores against the BNBUSDC targets; any winning config it finds is always re-validated on the production path before being adopted (it was for `Loop_20260518_31`, with identical numbers). `scripts/ethusdc_loop.py` and `scripts/xrpusdc_loop.py` are the analogous ETHUSDC/XRPUSDC search drivers; the XRPUSDC one scores with PnL as the dominant objective and trades/month as a soft tiebreaker (WR>80 + all-positive + strict-monotonic remain hard gates), and its `Loop_20260520_8` champion (RR≤0.5 regime) re-validated on the production path with identical numbers. The harness's generators now enforce reward ≥ 2× risk in the sample space when that MUST is active, and pin `position_equity_ratio` alongside `leverage` so sizing-creep cannot masquerade as algorithmic improvement.

## Known Limitations

- Divergence quality depends on pivot identification sensitivity.
- Support/resistance clustering can underfit or overfit volatile regimes.
- Funding, ADL, and liquidation engine effects are simplified in simulation.
- Fast-path harness uses pivots computed on the full base series; for nearly all bars this matches the engine's tail-restricted pivots, but boundary-case pivots near the left edge of a tail may differ by 1-2 trades over a 15-month window (verified against production engine for the final config).
