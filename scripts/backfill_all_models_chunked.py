#!/usr/bin/env python3
"""
Chunked backfill for ALL incremental models (no --select-model, so SQLMesh handles dependencies).

This is a workaround when individual model backfills fail due to missing dependencies.
SQLMesh will automatically backfill all models in dependency order.

By default, after each successful chunk, indexes are created on SQLMesh snapshot tables
via scripts/create_sqlmesh_indexes.py. Use --no-create-indexes to disable.

If you see "Another plan was applied ... while your current plan was still in progress":
  Re-apply the plan once, then re-run this script (see script output for the exact command).
  Do not run `sqlmesh plan` in another terminal while this backfill is running.

Usage:
  docker exec nba_analytics_dagster_webserver python /app/scripts/backfill_all_models_chunked.py --start-year 1986
"""

import argparse
import os
import re
import subprocess
import sys
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List

# Regex to extract INSERT target table: "schema"."table"
_INSERT_TARGET_RE = re.compile(r'INSERT\s+INTO\s+"([^"]+)"\s*\.\s*"([^"]+)"', re.IGNORECASE)

START_YEAR_DEFAULT = 1946
CHUNK_YEARS_DEFAULT = 2

# Try to import psycopg2 for database monitoring (optional)
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False


def get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def in_docker_env() -> bool:
    return os.path.exists("/.dockerenv") or (
        Path("/proc/1/cgroup").exists() and "docker" in Path("/proc/1/cgroup").read_text()
    )


def get_db_connection():
    """Get database connection for monitoring (if psycopg2 available)."""
    if not PSYCOPG2_AVAILABLE:
        return None
    
    try:
        # Try to get connection info from environment or use defaults
        db_host = os.getenv("POSTGRES_HOST", "localhost")
        db_port = os.getenv("POSTGRES_PORT", "5432")
        db_name = os.getenv("POSTGRES_DB", "nba_analytics")
        db_user = os.getenv("POSTGRES_USER", "postgres")
        db_password = os.getenv("POSTGRES_PASSWORD", "")
        
        # If in Docker, try connecting to postgres service
        if in_docker_env():
            db_host = os.getenv("POSTGRES_HOST", "nba_analytics_postgres")
        
        conn = psycopg2.connect(
            host=db_host,
            port=db_port,
            database=db_name,
            user=db_user,
            password=db_password,
            connect_timeout=5,
        )
        return conn
    except Exception as e:
        return None


