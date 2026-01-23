"""Force SQLMesh to rebuild marts.mart_game_features by dropping the latest snapshot."""

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

# Find the latest snapshot
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
    latest_snapshot = result['table_name']
    print(f"Latest snapshot: {latest_snapshot}")
    
    # Check if it has interaction features
    cursor.execute(f"""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_schema = 'marts' 
          AND table_name = %s
          AND column_name IN ('injury_impact_x_form_diff', 'away_injury_x_form')
    """, (latest_snapshot,))
    cols = [r['column_name'] for r in cursor.fetchall()]
    
    if len(cols) == 0:
        print(f"Snapshot {latest_snapshot} does NOT have interaction features")
        print("Dropping this snapshot to force SQLMesh to rebuild...")
        cursor.execute(f"DROP TABLE IF EXISTS marts.{latest_snapshot} CASCADE")
        conn.commit()
        print("Snapshot dropped. Now run: sqlmesh plan local --auto-apply --select-model marts.mart_game_features")
    else:
        print(f"Snapshot {latest_snapshot} HAS interaction features ({len(cols)})")
        print("The view should be updated to point to this snapshot")
else:
    print("No snapshot tables found")

cursor.close()
conn.close()
