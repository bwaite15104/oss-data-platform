"""
Monitor SQLMesh query execution and database locks.

Provides real-time visibility into:
- Active queries and their duration
- Blocking locks
- Query progress for long-running operations
- Table creation status
"""

import os
import psycopg2
import time
import sys
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import argparse


def get_connection():
    """Get PostgreSQL connection."""
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        database=os.getenv("POSTGRES_DB", "nba_analytics"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )


def format_duration(seconds: float) -> str:
    """Format duration in human-readable format."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}m {secs:.1f}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"{hours}h {minutes}m {secs:.1f}s"


def get_active_queries(conn) -> List[Dict]:
    """Get all active queries."""
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            pid,
            usename,
            application_name,
            state,
            wait_event_type,
            wait_event,
            query_start,
            state_change,
            NOW() - query_start as duration,
            query
        FROM pg_stat_activity 
        WHERE datname = current_database()
            AND state != 'idle'
            AND pid != pg_backend_pid()
        ORDER BY query_start;
    """)
    
    rows = cur.fetchall()
    cur.close()
    
    return [
        {
            "pid": r[0],
            "user": r[1],
            "application": r[2] or "unknown",
            "state": r[3],
            "wait_type": r[4],
            "wait_event": r[5],
            "start_time": r[6],
            "duration": r[8],  # This is the duration (interval) from the query
            "query_preview": r[9][:100] if r[9] else "",
        }
        for r in rows
    ]


def get_blocking_locks(conn) -> List[Dict]:
    """Get information about blocking locks."""
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            blocked_locks.pid AS blocked_pid,
            blocked_activity.usename AS blocked_user,
            blocking_locks.pid AS blocking_pid,
            blocking_activity.usename AS blocking_user,
            blocking_activity.query AS blocking_query,
            blocked_activity.query AS blocked_query,
            NOW() - blocked_activity.query_start AS blocked_duration
        FROM pg_catalog.pg_locks blocked_locks
        JOIN pg_catalog.pg_stat_activity blocked_activity 
            ON blocked_activity.pid = blocked_locks.pid
        JOIN pg_catalog.pg_locks blocking_locks 
            ON blocking_locks.locktype = blocked_locks.locktype
            AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
            AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
            AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
            AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
            AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
            AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
            AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
            AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
            AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
            AND blocking_locks.pid != blocked_locks.pid
        JOIN pg_catalog.pg_stat_activity blocking_activity 
            ON blocking_activity.pid = blocking_locks.pid
        WHERE NOT blocked_locks.granted;
    """)
    
    rows = cur.fetchall()
    cur.close()
    
    return [
        {
            "blocked_pid": r[0],
            "blocked_user": r[1],
            "blocking_pid": r[2],
            "blocking_user": r[3],
            "blocking_query": r[4][:100] if r[4] else "",
            "blocked_query": r[5][:100] if r[5] else "",
            "blocked_duration": r[6],
        }
        for r in rows
    ]


def get_table_size(conn, schema: str, table: str) -> Optional[int]:
    """Get approximate row count for a table."""
    try:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT reltuples::bigint AS estimate
            FROM pg_class
            WHERE relname = '{table}'
            AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = '{schema}');
        """)
        result = cur.fetchone()
        cur.close()
        return int(result[0]) if result and result[0] else None
    except:
        return None


def get_sqlmesh_progress(conn) -> List[Dict]:
    """Get progress on SQLMesh table creation by checking intermediate tables."""
    sqlmesh_tables = [
        ("intermediate", "int_team_rolling_stats"),
        ("intermediate", "int_team_season_stats"),
        ("intermediate", "int_star_players"),
        ("marts", "mart_game_features"),
        ("features_dev", "team_features"),
    ]
    
    progress = []
    for schema, table in sqlmesh_tables:
        row_count = get_table_size(conn, schema, table)
        if row_count is not None:
            progress.append({
                "schema": schema,
                "table": table,
                "row_count": row_count,
            })
    
    return progress


def print_status(queries: List[Dict], locks: List[Dict], progress: List[Dict]):
    """Print formatted status."""
    print("\n" + "=" * 80)
    print(f"SQLMesh Monitor - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    
    # Active Queries
    print(f"\n[Active Queries]: {len(queries)}")
    if queries:
        for q in queries:
            # duration is a timedelta from PostgreSQL
            duration_seconds = q["duration"].total_seconds() if hasattr(q["duration"], "total_seconds") else 0
            duration_str = format_duration(duration_seconds)
            wait_info = f" | Waiting: {q['wait_event']}" if q["wait_event"] else ""
            print(f"  PID {q['pid']}: {q['state']} | Duration: {duration_str}{wait_info}")
            print(f"    Application: {q['application']}")
            print(f"    Query: {q['query_preview']}...")
    else:
        print("  No active queries")
    
    # Blocking Locks
    print(f"\n[Blocking Locks]: {len(locks)}")
    if locks:
        for lock in locks:
            blocked_dur = format_duration(lock["blocked_duration"].total_seconds())
            print(f"  [BLOCKED] PID {lock['blocked_pid']} ({lock['blocked_user']}) is WAITING")
            print(f"     Waiting on PID {lock['blocking_pid']} ({lock['blocking_user']})")
            print(f"     Blocked for: {blocked_dur}")
            print(f"     Blocking query: {lock['blocking_query']}...")
            print(f"     Blocked query: {lock['blocked_query']}...")
    else:
        print("  [OK] No blocking locks")
    
    # Table Progress
    print(f"\n[Materialized Table Progress]:")
    if progress:
        for p in progress:
            print(f"  {p['schema']}.{p['table']}: {p['row_count']:,} rows")
    else:
        print("  No materialized tables found")
    
    print("\n" + "=" * 80)


def monitor(interval: int = 5, continuous: bool = False):
    """Monitor SQLMesh queries."""
    conn = get_connection()
    
    try:
        if continuous:
            print(f"Monitoring SQLMesh queries (updating every {interval}s). Press Ctrl+C to stop.")
            while True:
                queries = get_active_queries(conn)
                locks = get_blocking_locks(conn)
                progress = get_sqlmesh_progress(conn)
                
                # Clear screen (optional, can be disabled)
                # print("\033[2J\033[H")  # Uncomment for screen clearing
                
                print_status(queries, locks, progress)
                
                time.sleep(interval)
        else:
            queries = get_active_queries(conn)
            locks = get_blocking_locks(conn)
            progress = get_sqlmesh_progress(conn)
            print_status(queries, locks, progress)
    
    except KeyboardInterrupt:
        print("\n\nMonitoring stopped.")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    import os
    
    parser = argparse.ArgumentParser(description="Monitor SQLMesh query execution")
    parser.add_argument(
        "--interval",
        type=int,
        default=5,
        help="Update interval in seconds (default: 5)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Continuously monitor (watch mode)",
    )
    
    args = parser.parse_args()
    
    monitor(interval=args.interval, continuous=args.watch)
