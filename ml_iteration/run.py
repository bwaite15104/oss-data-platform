#!/usr/bin/env python3
"""
ML Iteration Loop: repeatedly invokes the Cursor Agent CLI to improve the
NBA game-winner model until a target accuracy or iteration cap is reached.

Usage:
    python ml_iteration/run.py --max-iterations 5 --target-accuracy 0.75
    python ml_iteration/run.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# Project root (parent of ml_iteration/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
NEXT_STEPS_PATH = PROJECT_ROOT / "ml_iteration" / "next_steps.md"
ACCURACY_HISTORY_PATH = PROJECT_ROOT / "ml_iteration" / "accuracy_history.json"

# Linux ARG_MAX is often ~128KB; keep prompt under this to avoid "Argument list too long" when
# passing via $(cat file) or argv (WSL/native).
PROMPT_ARG_LIMIT = 100_000
# Windows CreateProcess/cmd.exe limit is 8191; truncate prompt so full command line stays under.
WINDOWS_CMD_LIMIT = 8191
WINDOWS_PROMPT_SAFE = 7000  # leave room for agent_cmd, args, and quoting (~1.2k)

# Load .env so CURSOR_API_KEY etc. are available (no-op if .env missing or dotenv not installed)
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass
DEFAULT_NEXT_STEPS_INIT = """# Next steps

