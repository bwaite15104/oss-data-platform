"""Investigate why injury features have 0 importance."""

import psycopg2
from psycopg2.extras import RealDictCursor
import os
import pandas as pd

# Database connection
database = os.getenv("POSTGRES_DB", "nba_analytics")
host = os.getenv("POSTGRES_HOST", "localhost")

conn = psycopg2.connect(
    host=host,
    port=int(os.getenv("POSTGRES_PORT", "5432")),
    database=database,
    user=os.getenv("POSTGRES_USER", "postgres"),
    password=os.getenv("POSTGRES_PASSWORD", "postgres"),
)
cursor = conn.cursor(cursor_factory=RealDictCursor)

print("=" * 80)
print("INJURY FEATURE INVESTIGATION")
print("=" * 80)

# Check injury data coverage
print("\n1. Raw Injury Data Coverage:")
cursor.execute("""
    SELECT 
        COUNT(*) as total,
        MIN(capture_date) as earliest,
        MAX(capture_date) as latest,
        COUNT(DISTINCT capture_date) as unique_dates
    FROM raw_dev.injuries
""")
result = cursor.fetchone()
print(f"  Total records: {result['total']:,}")
print(f"  Date range: {result['earliest']} to {result['latest']}")
print(f"  Unique dates: {result['unique_dates']:,}")

# Check injury data by decade
print("\n2. Injury Data by Decade:")
cursor.execute("""
    SELECT 
        FLOOR(EXTRACT(YEAR FROM capture_date::date) / 10) * 10 as decade,
        COUNT(*) as count
    FROM raw_dev.injuries
    WHERE capture_date IS NOT NULL
    GROUP BY FLOOR(EXTRACT(YEAR FROM capture_date::date) / 10) * 10
    ORDER BY decade
""")
for row in cursor.fetchall():
    print(f"  {int(row['decade'])}s: {row['count']:,} records")

# Check game features table for injury data
print("\n3. Injury Features in Game Features Table:")
cursor.execute("""
    SELECT 
        COUNT(*) as total_games,
        COUNT(CASE WHEN home_injury_impact_score > 0 THEN 1 END) as games_with_home_impact,
        COUNT(CASE WHEN away_injury_impact_score > 0 THEN 1 END) as games_with_away_impact,
        COUNT(CASE WHEN injury_impact_diff != 0 THEN 1 END) as games_with_diff,
        AVG(home_injury_impact_score) as avg_home_impact,
        AVG(away_injury_impact_score) as avg_away_impact,
        AVG(injury_impact_diff) as avg_diff,
        STDDEV(home_injury_impact_score) as stddev_home,
        STDDEV(away_injury_impact_score) as stddev_away,
        MIN(home_injury_impact_score) as min_home,
        MAX(home_injury_impact_score) as max_home,
        MIN(away_injury_impact_score) as min_away,
        MAX(away_injury_impact_score) as max_away
    FROM marts.mart_game_features
    WHERE game_date >= '2010-10-01'
""")
result = cursor.fetchone()
print(f"  Total games (since 2010): {result['total_games']:,}")
print(f"  Games with home injury impact: {result['games_with_home_impact']:,} ({100*result['games_with_home_impact']/result['total_games']:.1f}%)")
print(f"  Games with away injury impact: {result['games_with_away_impact']:,} ({100*result['games_with_away_impact']/result['total_games']:.1f}%)")
print(f"  Games with non-zero impact diff: {result['games_with_diff']:,} ({100*result['games_with_diff']/result['total_games']:.1f}%)")
print(f"  Home impact - Avg: {result['avg_home_impact']:.4f}, StdDev: {result['stddev_home']:.4f}, Range: [{result['min_home']:.4f}, {result['max_home']:.4f}]")
print(f"  Away impact - Avg: {result['avg_away_impact']:.4f}, StdDev: {result['stddev_away']:.4f}, Range: [{result['min_away']:.4f}, {result['max_away']:.4f}]")
print(f"  Impact diff - Avg: {result['avg_diff']:.4f}")

# Check if injury features are all zeros or nulls
print("\n4. Injury Feature Distribution:")
cursor.execute("""
    SELECT 
        home_star_players_out,
        COUNT(*) as count
    FROM marts.mart_game_features
    WHERE game_date >= '2010-10-01'
    GROUP BY home_star_players_out
    ORDER BY home_star_players_out
    LIMIT 10
""")
print("  home_star_players_out distribution:")
for row in cursor.fetchall():
    print(f"    {row['home_star_players_out']}: {row['count']:,} games")

