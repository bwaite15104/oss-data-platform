"""Force SQLMesh to refresh mart_game_features by checking what's wrong."""

import psycopg2
from psycopg2.extras import RealDictCursor

conn = psycopg2.connect(
    host='localhost',
    port=5432,
    database='nba_analytics',
    user='postgres',
    password='postgres',
)
cursor = conn.cursor(cursor_factory=RealDictCursor)

# Check if marts.mart_game_features is a view or table
cursor.execute("""
    SELECT table_type
    FROM information_schema.tables
    WHERE table_schema = 'marts'
      AND table_name = 'mart_game_features'
""")
result = cursor.fetchone()
if result:
    print(f"marts.mart_game_features is a {result['table_type']}")
    
    if result['table_type'] == 'VIEW':
        # Get view definition snippet
        cursor.execute("""
            SELECT SUBSTRING(view_definition, 1, 500) as def_snippet
            FROM information_schema.views
            WHERE table_schema = 'marts'
              AND table_name = 'mart_game_features'
        """)
        view_def = cursor.fetchone()
        if view_def:
            def_text = view_def['def_snippet']
            # Check if it references injury
            if 'injury' in def_text.lower():
                print("  View definition includes 'injury'")
            else:
                print("  WARNING: View definition does NOT include 'injury'")
                print(f"  Snippet: {def_text[:200]}...")

# Check latest snapshot table
cursor.execute("""
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = 'marts'
      AND table_name LIKE '%mart_game_features%'
      AND table_type = 'BASE TABLE'
    ORDER BY table_name DESC
    LIMIT 1
""")
result = cursor.fetchone()
if result:
    table_name = result['table_name']
    print(f"\nLatest snapshot table: marts.{table_name}")
    
    # Check columns
    cursor.execute(f"""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'marts'
          AND table_name = '{table_name}'
          AND column_name LIKE '%injury%'
    """)
    injury_cols = [r['column_name'] for r in cursor.fetchall()]
    print(f"  Injury columns in snapshot: {len(injury_cols)}")
    if injury_cols:
        print(f"  Columns: {', '.join(injury_cols[:5])}")
    else:
        print("  WARNING: Snapshot table has NO injury columns!")

cursor.close()
conn.close()