- Run first agent iteration.
"""


def _env_get(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _ensure_next_steps() -> None:
    if not NEXT_STEPS_PATH.exists():
        NEXT_STEPS_PATH.parent.mkdir(parents=True, exist_ok=True)
        NEXT_STEPS_PATH.write_text(DEFAULT_NEXT_STEPS_INIT, encoding="utf-8")


def _read_next_steps() -> str:
    if NEXT_STEPS_PATH.exists():
        return NEXT_STEPS_PATH.read_text(encoding="utf-8")
    return ""


def _summarize_next_steps(
    raw: str,
    *,
    keep_recent: int = 5,
    max_chars: int = 60_000,
    older_max_lines: int = 15,
    project_root: Path | None = None,
) -> str:
    """When next_steps.md exceeds max_chars, keep the last keep_recent iteration blocks and the
    first '## Next Steps (Prioritized)' in full; replace older iterations with a short summary.
    When older iterations are summarized, their full content is appended to
    .cursor/docs/ml-iteration-history.md for long-term reference.
    """
    if len(raw) <= max_chars:
        return raw

    root = project_root or PROJECT_ROOT

    # Split on newline immediately before "## " (section headers)
    parts = re.split(r"\n(?=## )", raw)
    if parts and not parts[0].strip().startswith("## "):
        preamble, sections = parts[0], parts[1:]
    else:
        preamble, sections = "", parts

    out: list[str] = []
    if preamble.strip():
        out.append(preamble.strip() + "\n\n")

    iteration_count = 0
    next_steps_kept = False
    older_titles: list[str] = []
    older_raw_sections: list[str] = []

    for s in sections:
        if not s.strip():
            continue
        if re.match(r"^## Iteration \d+", s.strip()):
            iteration_count += 1
            if iteration_count <= keep_recent:
                out.append(s if s.endswith("\n") else s + "\n")
            else:
                first = s.split("\n")[0].strip()
                m = re.match(r"^## Iteration (\d+) \(([^)]+)\) - (.+)$", first)
                if m:
                    older_titles.append(f" - Iteration {m.group(1)} ({m.group(2)}): {m.group(3)}")
                older_raw_sections.append(s if s.endswith("\n") else s + "\n")
        elif "## Next Steps" in s[:60] and not next_steps_kept:
            next_steps_kept = True
            out.append(s if s.endswith("\n") else s + "\n")

    if older_titles:
        summary_lines = older_titles[:older_max_lines] if len(older_titles) > older_max_lines else older_titles
        n = len(older_titles)
        out.append("## Earlier iterations (summarized)\n\n")
        out.append("\n".join(summary_lines))
        if n > older_max_lines:
            out.append(f"\n\n... and {n - older_max_lines} more. ")
        out.append("\n\nSee ml_iteration/next_steps.md and .cursor/docs/ml-iteration-history.md for full history.\n\n")

        # Archive full content of older iterations to .cursor/docs for long-term reference
        if older_raw_sections:
            archive_path = root / ".cursor" / "docs" / "ml-iteration-history.md"
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if not archive_path.exists():
                    archive_path.write_text(
                        "# ML iteration history (archived older runs)\n\n"
                        "Appended when next_steps.md is summarized. See ml_iteration/next_steps.md for the latest.\n\n",
                        encoding="utf-8",
                    )
                with open(archive_path, "a", encoding="utf-8") as f:
                    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                    f.write(f"\n\n## Archived {ts} ({n} older iterations)\n\n")
                    f.write("".join(older_raw_sections))
            except OSError:
                pass

    return "".join(out)


def _get_mlflow_latest(mlflow_uri: str) -> dict:
    """Fetch latest run from MLflow experiment nba_game_winner_prediction."""
    try:
        from mlflow.tracking import MlflowClient

        client = MlflowClient(tracking_uri=mlflow_uri)
        exp = client.get_experiment_by_name("nba_game_winner_prediction")
        if not exp:
            return {"run_id": None, "test_accuracy": None, "summary": "No experiment nba_game_winner_prediction."}
        runs = client.search_runs(
            experiment_ids=[exp.experiment_id],
            order_by=["start_time DESC"],
            max_results=1,
        )
        if not runs:
            return {"run_id": None, "test_accuracy": None, "summary": "No runs in nba_game_winner_prediction."}
        r = runs[0]
        test_acc = r.data.metrics.get("test_accuracy")
        train_acc = r.data.metrics.get("train_accuracy")
        fc = r.data.params.get("feature_count", "?")
        top = (r.data.params.get("top_features") or "")[:200]
        alg = r.data.params.get("algorithm", "?")
        summary = (
            f"run_id={r.info.run_id}, test_accuracy={test_acc}, train_accuracy={train_acc}, "
            f"feature_count={fc}, algorithm={alg}, top_features={top}..."
        )
        return {
            "run_id": r.info.run_id,
            "test_accuracy": float(test_acc) if test_acc is not None else None,
            "train_accuracy": float(train_acc) if train_acc is not None else None,
            "feature_count": fc,
            "top_features": r.data.params.get("top_features", ""),
            "algorithm": alg,
            "summary": summary,
        }
    except Exception as e:
        return {"run_id": None, "test_accuracy": None, "summary": f"MLflow error: {e!r}"}


def _load_accuracy_history() -> list[dict]:
    """Load accuracy history from JSON file. Returns list of {iteration, test_accuracy, timestamp}."""
    if not ACCURACY_HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(ACCURACY_HISTORY_PATH.read_text(encoding="utf-8"))
        return data.get("history", [])
    except Exception:
        return []


def _save_accuracy_history(history: list[dict]) -> None:
    """Save accuracy history to JSON file."""
    try:
        ACCURACY_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        ACCURACY_HISTORY_PATH.write_text(
            json.dumps({"history": history}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _add_accuracy_record(iteration: int, test_accuracy: float | None, run_id: str | None = None) -> None:
    """Add a new accuracy record to history."""
    history = _load_accuracy_history()
    history.append({
        "iteration": iteration,
        "test_accuracy": test_accuracy,
        "run_id": run_id,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    })
    # Keep only last 50 iterations to avoid file bloat
    history = history[-50:]
    _save_accuracy_history(history)


def _detect_stagnation(
    history: list[dict],
    min_improvement: float = 0.001,
    consecutive_threshold: int = 5,
) -> tuple[bool, int, list[dict]]:
    """Detect if accuracy has stagnated (no material improvement for N consecutive runs).
    
    Args:
        history: List of {iteration, test_accuracy, ...} records
        min_improvement: Minimum accuracy delta to count as "improvement" (default 0.001 = 0.1%)
        consecutive_threshold: Number of consecutive runs without improvement to trigger (default 5)
    
    Returns:
        (is_stagnant, consecutive_no_improvement_count, recent_records)
    """
    if len(history) < consecutive_threshold + 1:
        return False, 0, history[-consecutive_threshold:] if history else []
    
    # Look at last N+1 records to check if last N had no improvement
    recent = history[-(consecutive_threshold + 1):]
    consecutive_no_improvement = 0
    
    for i in range(len(recent) - 1, 0, -1):  # Go backwards from most recent
        curr_acc = recent[i].get("test_accuracy")
        prev_acc = recent[i - 1].get("test_accuracy")
        
        if curr_acc is None or prev_acc is None:
            # If we can't compare, don't count as improvement
            consecutive_no_improvement += 1
            continue
        
        improvement = curr_acc - prev_acc
        if improvement < min_improvement:
            consecutive_no_improvement += 1
        else:
            # Found improvement, reset counter
            break
    
    is_stagnant = consecutive_no_improvement >= consecutive_threshold
    return is_stagnant, consecutive_no_improvement, recent[-consecutive_threshold:]


def _build_meta_analysis_prompt(
    history: list[dict],
    next_steps_content: str,
    mlflow_data: dict,
    project_root: Path,
) -> str:
    """Build meta-analysis prompt section when stagnation is detected.
    
    Analyzes recent iterations to identify patterns, blockers, and suggest refocusing.
    """
    recent = history[-10:] if len(history) >= 10 else history
    
    # Extract accuracy trend
    accuracies = [r.get("test_accuracy") for r in recent if r.get("test_accuracy") is not None]
    if accuracies:
        best_acc = max(accuracies)
        latest_acc = accuracies[-1]
        trend = "declining" if len(accuracies) >= 2 and accuracies[-1] < accuracies[-2] else "plateaued"
    else:
        best_acc = latest_acc = None
        trend = "unknown"
    
    # Analyze next_steps.md for patterns
    next_steps_lower = next_steps_content.lower()
    has_materialization_issues = any(
        keyword in next_steps_lower
        for keyword in ["sqlmesh", "materialization", "dependency", "breaking plan", "relation does not exist"]
    )
    has_data_quality_issues = any(
        keyword in next_steps_lower
        for keyword in ["data quality", "missing", "zeros", "incomplete", "backfill"]
    )
    has_performance_issues = any(
        keyword in next_steps_lower
        for keyword in ["slow", "timeout", "performance", "inefficient", "long-running"]
    )
    
    # Count iteration types from history (if we stored action summaries)
    # For now, infer from next_steps content
    repeated_patterns = []
    if "hyperparameter" in next_steps_lower or "learning rate" in next_steps_lower:
        repeated_patterns.append("hyperparameter tuning")
    if "feature" in next_steps_lower and "add" in next_steps_lower:
        repeated_patterns.append("adding new features")
    if "calibration" in next_steps_lower:
        repeated_patterns.append("calibration adjustments")
    
    # Format accuracy values
    best_acc_str = f"{best_acc:.4f}" if best_acc is not None else "N/A"
    latest_acc_str = f"{latest_acc:.4f}" if latest_acc is not None else "N/A"
    
    # Format blocker status
    materialization_status = "✅ Materialization issues detected in next_steps.md" if has_materialization_issues else "⚠️ Check for SQLMesh materialization failures"
    data_quality_status = "✅ Data quality issues detected" if has_data_quality_issues else "⚠️ Check for missing/incomplete data"
    performance_status = "✅ Performance issues detected" if has_performance_issues else "⚠️ Check for slow queries/timeouts"
    
    # Format pattern detection
    if repeated_patterns:
        pattern_status = f"⚠️ Repeated pattern detected: {', '.join(repeated_patterns)}"
    else:
        pattern_status = "⚠️ Check if same type of changes are being repeated (e.g., only hyperparameter tuning, only small feature additions)"
    
    meta_analysis = f"""
