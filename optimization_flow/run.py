#!/usr/bin/env python3
"""
Optimization Flow: iteratively fix issues from ml_iteration/next_steps.md using the
Cursor Agent CLI. Each run picks one prioritized issue, fixes it, runs validation,
and updates persistent context (next_steps.md + optimization state) for the next run.

Similar to the ML iteration loop but focused on infrastructure/data fixes (SQLMesh,
indexes, data quality, performance) until the system is fully functional.

Usage:
    python optimization_flow/run.py                    # 1 iteration (default)
    python optimization_flow/run.py --max-iterations 5  # 5 iterations
    python optimization_flow/run.py --reset-state       # reset state then run 1
    python optimization_flow/run.py --dry-run
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

# Project root (parent of optimization_flow/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
NEXT_STEPS_PATH = PROJECT_ROOT / "ml_iteration" / "next_steps.md"
OPTIMIZATION_STATE_PATH = PROJECT_ROOT / "optimization_flow" / "optimization_state.json"
LAST_FIX_PATH = PROJECT_ROOT / "optimization_flow" / "last_fix.json"
OPTIMIZATION_FLOW_DIR = PROJECT_ROOT / "optimization_flow"
PROMPT_ARG_LIMIT = 100_000

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

# Reuse ML iteration helpers for next_steps and agent execution
sys.path.insert(0, str(PROJECT_ROOT))
from ml_iteration.run import (
    _read_next_steps,
    _summarize_next_steps,
    run_agent,
    _check_in_progress_operations,
)


def _run_clean_start(project_root: Path, min_duration_sec: int = 120) -> None:
    """Terminate long-running DB queries (running longer than min_duration_sec) so we start clean."""
    try:
        out = subprocess.run(
            [
                sys.executable,
                str(project_root / "scripts" / "kill_all_queries.py"),
                "--min-duration",
                str(min_duration_sec),
            ],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if out.returncode == 0 and out.stdout:
            for line in out.stdout.strip().split("\n"):
                print(f"  {line.encode('ascii', 'replace').decode('ascii')}")
        elif out.returncode != 0 and out.stderr:
            print(f"  (kill_all_queries: {out.stderr.strip()[:200].encode('ascii', 'replace').decode('ascii')})", file=sys.stderr)
    except subprocess.TimeoutExpired:
        print("  Clean start timed out (continuing anyway).", file=sys.stderr)
    except Exception as e:
        print(f"  Clean start failed: {e!r} (continuing anyway).", file=sys.stderr)


def _heartbeat_while_agent(
    iteration_label: str,
    project_root: Path,
    dagster_mode: str,
    stop_event: threading.Event,
    interval_s: int = 60,
    db_check_interval_s: int = 120,
    kill_during_run: bool = False,
    kill_queries_over_sec: int = 300,
    kill_check_interval_s: int = 600,
) -> None:
    """Print heartbeat every interval_s; every db_check_interval_s report long-running queries.
    If kill_during_run, every kill_check_interval_s terminate queries running > kill_queries_over_sec (watchdog).
    """
    start = time.monotonic()
    last_db_check = 0.0
    last_kill_check = 0.0
    while not stop_event.wait(timeout=interval_s):
        elapsed = int(time.monotonic() - start)
        print(f"[{iteration_label}] Agent still running ({elapsed}s elapsed)...")
        if elapsed - last_db_check >= db_check_interval_s:
            last_db_check = elapsed
            try:
                ops = _check_in_progress_operations(project_root, dagster_mode)
                n_queries = len(ops.get("active_queries") or [])
                if n_queries > 0:
                    print(f"  -> Long-running DB queries (>2 min): {n_queries}")
            except Exception:
                pass
        if kill_during_run and elapsed - last_kill_check >= kill_check_interval_s:
            last_kill_check = elapsed
            try:
                print(f"  -> Watchdog: terminating queries running >{kill_queries_over_sec}s...")
                _run_clean_start(project_root, min_duration_sec=kill_queries_over_sec)
            except Exception as e:
                print(f"  -> Watchdog failed: {e!r}", file=sys.stderr)


def _load_optimization_state() -> dict:
    """Load optimization state from JSON. Returns dict with iteration, history, last_progress."""
    if not OPTIMIZATION_STATE_PATH.exists():
        return {
            "iteration": 0,
            "history": [],
            "last_progress_snapshot": None,
            "last_validation_passed": None,
        }
    try:
        data = json.loads(OPTIMIZATION_STATE_PATH.read_text(encoding="utf-8"))
        return data
    except Exception:
        return {
            "iteration": 0,
            "history": [],
            "last_progress_snapshot": None,
            "last_validation_passed": None,
        }


def _save_optimization_state(state: dict) -> None:
    """Persist optimization state."""
    try:
        OPTIMIZATION_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        OPTIMIZATION_STATE_PATH.write_text(
            json.dumps(state, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def _read_last_fix() -> dict | None:
    """Read last_fix.json written by the agent (section tackled, validation result)."""
    if not LAST_FIX_PATH.exists():
        return None
    try:
        return json.loads(LAST_FIX_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _count_remaining_items(next_steps_content: str) -> dict:
    """
    Heuristic count of remaining work from next_steps.md for progress tracking.
    Returns e.g. { "critical_phrases": N, "still_to_do_count": N, "summary": str }.
    """
    if not next_steps_content:
        return {"critical_phrases": 0, "still_to_do_count": 0, "summary": "No next_steps content"}
    lower = next_steps_content.lower()
    # Count "Still to do" / "remaining X" as proxy for open work
    still_to_do = len(re.findall(r"still to do|remaining \d+", lower))
    critical = len(re.findall(r"critical|blocking|must fix first", lower))
    # Section headers like 1., 1.5., 1.6., 2. that are CRITICAL
    section_critical = len(re.findall(r"^\s*1\.?\d*\.\s+.*?critical", lower, re.MULTILINE | re.DOTALL))
    summary = f"~{still_to_do} 'Still to do' mentions, {critical} critical/blocking mentions"
    return {
        "critical_phrases": critical,
        "still_to_do_count": still_to_do,
        "summary": summary,
    }


def _detect_stagnation(
    history: list[dict],
    consecutive_threshold: int = 3,
) -> tuple[bool, str]:
    """
    Detect if we're stuck: last N runs had validation failed, no last_fix.json, or same section
    retried without success. Do not treat "same section" as stagnant when validation passed
    (different items within that section, e.g. different models in 1.6, are being completed).
    Returns (is_stagnant, reason_string).
    """
    if len(history) < consecutive_threshold:
        return False, ""
    recent = history[-consecutive_threshold:]
    all_failed = all(r.get("validation_passed") is False for r in recent)
    if all_failed:
        return True, f"Last {consecutive_threshold} runs had validation_passed=False."
    all_no_report = all(r.get("section_tackled") is None for r in recent)
    if all_no_report:
        return True, f"Last {consecutive_threshold} runs did not write last_fix.json (no progress recorded)."
    # Same section N times *without completing*: only stagnant if validation failed (or no report)
    sections = [r.get("section_tackled") for r in recent if r.get("section_tackled")]
    if len(sections) >= consecutive_threshold and len(set(sections)) == 1:
        # If any of those runs passed validation, we're completing different items in that section
        any_passed = any(r.get("validation_passed") is True for r in recent)
        if not any_passed:
            return True, f"Same section ({sections[0]}) tackled {consecutive_threshold} times without completing (validation did not pass)."
    return False, ""


def _build_stagnation_meta_prompt(next_steps_content: str, reason: str) -> str:
    """Build meta prompt when stagnation is detected."""
    return f"""
