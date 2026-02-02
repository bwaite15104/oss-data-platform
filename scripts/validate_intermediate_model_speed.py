#!/usr/bin/env python3
"""
Time a single SQLMesh model materialization over a short date range to validate performance.

Use after refactoring intermediate models to verify they are "relatively quick" and still work.
Runs: sqlmesh plan local --select-model <model> --start <start> --end <end> --auto-apply --no-prompts

Usage:
  python scripts/validate_intermediate_model_speed.py intermediate.int_team_streaks
  python scripts/validate_intermediate_model_speed.py intermediate.int_team_streaks --start 2024-01-01 --end 2024-02-01
  docker exec nba_analytics_dagster_webserver python /app/scripts/validate_intermediate_model_speed.py intermediate.int_team_streaks
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


def get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def in_docker_env() -> bool:
    return os.path.exists("/.dockerenv") or (
        Path("/proc/1/cgroup").exists() and "docker" in Path("/proc/1/cgroup").read_text()
    )


def main():
    parser = argparse.ArgumentParser(description="Time single SQLMesh model materialization for a short range")
    parser.add_argument("model", help="Full model name (e.g. intermediate.int_team_streaks)")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD (default: 1 month ago)")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--docker", action="store_true", help="Run via docker exec")
    parser.add_argument("--timeout", type=int, default=600, help="Timeout in seconds (default: 600)")
    parser.add_argument("--max-seconds-ok", type=int, default=120, help="Max duration (s) to consider 'quick' (default: 120)")
    args = parser.parse_args()

    project_root = get_project_root()
    sqlmesh_dir = project_root / "transformation" / "sqlmesh"
    if not sqlmesh_dir.exists():
        print(f"ERROR: SQLMesh project not found at {sqlmesh_dir}", file=sys.stderr)
        return 1

    end_date = args.end or datetime.now().strftime("%Y-%m-%d")
    if args.start:
        start_date = args.start
    else:
        # 1 month before end
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        start_dt = end_dt - timedelta(days=31)
        start_date = start_dt.strftime("%Y-%m-%d")

    cmd = [
        "sqlmesh", "plan", "local",
        "--select-model", args.model,
        "--start", start_date,
        "--end", end_date,
        "--auto-apply",
        "--no-prompts",
    ]
    if args.docker or not in_docker_env():
        shell_cmd = " ".join(cmd)
        full_cmd = [
            "docker", "exec", "nba_analytics_dagster_webserver",
            "bash", "-c", f"cd /app/transformation/sqlmesh && {shell_cmd}",
        ]
        cwd = str(project_root)
    else:
        full_cmd = cmd
        cwd = str(sqlmesh_dir)

    print(f"Model: {args.model}")
    print(f"Range: {start_date} to {end_date}")
    print(f"Command: {' '.join(full_cmd[:8])}...")
    print("Running...")
    start_time = time.time()
    try:
        result = subprocess.run(
            full_cmd,
            cwd=cwd,
            env=os.environ.copy(),
            timeout=args.timeout,
            capture_output=False,
            text=True,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start_time
        print(f"\nFAILED: Timed out after {elapsed:.1f}s (limit: {args.timeout}s)")
        return 1
    elapsed = time.time() - start_time
    if result.returncode == 0:
        print(f"\nOK in {elapsed:.1f}s (exit 0)")
        if elapsed > args.max_seconds_ok:
            print(f"  (Slower than {args.max_seconds_ok}s target; consider further optimization.)")
        return 0
    print(f"\nFAILED in {elapsed:.1f}s (exit {result.returncode})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
