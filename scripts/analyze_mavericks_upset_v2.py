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
game_date = '2026-01-19'

print("=" * 80)
print("ANALYZING MAVERICKS @ KNICKS UPSET (1/19/2026)")
print("=" * 80)
print()

# Get game details and team IDs
cur = conn.cursor()
cur.execute("""
    SELECT g.game_id, g.home_team_id, g.away_team_id,
           ht.team_name as home_team, at.team_name as away_team,
           g.home_score, g.away_score
    FROM raw_dev.games g
    JOIN raw_dev.teams ht ON ht.team_id = g.home_team_id
    JOIN raw_dev.teams at ON at.team_id = g.away_team_id
    WHERE g.game_id = %s
""", [game_id])
game_row = cur.fetchone()
home_team_id, away_team_id, home_team, away_team, home_score, away_score = game_row[1:]

print(f"Game: {away_team} @ {home_team}")
print(f"Score: {away_team} {away_score} - {home_score} {home_team}")
print(f"Result: {away_team} WINS (UPSET!)")
print()

# Get current features
query = """
    SELECT *
    FROM marts.mart_game_features
    WHERE game_id = %s
"""
df_features = pd.read_sql_query(query, conn, params=[game_id])

if len(df_features) > 0:
    f = df_features.iloc[0]
    print("CURRENT FEATURES:")
    print("-" * 80)
    print(f"  Home ({home_team}) rolling 5-game PPG: {f.get('home_rolling_5_ppg', 'N/A')}")
    print(f"  Away ({away_team}) rolling 5-game PPG: {f.get('away_rolling_5_ppg', 'N/A')}")
    print(f"  Home rolling 5-game win %: {f.get('home_rolling_5_win_pct', 'N/A')}")
    print(f"  Away rolling 5-game win %: {f.get('away_rolling_5_win_pct', 'N/A')}")
    print(f"  Home season win %: {f.get('home_season_win_pct', 'N/A')}")
    print(f"  Away season win %: {f.get('away_season_win_pct', 'N/A')}")
    print(f"  Season win % diff: {f.get('season_win_pct_diff', 'N/A')}")
    print()

# Get recent games for both teams
print("RECENT FORM (Last 10 Games):")
print("-" * 80)

for team_id, team_name in [(home_team_id, home_team), (away_team_id, away_team)]:
    cur.execute("""
        SELECT 
            g.game_date,
            CASE 
                WHEN g.winner_team_id = %s THEN 'W'
                WHEN g.home_score IS NOT NULL AND g.away_score IS NOT NULL THEN 'L'
                ELSE NULL
            END as result,
            g.home_score,
            g.away_score,
            CASE 
                WHEN g.home_team_id = %s THEN at.team_name 
                ELSE ht.team_name 
            END as opponent,
            CASE 
                WHEN g.home_team_id = %s THEN 'Home'
                ELSE 'Away'
            END as location
        FROM raw_dev.games g
        JOIN raw_dev.teams ht ON ht.team_id = g.home_team_id
        JOIN raw_dev.teams at ON at.team_id = g.away_team_id
        WHERE (g.home_team_id = %s OR g.away_team_id = %s)
          AND g.game_date < %s
          AND g.home_score IS NOT NULL
          AND g.away_score IS NOT NULL
        ORDER BY g.game_date DESC
        LIMIT 10
    """, [team_id, team_id, team_id, team_id, team_id, game_date])
    
    games = cur.fetchall()
    wins = sum(1 for g in games if g[1] == 'W')
    losses = sum(1 for g in games if g[1] == 'L')
    print(f"\n{team_name} (Last 10): {wins}W-{losses}L")
    print(f"  Recent results: {' '.join([g[1] if g[1] else '?' for g in games[:5]])}")
    
    # Check for back-to-back games
    if len(games) >= 2:
        try:
            from datetime import datetime
            date1 = pd.to_datetime(games[0][0])
            date2 = pd.to_datetime(games[1][0])
            days_between = (date1 - date2).days
            if days_between <= 1:
                print(f"  ⚠️  Played {days_between} day(s) ago (potential fatigue)")
        except:
            pass

