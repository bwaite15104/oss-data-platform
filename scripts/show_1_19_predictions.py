#!/usr/bin/env python3
import psycopg2
import os

conn = psycopg2.connect(
    host=os.getenv("POSTGRES_HOST", "postgres"),
    port=5432,
    database=os.getenv("POSTGRES_DB", "nba_analytics"),
    user=os.getenv("POSTGRES_USER", "postgres"),
    password=os.getenv("POSTGRES_PASSWORD", "postgres"),
)
cur = conn.cursor()

# Get predictions for 1/19 games with team names and actual results
cur.execute("""
    SELECT 
        g.game_date::date as game_date,
        g.game_id,
        ht.team_name as home_team,
        at.team_name as away_team,
        p.predicted_value as predicted_winner,  -- 1 = home, 0 = away
        p.confidence,
        g.home_score,
        g.away_score,
        CASE 
            WHEN g.home_score > g.away_score THEN ht.team_name
            WHEN g.away_score > g.home_score THEN at.team_name
            ELSE 'Tie'
        END as actual_winner,
        CASE 
            WHEN (p.predicted_value = 1 AND g.home_score > g.away_score) 
                 OR (p.predicted_value = 0 AND g.away_score > g.home_score) 
            THEN 'Correct'
            WHEN g.home_score IS NULL OR g.away_score IS NULL THEN 'Pending'
            ELSE 'Wrong'
        END as prediction_result
    FROM ml_dev.predictions p
    JOIN raw_dev.games g ON g.game_id = p.game_id
    JOIN raw_dev.teams ht ON ht.team_id = g.home_team_id
    JOIN raw_dev.teams at ON at.team_id = g.away_team_id
    WHERE g.game_date::date = '2026-01-19'
      AND p.model_id = (SELECT model_id FROM ml_dev.model_registry ORDER BY created_at DESC LIMIT 1)
    ORDER BY g.game_id
""")

print("=" * 80)
print("Predictions for 1/19 Games")
print("=" * 80)
print()

for row in cur.fetchall():
    game_date, game_id, home_team, away_team, pred_winner, confidence, home_score, away_score, actual_winner, result = row
    
    predicted_team = home_team if pred_winner == 1 else away_team
    confidence_pct = f"{confidence * 100:.1f}%"
    
    print(f"Game: {away_team} @ {home_team}")
    print(f"  Prediction: {predicted_team} wins (confidence: {confidence_pct})")
    
    if home_score is not None and away_score is not None:
        print(f"  Actual: {actual_winner} wins ({home_score}-{away_score})")
        print(f"  Result: {result}")
    else:
        print(f"  Actual: Game in progress or not started")
        print(f"  Result: {result}")
    print()

conn.close()
