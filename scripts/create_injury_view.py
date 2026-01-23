"""Create the intermediate.int_game_injury_features view pointing to latest snapshot."""

import psycopg2

conn = psycopg2.connect(
    host='localhost',
    port=5432,
    database='nba_analytics',
    user='postgres',
    password='postgres',
)
cursor = conn.cursor()

# Get latest injury snapshot
cursor.execute("""
    SELECT table_name
    FROM information_schema.views
    WHERE table_schema = 'intermediate'
      AND table_name LIKE '%int_game_injury_features%'
    ORDER BY table_name DESC
    LIMIT 1
""")
result = cursor.fetchone()
latest_snapshot = result[0] if result else None

if latest_snapshot:
    print(f"Latest snapshot: intermediate.{latest_snapshot}")
    
    # Drop and recreate the view
    cursor.execute("DROP VIEW IF EXISTS intermediate.int_game_injury_features CASCADE")
    cursor.execute(f"""
        CREATE VIEW intermediate.int_game_injury_features AS
        SELECT * FROM intermediate.{latest_snapshot}
    """)
    conn.commit()
    print("Created view: intermediate.int_game_injury_features")
    
    # Verify it has data
    cursor.execute("""
        SELECT 
            COUNT(*) as total,
            COUNT(CASE WHEN home_injury_impact_score > 0 THEN 1 END) as non_zero
        FROM intermediate.int_game_injury_features
        WHERE game_date >= '2024-10-01'
    """)
    result = cursor.fetchone()
    print(f"  View has {result[0]:,} records, {result[1]:,} with non-zero impact")
else:
    print("ERROR: No injury snapshot found")

cursor.close()
conn.close()