# Check head-to-head record
print("\nHEAD-TO-HEAD HISTORY:")
print("-" * 80)
cur.execute("""
    SELECT 
        COUNT(*) as total_games,
        SUM(CASE WHEN g.winner_team_id = %s THEN 1 ELSE 0 END) as home_wins,
        SUM(CASE WHEN g.winner_team_id = %s THEN 1 ELSE 0 END) as away_wins
    FROM raw_dev.games g
    WHERE ((g.home_team_id = %s AND g.away_team_id = %s)
           OR (g.home_team_id = %s AND g.away_team_id = %s))
      AND g.game_date < %s
      AND g.home_score IS NOT NULL
      AND g.away_score IS NOT NULL
""", [home_team_id, away_team_id, home_team_id, away_team_id, away_team_id, home_team_id, game_date])
h2h = cur.fetchone()
if h2h[0] > 0:
    print(f"  Total meetings: {h2h[0]}")
    print(f"  {home_team} wins: {h2h[1]}")
    print(f"  {away_team} wins: {h2h[2]}")

# Check for key player injuries/availability
print("\nSTAR PLAYER AVAILABILITY:")
print("-" * 80)
cur.execute("""
    SELECT 
        p.player_name,
        t.team_name,
        b.game_id,
        b.minutes_played
    FROM raw_dev.boxscores b
    JOIN raw_dev.players p ON p.player_id = b.player_id
    JOIN raw_dev.teams t ON t.team_id = b.team_id
    WHERE b.game_id IN (
        SELECT game_id FROM raw_dev.games 
        WHERE game_date = %s::date - INTERVAL '1 day'
        AND (home_team_id = %s OR away_team_id = %s OR home_team_id = %s OR away_team_id = %s)
    )
    AND b.team_id IN (%s, %s)
    AND b.minutes_played > 30
    ORDER BY b.points DESC
    LIMIT 10
""", [game_date, home_team_id, home_team_id, away_team_id, away_team_id, home_team_id, away_team_id])
recent_players = cur.fetchall()
print(f"  Key players from previous day's games:")
for p in recent_players[:5]:
    print(f"    {p[1]}: {p[0]} ({p[3]} min)")

# Check rest days
print("\nREST DAYS:")
print("-" * 80)
for team_id, team_name in [(home_team_id, home_team), (away_team_id, away_team)]:
    cur.execute("""
        SELECT MAX(g.game_date) as last_game_date
        FROM raw_dev.games g
        WHERE (g.home_team_id = %s OR g.away_team_id = %s)
          AND g.game_date < %s
          AND g.home_score IS NOT NULL
          AND g.away_score IS NOT NULL
    """, [team_id, team_id, game_date])
    last_game = cur.fetchone()[0]
    if last_game:
        rest_days = (pd.to_datetime(game_date) - pd.to_datetime(last_game)).days
        print(f"  {team_name}: {rest_days} day(s) rest")

# Check conference/division standings context
print("\nSTANDINGS CONTEXT:")
print("-" * 80)
# Get season records
for team_id, team_name in [(home_team_id, home_team), (away_team_id, away_team)]:
    cur.execute("""
        SELECT 
            COUNT(*) as games_played,
            SUM(CASE WHEN g.winner_team_id = %s THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN g.winner_team_id != %s AND g.home_score IS NOT NULL THEN 1 ELSE 0 END) as losses
        FROM raw_dev.games g
        WHERE (g.home_team_id = %s OR g.away_team_id = %s)
          AND g.game_date < %s
          AND g.home_score IS NOT NULL
          AND g.away_score IS NOT NULL
    """, [team_id, team_id, team_id, team_id, game_date])
    record = cur.fetchone()
    if record[0] > 0:
        win_pct = record[1] / record[0]
        print(f"  {team_name}: {record[1]}-{record[2]} ({win_pct:.3f})")

conn.close()