## META: Optimization progress stalled

**Stagnation detected:** {reason}

**Before doing another fix:**

1. **Verify validation is actually run:** For the section you're working on, the prompt requires running a validation command (e.g. `validate_intermediate_model_speed.py`, `check_sqlmesh_bottlenecks.py`, or `db_query.py` to verify data). Did the previous run actually execute that command and succeed? If not, fix the validation step first.

2. **Check direction:** Is the same next_steps item being retried without success? Consider:
   - Breaking the item into smaller steps and completing one sub-step with passing validation.
   - Documenting a blocker in next_steps.md and moving to a different prioritized item.
   - Ensuring you update ml_iteration/next_steps.md (mark Done only when validation passed) and optimization_flow/last_fix.json (section_tackled, validation_passed, validation_cmd).

3. **Update persistent context:** Always update next_steps.md and write last_fix.json so the next run knows what was done and whether validation passed.

Do not repeat the same fix without addressing why validation failed or why the item wasn't marked done.
"""


def build_optimization_prompt(
    *,
    iteration: int,
    max_iterations: int | None,
    next_steps_content: str,
    state: dict,
    progress_snapshot: dict,
    in_progress_ops: dict | None = None,
    meta_analysis: str | None = None,
    dagster_mode: str = "docker",
) -> str:
    """Build the prompt for one optimization iteration."""
    max_iter_str = str(max_iterations) if max_iterations is not None else "unbounded"
    in_progress_block = ""
    if in_progress_ops and in_progress_ops.get("summary") and "⚠️" in in_progress_ops.get("summary", ""):
        in_progress_block = f"""
