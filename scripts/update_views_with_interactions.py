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
    
    # Update features_dev view - find its snapshot
    cursor.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'features_dev'
          AND table_name LIKE '%game_features%'
          AND table_type = 'BASE TABLE'
        ORDER BY table_name DESC
        LIMIT 5
    """)
    feature_snapshots = [r['table_name'] for r in cursor.fetchall()]
    
    best_feature_snapshot = None
    for snap in feature_snapshots:
        cursor.execute("""
            SELECT COUNT(*) as cnt
            FROM information_schema.columns 
            WHERE table_schema = 'features_dev' 
              AND table_name = %s
              AND column_name IN ('injury_impact_x_form_diff', 'away_injury_x_form')
        """, (snap,))
        result = cursor.fetchone()
        if result and result['cnt'] >= 2:
            best_feature_snapshot = snap
            print(f"Found features_dev snapshot with interaction features: {snap}")
            break
    
    if best_feature_snapshot:
        cursor.execute("DROP VIEW IF EXISTS features_dev.game_features CASCADE")
        cursor.execute(f"""
            CREATE VIEW features_dev.game_features AS
            SELECT * FROM features_dev.{best_feature_snapshot}
        """)
        print("Updated features_dev.game_features view")
    else:
        print("WARNING: No features_dev snapshot with interaction features found")
        print("Will need to rebuild features_dev.game_features")
    
    conn.commit()
    print("\nViews updated successfully!")
else:
    print("ERROR: No snapshot with interaction features found")

cursor.close()
conn.close()
