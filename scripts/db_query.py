#!/usr/bin/env python3
"""
Simple database query utility for validating data in the warehouse.

Usage:
    python scripts/db_query.py "SELECT * FROM raw_dev.teams LIMIT 5"
    python scripts/db_query.py --tables raw_dev
    python scripts/db_query.py --schemas
    python scripts/db_query.py --counts raw_dev
    python scripts/db_query.py --file queries/my_query.sql
    python scripts/db_query.py --timeout 10 "SELECT * FROM large_table"
"""

import argparse
import os
import sys
import signal
from contextlib import contextmanager
from tabulate import tabulate

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from psycopg2 import OperationalError
except ImportError:
    print("Error: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)


class QueryTimeoutError(Exception):
    """Raised when a query exceeds the timeout."""
    pass


@contextmanager
def timeout_context(seconds):
    """Context manager for timeout handling.
    
    Note: On Windows, PostgreSQL's statement_timeout handles the timeout.
    This context manager is primarily for Unix systems as a backup.
    """
    if sys.platform != 'win32':
        # Unix-like systems can use signals
        def timeout_handler(signum, frame):
            raise QueryTimeoutError(f"Query exceeded timeout of {seconds} seconds")
        
        old_handler = signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(seconds)
        try:
            yield
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)
    else:
        # Windows: rely on PostgreSQL statement_timeout
        yield


def get_connection(timeout_seconds=None):
    """Get database connection from environment or defaults.
    
    Args:
        timeout_seconds: Statement timeout in seconds. If None, uses default (30s).
    """
    # Check for dlt-style env vars first, then fall back to standard
    database = (
        os.getenv("NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__DATABASE") or
        os.getenv("POSTGRES_DB") or
        "nba_analytics"
    )
    host = (
        os.getenv("NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__HOST") or
        os.getenv("POSTGRES_HOST") or
        "localhost"
    )
    conn = psycopg2.connect(
        host=host,
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=database,
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
        connect_timeout=10,  # Connection timeout (not query timeout)
    )
    
    # Set statement timeout at connection level
    if timeout_seconds is not None:
        with conn.cursor() as cur:
            cur.execute(f"SET statement_timeout = {timeout_seconds * 1000}")  # PostgreSQL uses milliseconds
            conn.commit()
    
    return conn


def run_query(query: str, as_dict: bool = False, timeout_seconds: int = 30):
    """Execute a query and return results.
    
    Args:
        query: SQL query to execute
        as_dict: If True, return results as dictionaries
        timeout_seconds: Maximum time to wait for query (default: 30 seconds)
    
    Returns:
        Tuple of (columns, rows) for SELECT queries, or (None, message) for DML
    
    Raises:
        QueryTimeoutError: If query exceeds timeout
        psycopg2.OperationalError: For database errors including statement_timeout
    """
    conn = get_connection(timeout_seconds=timeout_seconds)
    try:
        cursor_factory = RealDictCursor if as_dict else None
        with conn.cursor(cursor_factory=cursor_factory) as cur:
            # Use timeout context as additional safety (especially for Windows)
            with timeout_context(timeout_seconds):
                cur.execute(query)
            
            if cur.description:  # SELECT query
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
                return columns, rows
            else:  # INSERT/UPDATE/DELETE
                conn.commit()
                return None, f"Rows affected: {cur.rowcount}"
    except QueryTimeoutError:
        # Try to cancel the query if possible
        try:
            conn.cancel()
        except:
            pass
        raise
    except OperationalError as e:
        # Check if it's a statement_timeout error
        if "statement_timeout" in str(e).lower() or "canceling statement" in str(e).lower():
            raise QueryTimeoutError(f"Query exceeded timeout of {timeout_seconds} seconds: {e}")
        raise
    finally:
        conn.close()


