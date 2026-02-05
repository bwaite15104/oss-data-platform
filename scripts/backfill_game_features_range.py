#!/usr/bin/env python3
"""
Partial backfill for marts.mart_game_features.

Use this when you **add a new column/feature** and don't want to backfill the entire
history. Only the date range you specify is rematerialized; older data is unchanged.

- By default, backfills only the last 5 years (suitable for ML training on recent data).
- For full history, use scripts/backfill_incremental_chunked.py marts.mart_game_features.

Usage (run from project root):

  # Backfill last 5 years only (default - good after adding a new feature)
  docker exec nba_analytics_dagster_webserver python /app/scripts/backfill_game_features_range.py

  # Backfill last 10 years
  docker exec nba_analytics_dagster_webserver python /app/scripts/backfill_game_features_range.py --years 10

  # Backfill a specific range (e.g. 2018–2025)
  docker exec nba_analytics_dagster_webserver python /app/scripts/backfill_game_features_range.py --start-year 2018 --end-year 2025

  # Dry run
  docker exec nba_analytics_dagster_webserver python /app/scripts/backfill_game_features_range.py --dry-run

  # From host
  python scripts/backfill_game_features_range.py --docker
"""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

MODEL_NAME = "marts.mart_game_features"
DEFAULT_YEARS = 5


def get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Partial backfill for mart_game_features (e.g. after adding a column). Only the given date range is rematerialized.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--years",
        type=int,
        default=DEFAULT_YEARS,
        help=f"Backfill only the last N years (default: {DEFAULT_YEARS}). Ignored if --start-year/--end-year are set.",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=None,
        help="Start year for backfill (overrides --years). Use with --end-year for a specific range.",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=None,
        help="End year for backfill (default: current year).",
    )
    parser.add_argument(
        "--chunk-years",
        type=int,
        default=2,
        help="Years per chunk (default: 2).",
    )
    parser.add_argument(
        "--docker",
        action="store_true",
        help="Run via docker exec (when running from host).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print what would be run.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show SQLMesh output.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Timeout per chunk in seconds (default: 600).",
    )
    args = parser.parse_args()

    now = datetime.now()
    current_year = now.year

    if args.start_year is not None:
        start_year = args.start_year
        end_year = args.end_year if args.end_year is not None else current_year
    else:
        end_year = args.end_year if args.end_year is not None else current_year
        start_year = end_year - args.years + 1
        if start_year < 1946:
            start_year = 1946

    if start_year > end_year:
        print("Error: start-year must be <= end-year", file=sys.stderr)
        return 1

    script_dir = get_project_root() / "scripts"
    backfill_script = script_dir / "backfill_incremental_chunked.py"
    if not backfill_script.exists():
        print(f"Error: {backfill_script} not found", file=sys.stderr)
        return 1

    cmd = [
        sys.executable,
        str(backfill_script),
        MODEL_NAME,
        "--start-year", str(start_year),
        "--end-year", str(end_year),
        "--chunk-years", str(args.chunk_years),
    ]
    if args.docker:
        cmd.append("--docker")
    if args.dry_run:
        cmd.append("--dry-run")
    if args.verbose:
        cmd.append("-v")
    if args.timeout != 600:
        cmd.extend(["--timeout", str(args.timeout)])

    if args.dry_run:
        print("Would run:")
        print("  " + " ".join(cmd))
        print(f"\nRange: {start_year} to {end_year} ({end_year - start_year + 1} years)")
        return 0

    print(f"Partial backfill for {MODEL_NAME}")
    print(f"Range: {start_year} to {end_year} (years {end_year - start_year + 1})")
    print("(ML and scripts read from marts.mart_game_features directly.)")
    print("-" * 60)

    return subprocess.call(cmd, cwd=str(get_project_root()))


if __name__ == "__main__":
    sys.exit(main())
