"""Update both marts and features_dev views to point to snapshot with interaction features."""

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

# Find snapshot with interaction features
cursor.execute("""
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = 'marts'
      AND table_name LIKE '%mart_game_features%'
      AND table_type = 'BASE TABLE'
    ORDER BY table_name DESC
    LIMIT 10
""")
snapshots = [r['table_name'] for r in cursor.fetchall()]

best_snapshot = None
for snap in snapshots:
    cursor.execute("""
        SELECT COUNT(*) as cnt
        FROM information_schema.columns 
        WHERE table_schema = 'marts' 
          AND table_name = %s
          AND column_name IN ('injury_impact_x_form_diff', 'away_injury_x_form', 'home_injury_x_form', 
                              'home_injury_impact_ratio', 'away_injury_impact_ratio')
    """, (snap,))
    result = cursor.fetchone()
    if result and result['cnt'] == 5:
        best_snapshot = snap
        print(f"Found snapshot with all interaction features: {snap}")
        break

if best_snapshot:
    # Update marts view
    cursor.execute("DROP VIEW IF EXISTS marts.mart_game_features CASCADE")
    cursor.execute(f"""
        CREATE VIEW marts.mart_game_features AS
        SELECT * FROM marts.{best_snapshot}
    """)
    print("Updated marts.mart_game_features view")
    # features_dev.game_features view was removed; ML and scripts use marts.mart_game_features directly.
    
    conn.commit()
    print("\nViews updated successfully!")
else:
    print("ERROR: No snapshot with interaction features found")

cursor.close()
conn.close()
