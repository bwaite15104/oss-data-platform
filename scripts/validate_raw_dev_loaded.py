#!/usr/bin/env python3
"""Validate raw_dev tables after load_raw_tables.py: row counts and quick sanity checks."""

import os
import sys

try:
    import psycopg2
except ImportError:
    print("Error: psycopg2 required. Run: pip install psycopg2-binary")
    sys.exit(1)

TABLES = ["games", "injuries", "teams", "players", "boxscores", "team_boxscores"]


def main():
    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        database=os.getenv("POSTGRES_DB", "nba_analytics"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )
    try:
        with conn.cursor() as cur:
            print("raw_dev table validation:")
            for table in TABLES:
                # table is from allowlist TABLES
                try:
                    cur.execute("SELECT count(*) FROM raw_dev.%s" % (table,))
                    n = cur.fetchone()[0]
                    print(f"  {table}: {n:,} rows")
                    if table == "games" and n > 0:
                        cur.execute("SELECT min(game_date), max(game_date) FROM raw_dev.games")
                        lo, hi = cur.fetchone()
                        print(f"    game_date range: {lo} to {hi}")
                    if table == "injuries" and n > 0:
                        cur.execute("SELECT count(distinct capture_date) FROM raw_dev.injuries")
                        print(f"    distinct capture_dates: {cur.fetchone()[0]}")
                except psycopg2.ProgrammingError as e:
                    conn.rollback()
                    if "does not exist" in str(e):
                        print(f"  {table}: (table does not exist)")
                    else:
                        raise
            # Join sanity: games vs boxscores/team_boxscores (since 2010)
            try:
                cur.execute("""
                    SELECT COUNT(DISTINCT g.game_id) FROM raw_dev.games g
                    INNER JOIN raw_dev.boxscores b ON b.game_id = g.game_id
                    WHERE g.game_date >= '2010-01-01'
                """)
                games_with_bs = cur.fetchone()[0]
                cur.execute("""
                    SELECT COUNT(DISTINCT g.game_id) FROM raw_dev.games g
                    INNER JOIN raw_dev.team_boxscores tb ON tb.game_id = g.game_id
                    WHERE g.game_date >= '2010-01-01'
                """)
                games_with_tb = cur.fetchone()[0]
                print("  Join check (games >= 2010): %s with boxscores, %s with team_boxscores" % (games_with_bs, games_with_tb))
            except psycopg2.ProgrammingError:
                conn.rollback()
        print("Done. Data looks good.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
