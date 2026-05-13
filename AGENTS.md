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

## Mandatory Trade-History Persistence

Every backtest run must persist the full per-trade history. The agent must not skip this step.

- Output directory: `backtest_history/loop_{version}/` (configurable base via `backtest.history_dir` in `config.yaml`; resolved relative to the project root, i.e. the directory containing `config.yaml`).
- File naming convention: `backtest_history/loop_{version}/{months}m.csv`
  - `{version}` comes from `strategy.strategy_version` in `config.yaml` (e.g. `v1`, `v2`, `v3` …).
  - `{months}` is the backtest window length in months (e.g. `1`, `3`, `6`, `12`, `15`).
  - Example: `backtest_history/loop_v1/15m.csv`.
- Required columns: `symbol, side, opened_at_ms, opened_at_utc, closed_at_ms, closed_at_utc, entry_price, stop_loss, take_profit, exit_price, quantity, pnl, pnl_pct, close_reason`.
- The agent must bump `strategy_version` whenever the algorithm changes (params, indicators, gates, sizing) so previous Loop history files are preserved for comparison.
- Same-version reruns intentionally overwrite the file so it always reflects the current configuration; cross-version comparison is done by reading multiple `Loop_*` files side by side.
- Both the canonical `BacktestRunner` and the BTCUSDC iterative harness (`scripts/btcusdc_optimize.py`) write history automatically; fast parameter-sweep harnesses (`scripts/btcusdc_fast.py`, `scripts/btcusdc_sweep.py`) intentionally do not write history to avoid creating thousands of files during search.

## Change Documentation Format

Each entry in `changes.md` should follow this format:

```md
## YYYY-MM-DD - Short Change Title

### Summary
Briefly describe what changed.

### Affected Files
- `path/to/file`

### Reason
Explain why the change was made.

### Backtest Result
- Command/method:
- Dataset/time range:
- Key metrics:
- Comparison with previous version:
- Limitations:

### Documentation Updated
- `algorithms.md`
- `architecture.md`
- `changes.md`