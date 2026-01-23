"""Update marts.mart_game_features view to point to latest snapshot with injury data."""

import psycopg2

conn = psycopg2.connect(
    host='localhost',
    port=5432,
    database='nba_analytics',
    user='postgres',
    password='postgres',
)
cursor = conn.cursor()

# Find snapshot with injury data
cursor.execute("""
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = 'marts'
      AND table_name LIKE '%mart_game_features%'
      AND table_type = 'BASE TABLE'
    ORDER BY table_name DESC
    LIMIT 5
""")
snapshots = [r[0] for r in cursor.fetchall()]

print("Checking snapshots for injury data...")
best_snapshot = None
best_count = 0

for snapshot in snapshots:
    cursor.execute(f"""
        SELECT COUNT(CASE WHEN home_injury_impact_score > 0 THEN 1 END) as non_zero
        FROM marts.{snapshot}
        WHERE game_date >= '2024-10-01'
    """)
    result = cursor.fetchone()
    count = result[0] if result else 0
    print(f"  {snapshot}: {count:,} games with injury impact > 0")
    if count > best_count:
        best_count = count
        best_snapshot = snapshot

if best_snapshot and best_count > 0:
    print(f"\nUsing snapshot: marts.{best_snapshot} ({best_count:,} games with injuries)")
    
    # Update view
    cursor.execute("DROP VIEW IF EXISTS marts.mart_game_features CASCADE")
    cursor.execute(f"""
        CREATE VIEW marts.mart_game_features AS
        SELECT * FROM marts.{best_snapshot}
    """)
    conn.commit()
    print("✓ Updated view marts.mart_game_features")
    
    # Verify
    cursor.execute("""
        SELECT 
            COUNT(*) as total,
            COUNT(CASE WHEN home_injury_impact_score > 0 THEN 1 END) as non_zero
        FROM marts.mart_game_features
        WHERE game_date >= '2024-10-01'
    """)
    result = cursor.fetchone()
    print(f"  View now has {result[0]:,} games, {result[1]:,} with injury impact > 0")
else:
    print("ERROR: No snapshot with injury data found")

cursor.close()
conn.close()
