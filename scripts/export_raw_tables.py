#!/usr/bin/env python3
"""
Export raw_dev tables that downstream transformations depend on to CSV flat files.
Use these exports to move backfilled data to another machine (e.g. shared drive)
and load via scripts/load_raw_tables.py.

Tables exported (all under raw_dev):
- games       – NBA game history (backfilled via historical backfill)
- injuries    – Injury history (backfilled via ESPN/ProSportsTransactions scripts)
- teams       – Team reference
- players     – Player reference
- boxscores   – Player boxscores
- team_boxscores – Team boxscores

Optional (uncomment in RAW_TABLES if needed):
- betting_odds, todays_games

Usage:
  python scripts/export_raw_tables.py [--out-dir DIR] [--docker]
  # Export to default ./data/raw_dev_export/
  python scripts/export_raw_tables.py
  # Export to shared drive
  python scripts/export_raw_tables.py --out-dir "D:/shared/nba_export"
  # Run export via Docker (DB in container)
  python scripts/export_raw_tables.py --docker --out-dir /app/data/raw_dev_export
"""

import argparse
import os
import sys

try:
    import psycopg2
except ImportError:
    print("Error: psycopg2 required. Run: pip install psycopg2-binary")
    sys.exit(1)

# Tables that all downstream staging/intermediate models rely on (backfilled or reference)
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


def export_table(conn, schema: str, table: str, path: str) -> int:
    """Export one table to CSV via COPY. Returns approximate row count."""
    full_name = f"{schema}.{table}"
    sql = f"COPY {full_name} TO STDOUT WITH (FORMAT csv, HEADER)"
    with conn.cursor() as cur:
        with open(path, "w", newline="", encoding="utf-8") as f:
            cur.copy_expert(sql, f)
        cur.execute(f"SELECT count(*) FROM {full_name}")
        return cur.fetchone()[0]


def main():
    parser = argparse.ArgumentParser(description="Export raw_dev tables to CSV for portability")
    parser.add_argument(
        "--out-dir",
        default=os.path.join(os.path.dirname(__file__), "..", "data", "raw_dev_export"),
        help="Output directory for CSV files (default: data/raw_dev_export)",
    )
    parser.add_argument(
        "--schema",
        default="raw_dev",
        help="Schema to export (default: raw_dev)",
    )
    parser.add_argument(
        "--docker",
        action="store_true",
        help="Use POSTGRES_HOST=postgres (for running inside Docker network)",
    )
    args = parser.parse_args()

    if args.docker:
        os.environ.setdefault("POSTGRES_HOST", "postgres")

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)
    print(f"Exporting to {out_dir}")

    conn = get_connection()
    try:
        for table in RAW_TABLES:
            path = os.path.join(out_dir, f"{table}.csv")
            try:
                n = export_table(conn, args.schema, table, path)
                size_mb = os.path.getsize(path) / (1024 * 1024)
                print(f"  {args.schema}.{table} -> {table}.csv ({n} rows, {size_mb:.2f} MB)")
            except Exception as e:
                print(f"  {args.schema}.{table} SKIP: {e}", file=sys.stderr)
        print("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