def get_query_status(conn) -> Dict:
    """Get current query status from database."""
    if not conn:
        return {}
    
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get active SQLMesh INSERT queries (what we're materializing)
            cur.execute("""
                SELECT 
                    pid,
                    state,
                    now() - query_start as duration,
                    wait_event_type,
                    wait_event,
                    SUBSTRING(query FROM 'sqlmesh_md5__([a-f0-9]+)') as table_hash,
                    LEFT(query, 100) as query_preview,
                    SUBSTRING(query FROM 1 FOR 350) as query_sample
                FROM pg_stat_activity 
                WHERE state != 'idle' 
                  AND query ILIKE '%sqlmesh%INSERT%'
                  AND query NOT ILIKE '%pg_stat_activity%'
                ORDER BY query_start
                LIMIT 10
            """)
            queries = cur.fetchall()
            # Parse target table (schema.table) from each query for bottleneck display
            for q in queries:
                sample = q.get('query_sample') or ''
                m = _INSERT_TARGET_RE.search(sample)
                if m:
                    q['target_table'] = f'{m.group(1)}.{m.group(2)}'
                    # Shorten long snapshot names for display (e.g. sqlmesh_md5__82dcc5e0437c3f3e... -> sqlmesh_md5__82dcc5...)
                    if 'sqlmesh_md5__' in q['target_table']:
                        short = re.sub(r'(sqlmesh_md5__[a-f0-9]{6})[a-f0-9]+', r'\1…', q['target_table'])
                        q['target_table_short'] = short
                    else:
                        q['target_table_short'] = q['target_table']
                else:
                    q['target_table'] = q.get('table_hash') or '?'
                    q['target_table_short'] = q['target_table']

            # Count any other active queries (planning, SELECTs, etc.) so user knows DB is busy
            cur.execute("""
                SELECT COUNT(*) as n
                FROM pg_stat_activity 
                WHERE state = 'active' 
                  AND query NOT LIKE '%pg_stat_activity%'
                  AND query NOT LIKE '%pg_terminate_backend%'
            """)
            other_active = cur.fetchone()
            other_active_count = other_active['n'] if other_active else 0
            
            # Get temp space usage
            cur.execute("""
                SELECT pg_size_pretty(temp_bytes) as temp_space
                FROM pg_stat_database 
                WHERE datname = current_database()
            """)
            temp_result = cur.fetchone()
            temp_space = temp_result['temp_space'] if temp_result else 'unknown'
            
            # Count snapshot tables
            cur.execute("""
                SELECT COUNT(*) as count
                FROM pg_tables 
                WHERE schemaname IN ('intermediate', 'marts') 
                  AND tablename LIKE 'sqlmesh_md5__%'
            """)
            table_result = cur.fetchone()
            table_count = table_result['count'] if table_result else 0
            
            return {
                'queries': [dict(q) for q in queries],
                'temp_space': temp_space,
                'snapshot_table_count': table_count,
                'other_active_count': other_active_count,
            }
    except Exception:
        return {}


class QueryMonitor:
    """Monitor database queries during backfill."""
    
    def __init__(self, enabled: bool = True, interval: int = 30):
        self.enabled = enabled and PSYCOPG2_AVAILABLE
        self.interval = interval
        self.conn = None
        self.monitoring = False
        self.monitor_thread = None
        self.start_time = None
        
    def start(self):
        """Start monitoring in background thread."""
        if not self.enabled:
            return
        
        self.conn = get_db_connection()
        if not self.conn:
            return
        
        self.start_time = time.time()
        self.monitoring = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
    
    def stop(self):
        """Stop monitoring."""
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2)
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
    
    def _monitor_loop(self):
        """Background monitoring loop. Prints on change or at least every 2 intervals so progress is visible."""
        last_status = {}
        tick = 0
        while self.monitoring:
            try:
                status = get_query_status(self.conn)
                if status:
                    tick += 1
                    # Print if status changed OR every 2nd tick so user always sees elapsed time
                    if self._status_changed(last_status, status) or (tick % 2 == 0):
                        self._print_status(status)
                        last_status = status
            except Exception:
                pass
            
            time.sleep(self.interval)
    
    def _status_changed(self, old: Dict, new: Dict) -> bool:
        """Check if status changed significantly."""
        if not old:
            return True
        
        # Check if query count changed
        old_query_count = len(old.get('queries', []))
        new_query_count = len(new.get('queries', []))
        if old_query_count != new_query_count:
            return True
        
        # Check if temp space changed significantly
        old_temp = old.get('temp_space', '')
        new_temp = new.get('temp_space', '')
        if old_temp != new_temp:
            return True
        
        # Check if table count changed
        if old.get('snapshot_table_count') != new.get('snapshot_table_count'):
            return True
        
        # Check if other active query count changed
        if old.get('other_active_count') != new.get('other_active_count'):
            return True
        
        return False
    
    def _print_status(self, status: Dict):
        """Print current status."""
        elapsed = time.time() - self.start_time if self.start_time else 0
        elapsed_str = f"{int(elapsed // 60)}m {int(elapsed % 60)}s"
        insert_count = len(status.get('queries', []))
        other_count = status.get('other_active_count', 0)
        
        print(f"\n[Monitor @ {elapsed_str}]", flush=True)
        print(f"  SQLMesh INSERTs: {insert_count}  |  Other active: {other_count}  |  Snapshot tables: {status.get('snapshot_table_count', 0)}  |  Temp: {status.get('temp_space', 'unknown')}")
        
        queries = status.get('queries', [])
        if queries:
            # Show which table(s) are currently being inserted (bottleneck = longest-running)
            def _duration_seconds(q):
                d = q.get('duration')
                if d is None:
                    return 0
                if hasattr(d, 'total_seconds'):
                    return d.total_seconds()
                return 0
            longest = max(queries, key=_duration_seconds)
            dur_str = str(longest.get('duration', ''))[:12]
            table_short = longest.get('target_table_short', longest.get('table_hash', '?'))
            print(f"  Current table (longest-running): {table_short}  |  Duration: {dur_str}")
            if len(queries) > 1:
                print("  Running INSERTs:")
                for q in queries[:3]:
                    duration = str(q.get('duration', ''))[:8]
                    wait = q.get('wait_event', '')
                    wait_str = f" [{wait}]" if wait else ""
                    tbl = q.get('target_table_short', q.get('table_hash', '?'))
                    print(f"    - {tbl}  PID {q.get('pid')}: {duration}{wait_str}")
        print("", flush=True)
    
    def print_final_status(self):
        """Print final status summary."""
        if not self.enabled or not self.conn:
            return
        
        status = get_query_status(self.conn)
        if status:
            print("\n" + "=" * 60)
            print("Final Status:")
            print(f"  Snapshot tables created: {status.get('snapshot_table_count', 0)}")
            print(f"  Temp space: {status.get('temp_space', 'unknown')}")
            if status.get('queries'):
                print(f"  Still running queries: {len(status['queries'])}")
            print("=" * 60)


