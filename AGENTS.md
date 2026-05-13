# AGENTS.md

## Required Project Context

Before making any code, design, architecture, or behavior changes, the agent must read and understand the following files:

- `algorithms.md`
- `architecture.md`
- `changes.md`

These files are the source of truth for the project’s logic, system structure, and change history.

## Mandatory Update Rule

Whenever the agent makes a change that affects algorithms, architecture, behavior, APIs, workflows, dependencies, or implementation decisions, the agent must update the relevant documentation files:

- Update `algorithms.md` when algorithmic logic, data flow, calculations, heuristics, or processing steps change.
- Update `architecture.md` when system structure, components, interfaces, storage, deployment, or integration design changes.
- Update `changes.md` for every meaningful change, including the date, summary, affected files, and reason for the change.

## Backtest Requirement for Algorithm Changes

If the user requests a new algorithm, an algorithm update, or an improvement to an existing algorithm, the agent must run the relevant backtest before completing the task.

The agent must record the backtest result in `changes.md` as part of the change entry.

The `changes.md` entry must include:

- Backtest command or method used
- Dataset, symbol, market, or time range tested, if applicable
- Key metrics, such as return, drawdown, win rate, Sharpe ratio, trade count, or any project-specific metrics
- Whether the result improved, worsened, or remained unchanged compared with the previous version
- Any known limitations of the backtest

If a backtest cannot be run, the agent must clearly document why in `changes.md` and include the closest available validation result instead.

## Loop Naming Convention

Every config update or algorithm improvement is identified by a single **Loop ID**:

```
Loop_{date}_{iter}
```

- `{date}` is today's UTC date in compact form `YYYYMMDD` (e.g. `20260513`).
- `{iter}` is a positive integer starting at `1` for the first change of that date and incrementing by 1 for each subsequent change on the same date.
  - Determine the next `{iter}` by scanning `changes.md` for the most recent `Loop_{date}_N` entry on the current date and using `N + 1`. If no entry exists for today, start at `1`.
- The same Loop ID is used as **both** the `changes.md` entry title **and** the `backtest_history/` subfolder name. They must always match.

Example for the first change on 2026-05-13:

- `changes.md` title: `## Loop_20260513_1 - Short Change Title`
- Backtest folder:   `backtest_history/Loop_20260513_1/`

The Loop ID is stored in `strategy.loop_id` inside the symbol's config YAML (e.g. `btcusdc_config.yaml`) and must be bumped to the new value **before** running the backtest for the change.

## Mandatory Trade-History Persistence

Every backtest run must persist the full per-trade history. The agent must not skip this step.

- Output directory: `backtest_history/{loop_id}/` (configurable base via `backtest.history_dir` in the per-symbol config; resolved relative to the project root, i.e. the directory containing `config.yaml`).
- File naming convention: `backtest_history/{loop_id}/{months}m.csv`
  - `{loop_id}` comes from `strategy.loop_id` and must follow the `Loop_{date}_{iter}` convention above.
  - `{months}` is the backtest window length in months (e.g. `1`, `3`, `6`, `12`, `15`).
  - Example: `backtest_history/Loop_20260513_1/15m.csv`.
- Required columns: `symbol, side, opened_at_ms, opened_at_utc, closed_at_ms, closed_at_utc, entry_price, stop_loss, take_profit, exit_price, quantity, pnl, pnl_pct, close_reason`.
- The agent must bump `loop_id` whenever the algorithm or config changes (params, indicators, gates, sizing) so previous Loop history files are preserved for comparison.
- Same-`loop_id` reruns intentionally overwrite the file so it always reflects the current configuration; cross-loop comparison is done by reading multiple `Loop_*` folders side by side.
- Both the canonical `BacktestRunner` and the BTCUSDC iterative harness (`scripts/btcusdc_optimize.py`) write history automatically; fast parameter-sweep harnesses (`scripts/btcusdc_fast.py`, `scripts/btcusdc_sweep.py`) intentionally do not write history to avoid creating thousands of files during search.

## Change Documentation Format

Each entry in `changes.md` should follow this format. The title MUST be the Loop ID computed per the Loop Naming Convention above.

```md
## Loop_{YYYYMMDD}_{iter} - Short Change Title

### Summary
Briefly describe what changed.

### Affected Files
- `path/to/file`

### Reason
Explain why the change was made.

### Backtest Result
- Command/method:
- Dataset/time range:
- Loop folder: `backtest_history/Loop_{YYYYMMDD}_{iter}/`
- Key metrics:
- Comparison with previous Loop:
- Limitations:

### Documentation Updated
- `algorithms.md`
- `architecture.md`
- `changes.md`