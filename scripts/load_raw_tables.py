#!/usr/bin/env python3
"""
Load raw_dev tables from CSV exports produced by export_raw_tables.py.
Run on the target machine after copying the export directory (e.g. from shared drive).

Expects CSV files in a directory: games.csv, injuries.csv, teams.csv, players.csv,
boxscores.csv, team_boxscores.csv.

Usage:
  python scripts/load_raw_tables.py --data-dir DIR [--truncate] [--docker]
  # Load into local DB
  python scripts/load_raw_tables.py --data-dir ./data/raw_dev_export
  # Truncate then load (replace existing data)
  python scripts/load_raw_tables.py --data-dir "D:/shared/nba_export" --truncate
  # When DB is in Docker (run from host; POSTGRES_HOST=localhost)
  python scripts/load_raw_tables.py --data-dir ./data/raw_dev_export --truncate
"""

import argparse
import os
import sys

try:
    import psycopg2
except ImportError:
    print("Error: psycopg2 required. Run: pip install psycopg2-binary")
    sys.exit(1)

# Must match export_raw_tables.py
RAW_TABLES = [
    "games",
    "injuries",
    "teams",
    "players",
    "boxscores",
    "team_boxscores",
]


def get_connection(host=None, port=None, database=None, user=None, password=None):
    host = host or os.getenv("POSTGRES_HOST", "localhost")
    port = port or int(os.getenv("POSTGRES_PORT", "5432"))
    database = database or os.getenv("POSTGRES_DB", "nba_analytics")
    user = user or os.getenv("POSTGRES_USER", "postgres")
    password = password or os.getenv("POSTGRES_PASSWORD", "postgres")
    return psycopg2.connect(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
    )


def load_table(conn, schema: str, table: str, path: str, truncate: bool) -> int:
    """Load one table from CSV via COPY. Returns row count."""
    full_name = f"{schema}.{table}"
    with conn.cursor() as cur:
        if truncate:
            cur.execute(f"TRUNCATE TABLE {full_name}")
        with open(path, "r", encoding="utf-8") as f:
            cur.copy_expert(
                f"COPY {full_name} FROM STDOUT WITH (FORMAT csv, HEADER)",
                f,
            )
        cur.execute(f"SELECT count(*) FROM {full_name}")
        return cur.fetchone()[0]


def main():
    parser = argparse.ArgumentParser(description="Load raw_dev tables from CSV exports")
    parser.add_argument(
        "--data-dir",
        required=True,
        help="Directory containing table CSV files (e.g. games.csv, injuries.csv, ...)",
    )
    parser.add_argument(
        "--schema",
        default="raw_dev",
        help="Schema to load into (default: raw_dev)",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Truncate each table before loading (replace existing data)",
    )
    parser.add_argument(
        "--docker",
        action="store_true",
        help="Use POSTGRES_HOST=postgres (for running inside Docker network)",
    )
    args = parser.parse_args()

    if args.docker:
        os.environ.setdefault("POSTGRES_HOST", "postgres")

    data_dir = os.path.abspath(args.data_dir)
    if not os.path.isdir(data_dir):
        print(f"Error: not a directory: {data_dir}", file=sys.stderr)
        sys.exit(1)

    conn = get_connection()
    try:
        for table in RAW_TABLES:
            path = os.path.join(data_dir, f"{table}.csv")
            if not os.path.isfile(path):
                print(f"  SKIP (file not found): {table}.csv")
                continue
            try:
                n = load_table(conn, args.schema, table, path, args.truncate)
                conn.commit()
                print(f"  {table}.csv -> {args.schema}.{table} ({n} rows)")
            except Exception as e:
                conn.rollback()
                print(f"  {table}.csv FAILED: {e}", file=sys.stderr)
        print("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
