#!/usr/bin/env python3
"""Analyze the Mavericks @ Knicks upset to identify missing predictive features."""
import psycopg2
import os
import pandas as pd

conn = psycopg2.connect(
    host=os.getenv("POSTGRES_HOST", "postgres"),
    port=5432,
    database=os.getenv("POSTGRES_DB", "nba_analytics"),
    user=os.getenv("POSTGRES_USER", "postgres"),
    password=os.getenv("POSTGRES_PASSWORD", "postgres"),
)

# Find the game
game_id = '0022500016'  # Mavericks @ Knicks on 1/19

print("=" * 80)
print("ANALYZING MAVERICKS @ KNICKS UPSET (1/19/2026)")
print("=" * 80)
print()

# Get game details and features
query = """
    SELECT 
        g.game_id,
        g.game_date,
        ht.team_name as home_team,
        at.team_name as away_team,
        g.home_score,
        g.away_score,
        -- Current features
        m.home_rolling_5_ppg,
        m.away_rolling_5_ppg,
        m.home_rolling_5_win_pct,
        m.away_rolling_5_win_pct,
        m.home_season_win_pct,
        m.away_season_win_pct,
        m.season_win_pct_diff,
        m.home_rolling_5_opp_ppg,
        m.away_rolling_5_opp_ppg,
        m.home_has_star_return,
        m.away_has_star_return,
        m.star_return_advantage
    FROM raw_dev.games g
    JOIN raw_dev.teams ht ON ht.team_id = g.home_team_id
    JOIN raw_dev.teams at ON at.team_id = g.away_team_id
    LEFT JOIN marts.mart_game_features m ON m.game_id = g.game_id
    WHERE g.game_id = %s
"""
df = pd.read_sql_query(query, conn, params=[game_id])

if len(df) > 0:
    row = df.iloc[0]
    print(f"Game: {row['away_team']} @ {row['home_team']}")
    print(f"Score: {row['away_team']} {row['away_score']} - {row['home_score']} {row['home_team']}")
    print()
    print("CURRENT FEATURES:")
    print(f"  Home (Knicks) rolling 5-game PPG: {row['home_rolling_5_ppg']:.1f}")
    print(f"  Away (Mavericks) rolling 5-game PPG: {row['away_rolling_5_ppg']:.1f}")
    print(f"  Home rolling 5-game win %: {row['home_rolling_5_win_pct']:.3f}")
    print(f"  Away rolling 5-game win %: {row['away_rolling_5_win_pct']:.3f}")
    print(f"  Home season win %: {row['home_season_win_pct']:.3f}")
    print(f"  Away season win %: {row['away_season_win_pct']:.3f}")
    print(f"  Season win % diff: {row['season_win_pct_diff']:.3f}")
    print(f"  Home rolling 5-game opp PPG: {row['home_rolling_5_opp_ppg']:.1f}")
    print(f"  Away rolling 5-game opp PPG: {row['away_rolling_5_opp_ppg']:.1f}")
    print(f"  Home has star return: {row['home_has_star_return']}")
    print(f"  Away has star return: {row['away_has_star_return']}")
    print(f"  Star return advantage: {row['star_return_advantage']}")
    print()

# Get recent form (last 10 games)
print("RECENT FORM ANALYSIS:")
print("-" * 80)

# Home team recent games
home_query = """
    SELECT 
        g.game_date,
        CASE WHEN g.winner_team_id = g.home_team_id THEN 'W' ELSE 'L' END as result,
        g.home_score,
        g.away_score,
        CASE WHEN g.home_team_id = %s THEN ht.team_name ELSE at.team_name END as opponent
    FROM raw_dev.games g
    JOIN raw_dev.teams ht ON ht.team_id = g.home_team_id
    JOIN raw_dev.teams at ON at.team_id = g.away_team_id
    WHERE (g.home_team_id = %s OR g.away_team_id = %s)
      AND g.game_date < %s
      AND g.home_score IS NOT NULL
      AND g.away_score IS NOT NULL
    ORDER BY g.game_date DESC
    LIMIT 10
"""
home_team_id = df.iloc[0]['game_id']  # We need to get team_id
# Let me fix this query
conn.close()
