#!/usr/bin/env python3
"""
Ensure indexes defined in ODCS contracts are created in the database.

This script reads contract definitions from contracts/schemas/*.yml and contracts/contracts/*.yml,
extracts index definitions, and creates any missing indexes on the corresponding tables.

Usage:
    python scripts/ensure_contract_indexes.py
    docker exec nba_analytics_dagster_webserver python /app/scripts/ensure_contract_indexes.py
"""

import os
import sys
import yaml
from pathlib import Path
from typing import List, Dict, Any, Optional

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("Error: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)


def get_project_root() -> Path:
    """Get project root directory."""
    return Path(__file__).resolve().parent.parent


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


def load_contracts(contracts_dir: Path) -> List[Dict[str, Any]]:
    """Load all contract YAML files."""
    contracts = []
    for contract_file in contracts_dir.glob("*.yml"):
        try:
            with open(contract_file, 'r') as f:
                contract = yaml.safe_load(f)
                if contract:
                    contracts.append(contract)
        except Exception as e:
            print(f"Warning: Could not load {contract_file}: {e}")
    return contracts


def extract_indexes_from_contract(contract: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract index definitions from a contract."""
    indexes = []
    schema = contract.get("schema", {})
    
    # Get table name (contract name or schema name)
    table_name = contract.get("name") or schema.get("name", "")
    
    # Get schema name (default to raw_dev for base tables)
    schema_name = contract.get("schema_name", "raw_dev")
    
    # Extract indexes from schema.indexes
    schema_indexes = schema.get("indexes", [])
    for idx_def in schema_indexes:
        if isinstance(idx_def, dict):
            columns = idx_def.get("columns", [])
            if columns:
                indexes.append({
                    "schema": schema_name,
                    "table": table_name,
                    "columns": columns,
                    "name": idx_def.get("name"),  # Optional explicit index name
                })
        elif isinstance(idx_def, list):
            # Simple list of column names
            indexes.append({
                "schema": schema_name,
                "table": table_name,
                "columns": idx_def,
                "name": None,
            })
    
    return indexes


def index_exists(conn, schema: str, table: str, columns: List[str]) -> bool:
    """Check if an index exists for the given columns."""
    cur = conn.cursor()
    try:
        # Get all indexes on this table
        cur.execute("""
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = %s AND tablename = %s
        """, (schema, table))
        
        existing_indexes = cur.fetchall()
        
        # Check if any index covers these columns (simple check - matches column names)
        columns_str = ", ".join(columns)
        for idx_name, idx_def in existing_indexes:
            # Check if all columns are in the index definition
            if all(col in idx_def for col in columns):
                return True
        
        return False
    finally:
        cur.close()


def create_index(conn, schema: str, table: str, columns: List[str], index_name: Optional[str] = None) -> bool:
    """Create an index if it doesn't exist."""
    if index_name is None:
        # Generate index name: idx_<table>_<columns>
        cols_str = "_".join(columns)
        index_name = f"idx_{table}_{cols_str}"
    
    # Check if index already exists
    if index_exists(conn, schema, table, columns):
        print(f"  ✓ Index already exists: {schema}.{table} on {columns}")
        return False
    
    cur = conn.cursor()
    try:
        columns_expr = ", ".join(columns)
        create_sql = f'CREATE INDEX IF NOT EXISTS {index_name} ON {schema}.{table}({columns_expr})'
        cur.execute(create_sql)
        conn.commit()
        print(f"  ✓ Created index: {index_name} on {schema}.{table}({columns_expr})")
        return True
    except Exception as e:
        print(f"  ✗ Failed to create index {index_name} on {schema}.{table}: {e}")
        conn.rollback()
        return False
    finally:
        cur.close()


def main():
    project_root = get_project_root()
    contracts_dir = project_root / "contracts" / "contracts"
    schemas_dir = project_root / "contracts" / "schemas"
    
    if not contracts_dir.exists():
        print(f"Error: Contracts directory not found: {contracts_dir}")
        sys.exit(1)
    
    # Load contracts
    print("Loading contracts...")
    contracts = load_contracts(contracts_dir)
    if schemas_dir.exists():
        # Also load schemas (they may have index definitions)
        schema_files = list(schemas_dir.glob("*.yml"))
        for schema_file in schema_files:
            try:
                with open(schema_file, 'r') as f:
                    schema_data = yaml.safe_load(f)
                    if schema_data:
                        # Convert schema to contract-like structure
                        contracts.append({
                            "name": schema_data.get("name"),
                            "schema": schema_data,
                            "schema_name": "raw_dev",  # Base tables are in raw_dev
                        })
            except Exception as e:
                print(f"Warning: Could not load schema {schema_file}: {e}")
    
    print(f"Found {len(contracts)} contracts")
    
    # Extract all indexes
    all_indexes = []
    for contract in contracts:
        indexes = extract_indexes_from_contract(contract)
        all_indexes.extend(indexes)
    
    print(f"Found {len(all_indexes)} index definitions")
    
    if not all_indexes:
        print("No indexes found in contracts. Exiting.")
        return
    
    # Connect to database
    try:
        conn = get_connection()
    except Exception as e:
        print(f"Error connecting to database: {e}")
        sys.exit(1)
    
    # Create missing indexes
    print("\nCreating indexes...")
    created_count = 0
    skipped_count = 0
    
    for idx_def in all_indexes:
        schema = idx_def["schema"]
        table = idx_def["table"]
        columns = idx_def["columns"]
        index_name = idx_def.get("name")
        
        # Check if table exists
        cur = conn.cursor()
        cur.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = %s AND table_name = %s
            )
        """, (schema, table))
        table_exists = cur.fetchone()[0]
        cur.close()
        
        if not table_exists:
            print(f"  ⚠ Table {schema}.{table} does not exist, skipping indexes")
            skipped_count += 1
            continue
        
        if create_index(conn, schema, table, columns, index_name):
            created_count += 1
        else:
            skipped_count += 1
    
    # Run ANALYZE on tables that had indexes created
    if created_count > 0:
        print("\nRunning ANALYZE on updated tables...")
        analyzed_tables = set()
        for idx_def in all_indexes:
            schema = idx_def["schema"]
            table = idx_def["table"]
            table_key = f"{schema}.{table}"
            if table_key not in analyzed_tables:
                try:
                    cur = conn.cursor()
                    cur.execute(f"ANALYZE {schema}.{table}")
                    conn.commit()
                    cur.close()
                    print(f"  ✓ Analyzed {schema}.{table}")
                    analyzed_tables.add(table_key)
                except Exception as e:
                    print(f"  ✗ Failed to ANALYZE {schema}.{table}: {e}")
    
    conn.close()
    
    print(f"\nSummary:")
    print(f"  Created: {created_count} indexes")
    print(f"  Skipped: {skipped_count} indexes (already exist or table missing)")


if __name__ == "__main__":
    main()
