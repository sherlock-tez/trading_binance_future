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