def print_results(columns, rows, fmt="simple"):
    """Pretty print query results."""
    if columns is None:
        print(rows)
        return
    
    if not rows:
        print("(0 rows)")
        return
    
    # Convert rows to list of lists for tabulate
    if isinstance(rows[0], dict):
        data = [list(row.values()) for row in rows]
    else:
        data = rows
    
    print(tabulate(data, headers=columns, tablefmt=fmt))
    print(f"\n({len(rows)} rows)")


def list_schemas(timeout_seconds=30):
    """List all schemas in the database."""
    query = """
        SELECT nspname as schema_name, 
               pg_catalog.obj_description(oid, 'pg_namespace') as description
        FROM pg_namespace 
        WHERE nspname NOT LIKE 'pg_%' 
          AND nspname != 'information_schema'
        ORDER BY nspname;
    """
    columns, rows = run_query(query, timeout_seconds=timeout_seconds)
    print_results(columns, rows)


def list_tables(schema: str, timeout_seconds=30):
    """List all tables in a schema with row counts."""
    query = f"""
        SELECT 
            table_name,
            pg_size_pretty(pg_total_relation_size('{schema}.' || table_name)) as size
        FROM information_schema.tables 
        WHERE table_schema = '{schema}'
        ORDER BY table_name;
    """
    columns, rows = run_query(query, timeout_seconds=timeout_seconds)
    print_results(columns, rows)


def table_counts(schema: str, timeout_seconds=30):
    """Get row counts for all tables in a schema."""
    # First get table names
    query = f"""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = '{schema}'
          AND table_name NOT LIKE '_dlt%'
        ORDER BY table_name;
    """
    _, tables = run_query(query, timeout_seconds=timeout_seconds)
    
    if not tables:
        print(f"No tables found in schema '{schema}'")
        return
    
    results = []
    for (table_name,) in tables:
        count_query = f"SELECT count(*) FROM {schema}.{table_name}"
        try:
            _, count_rows = run_query(count_query, timeout_seconds=timeout_seconds)
            results.append((table_name, count_rows[0][0]))
        except QueryTimeoutError:
            results.append((table_name, "TIMEOUT"))
    
    print(tabulate(results, headers=["Table", "Row Count"], tablefmt="simple"))


def main():
    parser = argparse.ArgumentParser(description="Query the NBA Analytics warehouse")
    parser.add_argument("query", nargs="?", help="SQL query to execute")
    parser.add_argument("--schemas", action="store_true", help="List all schemas")
    parser.add_argument("--tables", metavar="SCHEMA", help="List tables in schema")
    parser.add_argument("--counts", metavar="SCHEMA", help="Get row counts for schema")
    parser.add_argument("--file", "-f", metavar="FILE", help="Execute SQL from file")
    parser.add_argument("--format", default="simple", 
                       choices=["simple", "grid", "pipe", "html", "csv"],
                       help="Output format")
    parser.add_argument("--timeout", "-t", type=int, default=30,
                       help="Query timeout in seconds (default: 30). Set to 0 to disable.")
    
    args = parser.parse_args()
    
    # Convert 0 to None (disable timeout)
    timeout = None if args.timeout == 0 else args.timeout
    
    try:
        if args.schemas:
            list_schemas(timeout_seconds=timeout)
        elif args.tables:
            list_tables(args.tables, timeout_seconds=timeout)
        elif args.counts:
            table_counts(args.counts, timeout_seconds=timeout)
        elif args.file:
            with open(args.file, 'r') as f:
                query = f.read()
            columns, rows = run_query(query, timeout_seconds=timeout)
            print_results(columns, rows, args.format)
        elif args.query:
            columns, rows = run_query(args.query, timeout_seconds=timeout)
            print_results(columns, rows, args.format)
        else:
            parser.print_help()
    except QueryTimeoutError as e:
        print(f"Query timeout: {e}", file=sys.stderr)
        print("\nTip: Use --timeout to adjust the timeout, or optimize your query.", file=sys.stderr)
        sys.exit(1)
    except psycopg2.Error as e:
        print(f"Database error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