## 🚨 META-ANALYSIS REQUIRED: Stagnation Detected

**CRITICAL**: Accuracy has not materially improved (>=0.001) over the last 5 consecutive iterations.

### Current Situation

- **Recent accuracy trend**: {trend}
- **Best recent accuracy**: {best_acc_str}
- **Latest accuracy**: {latest_acc_str}
- **Consecutive runs without improvement**: 5+

### Analysis Required

**Before proceeding with new work, perform a meta-analysis:**

1. **Review recent iterations** (check ml_iteration/next_steps.md and MLflow run history):
   - What changes were attempted in the last 5-10 iterations?
   - Were the changes actually executed successfully (check MLflow for new runs)?
   - Were there materialization failures, data quality issues, or other blockers?

2. **Identify blockers**:
   - {materialization_status}
   - {data_quality_status}
   - {performance_status}
   - Check if features were actually added: `python scripts/db_query.py "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = 'features_dev' AND table_name = 'game_features'"`
   - Check if training actually ran: Review MLflow runs - are there new runs with different feature counts or parameters?

3. **Identify patterns**:
   - {pattern_status}
   - Are changes too incremental/safe? Consider bolder experiments.
   - Are changes not being executed? (e.g., features added but not materialized, training not run)

4. **Root cause analysis**:
   - **If materialization is broken**: Fix SQLMesh dependencies FIRST before any new features
   - **If data is incomplete**: Fix data quality/backfill FIRST before features that depend on it
   - **If same changes repeated**: Try a fundamentally different approach (algorithm change, larger feature set, different validation strategy)
   - **If changes aren't executing**: Verify Dagster runs complete successfully, check logs

### Action Plan

**DO NOT proceed with another incremental change. Instead:**

1. **Fix infrastructure blockers FIRST** (if any):
   - Materialization failures → Fix dependencies, run materialization, verify features exist
   - Data quality → Fix data sources, run backfills, verify completeness
   - Performance → Optimize queries, add indexes, fix slow materializations

2. **If infrastructure is solid, pivot strategy**:
   - **Algorithm change**: Try LightGBM, CatBoost, or ensemble if only using one algorithm
   - **Larger feature set**: Add multiple complementary features at once instead of one-by-one
   - **Data filtering**: Remove noisy data (e.g., early season games, outlier seasons)
   - **Validation strategy**: Check if train/test split is appropriate, consider time-based splits
   - **Feature engineering depth**: Instead of adding features, improve existing ones (interactions, transformations)

3. **Document findings**:
   - Update .cursor/docs/model-improvements-action-plan.md with meta-analysis findings
   - Update next_steps.md with root cause and new prioritized plan
   - If blockers found, prioritize fixing them over new features

### Expected Outcome

After meta-analysis, next_steps.md should contain:
- Clear identification of why progress stalled
- Prioritized action plan addressing root cause
- Either infrastructure fixes OR a pivot to a different improvement strategy

**Do not continue with incremental changes until blockers are resolved or strategy is pivoted.**

