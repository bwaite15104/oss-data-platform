#!/usr/bin/env python3
"""
Chunked backfill for any INCREMENTAL_BY_TIME_RANGE SQLMesh model.

Runs SQLMesh backfill in configurable year chunks so you get measurable progress
and can resume if a chunk fails. Use for: int_team_rolling_stats_lookup,
int_team_rolling_stats, mart_game_features, etc.

Usage (run from project root):

  # Backfill lookup table (default 2-year chunks)
  docker exec nba_analytics_dagster_webserver python /app/scripts/backfill_incremental_chunked.py intermediate.int_team_rolling_stats_lookup

  # Backfill rolling stats (upstream of lookup)
  docker exec nba_analytics_dagster_webserver python /app/scripts/backfill_incremental_chunked.py intermediate.int_team_rolling_stats

  # Backfill game features mart (after lookup and rolling stats are done)
  docker exec nba_analytics_dagster_webserver python /app/scripts/backfill_incremental_chunked.py marts.mart_game_features

  # Partial backfill: only last 5 years (e.g. after adding a new column)
  docker exec nba_analytics_dagster_webserver python /app/scripts/backfill_incremental_chunked.py marts.mart_game_features --start-year 2020

  # Custom range and chunk size
  docker exec nba_analytics_dagster_webserver python /app/scripts/backfill_incremental_chunked.py marts.mart_game_features --start-year 2015 --end-year 2024 --chunk-years 2

  # Heavy models (e.g. int_game_momentum_features): use day-based chunks and higher timeout
  docker exec nba_analytics_dagster_webserver python /app/scripts/backfill_incremental_chunked.py intermediate.int_game_momentum_features --start-year 2026 --end-year 2026 --chunk-days 7 --timeout 2400

  # From host
  python scripts/backfill_incremental_chunked.py marts.mart_game_features --docker
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

START_YEAR_DEFAULT = 1946
CHUNK_YEARS_DEFAULT = 2


def get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def in_docker_env() -> bool:
    return os.path.exists("/.dockerenv") or (
        Path("/proc/1/cgroup").exists() and "docker" in Path("/proc/1/cgroup").read_text()
    )


def run_sqlmesh_plan(
    model_name: str,
    start_date: str,
    end_date: str,
    sqlmesh_dir: Path,
    use_docker: bool,
    project_root: Path,
    verbose: bool,
    timeout: int,
) -> bool:
    """Run sqlmesh plan for one chunk. Returns True on success."""
    cmd = [
        "sqlmesh", "plan", "local",
        "--select-model", model_name,
        "--backfill-model", model_name,
        "--start", start_date,
        "--end", end_date,
        "--auto-apply",
    ]
    if use_docker:
        shell_cmd = " ".join(cmd)
        full_cmd = [
            "docker", "exec", "nba_analytics_dagster_webserver",
            "bash", "-c", f"cd /app/transformation/sqlmesh && {shell_cmd}",
        ]
        cwd = str(project_root)
        env = os.environ.copy()
    else:
        full_cmd = cmd
        cwd = str(sqlmesh_dir)
        env = os.environ.copy()

    try:
        result = subprocess.run(
            full_cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (result.stdout or "") + (result.stderr or "")
        batches_executed = "Model batches executed" in output or "Executing model batches" in output
        # Virtual layer can fail for other models (e.g. mart_team_standings); if our model's batches ran, treat as success
        failed_our_model = model_name.replace(".", "_").replace(" ", "") in (output or "") and "Execution failed for node" in (output or "")

        if result.returncode != 0:
            if batches_executed and "Updating virtual layer" in output and not failed_our_model:
                print("(virtual layer update failed, but data was inserted)")
                if verbose:
                    print(output)
                return True
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
        description="Backfill an INCREMENTAL_BY_TIME_RANGE SQLMesh model in year chunks.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "model",
        help="Model name (e.g. intermediate.int_team_rolling_stats_lookup, marts.mart_game_features)",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=START_YEAR_DEFAULT,
        help=f"First year to include (default: {START_YEAR_DEFAULT})",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=None,
        help="Last year to include (default: current year). Use with --start-year for partial backfill (e.g. after adding a column).",
    )
    parser.add_argument(
        "--chunk-years",
        type=int,
        default=CHUNK_YEARS_DEFAULT,
        help=f"Years per chunk (default: {CHUNK_YEARS_DEFAULT}). Ignored if --chunk-days is set.",
    )
    parser.add_argument(
        "--chunk-days",
        type=int,
        default=None,
        metavar="N",
        help="Use day-based chunks instead of year-based (e.g. 14 = 2-week chunks). Use for heavy models that timeout on full-year chunks.",
    )
    parser.add_argument(
        "--docker",
        action="store_true",
        help="Run each chunk via docker exec (when running from host)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print chunks and exit",
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

    now = datetime.now()
    today = now.date()
    end_year = args.end_year if args.end_year is not None else now.year
    if end_year < args.start_year:
        print("Error: --end-year must be >= --start-year", file=sys.stderr)
        return 1

    project_root = get_project_root()
    in_docker = in_docker_env()
    use_docker = args.docker
    if in_docker:
        sqlmesh_dir = Path("/app/transformation/sqlmesh")
    else:
        sqlmesh_dir = project_root / "transformation" / "sqlmesh"
        if not sqlmesh_dir.exists() and not use_docker:
            print(f"SQLMesh dir not found: {sqlmesh_dir}. Run inside container or use --docker.", file=sys.stderr)
            return 1

    chunks = []
    if args.chunk_days is not None:
        # Day-based chunks: range is start_year-01-01 through min(end_year+1-01-01, today)
        range_start = date(args.start_year, 1, 1)
        range_end = min(date(end_year, 12, 31) + timedelta(days=1), today)
        if range_end <= range_start:
            range_end = today
        cur = range_start
        while cur < range_end:
            chunk_end = min(cur + timedelta(days=args.chunk_days), range_end)
            start_date = cur.strftime("%Y-%m-%d")
            end_date = chunk_end.strftime("%Y-%m-%d")
            chunks.append((start_date, end_date, cur.year, chunk_end.year))
            cur = chunk_end
        chunk_desc = f"{args.chunk_days} day(s)"
    else:
        # Year-based chunks
        y = args.start_year
        while y <= end_year:
            chunk_end_year = y + args.chunk_years
            start_date = f"{y}-01-01"
            if chunk_end_year > now.year or (chunk_end_year == now.year and now.month == 1 and now.day == 1):
                end_date = now.strftime("%Y-%m-%d")
                chunks.append((start_date, end_date, y, now.year))
                break
            else:
                end_date = f"{chunk_end_year}-01-01"
                chunks.append((start_date, end_date, y, chunk_end_year - 1))
            y = chunk_end_year
        chunk_desc = f"{args.chunk_years} year(s)"

    print(f"Backfilling {args.model} in {len(chunks)} chunk(s) of {chunk_desc}")
    print(f"Range: {chunks[0][0]} to {chunks[-1][1]} (exclusive)")
    print("-" * 60)

    if args.dry_run:
        for i, (s, e, y1, y2) in enumerate(chunks, 1):
            print(f"  {i}. --start {s} --end {e}  (years {y1}-{y2})")
        print("Dry run. Run without --dry-run to execute.")
        return 0

    skip_year = None
    if args.chunk_days is None and chunks:
        skip_year = int(chunks[0][0][:4]) + args.chunk_years

    failed_chunk = None
    for i, (start_date, end_date, y1, y2) in enumerate(chunks, 1):
        print(f"[{i}/{len(chunks)}] {start_date} -> {end_date} (years {y1}-{y2}) ... ", end="", flush=True)
        ok = run_sqlmesh_plan(
            model_name=args.model,
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
        print("\nStopped at first failure.")
        print("  To retry: fix the error and run the same command again.")
        if args.chunk_days is not None:
            print("  (Re-running will redo all chunks; SQLMesh skips already-materialized intervals, so completed chunks will be quick.)")
        elif skip_year is not None:
            print(f"  To skip this chunk and continue: add --start-year {skip_year}")
        return 1
    print("\nAll chunks backfilled successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
