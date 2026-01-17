#!/usr/bin/env python3
"""
Simple database query utility for validating data in the warehouse.

Usage:
    python scripts/db_query.py "SELECT * FROM raw_dev.teams LIMIT 5"
    python scripts/db_query.py --tables raw_dev
    python scripts/db_query.py --schemas
    python scripts/db_query.py --counts raw_dev
    python scripts/db_query.py --file queries/my_query.sql
"""

import argparse
import os
import sys
from tabulate import tabulate

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("Error: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)


def get_connection():
    """Get database connection from environment or defaults."""
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
    return psycopg2.connect(
        host=host,
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=database,
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )


def run_query(query: str, as_dict: bool = False):
    """Execute a query and return results."""
    conn = get_connection()
    try:
        cursor_factory = RealDictCursor if as_dict else None
        with conn.cursor(cursor_factory=cursor_factory) as cur:
            cur.execute(query)
            if cur.description:  # SELECT query
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
                return columns, rows
            else:  # INSERT/UPDATE/DELETE
                conn.commit()
                return None, f"Rows affected: {cur.rowcount}"
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


def list_schemas():
    """List all schemas in the database."""
    query = """
        SELECT nspname as schema_name, 
               pg_catalog.obj_description(oid, 'pg_namespace') as description
        FROM pg_namespace 
        WHERE nspname NOT LIKE 'pg_%' 
          AND nspname != 'information_schema'
        ORDER BY nspname;
    """
    columns, rows = run_query(query)
    print_results(columns, rows)


def list_tables(schema: str):
    """List all tables in a schema with row counts."""
    query = f"""
        SELECT 
            table_name,
            pg_size_pretty(pg_total_relation_size('{schema}.' || table_name)) as size
        FROM information_schema.tables 
        WHERE table_schema = '{schema}'
        ORDER BY table_name;
    """
    columns, rows = run_query(query)
    print_results(columns, rows)


def table_counts(schema: str):
    """Get row counts for all tables in a schema."""
    # First get table names
    query = f"""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = '{schema}'
          AND table_name NOT LIKE '_dlt%'
        ORDER BY table_name;
    """
    _, tables = run_query(query)
    
    if not tables:
        print(f"No tables found in schema '{schema}'")
        return
    
    results = []
    for (table_name,) in tables:
        count_query = f"SELECT count(*) FROM {schema}.{table_name}"
        _, count_rows = run_query(count_query)
        results.append((table_name, count_rows[0][0]))
    
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
    
    args = parser.parse_args()
    
    try:
        if args.schemas:
            list_schemas()
        elif args.tables:
            list_tables(args.tables)
        elif args.counts:
            table_counts(args.counts)
        elif args.file:
            with open(args.file, 'r') as f:
                query = f.read()
            columns, rows = run_query(query)
            print_results(columns, rows, args.format)
        elif args.query:
            columns, rows = run_query(args.query)
            print_results(columns, rows, args.format)
        else:
            parser.print_help()
    except psycopg2.Error as e:
        print(f"Database error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
