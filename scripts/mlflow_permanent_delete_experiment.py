#!/usr/bin/env python3
"""Permanently delete an MLflow experiment so its name can be reused.

Run inside the MLflow container where the DB is accessible, e.g.:
  docker exec oss_data_platform_mlflow python /app/scripts/mlflow_permanent_delete_experiment.py nba_game_winner_prediction

Or if the project is mounted at /app:
  docker exec oss_data_platform_mlflow python /app/scripts/mlflow_permanent_delete_experiment.py nba_game_winner_prediction
"""
import sqlite3
import sys

DB = "/mlflow/mlflow.db"
NAME = sys.argv[1] if len(sys.argv) > 1 else "nba_game_winner_prediction"

def main():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT experiment_id FROM experiments WHERE name = ?", (NAME,))
    row = cur.fetchone()
    if not row:
        print(f"Experiment '{NAME}' not found (may already be permanently deleted).")
        conn.close()
        return 0
    eid = row[0]
    # Delete in FK order: params, metrics, tags (ref runs), runs, experiment_tags, experiments
    for tbl, col in [("params", "run_uuid"), ("metrics", "run_uuid"), ("tags", "run_uuid")]:
        try:
            cur.execute(f"DELETE FROM {tbl} WHERE {col} IN (SELECT run_uuid FROM runs WHERE experiment_id = ?)", (eid,))
        except Exception as e:
            print(f"Note: {tbl} {e}")
    cur.execute("DELETE FROM runs WHERE experiment_id = ?", (eid,))
    cur.execute("DELETE FROM experiment_tags WHERE experiment_id = ?", (eid,))
    cur.execute("DELETE FROM experiments WHERE experiment_id = ?", (eid,))
    conn.commit()
    print(f"Permanently deleted experiment '{NAME}' (experiment_id={eid}).")
    conn.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
