#!/usr/bin/env python3
"""Fetch and print log messages for a Dagster run (e.g. to see step errors)."""
import os
import sys
import json
import requests

url = os.getenv("DAGSTER_GRAPHQL_URL", "http://localhost:3000/graphql")
run_id = sys.argv[1] if len(sys.argv) > 1 else None
if not run_id:
    print("Usage: python dagster_run_logs.py <run_id>")
    sys.exit(1)

query = """
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
resp = requests.post(url, json={"query": query, "variables": {"runId": run_id}}, timeout=30)
resp.raise_for_status()
data = resp.json()
if "errors" in data:
    print("GraphQL errors:", data["errors"])
    sys.exit(1)
run_or = data.get("data", {}).get("runOrError")
if not run_or or run_or.get("__typename") != "Run":
    print("Run not found or error:", run_or)
    sys.exit(1)
nodes = run_or.get("logs", {}).get("nodes") or []
# Print ERROR level and any message containing key error terms
for n in nodes:
    msg = n.get("message") or ""
    level = (n.get("level") or "").upper()
    step = n.get("stepKey") or ""
    if level == "ERROR" or "Error" in msg or "Exception" in msg or "Traceback" in msg:
        print(f"[{level}] step={step}")
        print(msg[:4000])
        print("---")