# Check sample of games with injuries
print("\n5. Sample Games with Injury Data (2024-25 season):")
cursor.execute("""
    SELECT 
        game_date,
        home_injury_impact_score,
        away_injury_impact_score,
        injury_impact_diff,
        home_star_players_out,
        away_star_players_out
    FROM marts.mart_game_features
    WHERE game_date >= '2024-10-01'
      AND (home_injury_impact_score > 0 OR away_injury_impact_score > 0)
    ORDER BY game_date DESC
    LIMIT 10
""")
rows = cursor.fetchall()
if rows:
    print("  Sample games:")
    for row in rows:
        print(f"    {row['game_date']}: Home={row['home_injury_impact_score']:.2f}, Away={row['away_injury_impact_score']:.2f}, Diff={row['injury_impact_diff']:.2f}, HomeStarsOut={row['home_star_players_out']}, AwayStarsOut={row['away_star_players_out']}")
else:
    print("  ⚠️  No games found with injury impact > 0 in 2024-25 season!")

# Check correlation between injury features and game outcomes
print("\n6. Injury Impact vs Game Outcomes:")
cursor.execute("""
    SELECT 
        CASE 
            WHEN injury_impact_diff > 2 THEN 'Large Home Advantage'
            WHEN injury_impact_diff > 0.5 THEN 'Moderate Home Advantage'
            WHEN injury_impact_diff > -0.5 THEN 'Neutral'
            WHEN injury_impact_diff > -2 THEN 'Moderate Away Advantage'
            ELSE 'Large Away Advantage'
        END as injury_advantage,
        COUNT(*) as total_games,
        COUNT(CASE WHEN home_win = 1 THEN 1 END) as home_wins,
        ROUND(100.0 * COUNT(CASE WHEN home_win = 1 THEN 1 END) / COUNT(*), 1) as home_win_pct
    FROM marts.mart_game_features
    WHERE game_date >= '2010-10-01'
      AND injury_impact_diff != 0
    GROUP BY 
        CASE 
            WHEN injury_impact_diff > 2 THEN 'Large Home Advantage'
            WHEN injury_impact_diff > 0.5 THEN 'Moderate Home Advantage'
            WHEN injury_impact_diff > -0.5 THEN 'Neutral'
            WHEN injury_impact_diff > -2 THEN 'Moderate Away Advantage'
            ELSE 'Large Away Advantage'
        END
    ORDER BY 
        CASE 
            WHEN injury_impact_diff > 2 THEN 1
            WHEN injury_impact_diff > 0.5 THEN 2
            WHEN injury_impact_diff > -0.5 THEN 3
            WHEN injury_impact_diff > -2 THEN 4
            ELSE 5
        END
""")
print("  Injury advantage vs home win rate:")
for row in cursor.fetchall():
    print(f"    {row['injury_advantage']}: {row['home_wins']}/{row['total_games']} = {row['home_win_pct']:.1f}%")

# Check if injury features table exists
print("\n7. Injury Features Table (features_dev.team_injury_features):")
try:
    cursor.execute("""
        SELECT COUNT(*) as total
        FROM features_dev.team_injury_features
    """)
    result = cursor.fetchone()
    print(f"  Total records: {result['total']:,}")
    
    cursor.execute("""
        SELECT 
            MIN(game_date) as earliest,
            MAX(game_date) as latest
        FROM features_dev.team_injury_features
    """)
    result = cursor.fetchone()
    print(f"  Date range: {result['earliest']} to {result['latest']}")
except Exception as e:
    print(f"  ⚠️  Table doesn't exist or error: {e}")

# Check date alignment
print("\n8. Date Alignment Check (Recent Games):")
cursor.execute("""
    SELECT 
        g.game_date,
        COUNT(DISTINCT g.game_id) as games,
        COUNT(DISTINCT i.capture_date) as injury_dates_available
    FROM staging.stg_games g
    LEFT JOIN raw_dev.injuries i ON i.capture_date = g.game_date::date
    WHERE g.game_date >= '2024-10-01'
      AND g.is_completed = true
    GROUP BY g.game_date
    ORDER BY g.game_date DESC
    LIMIT 10
""")
print("  Recent games and available injury data:")
for row in cursor.fetchall():
    print(f"    {row['game_date']}: {row['games']} games, {row['injury_dates_available']} injury dates")

cursor.close()
conn.close()

print("\n" + "=" * 80)
