"""Analyze prediction errors to understand why accuracy dropped."""

import psycopg2
from psycopg2.extras import RealDictCursor
import pandas as pd

conn = psycopg2.connect(
    host='localhost',
    port=5432,
    database='nba_analytics',
    user='postgres',
    password='postgres',
)
cursor = conn.cursor(cursor_factory=RealDictCursor)

# Get predictions and actual results for 2026-01-22
cursor.execute("""
    SELECT 
        p.predicted_value,
        p.confidence,
        CASE WHEN g.home_score > g.away_score THEN 1 ELSE 0 END as actual_home_win,
        g.home_score,
        g.away_score,
        ht.team_name as home_team,
        at.team_name as away_team,
        CASE WHEN p.predicted_value = (CASE WHEN g.home_score > g.away_score THEN 1 ELSE 0 END) THEN 1 ELSE 0 END as is_correct
    FROM ml_dev.predictions p
    JOIN staging.stg_games g ON p.game_id = g.game_id
    JOIN raw_dev.teams ht ON g.home_team_id = ht.team_id
    JOIN raw_dev.teams at ON g.away_team_id = at.team_id
    WHERE g.game_date::date = '2026-01-22'
      AND p.model_id = (SELECT model_id FROM ml_dev.model_registry WHERE is_active = true ORDER BY created_at DESC LIMIT 1)
    ORDER BY p.confidence DESC
""")

results = cursor.fetchall()

print("=" * 80)
print("PREDICTION ANALYSIS FOR 2026-01-22")
print("=" * 80)
print()

correct = 0
total = len(results)
high_confidence_wrong = 0
low_confidence_correct = 0

for r in results:
    predicted = "HOME" if r['predicted_value'] == 1 else "AWAY"
    actual = "HOME" if r['actual_home_win'] == 1 else "AWAY"
    correct_flag = "[OK]" if r['is_correct'] == 1 else "[X]"
    
    if r['is_correct'] == 1:
        correct += 1
        if r['confidence'] < 0.6:
            low_confidence_correct += 1
    else:
        if r['confidence'] > 0.7:
            high_confidence_wrong += 1
    
    print(f"{correct_flag} {r['home_team']} vs {r['away_team']}")
    print(f"   Predicted: {predicted} ({r['confidence']:.1%}) | Actual: {actual} ({r['home_score']}-{r['away_score']})")
    print()

print("=" * 80)
print(f"Summary: {correct}/{total} correct ({correct/total:.1%})")
print(f"High confidence (>70%) wrong predictions: {high_confidence_wrong}")
print(f"Low confidence (<60%) correct predictions: {low_confidence_correct}")
print("=" * 80)

# Check injury impact ratios for wrong predictions
print("\nChecking injury impact ratios for incorrect predictions...")
cursor.execute("""
    SELECT 
        ht.team_name as home_team,
        at.team_name as away_team,
        p.confidence,
        p.predicted_value,
        CASE WHEN g.home_score > g.away_score THEN 1 ELSE 0 END as actual_home_win,
        mf.home_injury_impact_ratio,
        mf.away_injury_impact_ratio,
        mf.injury_impact_diff,
        mf.win_pct_diff_10
    FROM ml_dev.predictions p
    JOIN staging.stg_games g ON p.game_id = g.game_id
    JOIN raw_dev.teams ht ON g.home_team_id = ht.team_id
    JOIN raw_dev.teams at ON g.away_team_id = at.team_id
    JOIN marts.mart_game_features mf ON mf.game_id = g.game_id
    WHERE g.game_date::date = '2026-01-22'
      AND p.model_id = (SELECT model_id FROM ml_dev.model_registry WHERE is_active = true ORDER BY created_at DESC LIMIT 1)
      AND p.predicted_value != (CASE WHEN g.home_score > g.away_score THEN 1 ELSE 0 END)
    ORDER BY ABS(mf.away_injury_impact_ratio) DESC, ABS(mf.home_injury_impact_ratio) DESC
""")

wrong_predictions = cursor.fetchall()
print(f"\nFound {len(wrong_predictions)} incorrect predictions:")
for wp in wrong_predictions:
    predicted = "HOME" if wp['predicted_value'] == 1 else "AWAY"
    actual = "HOME" if wp['actual_home_win'] == 1 else "AWAY"
    print(f"\n{wp['home_team']} vs {wp['away_team']}")
    print(f"  Predicted: {predicted} ({wp['confidence']:.1%}) | Actual: {actual}")
    print(f"  home_injury_impact_ratio: {wp['home_injury_impact_ratio']:.2f}")
    print(f"  away_injury_impact_ratio: {wp['away_injury_impact_ratio']:.2f}")
    print(f"  injury_impact_diff: {wp['injury_impact_diff']:.2f}")
    print(f"  win_pct_diff_10: {wp['win_pct_diff_10']:.3f}")

cursor.close()
conn.close()
