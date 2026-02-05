#!/usr/bin/env python3
"""
Terminate Dagster run(s) via GraphQL API.
Use when a backfill or run keeps spawning or won't cancel from the UI.

Usage:
  python scripts/dagster_terminate_runs.py                    # terminate run hejnpqan + any STARTED runs
  python scripts/dagster_terminate_runs.py --run-id abc123    # terminate specific run only
  python scripts/dagster_terminate_runs.py --all-started      # terminate all runs in STARTED state only
  python scripts/dagster_terminate_runs.py --dry-run           # list run IDs only, do not terminate
"""

import argparse
import os
import sys

try:
    import requests
except ImportError:
    print("Error: requests not installed. Run: pip install requests")
    sys.exit(1)

DAGSTER_GRAPHQL_URL = os.getenv("DAGSTER_GRAPHQL_URL", "http://localhost:3000/graphql")

TERMINATE_MUTATION = """
mutation TerminateRun($runId: String!) {
  terminateRun(runId: $runId) {
    __typename
    ... on TerminateRunSuccess {
      run { runId }
    }
    ... on TerminateRunFailure {
      message
    }
    ... on RunNotFoundError {
      runId
    }
    ... on PythonError {
      message
    }
  }
}
"""

RUNS_QUERY = """
query RunsQuery($filter: RunsFilter) {
  runsOrError(filter: $filter, limit: 100) {
    __typename
    ... on Runs {
      results {
        runId
        status
        jobName
      }
    }
    ... on PythonError {
      message
    }
  }
}
"""

# No filter - get recent runs (for listing)
RUNS_RECENT_QUERY = """
query RunsRecentQuery {
  runsOrError(limit: 20) {
    __typename
    ... on Runs {
      results {
        runId
        status
        jobName
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


def terminate_run(run_id: str, dry_run: bool) -> bool:
    if dry_run:
        print(f"  [dry-run] would terminate run_id={run_id}")
        return True
    data = graphql(TERMINATE_MUTATION, {"runId": run_id})
    result = data.get("terminateRun", {})
    typ = result.get("__typename", "")
    if typ == "TerminateRunSuccess":
        print(f"  Terminated run_id={run_id}")
        return True
    if typ == "TerminateRunFailure":
        print(f"  Failed to terminate run_id={run_id}: {result.get('message', '')}")
        return False
    if typ == "RunNotFoundError":
        print(f"  Run not found: run_id={run_id}")
        return False
    print(f"  Error terminating run_id={run_id}: {result.get('message', typ)}")
    return False


def main():
    ap = argparse.ArgumentParser(description="Terminate Dagster run(s) via GraphQL.")
    ap.add_argument("--run-id", default="hejnpqan", help="Run ID to terminate (default: hejnpqan). Use --run-id none to only terminate STARTED runs.")
    ap.add_argument(
        "--all-started",
        action="store_true",
        help="Also terminate all runs in STARTED state (stops spawning backfills)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Only list run IDs, do not terminate")
    ap.add_argument("--list", action="store_true", help="List recent runs (runId, status, jobName) then exit")
    ap.add_argument(
        "--url",
        default=os.getenv("DAGSTER_GRAPHQL_URL", "http://localhost:3000/graphql"),
        help="Dagster GraphQL URL",
    )
    args = ap.parse_args()
    global DAGSTER_GRAPHQL_URL
    DAGSTER_GRAPHQL_URL = args.url

    if args.list:
        try:
            data = graphql(RUNS_RECENT_QUERY)
            runs_data = data.get("runsOrError", {})
            if runs_data.get("__typename") == "Runs":
                for r in runs_data.get("results", []):
                    print(f"  {r.get('runId')}  {r.get('status')}  {r.get('jobName')}")
            else:
                print("No runs or error:", runs_data)
        except Exception as e:
            print("Error:", e)
        return

    run_ids_to_terminate = []
    if args.run_id and args.run_id.lower() not in ("none", ""):
        run_ids_to_terminate.append(args.run_id)

    if args.all_started:
        try:
            # STARTED and CANCELING (stuck cancels)
            data = graphql(RUNS_QUERY, {"filter": {"statuses": ["STARTED", "CANCELING"]}})
            runs_data = data.get("runsOrError", {})
            if runs_data.get("__typename") == "Runs":
                for r in runs_data.get("results", []):
                    rid = r.get("runId")
                    if rid and rid not in run_ids_to_terminate:
                        run_ids_to_terminate.append(rid)
        except Exception as e:
            print(f"Warning: could not fetch STARTED/CANCELING runs: {e}")

    if not run_ids_to_terminate:
        print("No runs to terminate.")
        return

    print(f"Terminating {len(run_ids_to_terminate)} run(s): {run_ids_to_terminate}")
    for run_id in run_ids_to_terminate:
        terminate_run(run_id, args.dry_run)
    print("Done.")


if __name__ == "__main__":
    main()