"""
    return meta_analysis


def _get_latest_evaluation(project_root: Path) -> dict | None:
    """Optionally fetch latest row from ml_dev.model_evaluations via db_query."""
    try:
        q = (
            "SELECT accuracy, upset_accuracy, total_predictions, model_version FROM ml_dev.model_evaluations "
            "ORDER BY created_at DESC LIMIT 1"
        )
        out = subprocess.run(
            [sys.executable, str(project_root / "scripts" / "db_query.py"), q],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if out.returncode == 0 and out.stdout and "accuracy" in out.stdout.lower():
            return {"raw": out.stdout.strip()}
        return None
    except Exception:
        return None


def _check_in_progress_operations(project_root: Path, dagster_mode: str) -> dict:
    """Check for long-running database queries, active Dagster runs, or blocking operations.
    
    Returns a dict with:
    - active_queries: list of active long-running queries (>2 minutes)
    - blocking_locks: list of blocking locks
    - dagster_runs: list of in-progress Dagster runs
    - summary: human-readable summary string
    """
    result = {
        "active_queries": [],
        "blocking_locks": [],
        "dagster_runs": [],
        "summary": "",
    }
    
    try:
        # Check for long-running database queries (especially SQLMesh materializations)
        query = """
            SELECT 
                pid,
                state,
                wait_event_type,
                wait_event,
                query_start,
                now() - query_start as duration,
                LEFT(query, 150) as query_preview
            FROM pg_stat_activity
            WHERE state = 'active'
              AND query NOT LIKE '%pg_stat_activity%'
              AND query NOT LIKE '%information_schema%'
              AND now() - query_start > INTERVAL '2 minutes'
            ORDER BY query_start
        """
        out = subprocess.run(
            [sys.executable, str(project_root / "scripts" / "db_query.py"), query],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if out.returncode == 0 and out.stdout:
            # Parse output (db_query.py returns tabulated results)
            lines = out.stdout.strip().split("\n")
            if len(lines) > 2:  # Header + separator + data
                for line in lines[2:]:  # Skip header and separator
                    if line.strip():
                        result["active_queries"].append(line.strip())
        
        # Check for blocking locks
        lock_query = """
            SELECT 
                blocked_locks.pid AS blocked_pid,
                blocking_locks.pid AS blocking_pid,
                blocked_activity.query AS blocked_query,
                blocking_activity.query AS blocking_query,
                now() - blocked_activity.query_start AS blocked_duration
            FROM pg_catalog.pg_locks blocked_locks
            JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
            JOIN pg_catalog.pg_locks blocking_locks 
                ON blocking_locks.locktype = blocked_locks.locktype
                AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
                AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
                AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
                AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
                AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
                AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
                AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
                AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
                AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
                AND blocking_locks.pid != blocked_locks.pid
            JOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid
            WHERE NOT blocked_locks.granted
        """
        lock_out = subprocess.run(
            [sys.executable, str(project_root / "scripts" / "db_query.py"), lock_query],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if lock_out.returncode == 0 and lock_out.stdout:
            lines = lock_out.stdout.strip().split("\n")
            if len(lines) > 2:
                for line in lines[2:]:
                    if line.strip():
                        result["blocking_locks"].append(line.strip())
        
        # Check for active Dagster runs (if we can access Dagster)
        # This is a best-effort check - may not work in all environments
        try:
            if dagster_mode == "docker":
                # Try to check Dagster runs via docker exec
                dagster_check = subprocess.run(
                    ["docker", "exec", "nba_analytics_dagster_webserver", "bash", "-c",
                     "dagster run list --limit 5 2>/dev/null | grep -E '(STARTED|QUEUED)' || true"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if dagster_check.returncode == 0 and dagster_check.stdout.strip():
                    result["dagster_runs"] = dagster_check.stdout.strip().split("\n")
        except Exception:
            pass  # Ignore Dagster check failures
        
        # Build summary
        parts = []
        if result["active_queries"]:
            parts.append(f"{len(result['active_queries'])} long-running query(ies) (>2 min)")
        if result["blocking_locks"]:
            parts.append(f"{len(result['blocking_locks'])} blocking lock(s)")
        if result["dagster_runs"]:
            parts.append(f"{len(result['dagster_runs'])} active Dagster run(s)")
        
        if parts:
            result["summary"] = "⚠️ In-progress operations detected: " + ", ".join(parts) + " - check status before starting new work"
        else:
            result["summary"] = "✓ No long-running operations detected"
            
    except Exception as e:
        result["summary"] = f"⚠️ Could not check in-progress operations: {e!r}"
    
    return result


def _dagster_cmd(dagster_mode: str) -> str:
    if dagster_mode == "local":
        return "dagster asset materialize -f definitions.py --select train_game_winner_model"
    return (
        "docker exec nba_analytics_dagster_webserver dagster asset materialize -f /app/definitions.py "
        "--select train_game_winner_model"
    )


def build_prompt(
    *,
    target_accuracy: float,
    iteration: int,
    max_iterations: int | None,
    mlflow_uri: str,
    dagster_mode: str,
    next_steps_content: str,
    mlflow_data: dict,
    eval_data: dict | None,
    in_progress_ops: dict | None = None,
    meta_analysis: str | None = None,
) -> str:
    ml = mlflow_data
    test_acc_str = f"{ml['test_accuracy']:.4f}" if ml.get("test_accuracy") is not None else "N/A"
    eval_block = ""
    if eval_data and eval_data.get("raw"):
        eval_block = f"\n- Latest ml_dev.model_evaluations: {eval_data['raw'][:500]}"

    max_iter_str = str(max_iterations) if max_iterations is not None else "unbounded"
    dagster = _dagster_cmd(dagster_mode)
    
    # Add in-progress operations section if any are detected
    in_progress_block = ""
    if in_progress_ops and in_progress_ops.get("summary") and "⚠️" in in_progress_ops["summary"]:
        in_progress_block = f"""
