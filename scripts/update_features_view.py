"""Manually update features_dev.game_features view to point to latest snapshot with interaction features."""

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

# Find the latest snapshot table that has the interaction features
cursor.execute("""
    SELECT table_name 
    FROM information_schema.tables 
    WHERE table_schema = 'features_dev' 
      AND table_name LIKE 'game_features__%'
    ORDER BY table_name DESC
    LIMIT 5
""")

snapshots = [r['table_name'] for r in cursor.fetchall()]
print(f"Found {len(snapshots)} snapshot tables")
for snap in snapshots:
    print(f"  {snap}")

# Check which snapshot has the interaction features
for snap in snapshots:
    cursor.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_schema = 'features_dev' 
          AND table_name = %s
          AND column_name IN ('injury_impact_x_form_diff', 'away_injury_x_form', 'home_injury_x_form')
    """, (snap,))
    cols = [r['column_name'] for r in cursor.fetchall()]
    if len(cols) >= 3:
        print(f"\nFound snapshot with interaction features: {snap}")
        print(f"  Has {len(cols)} interaction features")
        
        # Update the view
        print(f"\nUpdating view features_dev.game_features to point to {snap}...")
        cursor.execute(f"DROP VIEW IF EXISTS features_dev.game_features")
        cursor.execute(f"""
            CREATE VIEW features_dev.game_features AS 
            SELECT * FROM features_dev.{snap}
        """)
        conn.commit()
        print("View updated successfully!")
        break
else:
    print("\nNo snapshot found with interaction features. Checking marts.mart_game_features...")
    # Check if marts has the features
    cursor.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_schema = 'marts' 
          AND table_name = 'mart_game_features'
          AND column_name IN ('injury_impact_x_form_diff', 'away_injury_x_form', 'home_injury_x_form')
    """)
    cols = [r['column_name'] for r in cursor.fetchall()]
    if len(cols) >= 3:
        print(f"marts.mart_game_features has {len(cols)} interaction features")
        print("The view should be selecting from marts.mart_game_features, checking view definition...")
    else:
        print("Interaction features not found in marts.mart_game_features either")

cursor.close()
conn.close()
