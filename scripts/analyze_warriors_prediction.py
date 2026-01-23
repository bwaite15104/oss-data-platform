"""Analyze why the Warriors prediction was incorrect - check injury data and features."""

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

# Get the Mavericks vs Warriors game
cursor.execute("""
    SELECT 
        g.game_id,
        g.game_date,
        ht.team_name as home_team,
        at.team_name as away_team,
        g.home_score,
        g.away_score,
        g.winner_team_id
    FROM staging.stg_games g
    JOIN raw_dev.teams ht ON g.home_team_id = ht.team_id
    JOIN raw_dev.teams at ON g.away_team_id = at.team_id
    WHERE g.game_date::date = '2026-01-22'
      AND (ht.team_name = 'Mavericks' OR at.team_name = 'Mavericks')
      AND (ht.team_name = 'Warriors' OR at.team_name = 'Warriors')
""")

game = cursor.fetchone()
if not game:
    print("Game not found")
    exit(1)

print("=" * 80)
print(f"ANALYZING: {game['home_team']} vs {game['away_team']} ({game['game_date']})")
print("=" * 80)
print()

# Get injury data for this game
cursor.execute("""
    SELECT 
        i.injury_id,
        i.player_name,
        i.team_abbrev,
        i.capture_date,
        i.injury_details,
        i.status,
        t.team_name,
        p.player_id,
        sp.player_tier,
        sp.impact_score
    FROM raw_dev.injuries i
    LEFT JOIN raw_dev.teams t ON i.team_abbrev = t.team_abbreviation
    LEFT JOIN raw_dev.players p ON UPPER(TRIM(i.player_name)) = UPPER(TRIM(p.player_name))
        AND (i.team_abbrev = p.team_abbreviation OR i.team_abbrev = 'UNK')
    LEFT JOIN intermediate.int_star_players sp ON p.player_id = sp.player_id
    WHERE i.capture_date::date <= %s::date
      AND (i.status IS NULL OR i.status NOT IN ('Returned', 'Returned to lineup'))
      AND (t.team_name IN ('Mavericks', 'Warriors') OR i.team_abbrev IN ('DAL', 'GSW'))
    ORDER BY i.capture_date DESC, sp.impact_score DESC NULLS LAST
""", (game['game_date'],))

injuries = cursor.fetchall()

print("INJURY DATA FOR THIS GAME:")
print("-" * 80)
if injuries:
    for injury in injuries:
        team = injury['team_name'] or injury['team_abbrev'] or 'Unknown'
        tier = injury['player_tier'] or 'Not Star'
        impact = injury['impact_score'] or 0
        print(f"Player: {injury['player_name']} ({team})")
        print(f"  Date: {injury['capture_date']}")
        print(f"  Status: {injury['status']}")
        print(f"  Details: {injury['injury_details']}")
        print(f"  Tier: {tier}, Impact: {impact}")
        print()
else:
    print("No injury data found for this game")
    print()

# Get game features for this game
cursor.execute("""
    SELECT 
        gf.*
    FROM marts.mart_game_features gf
    WHERE gf.game_id = %s
""", (game['game_id'],))

features = cursor.fetchone()

if features:
    print("KEY FEATURES FOR THIS GAME:")
    print("-" * 80)
    
    # Injury features
    injury_features = [
        'home_star_players_out', 'away_star_players_out',
        'injury_impact_diff', 'home_has_key_injury', 'away_has_key_injury',
        'home_injury_impact_score', 'away_injury_impact_score'
    ]
    
    print("\nInjury Features:")
    for feat in injury_features:
        if feat in features:
            print(f"  {feat}: {features[feat]}")
    
    # Team strength features
    print("\nTeam Strength Features:")
    strength_features = [
        'home_team_win_pct', 'away_team_win_pct',
        'home_team_ppg', 'away_team_ppg',
        'home_team_opp_ppg', 'away_team_opp_ppg',
        'home_team_net_rating', 'away_team_net_rating',
        'away_team_net_rating'
    ]
    for feat in strength_features:
        if feat in features:
            print(f"  {feat}: {features[feat]}")
    
    # Star player features
    print("\nStar Player Features:")
    star_features = [
        'home_star_player_avg_pts', 'away_star_player_avg_pts',
        'home_star_player_count', 'away_star_player_count'
    ]
    for feat in star_features:
        if feat in features:
            print(f"  {feat}: {features[feat]}")

# Get prediction details
cursor.execute("""
    SELECT 
        p.predicted_value,
        p.confidence,
        p.predicted_at,
        m.model_version,
        m.hyperparameters
    FROM ml_dev.predictions p
    JOIN ml_dev.model_registry m ON p.model_id = m.model_id
    WHERE p.game_id = %s
      AND m.is_active = true
    ORDER BY p.predicted_at DESC
    LIMIT 1
""", (game['game_id'],))

prediction = cursor.fetchone()

if prediction:
    print("\n" + "=" * 80)
    print("PREDICTION DETAILS:")
    print("-" * 80)
    predicted_winner = "HOME (Mavericks)" if prediction['predicted_value'] == 1 else "AWAY (Warriors)"
    actual_winner = "HOME (Mavericks)" if game['winner_team_id'] == game['home_team_id'] else "AWAY (Warriors)"
    
    print(f"Predicted: {predicted_winner} (confidence: {prediction['confidence']:.1%})")
    print(f"Actual: {actual_winner} ({game['home_score']}-{game['away_score']})")
    print(f"Model Version: {prediction['model_version']}")
    print()

# Check if there were any recent injuries that might have been missed
cursor.execute("""
    SELECT 
        i.injury_id,
        i.player_name,
        i.team_abbrev,
        i.injury_date,
        i.injury_details,
        i.status,
        t.team_name
    FROM raw_dev.injuries i
    LEFT JOIN raw_dev.teams t ON i.team_abbrev = t.team_abbreviation
    WHERE i.injury_date BETWEEN %s::date - INTERVAL '7 days' AND %s::date
      AND (t.team_name IN ('Mavericks', 'Warriors') OR i.team_abbrev IN ('DAL', 'GSW'))
    ORDER BY i.injury_date DESC
""", (game['game_date'], game['game_date']))

recent_injuries = cursor.fetchall()

if recent_injuries:
    print("=" * 80)
    print("RECENT INJURIES (7 days before game):")
    print("-" * 80)
    for injury in recent_injuries:
        team = injury['team_name'] or injury['team_abbrev'] or 'Unknown'
        print(f"{injury['player_name']} ({team}) - {injury['capture_date']}: {injury['injury_details']} [{injury['status']}]")
    print()

cursor.close()
conn.close()