## ⚠️ In-Progress Operations (CRITICAL - check before starting new work)

{in_progress_ops['summary']}

**Before starting new work, check if these operations need to complete first:**
- If SQLMesh materializations are running, **wait for them to finish** before starting new materializations or using those tables
- If Dagster runs are active, **check their status** - don't start conflicting operations
- If database queries are blocking, **investigate and resolve** before proceeding

**Action required:**
1. **Check status** of in-progress operations (e.g., `docker exec nba_analytics_postgres psql -U postgres -d nba_analytics -c "SELECT pid, state, now() - query_start as duration FROM pg_stat_activity WHERE state = 'active' AND now() - query_start > INTERVAL '2 minutes';"`)
2. **Wait for completion** if operations are critical dependencies (e.g., materializing tables you need)
3. **Document in next_steps.md** if you're waiting for something to complete
4. **Only proceed with new work** once dependencies are ready

"""
    elif in_progress_ops:
        in_progress_block = f"\n## System Status\n\n{in_progress_ops['summary']}\n\n"
    
    meta_analysis_block = meta_analysis if meta_analysis else ""

    return f"""You are improving the NBA game-winner ML model. Goal: test_accuracy >= {target_accuracy}. This is iteration {iteration + 1} (max {max_iter_str}). If the target is already met, say so and do nothing; otherwise implement one focused improvement and run training.
{in_progress_block}{meta_analysis_block}## Current state (from MLflow)

- Latest run in nba_game_winner_prediction: {ml.get("summary", "N/A")}
- test_accuracy: {test_acc_str}
{eval_block}

## Next steps from the previous run (read and then update at the end)

```
{next_steps_content or "(none)"}
```

## Focal points (from .cursor/docs)

Prefer improving based on:
- .cursor/docs/ml-features.md: features, data gaps, next steps, future enhancements
- .cursor/docs/mlflow-and-feast-context.md: how to use MLflow/Feast for model work
- .cursor/docs/model-improvements-action-plan.md: priorities (e.g. confidence calibration, feature interactions, away upset detection, injury data quality)

Or propose something new: new features, new data to ingest, feature engineering, hyperparameter tuning, filtering out bad data, algorithm change. Constraint: free sources only (AGENTS.md).

## Priority order (CRITICAL - follow this order)

**Before adding new features, ensure the foundation is solid:**

0. **Check in-progress operations FIRST** (if any are detected above):
   - If long-running queries or Dagster runs are active, **wait for them to complete** or check their status before starting new work
   - Don't start conflicting materializations or operations
   - Document in next_steps.md if you're waiting for something to finish

1. **Fix SQLMesh materialization issues** (if any are mentioned in next_steps.md):
   - If next_steps.md mentions "SQLMesh materialization", "dependency issue", "Breaking plan", "relation does not exist", or "feature count still 111" when new features were added, **this is your #1 priority**.
   - Resolve dependency issues (e.g. `int_team_star_player_features`, `int_star_player_availability`).
   - Run `make sqlmesh-plan` and `make sqlmesh-run` (or Dagster assets) to materialize pending features.
   - Verify features exist in database: `python scripts/db_query.py "SELECT column_name FROM information_schema.columns WHERE table_schema = 'features_dev' AND table_name = 'game_features' ORDER BY column_name"`
   - **Do not add new features until existing SQLMesh features are materialized and available.**

2. **Fix data quality issues** (if mentioned in next_steps.md):
   - If next_steps.md mentions "injury data all zeros", "missing values", "data quality", or "historical backfill", prioritize fixing these.
   - Verify data completeness and freshness before adding features that depend on incomplete data.
   - **Do not add features that depend on broken/missing data sources.**

3. **Optimize slow/inefficient database queries** (if materializations take >5 minutes):
   - If SQLMesh materializations or Dagster assets take excessively long (>5 minutes for a single model), investigate and optimize.
   - **Check for missing indexes**: `python scripts/db_query.py "SELECT indexname FROM pg_indexes WHERE schemaname = 'raw_dev'"`
   - **Profile slow queries**: Use `EXPLAIN ANALYZE` on slow queries to identify bottlenecks.
   - **Add indexes on join columns**: Common candidates are game_id, team_id, player_id, game_date.
   - **Example index creation**: `python scripts/db_query.py "CREATE INDEX IF NOT EXISTS idx_tablename_column ON schema.tablename(column)"`
   - **Run ANALYZE after adding indexes**: `python scripts/db_query.py "ANALYZE raw_dev.games; ANALYZE raw_dev.team_boxscores; ANALYZE raw_dev.boxscores"`
   - **Do not ignore performance issues** - slow queries block iteration velocity and waste compute.

