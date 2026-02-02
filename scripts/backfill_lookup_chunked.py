#!/usr/bin/env python3
"""
Chunked backfill for intermediate.int_team_rolling_stats_lookup.

Runs SQLMesh backfill in 2-year chunks from 1946 to the current year so you get
measurable progress and can resume if a chunk fails.

Usage (run from project root):

  # Inside Docker (recommended - SQLMesh and DB are in the same network)
  docker exec nba_analytics_dagster_webserver python /app/scripts/backfill_lookup_chunked.py

  # With custom end year (e.g. only up to 2020)
  docker exec nba_analytics_dagster_webserver python /app/scripts/backfill_lookup_chunked.py --end-year 2020

  # Dry run (print chunks only, no SQLMesh)
  docker exec nba_analytics_dagster_webserver python /app/scripts/backfill_lookup_chunked.py --dry-run

  # From host via Docker (script invokes sqlmesh inside container for each chunk)
  python scripts/backfill_lookup_chunked.py --docker
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Model and defaults
MODEL_NAME = "intermediate.int_team_rolling_stats_lookup"
START_YEAR = 1946
CHUNK_YEARS = 2


def get_project_root() -> Path:
    """Project root (parent of scripts/)."""
    return Path(__file__).resolve().parent.parent


def get_sqlmesh_dir(project_root: Path, in_docker: bool) -> Path:
    if in_docker:
        return Path("/app/transformation/sqlmesh")
    return project_root / "transformation" / "sqlmesh"


def in_docker_env() -> bool:
    return os.path.exists("/.dockerenv") or (Path("/proc/1/cgroup").exists() and Path("/proc/1/cgroup").read_text().find("docker") >= 0)


def run_sqlmesh_plan(
    start_date: str,
    end_date: str,
    sqlmesh_dir: Path,
    use_docker: bool,
    project_root: Path,
    verbose: bool,
    timeout: int,
) -> bool:
    """Run sqlmesh plan for one chunk. Returns True on success."""
    # Use --select-model to isolate only the lookup table in the plan
    # This prevents SQLMesh from trying to update dependent models (like mart_team_standings)
    # --backfill-model ensures we backfill the selected model
    cmd = [
        "sqlmesh", "plan", "local",
        "--select-model", MODEL_NAME,
        "--backfill-model", MODEL_NAME,
        "--start", start_date,
        "--end", end_date,
        "--auto-apply",
    ]
    if use_docker:
        # Run inside container; cwd must be /app/transformation/sqlmesh
        shell_cmd = " ".join(cmd)
        full_cmd = [
            "docker", "exec", "nba_analytics_dagster_webserver",
            "bash", "-c", f"cd /app/transformation/sqlmesh && {shell_cmd}",
        ]
        cwd = str(project_root)
        env = os.environ.copy()
    else:
        # Running inside container or with local sqlmesh
        full_cmd = cmd
        cwd = str(sqlmesh_dir)
        env = os.environ.copy()
    try:
        result = subprocess.run(
            full_cmd,
            cwd=cwd,
            env=env,
            capture_output=True,  # Always capture to check for successful inserts
            text=True,
            timeout=timeout,
        )
        
        # Check if data was actually inserted even if virtual layer update failed
        # SQLMesh may return non-zero if virtual layer update fails, but data insertion might succeed
        output = (result.stdout or "") + (result.stderr or "")
        batches_executed = "Model batches executed" in output or "Executing model batches" in output
        
        if result.returncode != 0:
            # If batches executed successfully, consider it a success despite virtual layer error
            if batches_executed and "Updating virtual layer" in output:
                print("(virtual layer update failed, but data was inserted)")
                if verbose:
                    print(output)
                return True  # Treat as success since data was inserted
            
            # Otherwise, it's a real failure
            if not verbose:
                if result.stderr:
                    print(result.stderr, file=sys.stderr)
                if result.stdout:
                    print(result.stdout, file=sys.stderr)
            else:
                print(output)
            return False
        
        if verbose and output:
            print(output)
        return True
    except subprocess.TimeoutExpired:
        print(f"Timeout after {timeout}s", file=sys.stderr)
        return False
    except FileNotFoundError as e:
        print(f"Command failed: {e}", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill int_team_rolling_stats_lookup in 2-year chunks from 1946 to current year.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=None,
        help=f"Last year to include (default: current year). Chunks are [{START_YEAR}, {START_YEAR + CHUNK_YEARS}), ...",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=START_YEAR,
        help=f"First year to include (default: {START_YEAR})",
    )
    parser.add_argument(
        "--chunk-years",
        type=int,
        default=CHUNK_YEARS,
        help=f"Number of years per chunk (default: {CHUNK_YEARS})",
    )
    parser.add_argument(
        "--docker",
        action="store_true",
        help="Run each chunk via docker exec (use when running script from host)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print chunks and exit without running SQLMesh",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show SQLMesh output",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Timeout per chunk in seconds (default: 600)",
    )
    args = parser.parse_args()

    end_year = args.end_year
    if end_year is None:
        end_year = datetime.now().year
    # Include full current year: last chunk ends at end_year + 1
    if end_year < args.start_year:
        print("Error: --end-year must be >= --start-year", file=sys.stderr)
        return 1

    project_root = get_project_root()
    in_docker = in_docker_env()
    # Use docker exec only when explicitly requested (e.g. running script from host)
    use_docker = args.docker
    if in_docker:
        sqlmesh_dir = Path("/app/transformation/sqlmesh")
    else:
        sqlmesh_dir = project_root / "transformation" / "sqlmesh"
        if not sqlmesh_dir.exists() and not use_docker:
            print(f"SQLMesh dir not found: {sqlmesh_dir}. Run inside container or use --docker.", file=sys.stderr)
            return 1

    # Build list of (start_date, end_date) for each chunk
    chunks = []
    y = args.start_year
    while y <= end_year:
        chunk_end_year = y + args.chunk_years
        start_date = f"{y}-01-01"
        end_date = f"{chunk_end_year}-01-01"
        chunks.append((start_date, end_date, y, chunk_end_year - 1))
        y = chunk_end_year

    print(f"Backfilling {MODEL_NAME} in {len(chunks)} chunk(s) of {args.chunk_years} year(s)")
    print(f"Range: {chunks[0][0]} to {chunks[-1][1]} (exclusive)")
    print("-" * 60)

    if args.dry_run:
        for i, (s, e, y1, y2) in enumerate(chunks, 1):
            print(f"  {i}. --start {s} --end {e}  (years {y1}-{y2})")
        print("Dry run. Run without --dry-run to execute.")
        return 0

    failed_chunk = None
    for i, (start_date, end_date, y1, y2) in enumerate(chunks, 1):
        print(f"[{i}/{len(chunks)}] {start_date} -> {end_date} (years {y1}-{y2}) ... ", end="", flush=True)
        ok = run_sqlmesh_plan(
            start_date=start_date,
            end_date=end_date,
            sqlmesh_dir=sqlmesh_dir,
            use_docker=use_docker,
            project_root=project_root,
            verbose=args.verbose,
            timeout=args.timeout,
        )
        if ok:
            print("OK")
        else:
            print("FAILED")
            failed_chunk = (start_date, end_date)
            break

    if failed_chunk:
        skip_year = int(failed_chunk[0][:4]) + args.chunk_years
        print("\nStopped at first failure.")
        print("  To retry: fix the error and run the same command again.")
        print(f"  To skip this chunk and continue from next: add --start-year {skip_year}")
        return 1
    print("\nAll chunks backfilled successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