## In-Progress Operations (check before starting)

{in_progress_ops['summary']}

If long-running queries or Dagster runs are active, wait for them to complete or check status before starting new work. Document in next_steps.md if waiting.

"""
    elif in_progress_ops:
        in_progress_block = f"\n## System status\n\n{in_progress_ops.get('summary', '')}\n\n"

    meta_block = meta_analysis if meta_analysis else ""

    last_fix = state.get("history", [])[-1] if state.get("history") else None
    last_fix_block = ""
    if last_fix:
        v = last_fix.get("validation_passed")
        v_str = "passed" if v else "failed" if v is False else "unknown"
        last_fix_block = f"\n- **Previous run:** Section {last_fix.get('section_tackled', '?')}, validation {v_str}.\n"

    progress_block = f"\n- **Progress snapshot:** {progress_snapshot.get('summary', 'N/A')}\n" if progress_snapshot else ""

    return f"""You are fixing **data-platform and query/table optimization** issues only (bottleneck queries, inefficient tables, SQLMesh materialization, indexes). This is **optimization iteration {iteration + 1}** (max {max_iter_str}). Goal: work through infrastructure items until they are done (validated and marked complete).

**SCOPE – YOU MUST ONLY WORK ON INFRASTRUCTURE:** This flow is for bottleneck queries, inefficient table patterns, materialization performance, and data quality — **NOT** for ML model training, feature reverts, temporal split, hyperparameters, or algorithm changes. You must **ONLY** pick from sections **1, 1.5, 1.6, 2, 3** (Data Infrastructure). Do **NOT** pick section 4, 5, 6, or any item under "Model Improvements" or "Strategic Pivots".
{in_progress_block}{meta_block}## Current state
{last_fix_block}{progress_block}

## Next steps (prioritized – pick ONE infrastructure item)

Read the content below. Pick **exactly one** issue from **sections 1, 1.5, 1.6, 2, or 3 only** (Data Infrastructure: SQLMesh, indexes, intermediate model refactors, injury data quality, Baselinr). Prefer 1, then 1.5, then 1.6, then 2, then 3. Ignore sections 4+ (model improvements).

```
{next_steps_content or "(none)"}
```

## What you must do this run

1. **Pick one infrastructure issue** from sections **1, 1.5, 1.6, 2, or 3 only**. Do not pick section 4 or higher (no training, no feature reverts, no model improvements).

2. **Implement the fix** (code/SQL/config changes as needed).

3. **Run validation (MANDATORY).** After your fix, you MUST run the validation indicated for that item (or a sensible default). Use **short date ranges** (e.g. 1 month) so validation completes quickly; avoid full backfills in this flow. Target: validation completes in under 2 minutes per model.
   - **Section 1.6 (intermediate models):** Run `python scripts/validate_intermediate_model_speed.py <model_name> --docker --start 2024-01-01 --end 2024-02-01` (or similar 1-month range). Consider it passing if the run completes in under 120s.
   - **Section 1 / 1.5 (materialization/indexes):** Run the Dagster/materialization steps mentioned and verify with `python scripts/db_query.py` (e.g. columns exist, counts look correct).
   - **Section 2 (data quality):** Run the check commands mentioned in that section (e.g. db_query for zeros/counts).
   - **If no specific validation is stated:** Run at least one of: `python scripts/check_sqlmesh_bottlenecks.py`, or a `validate_intermediate_model_speed.py` run for a touched model, or a `db_query.py` check.
   Do not mark the item complete if validation fails. Fix or document the failure and leave the item in the list.

