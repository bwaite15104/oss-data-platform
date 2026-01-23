"""Diagnose why injury features are all zeros."""

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

print("=" * 80)
print("INJURY MATCHING DIAGNOSIS")
print("=" * 80)

# Check int_game_injury_features view
print("\n1. intermediate.int_game_injury_features view:")
try:
    cursor.execute("""
        SELECT 
            COUNT(*) as total,
            COUNT(CASE WHEN home_injury_impact_score > 0 THEN 1 END) as non_zero
        FROM intermediate.int_game_injury_features
    """)
    result = cursor.fetchone()
    print(f"  Total records: {result['total']:,}")
    print(f"  Records with non-zero impact: {result['non_zero']:,}")
except Exception as e:
    print(f"  ERROR: {e}")

# Check star players table
print("\n2. intermediate.int_star_players table:")
try:
    cursor.execute("""
        SELECT 
            COUNT(*) as total,
            COUNT(DISTINCT player_id) as unique_players,
            COUNT(DISTINCT team_id) as unique_teams
        FROM intermediate.int_star_players
    """)
    result = cursor.fetchone()
    print(f"  Total records: {result['total']:,}")
    print(f"  Unique players: {result['unique_players']:,}")
    print(f"  Unique teams: {result['unique_teams']}")
except Exception as e:
    print(f"  ERROR: {e}")

# Check if injuries can match to star players
print("\n3. Injury to Star Player Matching:")
cursor.execute("""
    SELECT 
        COUNT(DISTINCT i.injury_id) as total_injuries,
        COUNT(DISTINCT CASE WHEN sp.player_id IS NOT NULL THEN i.injury_id END) as matched_to_star,
        COUNT(DISTINCT CASE WHEN p.player_id IS NOT NULL THEN i.injury_id END) as matched_to_any_player
    FROM staging.stg_injuries i
    LEFT JOIN raw_dev.players p 
        ON UPPER(TRIM(i.player_name)) = UPPER(TRIM(p.player_name))
        AND i.team_abbrev = p.team_abbreviation
    LEFT JOIN intermediate.int_star_players sp ON p.player_id = sp.player_id
    WHERE i.status != 'N/A'
      AND i.capture_date >= '2024-10-01'
""")
result = cursor.fetchone()
print(f"  Total injuries (since 2024-10-01): {result['total_injuries']:,}")
print(f"  Matched to any player: {result['matched_to_any_player']:,}")
print(f"  Matched to star player: {result['matched_to_star']:,}")
print(f"  Match rate to star: {100*result['matched_to_star']/result['total_injuries']:.1f}%")

# Check sample unmatched injuries
print("\n4. Sample Unmatched Injuries:")
cursor.execute("""
    SELECT 
        i.player_name,
        i.team_abbrev,
        i.status,
        i.capture_date,
        p.player_id as matched_player_id,
        sp.player_id as matched_star_id
    FROM staging.stg_injuries i
    LEFT JOIN raw_dev.players p 
        ON UPPER(TRIM(i.player_name)) = UPPER(TRIM(p.player_name))
        AND i.team_abbrev = p.team_abbreviation
    LEFT JOIN intermediate.int_star_players sp ON p.player_id = sp.player_id
    WHERE i.status != 'N/A'
      AND i.capture_date >= '2024-10-01'
      AND sp.player_id IS NULL
    LIMIT 10
""")
rows = cursor.fetchall()
print(f"  Sample injuries NOT matched to star players:")
for row in rows:
    print(f"    {row['player_name']} ({row['team_abbrev']}) - {row['status']} on {row['capture_date']} - Player matched: {row['matched_player_id'] is not None}")

# Check if the view query would work
print("\n5. Testing injury_star_matches CTE logic:")
cursor.execute("""
    WITH injury_star_matches AS (
        SELECT 
            i.injury_id,
            i.capture_date,
            i.player_name,
            i.team_abbrev,
            i.status,
            p.player_id,
            sp.player_tier,
            sp.impact_score,
            t.team_id as injury_team_id
        FROM staging.stg_injuries i
        LEFT JOIN raw_dev.teams t ON i.team_abbrev = t.team_abbreviation
        LEFT JOIN raw_dev.players p 
            ON UPPER(TRIM(i.player_name)) = UPPER(TRIM(p.player_name))
            AND i.team_abbrev = p.team_abbreviation
        LEFT JOIN intermediate.int_star_players sp ON p.player_id = sp.player_id
        WHERE i.status != 'N/A'
          AND sp.player_id IS NOT NULL
          AND i.capture_date >= '2024-10-01'
    )
    SELECT COUNT(*) as matches
    FROM injury_star_matches
""")
result = cursor.fetchone()
print(f"  Injury-star matches found: {result['matches']:,}")

cursor.close()
conn.close()

print("\n" + "=" * 80)
