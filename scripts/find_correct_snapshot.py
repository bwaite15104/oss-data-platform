"""Find the snapshot that has injury columns with data."""

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

# Check all snapshots
cursor.execute("""
    SELECT table_name
    FROM information_schema.tables
    WHERE table_schema = 'marts'
      AND table_name LIKE '%mart_game_features%'
      AND table_type = 'BASE TABLE'
    ORDER BY table_name DESC
""")
snapshots = [r['table_name'] for r in cursor.fetchall()]

print("Checking snapshots for injury columns and data...\n")
best_snapshot = None

for snapshot in snapshots[:5]:  # Check latest 5
    # Check if it has injury columns
    cursor.execute(f"""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'marts'
          AND table_name = '{snapshot}'
          AND column_name = 'home_injury_impact_score'
    """)
    has_col = cursor.fetchone() is not None
    
    if has_col:
        cursor.execute(f"""
            SELECT 
                COUNT(*) as total,
                COUNT(CASE WHEN home_injury_impact_score > 0 THEN 1 END) as non_zero
            FROM marts.{snapshot}
            WHERE game_date >= '2024-10-01'
        """)
        result = cursor.fetchone()
        print(f"{snapshot}:")
        print(f"  Has injury columns: Yes")
        print(f"  Games with injury impact > 0: {result['non_zero']:,} / {result['total']:,}")
        if result['non_zero'] > 0:
            best_snapshot = snapshot
            print(f"  [OK] This snapshot has injury data!")
    else:
        print(f"{snapshot}: No injury columns")
    print()

if best_snapshot:
    print(f"Best snapshot: marts.{best_snapshot}")
    # Update view
    cursor.execute("DROP VIEW IF EXISTS marts.mart_game_features CASCADE")
    cursor.execute(f"""
        CREATE VIEW marts.mart_game_features AS
        SELECT * FROM marts.{best_snapshot}
    """)
    conn.commit()
    print("[OK] Updated view to point to best snapshot")
else:
    print("ERROR: No snapshot with injury data found")

cursor.close()
conn.close()