4. **Then proceed with feature/model improvements:**
   - Only after materialization, data quality, and query performance are resolved, proceed with new features, hyperparameters, or algorithm changes.

## Making progress

- **One focused change per run.** Do not repeat the same kind of change as the last 2–3 iterations (e.g. if you just tuned learning rate or regularization, try features, data, or algorithm next).
- **Pick from Next Steps (Prioritized) - BUT respect the Priority order above.** If next_steps.md mentions SQLMesh/materialization or data quality issues, those take precedence over feature additions, even if they're not #1 in the list.
- **If test_accuracy has not improved over the last 3+ runs:** consider a bolder change: different algorithm (e.g. LightGBM, CatBoost), a larger feature or data change, or reviewing data quality / validation split. Small hyperparameter or calibration tweaks often have diminishing returns.
- **If blocked** (dependency, data, or tool issue): state it clearly in next_steps.md and suggest a concrete alternative for the next run.

## Tools and validation

- **Dagster CLI** (use this exact command for training; also --select game_features or other assets if your change requires it):

  {dagster}

- **SQLMesh materialization via Dagster CLI** (CRITICAL if broken - USE DAGSTER, NOT make commands):
  - **ALWAYS use Dagster CLI** for SQLMesh operations (provides observability, retry logic, and proper dependency ordering).
  - If next_steps.md mentions SQLMesh materialization issues, **fix them first** before adding new features.
  - **Star player dependency chain** (run in this exact order):
    ```bash
    dagster asset materialize -f definitions.py --select int_star_players
    dagster asset materialize -f definitions.py --select int_star_player_availability
    dagster asset materialize -f definitions.py --select int_team_star_player_features
    dagster asset materialize -f definitions.py --select game_features
    ```
  - **Other intermediate models** (can be run as needed):
    ```bash
    dagster asset materialize -f definitions.py --select int_team_rolling_stats
    dagster asset materialize -f definitions.py --select int_team_season_stats
    dagster asset materialize -f definitions.py --select int_team_streaks
    dagster asset materialize -f definitions.py --select int_team_rest_days
    dagster asset materialize -f definitions.py --select int_game_momentum_features
    ```
  - **Verify materialization succeeded**: `python scripts/db_query.py "SELECT column_name FROM information_schema.columns WHERE table_schema = 'features_dev' AND table_name = 'game_features' ORDER BY column_name"`
  - Common issues: dependency chain not materialized in order, "Breaking" plan not auto-applied, or relation does not exist errors.
  - **DO NOT use `make sqlmesh-plan` or `make sqlmesh-run`** - use Dagster CLI for observability.
- **Feast**: if you change feature_repo/ (features.py, data_sources.py, entities.py), run `feast apply` from feature_repo/.
- **MLflow**: use as source of truth for runs, params, metrics, artifacts (.cursor/docs/mlflow-and-feast-context.md). Check training_artifacts/features.json, feature_importances.json, Model Registry game_winner_model.
- **feature_repo**: ensure alignment with orchestration/dagster/assets/ml/training.py.
- **Validation**: After any change to assets/schemas/data, run the relevant Dagster assets and `python scripts/db_query.py` checks per AGENTS.md. Do not skip validation.

## Output for the next run

Before finishing, **update ml_iteration/next_steps.md**:
- What you did this round (brief).
- 3-5 concrete, prioritized next steps for the next agent run. The next run will read this file.

## Update .cursor/docs with learnings

**Update .cursor/docs with any learnings** from this round so future runs (and humans) benefit. Examples: what worked or did not (features, hyperparams, data filters), feature-importance or calibration findings, data-quality issues, alignment notes between Feast and training. Prefer **updating existing docs** where they fit (e.g. .cursor/docs/ml-features.md, .cursor/docs/model-improvements-action-plan.md, .cursor/docs/mlflow-and-feast-context.md); if a learning does not fit, **add a new doc** (e.g. model-improvement-learnings.md) or a dated subsection in model-improvements-action-plan.md. Follow the "When to Update" guidance in .cursor/docs/README.md.

## Constraints

