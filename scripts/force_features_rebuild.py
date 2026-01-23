"""Force rebuild of features_dev.game_features."""

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

# Find and drop latest snapshot
cursor.execute("""
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = 'features_dev'
      AND table_name LIKE '%game_features%'
      AND table_type = 'BASE TABLE'
    ORDER BY table_name DESC
    LIMIT 1
""")
result = cursor.fetchone()
if result:
    snap = result['table_name']
    print(f"Dropping snapshot: {snap}")
    cursor.execute(f"DROP TABLE IF EXISTS features_dev.{snap} CASCADE")
    conn.commit()
    print("Snapshot dropped. Now run: sqlmesh plan local --auto-apply --select-model features_dev.game_features")
else:
    print("No snapshot found")

cursor.close()
conn.close()