4. **Update ml_iteration/next_steps.md:**
   - If validation **passed**: Move the fixed item to "Done" / mark it complete and remove it from "Still to do" (or update STATUS). Add a brief note on what was done.
   - If validation **failed**: Do NOT mark it complete. Document what failed and what to try next. Leave the item in "Still to do".

5. **Write last_fix.json** (REQUIRED – the runner reads this to track progress). Write to this exact path:
   {LAST_FIX_PATH.as_posix()}
   Contents (JSON): {{ "section_tackled": "1.6", "validation_passed": true, "validation_cmd": "python scripts/validate_intermediate_model_speed.py ..." }}
   Use the actual section you tackled and the validation command you ran. Set `validation_passed` to true only if validation succeeded. If you do not write this file, the next run cannot record progress and will treat the run as incomplete.

## Guardrails

- **One focused fix per run.** Do not fix multiple sections in one go.
- **Validation required.** If you skip validation or it fails, do not mark the item done. The next run will detect stagnation if validation keeps failing.
- **Keep validation fast.** Use 1-month date ranges for speed checks; avoid full backfills. Long-running queries are killed between iterations.
- **Update both next_steps.md and last_fix.json.** Persistent context is how the next agent run knows what was done and whether we're making progress.
- **If blocked:** Document the blocker in next_steps.md and suggest a concrete alternative (e.g. try a different item, or a smaller sub-step).

## Tools

- **Docker (typical):** `docker exec nba_analytics_dagster_webserver ...` for SQLMesh/Dagster inside the stack.
- **Validation:** `python scripts/validate_intermediate_model_speed.py <model> --docker --start YYYY-MM-DD --end YYYY-MM-DD`
- **Bottleneck check:** `python scripts/check_sqlmesh_bottlenecks.py` (from project root; script may expect transformation/sqlmesh path).
- **DB checks:** `python scripts/db_query.py "SELECT ..."`
- **Dagster:** `docker exec nba_analytics_dagster_webserver dagster asset materialize -f /app/definitions.py --select <asset>`

Reference: AGENTS.md, .cursor/docs/sqlmesh-performance-optimization.md, .cursor/docs/sqlmesh-troubleshooting-quick-reference.md.

## Output for the next run

