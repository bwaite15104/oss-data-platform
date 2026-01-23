"""Show predictions for 1/22/2026 games with actual results."""

import psycopg2
from psycopg2.extras import RealDictCursor
import os

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

# Get predictions for 1/22 games with team names and actual results
cursor.execute("""
    SELECT 
        p.prediction_id,
        p.game_id,
        p.predicted_value as predicted_home_win,
        p.confidence,
        p.predicted_at,
        g.game_date,
        ht.team_name as home_team,
        at.team_name as away_team,
        g.home_score,
        g.away_score,
        g.winner_team_id,
        CASE WHEN g.winner_team_id = g.home_team_id THEN 1 ELSE 0 END as actual_home_win,
        CASE 
            WHEN g.winner_team_id = g.home_team_id AND p.predicted_value = 1 THEN 'CORRECT'
            WHEN g.winner_team_id != g.home_team_id AND p.predicted_value = 0 THEN 'CORRECT'
            ELSE 'WRONG'
        END as result,
        CASE 
            WHEN g.winner_team_id != g.home_team_id AND p.predicted_value = 1 THEN 'UPSET'
            WHEN g.winner_team_id = g.home_team_id AND p.predicted_value = 0 THEN 'UPSET'
            ELSE NULL
        END as upset_type
    FROM ml_dev.predictions p
    JOIN staging.stg_games g ON p.game_id = g.game_id
    JOIN raw_dev.teams ht ON g.home_team_id = ht.team_id
    JOIN raw_dev.teams at ON g.away_team_id = at.team_id
    WHERE g.game_date::date = '2026-01-22'
      AND p.model_id = (SELECT model_id FROM ml_dev.model_registry WHERE is_active = true ORDER BY created_at DESC LIMIT 1)
    ORDER BY g.game_date, ht.team_name
""")

print("=" * 80)
print("PREDICTIONS FOR 1/22/2026 GAMES (After Injury Feature Retraining)")
print("=" * 80)
print()

rows = cursor.fetchall()
if not rows:
    print("No predictions found for 2026-01-22")
else:
    correct = 0
    wrong = 0
    upsets_correct = 0
    upsets_total = 0
    
    for row in rows:
        home_team = row['home_team']
        away_team = row['away_team']
        predicted = "HOME" if row['predicted_home_win'] == 1 else "AWAY"
        confidence = row['confidence']
        
        if row['home_score'] is not None and row['away_score'] is not None:
            actual = "HOME" if row['actual_home_win'] == 1 else "AWAY"
            score = f"{row['home_score']}-{row['away_score']}"
            result = row['result']
            
            # Check if this was an upset (away team won)
            is_upset = row['actual_home_win'] == 0
            predicted_upset = row['predicted_home_win'] == 0
            
            print(f"{home_team} vs {away_team}")
            print(f"  Prediction: {predicted} win (confidence: {confidence:.1%})")
            print(f"  Actual: {actual} win ({score})")
            print(f"  Result: {result}")
            
            if is_upset:
                upsets_total += 1
                if predicted_upset:
                    upsets_correct += 1
                    print(f"  [OK] Correctly predicted UPSET!")
                else:
                    print(f"  [X] Missed UPSET prediction")
            
            if result == 'CORRECT':
                correct += 1
            else:
                wrong += 1
        else:
            print(f"{home_team} vs {away_team}")
            print(f"  Prediction: {predicted} win (confidence: {confidence:.1%})")
            print(f"  Actual: Game not completed yet")
            print(f"  Status: PENDING")
        
        print()
    
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    total = correct + wrong
    if total > 0:
        print(f"Overall: {correct}/{total} correct ({100*correct/total:.1f}%)")
        if upsets_total > 0:
            print(f"Upsets: {upsets_correct}/{upsets_total} correctly predicted ({100*upsets_correct/upsets_total:.1f}%)")
        else:
            print("Upsets: No upsets in completed games")
    else:
        print("No completed games yet")

cursor.close()
conn.close()
