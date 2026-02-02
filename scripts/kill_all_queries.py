#!/usr/bin/env python3
"""
Terminate all active (non-idle) PostgreSQL queries.
Use when long-running or stuck SQLMesh/materialization queries need to be stopped.

Uses same connection env as backfill: POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, etc.
From Docker: POSTGRES_HOST=nba_analytics_postgres (or set in env).

Usage:
  python scripts/kill_all_queries.py
  python scripts/kill_all_queries.py --dry-run              # list PIDs and queries only, do not terminate
  python scripts/kill_all_queries.py --min-duration 120    # only terminate queries running >2 min
"""

import argparse
import os
import sys

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("Error: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)


def get_connection():
    database = os.getenv("POSTGRES_DB", "nba_analytics")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    return psycopg2.connect(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        connect_timeout=10,
    )


def main():
    ap = argparse.ArgumentParser(description="Terminate all active PostgreSQL queries.")
    ap.add_argument("--dry-run", action="store_true", help="Only list queries, do not terminate")
    ap.add_argument(
        "--min-duration",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Only terminate queries running longer than this (default: 0 = all active)",
    )
    args = ap.parse_args()

    conn = get_connection()
    my_pid = conn.get_backend_pid()

    duration_filter = ""
    if args.min_duration > 0:
        duration_filter = " AND (now() - query_start) > interval '%d seconds' " % args.min_duration

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT pid, state, application_name,
                   now() - query_start AS duration,
                   LEFT(query, 120) AS query_preview
            FROM pg_stat_activity
            WHERE state != 'idle'
              AND pid != %s
              AND query NOT ILIKE '%%pg_stat_activity%%'
              AND query NOT ILIKE '%%pg_terminate_backend%%'
              """ + duration_filter + """
            ORDER BY query_start
        """, (my_pid,))
        rows = cur.fetchall()

    if not rows:
        print("No active queries to terminate.")
        conn.close()
        return

    print(f"Found {len(rows)} active query/queries:")
    for r in rows:
        print(f"  pid={r['pid']} state={r['state']} duration={r['duration']} app={r['application_name']}")
        print(f"    {r['query_preview']}")

    if args.dry_run:
        print("\nDry run: not terminating.")
        conn.close()
        return

    with conn.cursor() as cur:
        for r in rows:
            pid = r["pid"]
            try:
                cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
                conn.commit()
                print(f"Terminated pid={pid}")
            except Exception as e:
                print(f"Failed to terminate pid={pid}: {e}")
                conn.rollback()

    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