Before finishing:
1. Update **ml_iteration/next_steps.md** (mark item done only if validation passed).
2. Write **last_fix.json** to this path: {LAST_FIX_PATH.as_posix()} with keys: section_tackled, validation_passed, validation_cmd. This is required for the optimization flow to record progress.
3. Optionally update .cursor/docs with any learnings (e.g. what fixed a bottleneck).
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Optimization Flow: iteratively fix next_steps issues with validation and persistent context.",
    )
    parser.add_argument("--max-iterations", type=int, default=1, help="Cap iterations (default: 1; use 5 for a batch)")
    parser.add_argument("--reset-state", action="store_true", help="Reset optimization state (iteration 0, empty history) before running")
    parser.add_argument("--dagster-mode", type=str, default="docker", choices=("docker", "local"))
    parser.add_argument(
        "--agent-cmd",
        type=str,
        default=(
            "wsl -d Ubuntu -- /home/bwaite/.local/bin/agent"
            if sys.platform == "win32"
            else "agent"
        ),
        help="Agent CLI command (default on Windows: WSL agent; override if your path differs)",
    )
    parser.add_argument("--agent-timeout", type=int, default=1800)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-dir", type=str, default=str(OPTIMIZATION_FLOW_DIR / "logs"))
    parser.add_argument("--backoff-base", type=float, default=5)
    parser.add_argument("--backoff-factor", type=float, default=2)
    parser.add_argument("--backoff-max", type=float, default=300)
    parser.add_argument("--max-consecutive-failures", type=int, default=5)
    parser.add_argument("--stagnation-threshold", type=int, default=3, help="Runs without progress before meta prompt")
    parser.add_argument(
        "--clean-start",
        action="store_true",
        default=True,
        help="Terminate long-running DB queries (>2 min) before starting (default: True)",
    )
    parser.add_argument("--no-clean-start", action="store_false", dest="clean_start", help="Skip terminating long-running queries")
    parser.add_argument(
        "--kill-long-queries-during-run",
        action="store_true",
        default=True,
        help="During agent run, periodically kill DB queries running longer than threshold (default: True)",
    )
    parser.add_argument(
        "--no-kill-long-queries-during-run",
        action="store_false",
        dest="kill_long_queries_during_run",
        help="Disable killing long-running queries during agent run",
    )
    parser.add_argument(
        "--kill-queries-over-sec",
        type=int,
        default=300,
        metavar="SEC",
        help="Kill queries running longer than this (default: 300 = 5 min)",
    )
    parser.add_argument(
        "--kill-check-interval-sec",
        type=int,
        default=600,
        metavar="SEC",
        help="Run kill check every this many seconds during agent run (default: 600 = 10 min)",
    )
    args = parser.parse_args()

    if not args.dry_run and not os.environ.get("CURSOR_API_KEY"):
        print("Error: CURSOR_API_KEY is required when not using --dry-run.", file=sys.stderr)
        return 1

    if not NEXT_STEPS_PATH.exists():
        print(f"Error: {NEXT_STEPS_PATH} not found. Create ml_iteration/next_steps.md first.", file=sys.stderr)
        return 1

    OPTIMIZATION_FLOW_DIR.mkdir(parents=True, exist_ok=True)
    state = _load_optimization_state()
    if args.reset_state:
        state = {
            "iteration": 0,
            "history": [],
            "last_progress_snapshot": None,
            "last_validation_passed": None,
        }
        _save_optimization_state(state)
        print("Optimization state reset (iteration 0, empty history).")
    iteration = state.get("iteration", 0)  # total completed across all runs (for display and state)
    max_iterations = args.max_iterations  # cap for *this* invocation (how many to run now)
    iterations_this_run = 0
    consecutive_failures = 0
    log_dir = Path(args.log_dir)

    while max_iterations is None or iterations_this_run < max_iterations:
        # Clean start every iteration: kill long-running queries so we don't accumulate them
        if args.clean_start and not args.dry_run:
            print(f"[optimization {iteration + 1}] Clean start: terminating long-running queries (>2 min)...")
            _run_clean_start(PROJECT_ROOT)

        print(f"[optimization {iteration + 1}] Checking in-progress operations...")
        in_progress_ops = _check_in_progress_operations(PROJECT_ROOT, args.dagster_mode)
        summary = in_progress_ops.get("summary", "") or ""
        print(f"[optimization {iteration + 1}] {summary.encode('ascii', 'replace').decode('ascii')}")

        print(f"[optimization {iteration + 1}] Reading next_steps...")
        next_steps_raw = _read_next_steps()
        next_steps = _summarize_next_steps(next_steps_raw, project_root=PROJECT_ROOT, max_chars=80_000)
        progress_snapshot = _count_remaining_items(next_steps)
        prog_sum = (progress_snapshot.get("summary") or "").encode("ascii", "replace").decode("ascii")
        print(f"[optimization {iteration + 1}] Progress: {prog_sum}")

        meta_analysis = None
        history = state.get("history", [])
        if len(history) >= args.stagnation_threshold:
            is_stagnant, reason = _detect_stagnation(history, consecutive_threshold=args.stagnation_threshold)
            if is_stagnant:
                print(f"[optimization {iteration + 1}] Stagnation detected: {reason}")
                meta_analysis = _build_stagnation_meta_prompt(next_steps, reason)

        prompt = build_optimization_prompt(
            iteration=iteration,
            max_iterations=max_iterations,
            next_steps_content=next_steps,
            state=state,
            progress_snapshot=progress_snapshot,
            in_progress_ops=in_progress_ops,
            meta_analysis=meta_analysis,
            dagster_mode=args.dagster_mode,
        )

        if args.dry_run:
            dry_run_path = log_dir / "dry_run_prompt.txt"
            dry_run_path.parent.mkdir(parents=True, exist_ok=True)
            dry_run_path.write_text(prompt, encoding="utf-8")
            print(f"Dry-run: prompt written to {dry_run_path} ({len(prompt)} chars)")
            print("First 1500 chars (ASCII-safe):", prompt[:1500].encode("ascii", "replace").decode("ascii"), "\n...")
            return 0

        log_path = log_dir / f"opt_iter_{iteration:03d}.txt"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        print(f"[optimization {iteration + 1}] Running agent (timeout={args.agent_timeout}s)...")
        if len(prompt) > PROMPT_ARG_LIMIT:
            prompt = (
                prompt[: PROMPT_ARG_LIMIT - 500]
                + "\n\n[truncated for length]\n\n"
                + prompt[-500:]
            )
        agent_done = threading.Event()
        start_time = time.monotonic()
        heartbeat_thread = threading.Thread(
            target=_heartbeat_while_agent,
            args=(f"optimization {iteration + 1}", PROJECT_ROOT, args.dagster_mode, agent_done),
            kwargs={
                "interval_s": 60,
                "db_check_interval_s": 120,
                "kill_during_run": args.kill_long_queries_during_run and not args.dry_run,
                "kill_queries_over_sec": args.kill_queries_over_sec,
                "kill_check_interval_s": args.kill_check_interval_sec,
            },
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            ret, stdout, stderr = run_agent(
                prompt, args.agent_cmd, args.agent_timeout, PROJECT_ROOT, log_path
            )
        finally:
            agent_done.set()
            heartbeat_thread.join(timeout=5)
        elapsed = time.monotonic() - start_time
        print(f"[optimization {iteration + 1}] Agent finished (exit {ret}) in {elapsed:.0f}s.")
        if elapsed > 600:
            print(f"[optimization {iteration + 1}] Run took >10m - review log for long-running queries: {log_path}", file=sys.stderr)

        if ret != 0:
            consecutive_failures += 1
            if consecutive_failures >= args.max_consecutive_failures:
                print(f"Too many consecutive agent failures ({consecutive_failures}); stopping.", file=sys.stderr)
                return 1
            delay = min(
                args.backoff_base * (args.backoff_factor ** (consecutive_failures - 1)),
                args.backoff_max,
            )
            print(f"Backoff {delay:.0f}s before retry...")
            time.sleep(delay)
            iteration += 1
            iterations_this_run += 1
            state["iteration"] = iteration
            _save_optimization_state(state)
            continue
        consecutive_failures = 0

        # Read what the agent reported (last_fix.json)
        last_fix = _read_last_fix()
        if last_fix:
            state.setdefault("history", []).append({
                "iteration": iteration + 1,
                "section_tackled": last_fix.get("section_tackled"),
                "validation_passed": last_fix.get("validation_passed"),
                "validation_cmd": last_fix.get("validation_cmd", "")[:200],
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            })
            state["last_validation_passed"] = last_fix.get("validation_passed")
        else:
            print(f"[optimization {iteration + 1}] Warning: last_fix.json not found. Agent did not write {LAST_FIX_PATH}. Recording run as incomplete.", file=sys.stderr)
            state.setdefault("history", []).append({
                "iteration": iteration + 1,
                "section_tackled": None,
                "validation_passed": None,
                "validation_cmd": None,
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            })
            state["last_validation_passed"] = None
        state["history"] = state["history"][-50:]
        state["last_progress_snapshot"] = _count_remaining_items(_read_next_steps())
        state["iteration"] = iteration + 1
        _save_optimization_state(state)

        iteration += 1
        iterations_this_run += 1
        if max_iterations is not None and iterations_this_run >= max_iterations:
            print(f"Max iterations reached for this run ({max_iterations}).")
            break

    print(f"Done. Optimization iterations={state.get('iteration', 0)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
