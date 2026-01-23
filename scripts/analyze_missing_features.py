#!/usr/bin/env python3
"""Identify missing features that could predict upsets."""
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

print("=" * 80)
print("ANALYZING MISSING FEATURES FOR UPSET PREDICTION")
print("=" * 80)
print()

# Get all upsets (where lower win% team won) from recent games
query = """
    WITH team_records AS (
        SELECT 
            g.game_id,
            g.game_date,
            g.home_team_id,
            g.away_team_id,
            g.winner_team_id,
            ht.team_name as home_team,
            at.team_name as away_team,
            -- Season records
            (SELECT COUNT(*) FROM raw_dev.games g2 
             WHERE (g2.home_team_id = g.home_team_id OR g2.away_team_id = g.home_team_id)
               AND g2.game_date < g.game_date
               AND g2.home_score IS NOT NULL) as home_games,
            (SELECT SUM(CASE WHEN g2.winner_team_id = g.home_team_id THEN 1 ELSE 0 END)
             FROM raw_dev.games g2 
             WHERE (g2.home_team_id = g.home_team_id OR g2.away_team_id = g.home_team_id)
               AND g2.game_date < g.game_date
               AND g2.home_score IS NOT NULL) as home_wins,
            (SELECT COUNT(*) FROM raw_dev.games g2 
             WHERE (g2.home_team_id = g.away_team_id OR g2.away_team_id = g.away_team_id)
               AND g2.game_date < g.game_date
               AND g2.home_score IS NOT NULL) as away_games,
            (SELECT SUM(CASE WHEN g2.winner_team_id = g.away_team_id THEN 1 ELSE 0 END)
             FROM raw_dev.games g2 
             WHERE (g2.home_team_id = g.away_team_id OR g2.away_team_id = g.away_team_id)
               AND g2.game_date < g.game_date
               AND g2.home_score IS NOT NULL) as away_wins
        FROM raw_dev.games g
        JOIN raw_dev.teams ht ON ht.team_id = g.home_team_id
        JOIN raw_dev.teams at ON at.team_id = g.away_team_id
        WHERE g.game_date >= '2026-01-01'
          AND g.home_score IS NOT NULL
          AND g.away_score IS NOT NULL
    )
    SELECT 
        game_id,
        game_date,
        home_team,
        away_team,
        CASE WHEN winner_team_id = home_team_id THEN home_team ELSE away_team END as winner,
        CASE 
            WHEN home_games > 0 AND away_games > 0 THEN
                CASE WHEN (home_wins::float / home_games) < (away_wins::float / away_games) 
                     AND winner_team_id = home_team_id THEN 'Home Upset'
                     WHEN (away_wins::float / away_games) < (home_wins::float / home_games)
                     AND winner_team_id = away_team_id THEN 'Away Upset'
                     ELSE 'Expected'
                END
            ELSE 'Unknown'
        END as upset_type
    FROM team_records
    WHERE upset_type IN ('Home Upset', 'Away Upset')
    ORDER BY game_date DESC
    LIMIT 20
"""

df_upsets = pd.read_sql_query(query, conn)
print(f"Found {len(df_upsets)} upsets in January 2026")
print()

# Analyze patterns in upsets
print("PATTERNS IN RECENT UPSETS:")
print("-" * 80)
for idx, row in df_upsets.head(10).iterrows():
    print(f"{row['game_date']}: {row['winner']} wins ({row['upset_type']})")

conn.close()
