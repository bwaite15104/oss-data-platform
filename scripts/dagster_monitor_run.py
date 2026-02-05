#!/usr/bin/env python3
"""
Monitor a single Dagster run: poll status and on failure fetch run/step details and error info.

Usage:
  python scripts/dagster_monitor_run.py 0e729c6c-da49-4526-9167-e081a9874a58
  python scripts/dagster_monitor_run.py --run-id 0e729c6c-da49-4526-9167-e081a9874a58 [--interval 30] [--once]
"""

import argparse
import os
import sys
import time

try:
    import requests
except ImportError:
    print("Error: requests not installed. Run: pip install requests")
    sys.exit(1)

DAGSTER_GRAPHQL_URL = os.getenv("DAGSTER_GRAPHQL_URL", "http://localhost:3000/graphql")

# Get recent runs (we'll find our runId in the list; filter by runIds can be schema-dependent)
RUNS_RECENT_QUERY = """
query RunsRecent($limit: Int) {
  runsOrError(limit: $limit) {
    __typename
    ... on Runs {
      results {
        runId
        status
        jobName
        startTime
        endTime
      }
    }
    ... on PythonError {
      message
    }
  }
}
"""

# Deeper run details including step stats (for failure diagnosis)
RUN_DETAIL_QUERY = """
query RunDetail($runId: ID!) {
  runOrError(runId: $runId) {
    __typename
    ... on Run {
      runId
      status
      jobName
      startTime
      endTime
      stepStats {
        stepKey
        status
        startTime
        endTime
      }
    }
    ... on RunNotFoundError {
      runId
    }
    ... on PythonError {
      message
      stack
    }
  }
}
"""

# Captured logs for a run (to get failure output)
RUN_LOGS_QUERY = """
query RunLogs($runId: ID!) {
  runOrError(runId: $runId) {
    __typename
    ... on Run {
      runId
      status
      logs {
        nodes {
          message
          level
          stepKey
          timestamp
        }
      }
    }
  }
}
"""


def graphql(query: str, variables: dict | None = None) -> dict:
    resp = requests.post(
        DAGSTER_GRAPHQL_URL,
        json={"query": query, "variables": variables or {}},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data.get("data", {})


def get_run_status(run_id: str) -> dict | None:
    """Return run info { runId, status, jobName, startTime, endTime } or None if not found."""
    data = graphql(RUNS_RECENT_QUERY, {"limit": 200})
    runs_data = data.get("runsOrError", {})
    if runs_data.get("__typename") == "Runs":
        for r in runs_data.get("results", []):
            if r.get("runId") == run_id:
                return r
    return None


def get_run_detail(run_id: str) -> dict | None:
    """Get run with step stats (runOrError)."""
    data = graphql(RUN_DETAIL_QUERY, {"runId": run_id})
    run_or = data.get("runOrError")
    if not run_or:
        return None
    if run_or.get("__typename") == "Run":
        return run_or
    if run_or.get("__typename") == "RunNotFoundError":
        return None
    return None


def print_failure_info(run_id: str) -> None:
    """Print run detail and which step failed."""
    try:
        detail = get_run_detail(run_id)
    except Exception as e:
        detail = None
        print(f"  (runOrError not available: {e})")
    if not detail:
        run = get_run_status(run_id)
        if run:
            print(f"  Run: {run.get('runId')}  status={run.get('status')}  job={run.get('jobName')}")
        return
    print(f"  Run: {detail.get('runId')}  status={detail.get('status')}  job={detail.get('jobName')}")
    step_stats = detail.get("stepStats") or []
    failed = [s for s in step_stats if s.get("status") == "FAILURE"]
    if failed:
        print("  Failed step(s):")
        for s in failed:
            print(f"    - stepKey: {s.get('stepKey')}")
    else:
        print("  Step stats:", [s.get("stepKey") for s in step_stats])


def main():
    ap = argparse.ArgumentParser(description="Monitor a Dagster run and print failure details on failure.")
    ap.add_argument("run_id", nargs="?", help="Run ID (e.g. 0e729c6c-da49-4526-9167-e081a9874a58)")
    ap.add_argument("--run-id", dest="run_id_opt", help="Run ID (alternative)")
    ap.add_argument("--interval", type=int, default=30, help="Poll interval in seconds (default 30)")
    ap.add_argument("--once", action="store_true", help="Check once and exit (no polling)")
    ap.add_argument("--url", default=os.getenv("DAGSTER_GRAPHQL_URL", "http://localhost:3000/graphql"), help="GraphQL URL")
    args = ap.parse_args()
    run_id = args.run_id or args.run_id_opt
    if not run_id:
        ap.error("Provide run_id as positional arg or --run-id")
    global DAGSTER_GRAPHQL_URL
    DAGSTER_GRAPHQL_URL = args.url

    if args.once:
        run = get_run_status(run_id)
        if not run:
            print("Run not found.")
            sys.exit(1)
        print(f"  {run.get('runId')}  {run.get('status')}  {run.get('jobName')}")
        if run.get("status") == "FAILURE":
            print("\n--- Failure details ---")
            print_failure_info(run_id)
        return

    print(f"Monitoring run {run_id} (poll every {args.interval}s). Ctrl+C to stop.\n")
    while True:
        try:
            run = get_run_status(run_id)
            if not run:
                print(f"[{time.strftime('%H:%M:%S')}] Run not found.")
            else:
                status = run.get("status", "")
                print(f"[{time.strftime('%H:%M:%S')}] status={status}  job={run.get('jobName')}")
                if status == "FAILURE":
                    print("\n--- Failure details ---")
                    print_failure_info(run_id)
                    sys.exit(1)
                if status in ("SUCCESS", "CANCELED"):
                    print("Run finished.")
                    sys.exit(0)
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nStopped.")
            sys.exit(0)
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] Error: {e}")
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
