#!/usr/bin/env python3
"""
Monitor SQLMesh backfill progress by checking table row counts and date ranges.

Usage:
    python scripts/monitor_backfill.py
    python scripts/monitor_backfill.py --start-year 1986
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("Error: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)


def get_connection():
    """Get database connection."""
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
    )


def check_backfill_progress(conn, start_year: int = 1986):
    """Check backfill progress by examining table date ranges."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    print("=" * 80)
    print(f"Backfill Progress Monitor (starting from {start_year})")
    print("=" * 80)
    print()
    
    # Check key intermediate tables
    print("📊 Intermediate Tables Progress:")
    print("-" * 80)
    
    intermediate_tables = [
        "int_team_rest_days",
        "int_team_streaks", 
        "int_team_h2h_stats",
        "int_team_season_stats",
        "int_team_rolling_stats",
        "int_game_momentum_features",
    ]
    
    for table_pattern in intermediate_tables:
        # Find actual snapshot table name
        cur.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'intermediate'
              AND table_name LIKE %s
            ORDER BY table_name DESC
            LIMIT 1
        """, (f"intermediate__{table_pattern}__%",))
        
        result = cur.fetchone()
        if not result:
            print(f"  ⚠ {table_pattern}: No snapshot table found")
            continue
        
        snapshot_table = result['table_name']
        
        # Check date range
        try:
            cur.execute(f"""
                SELECT 
                    MIN(game_date) as min_date,
                    MAX(game_date) as max_date,
                    COUNT(*) as row_count
                FROM intermediate.{snapshot_table}
                WHERE game_date >= %s::date
            """, (f"{start_year}-01-01",))
            
            stats = cur.fetchone()
            if stats and stats['min_date']:
                min_date = stats['min_date'].strftime('%Y-%m-%d') if stats['min_date'] else 'N/A'
                max_date = stats['max_date'].strftime('%Y-%m-%d') if stats['max_date'] else 'N/A'
                row_count = stats['row_count'] or 0
                
                # Calculate progress
                if min_date != 'N/A':
                    min_year = int(min_date[:4])
                    current_year = datetime.now().year
                    total_years = current_year - start_year + 1
                    covered_years = current_year - min_year + 1
                    progress_pct = min(100, (covered_years / total_years) * 100)
                    
                    print(f"  ✓ {table_pattern}:")
                    print(f"      Date range: {min_date} to {max_date}")
                    print(f"      Rows: {row_count:,}")
                    print(f"      Progress: ~{progress_pct:.1f}% ({covered_years}/{total_years} years)")
                else:
                    print(f"  ⚠ {table_pattern}: No data found")
            else:
                print(f"  ⚠ {table_pattern}: No data found")
        except Exception as e:
            print(f"  ✗ {table_pattern}: Error checking - {e}")
    
    print()
    print("📊 Mart Tables Progress:")
    print("-" * 80)
    
    # Check mart_game_features
    cur.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'marts'
          AND table_name LIKE 'marts__mart_game_features__%'
        ORDER BY table_name DESC
        LIMIT 1
    """)
    
    result = cur.fetchone()
    if result:
        snapshot_table = result['table_name']
        try:
            cur.execute(f"""
                SELECT 
                    MIN(game_date) as min_date,
                    MAX(game_date) as max_date,
                    COUNT(*) as row_count
                FROM marts.{snapshot_table}
                WHERE game_date >= %s::date
            """, (f"{start_year}-01-01",))
            
            stats = cur.fetchone()
            if stats and stats['min_date']:
                min_date = stats['min_date'].strftime('%Y-%m-%d') if stats['min_date'] else 'N/A'
                max_date = stats['max_date'].strftime('%Y-%m-%d') if stats['max_date'] else 'N/A'
                row_count = stats['row_count'] or 0
                
                if min_date != 'N/A':
                    min_year = int(min_date[:4])
                    current_year = datetime.now().year
                    total_years = current_year - start_year + 1
                    covered_years = current_year - min_year + 1
                    progress_pct = min(100, (covered_years / total_years) * 100)
                    
                    print(f"  ✓ mart_game_features:")
                    print(f"      Date range: {min_date} to {max_date}")
                    print(f"      Rows: {row_count:,}")
                    print(f"      Progress: ~{progress_pct:.1f}% ({covered_years}/{total_years} years)")
                else:
                    print(f"  ⚠ mart_game_features: No data found")
            else:
                print(f"  ⚠ mart_game_features: No data found")
        except Exception as e:
            print(f"  ✗ mart_game_features: Error checking - {e}")
    else:
        print("  ⚠ mart_game_features: No snapshot table found")
    
    print()
    print("=" * 80)
    
    cur.close()


def main():
    parser = argparse.ArgumentParser(description="Monitor SQLMesh backfill progress")
    parser.add_argument(
        "--start-year",
        type=int,
        default=1986,
        help="Start year for backfill (default: 1986)",
    )
    
    args = parser.parse_args()
    
    try:
        conn = get_connection()
        check_backfill_progress(conn, args.start_year)
        conn.close()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
