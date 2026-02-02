# Optimization Flow

Iterative flow that fixes **data-platform and query/table optimization** only (bottleneck queries, inefficient tables, SQLMesh, indexes, data quality). It reads **ml_iteration/next_steps.md** but **only** works on sections **1, 1.5, 1.6, 2, 3** (Data Infrastructure). It does **not** run ML training, feature reverts, or model-improvement items (sections 4+).

## How it works

1. **Read** ml_iteration/next_steps.md (prioritized list).
2. **Pick one infrastructure item** from sections **1, 1.5, 1.6, 2, or 3 only** (no section 4+ — no training, no feature reverts) and implement the fix.
3. **Validate** using the suggested command (e.g. `validate_intermediate_model_speed.py`, `check_sqlmesh_bottlenecks.py`, or `db_query.py`).
4. **Update** next_steps.md (mark item done only if validation passed) and write **optimization_flow/last_fix.json** so the next run knows what was done and whether validation passed.
5. **Loop** until max iterations or no remaining work.

## Guardrails

- **One fix per run** – one section/item per iteration.
- **Validation required** – the agent must run a validation command; do not mark an item done if validation failed.
- **Persistent context** – next_steps.md and last_fix.json are the source of truth for the next agent run.
- **Stagnation detection** – if the last N runs have validation failures or the same section is retried without completion, the next prompt includes a meta block asking to verify validation, break the work into smaller steps, or switch item.
- **Progress check** – remaining work is counted from next_steps (e.g. “Still to do” / “remaining X”) and stored in optimization_state.json for direction checks.
- **Clean start every iteration** – long-running DB queries (>2 min) are terminated at the start of each iteration; use `--no-clean-start` to skip.
- **Observability** – while the agent runs, a heartbeat prints every 60s ("Agent still running (Xs elapsed)...") and every 120s reports long-running DB query count. Runs >10 min get a post-run warning to review the log.
- **Watchdog during run** – while the agent runs, every 10 min (default) any DB query running longer than 5 min is terminated so runaway backfills don’t hang the run. Use `--no-kill-long-queries-during-run` to disable; `--kill-queries-over-sec` and `--kill-check-interval-sec` to tune.
- **Speed expectations** – the prompt tells the agent to use short date ranges (e.g. 1 month) for validation and avoid full backfills.

## Usage

**On Windows** the default agent command is the same as ML iteration (WSL). Just run:

```bash
# One iteration (default; uses WSL agent on Windows)
python optimization_flow/run.py

# Multiple iterations this run (runs the agent 5 times in this invocation)
python optimization_flow/run.py --max-iterations 5
```

If your WSL agent path is different, pass it explicitly:

```bash
python optimization_flow/run.py --agent-cmd "wsl -d Ubuntu -- /path/to/agent"
```

Other options:

```bash
# Dry-run: print the prompt without calling the agent
python optimization_flow/run.py --dry-run

# Skip terminating long-running DB queries at start
python optimization_flow/run.py --no-clean-start

# Disable killing long-running queries during agent run (watchdog)
python optimization_flow/run.py --no-kill-long-queries-during-run

# Tune watchdog: kill queries >3 min, check every 5 min
python optimization_flow/run.py --kill-queries-over-sec 180 --kill-check-interval-sec 300
```

Requires **CURSOR_API_KEY** in the environment (or `.env`) when not using `--dry-run`.

## State and logs

- **optimization_flow/optimization_state.json** – iteration count, history of (section_tackled, validation_passed), last progress snapshot. Updated after each run.
- **optimization_flow/last_fix.json** – written by the agent each run: `section_tackled`, `validation_passed`, `validation_cmd`. Used to update state and detect stagnation.
- **optimization_flow/logs/** – per-iteration logs (prompt + stdout/stderr) when `--log-dir` is set (default).

These paths are gitignored so runtime state is not committed.

## Relation to ML iteration

- **ML iteration** (ml_iteration/run.py): improves the game-winner model (features, training, accuracy); uses next_steps.md for *model* next steps and accuracy history.
- **Optimization flow** (optimization_flow/run.py): fixes *infrastructure only* — sections **1, 1.5, 1.6, 2, 3** (SQLMesh, indexes, intermediate model refactors, injury data, Baselinr). It does **not** pick sections 4+ (revert features, temporal split, hyperparameters, etc.).

Both read ml_iteration/next_steps.md. Optimization flow is scoped to bottleneck/query/table work; ML iteration handles model improvements.