- Do not modify ml_iteration/run.py or the iteration harness. Only change model-related code, features, data, training, evaluation.
- **Exception**: You MAY modify SQLMesh models, Dagster assets, and transformation code to fix materialization/dependency issues - this is infrastructure necessary for features to work.
- **Exception**: You MAY create database indexes, run ANALYZE, or optimize slow queries - efficient infrastructure is necessary for iteration velocity.
- All data sources must be free (AGENTS.md).
"""


def _stream_read(pipe, lines_list: list, prefix: str) -> None:
    for line in iter(pipe.readline, ""):
        print(prefix, line, end="", flush=True)
        lines_list.append(line)


def run_agent(
    prompt: str,
    agent_cmd: str,
    agent_timeout: int,
    project_root: Path,
    log_path: Path | None,
) -> tuple[int, str, str]:
    env = os.environ.copy()
    if "CURSOR_API_KEY" not in env:
        env["CURSOR_API_KEY"] = ""  # agent may still use its own auth

    # Truncate if over ARG_MAX to avoid "Argument list too long" (e.g. when next_steps.md is huge).
    if len(prompt) > PROMPT_ARG_LIMIT:
        keep_tail = 94_000
        keep_head = PROMPT_ARG_LIMIT - keep_tail - 80
        n_omitted = len(prompt) - keep_head - keep_tail
        prompt = (
            prompt[:keep_head]
            + f"\n\n[truncated: {n_omitted} chars omitted from middle to stay under argument limit]\n\n"
            + prompt[-keep_tail:]
        )

    # Windows cmd.exe/CreateProcess command line limit is 8191; truncate prompt when over safe limit.
    # Skip truncation when using WSL: prompt is passed via file and cat, so full prompt is used.
    prompt_path_to_clean: Path | None = None
    if (
        sys.platform == "win32"
        and len(prompt) > WINDOWS_PROMPT_SAFE
        and "wsl" not in agent_cmd.lower()
    ):
        keep_head = 4000
        keep_tail = WINDOWS_PROMPT_SAFE - keep_head - 120
        prompt = (
            prompt[:keep_head]
            + "\n\n[truncated for Windows command line limit (~8191 chars). "
            "Full next_steps in ml_iteration/next_steps.md - read that file for the complete list.]\n\n"
            + prompt[-keep_tail:]
        )

    # Resolve executable: shutil.which finds it in PATH (needed on Windows when agent isn't .exe in list form)
    exe = shutil.which(agent_cmd) or agent_cmd
    use_shell = sys.platform == "win32" and exe == agent_cmd

    if use_shell:
        # On Windows, if "agent" isn't found via which, run via shell (e.g. "wsl -d Ubuntu -- /path/agent").
        if "wsl" in agent_cmd.lower():
            # Pass prompt via file to avoid cmd->wsl->bash quoting breaks with ", newlines, etc.
            prompt_path = (project_root / "ml_iteration" / "logs" / ".prompt_current.txt").resolve()
            prompt_path.parent.mkdir(parents=True, exist_ok=True)
            prompt_path.write_text(prompt, encoding="utf-8")
            prompt_path_to_clean = prompt_path
            # C:\Users\... -> /mnt/c/Users/...
            root = project_root.resolve()
            wsl_base = "/mnt/" + root.drive[0].lower() + root.as_posix().replace(root.drive, "")
            wsl_prompt = wsl_base + "/ml_iteration/logs/.prompt_current.txt"
            parts = agent_cmd.split(" -- ", 1)
            if len(parts) == 2:
                wsl_prefix, linux_agent = parts[0], parts[1].strip()
                cmd = f'{wsl_prefix} -- bash -c \'{linux_agent} -p --force --output-format text --model auto "$(cat "{wsl_prompt}")"\''
            else:
                p = prompt.replace('"', '""').replace("%", "%%")
                cmd = f'{agent_cmd} -p --force --output-format text --model auto "{p}"'
        else:
            p = prompt.replace('"', '""').replace("%", "%%")
            cmd = f'{agent_cmd} -p --force --output-format text --model auto "{p}"'
        proc = subprocess.Popen(
            cmd,
            cwd=str(project_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=True,
        )
    else:
        proc = subprocess.Popen(
            [exe, "-p", "--force", "--output-format", "text", "--model", "auto", prompt],
            cwd=str(project_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    t_out = threading.Thread(target=_stream_read, args=(proc.stdout, stdout_lines, "[agent] "))
    t_err = threading.Thread(target=_stream_read, args=(proc.stderr, stderr_lines, "[agent stderr] "))
    t_out.daemon = True
    t_err.daemon = True
    t_out.start()
    t_err.start()

    try:
        proc.wait(timeout=agent_timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        print("[agent] Timed out and killed.", file=sys.stderr)

    t_out.join(timeout=2)
    t_err.join(timeout=2)
    out = "".join(stdout_lines)
    err = "".join(stderr_lines)

    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"# Prompt\n\n{prompt[:8000]}...\n\n# Stdout\n\n{out}\n\n# Stderr\n\n{err}",
            encoding="utf-8",
        )
        print(f"  Log: {log_path}")

    if prompt_path_to_clean is not None and prompt_path_to_clean.exists():
        try:
            prompt_path_to_clean.unlink()
        except OSError:
            pass

    return proc.returncode, out, err


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ML Iteration Loop: invoke Cursor Agent CLI to improve the game-winner model until target or cap."
    )
    parser.add_argument("--target-accuracy", type=float, default=0.75, help="Stop when test_accuracy >= this")
    parser.add_argument("--max-iterations", type=int, default=None, help="Cap iterations (e.g. 5 for testing)")
    parser.add_argument("--target-metric", type=str, default="test_accuracy", help="Metric to compare (v1: test_accuracy)")
    parser.add_argument("--dagster-mode", type=str, default="docker", choices=("docker", "local"))
    parser.add_argument("--agent-cmd", type=str, default="agent")
    parser.add_argument("--agent-timeout", type=int, default=1800)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-dir", type=str, default=str(PROJECT_ROOT / "ml_iteration" / "logs"))
    parser.add_argument("--backoff-base", type=float, default=5, help="Base delay (seconds) before retry after agent failure")
    parser.add_argument("--backoff-factor", type=float, default=2, help="Exponential factor for backoff (delay = base * factor^n)")
    parser.add_argument("--backoff-max", type=float, default=300, help="Max backoff delay (seconds)")
    parser.add_argument("--max-consecutive-failures", type=int, default=7, help="Stop after this many consecutive agent failures (default 7)")
    args = parser.parse_args()

    if not args.dry_run and not os.environ.get("CURSOR_API_KEY"):
        print("Error: CURSOR_API_KEY is required when not using --dry-run. Set it in .env or the environment.", file=sys.stderr)
        return 1

    _ensure_next_steps()
    mlflow_uri = _env_get("MLFLOW_TRACKING_URI", "http://localhost:5000")

    iteration = 0
    max_iterations = args.max_iterations
    consecutive_failures = 0
    last_run_path = PROJECT_ROOT / "ml_iteration" / "last_run.json"
    log_dir = Path(args.log_dir)
    ml_data: dict = {}
    accuracy_history = _load_accuracy_history()

    while max_iterations is None or iteration < max_iterations:
        print(f"[iteration {iteration + 1}] Checking for in-progress operations...")
        in_progress_ops = _check_in_progress_operations(PROJECT_ROOT, args.dagster_mode)
        print(f"[iteration {iteration + 1}] {in_progress_ops['summary']}")
        
        print(f"[iteration {iteration + 1}] Fetching MLflow, next_steps...")
        ml_data = _get_mlflow_latest(mlflow_uri)
        eval_data = _get_latest_evaluation(PROJECT_ROOT)
        next_steps = _summarize_next_steps(_read_next_steps(), project_root=PROJECT_ROOT)
        
        # Check for stagnation (after at least 5 iterations)
        meta_analysis = None
        if len(accuracy_history) >= 5:
            is_stagnant, consecutive_no_improvement, recent_records = _detect_stagnation(
                accuracy_history,
                min_improvement=0.001,  # 0.1% minimum improvement threshold
                consecutive_threshold=5,
            )
            if is_stagnant:
                print(f"[iteration {iteration + 1}] ⚠️ STAGNATION DETECTED: No improvement for {consecutive_no_improvement} consecutive runs")
                meta_analysis = _build_meta_analysis_prompt(
                    accuracy_history,
                    next_steps,
                    ml_data,
                    PROJECT_ROOT,
                )

        prompt = build_prompt(
            target_accuracy=args.target_accuracy,
            iteration=iteration,
            max_iterations=max_iterations,
            mlflow_uri=mlflow_uri,
            dagster_mode=args.dagster_mode,
            next_steps_content=next_steps,
            mlflow_data=ml_data,
            eval_data=eval_data,
            in_progress_ops=in_progress_ops,
            meta_analysis=meta_analysis,
        )

        if args.dry_run:
            print(prompt)
            return 0

        log_path = log_dir / f"iter_{iteration:03d}.txt" if args.log_dir else None

        print(f"[iteration {iteration + 1}] Running agent (timeout={args.agent_timeout}s)...")
        ret, stdout, stderr = run_agent(
            prompt, args.agent_cmd, args.agent_timeout, PROJECT_ROOT, log_path
        )
        print(f"[iteration {iteration + 1}] Agent finished (exit {ret}).")

        if ret != 0:
            print(f"[iteration {iteration + 1}] Agent exited with code {ret}. stderr: {stderr[:1000]}", file=sys.stderr)
            consecutive_failures += 1
            if consecutive_failures >= args.max_consecutive_failures:
                print(f"Too many consecutive agent failures ({consecutive_failures}); stopping.", file=sys.stderr)
                return 1
            delay = min(
                args.backoff_base * (args.backoff_factor ** (consecutive_failures - 1)),
                args.backoff_max,
            )
            print(f"[backoff] Waiting {delay:.0f}s before retry ({consecutive_failures} consecutive failure(s))...")
            time.sleep(delay)
            iteration += 1
            continue
        consecutive_failures = 0

        ml_data = _get_mlflow_latest(mlflow_uri)
        test_acc = ml_data.get("test_accuracy")
        
        # Record accuracy for stagnation tracking
        if test_acc is not None:
            _add_accuracy_record(iteration + 1, test_acc, ml_data.get("run_id"))
            accuracy_history = _load_accuracy_history()  # Reload to include new record

        if test_acc is not None and test_acc >= args.target_accuracy:
            print(f"Target reached: test_accuracy={test_acc:.4f} >= {args.target_accuracy}")
            break

        if max_iterations is not None and iteration + 1 >= max_iterations:
            print(f"Max iterations reached ({max_iterations}). Last test_accuracy={test_acc}")
            break

        if last_run_path:
            try:
                last_run_path.write_text(
                    json.dumps({
                        "iteration": iteration + 1,
                        "test_accuracy": test_acc,
                        "run_id": ml_data.get("run_id"),
                        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                    }, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass

        iteration += 1

    print(f"Done. Iterations={iteration + 1}, final test_accuracy={ml_data.get('test_accuracy')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
