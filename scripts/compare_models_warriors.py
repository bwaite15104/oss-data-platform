"""Compare Warriors prediction across different model versions."""

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

# Get Warriors game
cursor.execute("""
    SELECT g.game_id
    FROM staging.stg_games g
    JOIN raw_dev.teams ht ON g.home_team_id = ht.team_id
    JOIN raw_dev.teams at ON g.away_team_id = at.team_id
    WHERE g.game_date::date = '2026-01-22'
      AND (ht.team_name = 'Mavericks' OR at.team_name = 'Mavericks')
      AND (ht.team_name = 'Warriors' OR at.team_name = 'Warriors')
    LIMIT 1
""")
game = cursor.fetchone()

# Get all predictions for this game
cursor.execute("""
    SELECT 
        mr.model_version,
        mr.created_at,
        p.predicted_value,
        p.confidence,
        p.predicted_at
    FROM ml_dev.predictions p
    JOIN ml_dev.model_registry mr ON p.model_id = mr.model_id
    WHERE p.game_id = %s
    ORDER BY mr.created_at DESC
""", (game['game_id'],))

predictions = cursor.fetchall()

print("=" * 80)
print("WARRIORS PREDICTION EVOLUTION")
print("=" * 80)
print()

for pred in predictions:
    predicted = "HOME" if pred['predicted_value'] == 1 else "AWAY"
    actual = "HOME"  # Mavericks won
    is_correct = "CORRECT" if (pred['predicted_value'] == 1) else "WRONG"
    
    print(f"Model: {pred['model_version']}")
    print(f"  Created: {pred['created_at']}")
    print(f"  Prediction: {predicted} ({pred['confidence']:.1%})")
    print(f"  Actual: {actual}")
    print(f"  Result: {is_correct}")
    print()

cursor.close()
conn.close()
