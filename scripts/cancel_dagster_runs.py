#!/usr/bin/env python3
"""
Cancel all in-progress (STARTED) and queued (QUEUED) Dagster runs.
Use when automation kicked off too many runs in parallel and you want a clean slate.

Must run with DAGSTER_HOME set (e.g. inside the Dagster container, or:
  DAGSTER_HOME=orchestration/dagster python scripts/cancel_dagster_runs.py
).

Usage:
  python scripts/cancel_dagster_runs.py           # cancel all STARTED + QUEUED
  python scripts/cancel_dagster_runs.py --dry-run # list only, do not cancel
"""

import argparse
import os
import sys

try:
    from dagster import DagsterInstance, RunsFilter, DagsterRunStatus
except ImportError:
    print("Error: dagster not installed. Run: pip install dagster")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="Cancel all in-progress and queued Dagster runs.")
    ap.add_argument("--dry-run", action="store_true", help="List runs only, do not cancel")
    args = ap.parse_args()

    if not os.getenv("DAGSTER_HOME"):
        print("Error: DAGSTER_HOME must be set (e.g. run from Dagster container or set DAGSTER_HOME=orchestration/dagster)")
        sys.exit(1)

    instance = DagsterInstance.get()
    statuses_to_cancel = [DagsterRunStatus.STARTED, DagsterRunStatus.QUEUED]
    runs = instance.get_runs(limit=500, filters=RunsFilter(statuses=statuses_to_cancel))

    if not runs:
        print("No STARTED or QUEUED runs to cancel.")
        return

    print(f"Found {len(runs)} run(s) to cancel:")
    for r in runs:
        print(f"  {r.run_id}  status={r.status}  job={getattr(r, 'job_name', '?')}")

    if args.dry_run:
        print("\nDry run: not canceling.")
        return

    canceled = 0
    for r in runs:
        run_id = r.run_id
        try:
            if hasattr(instance, "run_coordinator") and instance.run_coordinator and getattr(instance.run_coordinator, "can_cancel_run", None) and instance.run_coordinator.can_cancel_run(run_id):
                instance.run_coordinator.cancel_run(run_id)
                print(f"Canceled run_id={run_id}")
            else:
                instance.report_run_canceled(r)
                print(f"Reported canceled run_id={run_id}")
            canceled += 1
        except Exception as e:
            print(f"Failed to cancel run_id={run_id}: {e}")

    print(f"Done. Canceled {canceled} run(s).")


if __name__ == "__main__":
    main()
