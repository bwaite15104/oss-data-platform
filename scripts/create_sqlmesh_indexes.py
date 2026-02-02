#!/usr/bin/env python3
"""
Create indexes on SQLMesh snapshot tables to improve query performance.

This script finds all SQLMesh snapshot tables and creates appropriate indexes
based on common join patterns (game_id, team_id, game_date, etc.).

Usage:
    python scripts/create_sqlmesh_indexes.py
    docker exec nba_analytics_dagster_webserver python /app/scripts/create_sqlmesh_indexes.py
"""

import os
import sys
from pathlib import Path

try:
    import psycopg2
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


def create_indexes_for_table(conn, schema: str, table_name: str) -> int:
    """Create indexes on a SQLMesh snapshot table based on its columns."""
    cur = conn.cursor()
    indexes_created = 0
    
    try:
        # Check what columns exist
        cur.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_schema = %s 
              AND table_name = %s
              AND column_name IN ('game_id', 'team_id', 'player_id', 'game_date')
            ORDER BY CASE column_name
                WHEN 'game_id' THEN 1
                WHEN 'team_id' THEN 2
                WHEN 'player_id' THEN 3
                WHEN 'game_date' THEN 4
            END
        """, (schema, table_name))
        
        columns = [row[0] for row in cur.fetchall()]
        
        # Determine indexes based on columns
        indexes_to_create = []
        
        if 'game_id' in columns:
            indexes_to_create.append((f"idx_{table_name}_game_id", "(game_id)"))
        
        # Composite (game_id, team_id) for mart joins (e.g. int_team_star_player_features)
        if 'game_id' in columns and 'team_id' in columns:
            indexes_to_create.append((f"idx_{table_name}_game_team", "(game_id, team_id)"))
        
        if 'team_id' in columns and 'game_date' in columns:
            indexes_to_create.append((f"idx_{table_name}_team_date", "(team_id, game_date DESC)"))
        elif 'team_id' in columns:
            indexes_to_create.append((f"idx_{table_name}_team_id", "(team_id)"))
        
        if 'game_date' in columns and 'game_id' not in columns:
            indexes_to_create.append((f"idx_{table_name}_game_date", "(game_date)"))
        
        if 'player_id' in columns:
            indexes_to_create.append((f"idx_{table_name}_player_id", "(player_id)"))
        
        # Create indexes
        for idx_name, columns_expr in indexes_to_create:
            try:
                # Check if index already exists
                cur.execute("""
                    SELECT 1 FROM pg_indexes 
                    WHERE schemaname = %s 
                      AND tablename = %s 
                      AND indexname = %s
                """, (schema, table_name, idx_name))
                
                if cur.fetchone():
                    print(f"  ✓ Index {idx_name} already exists")
                    continue
                
                create_sql = f"CREATE INDEX {idx_name} ON {schema}.{table_name}{columns_expr}"
                cur.execute(create_sql)
                indexes_created += 1
                print(f"  ✓ Created index {idx_name}")
            except Exception as e:
                print(f"  ✗ Failed to create index {idx_name}: {e}")
        
        # Run ANALYZE
        try:
            cur.execute(f"ANALYZE {schema}.{table_name}")
            print(f"  ✓ Ran ANALYZE on {schema}.{table_name}")
        except Exception as e:
            print(f"  ⚠ Failed to ANALYZE {schema}.{table_name}: {e}")
        
        conn.commit()
        
    except Exception as e:
        print(f"  ✗ Error processing {schema}.{table_name}: {e}")
        conn.rollback()
    finally:
        cur.close()
    
    return indexes_created


def main():
    conn = get_connection()
    cur = conn.cursor()
    
    print("Finding SQLMesh snapshot tables...")
    
    # Find all SQLMesh snapshot tables (pattern: schema__model_name__<id>; id is numeric or hex hash)
    cur.execute("""
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_schema IN ('intermediate', 'marts', 'features_dev')
          AND table_name LIKE '%__%'
          AND table_type = 'BASE TABLE'
          AND table_name ~ '__[0-9a-fA-F]+$'
        ORDER BY table_schema, table_name
    """)
    
    tables = cur.fetchall()
    print(f"Found {len(tables)} snapshot tables\n")
    if len(tables) == 0:
        print("Hint: Snapshot tables match pattern schema__model__<id> (id numeric or hex) in schemas intermediate, marts, features_dev. If you ran sqlmesh plan and expected tables here, run this script again after materialization.")
        print()

    total_indexes = 0
    for schema, table_name in tables:
        print(f"Processing {schema}.{table_name}...")
        indexes_created = create_indexes_for_table(conn, schema, table_name)
        total_indexes += indexes_created
        print()
    
    cur.close()
    conn.close()
    
    print("=" * 60)
    print(f"Created {total_indexes} indexes across {len(tables)} tables")
    print("=" * 60)


if __name__ == "__main__":
    main()
