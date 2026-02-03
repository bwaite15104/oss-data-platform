#!/usr/bin/env python3
"""
Ensure indexes on raw_dev.games for SQLMesh intermediate model performance.

Many intermediate models (int_team_clutch_perf, int_team_cb_closeout_perf, etc.)
scan raw_dev.games with JOINs on (home_team_id, game_date) and (away_team_id, game_date).
Without indexes, these run full table scans and are very slow.

Usage:
    python scripts/ensure_raw_dev_games_indexes.py
    docker exec nba_analytics_dagster_webserver python /app/scripts/ensure_raw_dev_games_indexes.py
"""

import os
import sys

try:
    import psycopg2
except ImportError:
    print("Error: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)


def get_connection():
    """Get database connection."""
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB", "nba_analytics"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )


def main():
    conn = get_connection()
    cur = conn.cursor()
    indexes_created = 0

    indexes = [
        ("idx_raw_dev_games_game_date", "(game_date)"),
        ("idx_raw_dev_games_home_team_date", "(home_team_id, game_date)"),
        ("idx_raw_dev_games_away_team_date", "(away_team_id, game_date)"),
    ]

    try:
        cur.execute("""
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'raw_dev' AND table_name = 'games'
        """)
        if not cur.fetchone():
            print("raw_dev.games does not exist yet. Run ingestion first.")
            return 1

        for idx_name, columns in indexes:
            try:
                cur.execute(
                    "SELECT 1 FROM pg_indexes WHERE schemaname = 'raw_dev' AND tablename = 'games' AND indexname = %s",
                    (idx_name,),
                )
                if cur.fetchone():
                    print(f"  ✓ {idx_name} already exists")
                    continue
                cur.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON raw_dev.games {columns}")
                indexes_created += 1
                print(f"  ✓ Created {idx_name}")
            except Exception as e:
                print(f"  ✗ Failed {idx_name}: {e}")

        cur.execute("ANALYZE raw_dev.games")
        print("  ✓ Ran ANALYZE on raw_dev.games")
        conn.commit()
    finally:
        cur.close()
        conn.close()

    print(f"\nIndexes created: {indexes_created}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