# FULL models that are dependencies of incremental models but may not run in chunked plans
# (SQLMesh only backfills "models needing backfill" per range; FULL deps can be skipped).
# Bootstrap these once so their snapshot tables exist before the first chunk.
# int_team_recent_momentum is now INCREMENTAL_BY_TIME_RANGE so it runs in chunk order.
BOOTSTRAP_FULL_MODELS = []


def run_sqlmesh_plan(
    start_date: str,
    end_date: str,
    sqlmesh_dir: Path,
    use_docker: bool,
    project_root: Path,
    verbose: bool,
    timeout: int,
    monitor: Optional[QueryMonitor] = None,
    select_models: Optional[List[str]] = None,
) -> bool:
    """Run sqlmesh plan for one chunk. If select_models is set, only those models run (bootstrap)."""
    cmd = [
        "sqlmesh", "plan", "local",
        "--start", start_date,
        "--end", end_date,
        "--auto-apply",
        "--no-prompts",
    ]
    if select_models:
        for m in select_models:
            cmd.extend(["--select-model", m])
    # else: NO --select-model - processes ALL models in dependency order
    if use_docker:
        shell_cmd = " ".join(cmd)
        full_cmd = [
            "docker", "exec", "nba_analytics_dagster_webserver",
            "bash", "-c", f"cd /app/transformation/sqlmesh && {shell_cmd}",
        ]
        cwd = str(project_root)
        env = os.environ.copy()
    else:
        full_cmd = cmd
        cwd = str(sqlmesh_dir)
        env = os.environ.copy()

    chunk_start_time = time.time()
    
    try:
        # Start monitoring if available
        if monitor:
            monitor.start()
        
        # Run with stdout/stderr streamed so user sees SQLMesh progress; capture for return-code parsing
        proc = subprocess.Popen(
            full_cmd,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        output_lines = []

        def read_stdout():
            for line in proc.stdout:
                print(line, end="", flush=True)
                output_lines.append(line)

        reader = threading.Thread(target=read_stdout, daemon=True)
        reader.start()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            output = "".join(output_lines)
            if monitor:
                monitor.stop()
                monitor.print_final_status()
            elapsed = time.time() - chunk_start_time
            print(f"\nERROR: Command timed out after {int(elapsed)}s (limit: {timeout}s)", file=sys.stderr)
            return False
        reader.join(timeout=5)
        output = "".join(output_lines)
        result = subprocess.CompletedProcess(proc.args, proc.returncode, output, None)
        
        # Stop monitoring
        if monitor:
            monitor.stop()
        batches_executed = "Model batches executed" in output or "Executing model batches" in output
        
        if result.returncode != 0:
            if batches_executed and "Updating virtual layer" in output:
                # Virtual layer can fail for other models; if batches ran, treat as success
                print("(virtual layer update failed, but data was inserted)")
                if verbose:
                    print(output)
                return True
            if "Another plan" in output and "was applied" in output:
                print(
                    "\n  Plan mismatch: the environment's plan changed while this chunk was running.\n"
                    "  To fix: re-apply the plan once, then re-run this backfill.\n"
                    "  Example (from host, same as backfill):\n"
                    "    docker exec nba_analytics_dagster_webserver bash -c "
                    '"cd /app/transformation/sqlmesh && sqlmesh plan local --auto-apply --no-prompts"\n'
                    "  Then run this script again (same --start-year to retry this chunk, or --start-year N to skip).",
                    file=sys.stderr,
                )
            if not verbose:
                if result.stderr:
                    print(result.stderr, file=sys.stderr)
                if result.stdout:
                    print(result.stdout, file=sys.stderr)
            else:
                print(output)
            return False
        
        if verbose:
            print(output)
        return True
        
    except subprocess.TimeoutExpired:
        if monitor:
            monitor.stop()
            monitor.print_final_status()
        elapsed = time.time() - chunk_start_time
        print(f"\nERROR: Command timed out after {int(elapsed)}s (limit: {timeout}s)", file=sys.stderr)
        return False
    except Exception as e:
        if monitor:
            monitor.stop()
        print(f"ERROR: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description="Chunked backfill for ALL incremental models")
    parser.add_argument(
        "--start-year",
        type=int,
        default=START_YEAR_DEFAULT,
        help=f"Start year (default: {START_YEAR_DEFAULT})",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=None,
        help="End year (default: current year)",
    )
    parser.add_argument(
        "--chunk-years",
        type=int,
        default=CHUNK_YEARS_DEFAULT,
        help=f"Years per chunk (default: {CHUNK_YEARS_DEFAULT})",
    )
    parser.add_argument(
        "--docker",
        action="store_true",
        help="Run via docker exec (default: auto-detect)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show full output",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,  # 30 minutes per chunk
        help="Timeout per chunk in seconds (default: 1800)",
    )
    parser.add_argument(
        "--create-indexes",
        action="store_true",
        default=True,
        help="After each successful chunk, run create_sqlmesh_indexes.py to add indexes on snapshot tables (default: True). Set --no-create-indexes to disable.",
    )
    parser.add_argument(
        "--no-create-indexes",
        action="store_false",
        dest="create_indexes",
        help="Disable index creation after each chunk.",
    )
    parser.add_argument(
        "--monitor",
        action="store_true",
        default=True,
        help="Enable database query monitoring (default: True). Requires psycopg2.",
    )
    parser.add_argument(
        "--no-monitor",
        action="store_false",
        dest="monitor",
        help="Disable database query monitoring.",
    )
    parser.add_argument(
        "--monitor-interval",
        type=int,
        default=30,
        help="Monitoring status update interval in seconds (default: 30).",
    )
    
    args = parser.parse_args()
    
    project_root = get_project_root()
    sqlmesh_dir = project_root / "transformation" / "sqlmesh"
    use_docker = args.docker or not in_docker_env()
    
    now = datetime.now()
    end_year = args.end_year if args.end_year else now.year
    start_date = f"{args.start_year}-01-01"
    
    # Calculate number of chunks
    total_years = end_year - args.start_year + 1
    num_chunks = (total_years + args.chunk_years - 1) // args.chunk_years
    
    print(f"Backfilling ALL models in {num_chunks} chunk(s) of {args.chunk_years} year(s)")
    print(f"Range: {start_date} to {end_year + 1}-01-01 (exclusive)")
    print("-" * 60)
    
    # Bootstrap FULL-model dependencies so their snapshot tables exist before chunked plans.
    # Otherwise int_game_momentum_features can fail with "relation ... does not exist".
    first_chunk_end = min(args.start_year + args.chunk_years, end_year + 1)
    first_end_date = f"{first_chunk_end}-01-01" if first_chunk_end < now.year else now.strftime("%Y-%m-%d")
    if BOOTSTRAP_FULL_MODELS:
        print("Bootstrap: materializing FULL dependencies ... ", end="", flush=True)
        bootstrap_ok = run_sqlmesh_plan(
            start_date,
            first_end_date,
            sqlmesh_dir,
            use_docker,
            project_root,
            args.verbose,
            min(args.timeout, 600),
            monitor=None,
            select_models=BOOTSTRAP_FULL_MODELS,
        )
        if bootstrap_ok:
            print("OK")
        else:
            # Virtual layer update may fail after batches run; snapshot may still exist.
            print("WARN (continuing; snapshot may exist)")
    
    current_year = args.start_year
    chunk_num = 0
    
    while current_year < end_year + 1:
        chunk_num += 1
        chunk_start_year = current_year
        chunk_end_year = min(current_year + args.chunk_years, end_year + 1)
        
        # Cap last chunk at today
        if chunk_end_year >= now.year:
            end_date = now.strftime("%Y-%m-%d")
        else:
            end_date = f"{chunk_end_year}-01-01"
        
        start_date_str = f"{chunk_start_year}-01-01"
        
        print(f"[{chunk_num}/{num_chunks}] {start_date_str} -> {end_date} (years {chunk_start_year}-{chunk_end_year - 1}) ... ", end="", flush=True)
        
        # Create monitor for this chunk
        monitor = QueryMonitor(enabled=args.monitor, interval=args.monitor_interval) if args.monitor else None
        
        chunk_start_time = time.time()
        success = run_sqlmesh_plan(
            start_date_str,
            end_date,
            sqlmesh_dir,
            use_docker,
            project_root,
            args.verbose,
            args.timeout,
            monitor=monitor,
        )
        chunk_duration = time.time() - chunk_start_time
        
        if success:
            duration_str = f" ({int(chunk_duration // 60)}m {int(chunk_duration % 60)}s)"
            print(f"OK{duration_str}")
            
            # Print final status if monitoring was enabled
            if monitor:
                monitor.print_final_status()
            # Create indexes on SQLMesh snapshot tables so subsequent chunks and queries are fast.
            if args.create_indexes:
                print(f"      Creating indexes on snapshot tables ... ", end="", flush=True)
                try:
                    # When run via docker exec, we are already in the container; run script in-process.
                    index_script = Path("/app/scripts/create_sqlmesh_indexes.py") if in_docker_env() else project_root / "scripts" / "create_sqlmesh_indexes.py"
                    if index_script.exists():
                        index_cmd = [sys.executable, str(index_script)]
                        index_result = subprocess.run(
                            index_cmd,
                            cwd=str(project_root),
                            capture_output=True,
                            text=True,
                            timeout=300,  # 5 min max for index creation
                        )
                        if index_result.returncode == 0:
                            print("OK")
                        else:
                            print("WARN (non-fatal)")
                            if args.verbose and index_result.stderr:
                                print(index_result.stderr, file=sys.stderr)
                    else:
                        print("skip (script not found)")
                except Exception as e:
                    print(f"WARN ({e})")
        else:
            print("FAILED")
            print(f"\nStopped at first failure.")
            print(f"  To retry: fix the error and run the same command again.")
            print(f"  To skip this chunk and continue: add --start-year {chunk_end_year}")
            sys.exit(1)
        
        current_year = chunk_end_year
    
    print("\n" + "=" * 60)
    print("All chunks completed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    main()
