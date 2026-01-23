"""Deep feature analysis for Warriors vs Mavericks game to understand prediction."""

import psycopg2
from psycopg2.extras import RealDictCursor
import pandas as pd
import joblib
from pathlib import Path
import json
import numpy as np

# Database connection
conn = psycopg2.connect(
    host='localhost',
    port=5432,
    database='nba_analytics',
    user='postgres',
    password='postgres',
)
cursor = conn.cursor(cursor_factory=RealDictCursor)

# Get the latest model
cursor.execute('SELECT model_version, hyperparameters FROM ml_dev.model_registry WHERE is_active = true ORDER BY created_at DESC LIMIT 1')
model_info = cursor.fetchone()
model_version = model_info['model_version']
hyperparams = json.loads(model_info['hyperparameters']) if isinstance(model_info['hyperparameters'], str) else model_info['hyperparameters']
feature_names = hyperparams.get('features', [])

# Load model
models_dir1 = Path(__file__).parent.parent / 'models'
models_dir2 = Path('C:/Users/benja/Documents/code_projects/models')
model_files = []
if models_dir1.exists():
    model_files.extend(models_dir1.glob('game_winner_model_*.pkl'))
if models_dir2.exists():
    model_files.extend(models_dir2.glob('game_winner_model_*.pkl'))

model_path = None
if model_version:
    version_str = model_version.replace('v1.0.', '').replace('.pkl', '')
    for mf in model_files:
        if version_str in mf.name:
            model_path = mf
            break

if not model_path:
    model_path = max(model_files, key=lambda p: p.stat().st_mtime)

print(f"Using model: {model_path.name}")
model = joblib.load(model_path)
if isinstance(model, dict):
    model = model.get('model', model)

# Get Warriors game features
cursor.execute("""
    SELECT 
        g.game_id,
        ht.team_name as home_team,
        at.team_name as away_team
    FROM staging.stg_games g
    JOIN raw_dev.teams ht ON g.home_team_id = ht.team_id
    JOIN raw_dev.teams at ON g.away_team_id = at.team_id
    WHERE g.game_date::date = '2026-01-22'
      AND (ht.team_name = 'Mavericks' OR at.team_name = 'Mavericks')
      AND (ht.team_name = 'Warriors' OR at.team_name = 'Warriors')
""")
game = cursor.fetchone()

# Get all features for this game (matching training query structure)
cursor.execute("""
    SELECT 
        gf.game_id,
        -- Rolling 5-game features
        gf.home_rolling_5_ppg,
        gf.away_rolling_5_ppg,
        gf.home_rolling_5_win_pct,
        gf.away_rolling_5_win_pct,
        gf.home_rolling_5_opp_ppg,
        gf.away_rolling_5_opp_ppg,
        -- Rolling 10-game features
        gf.home_rolling_10_ppg,
        gf.away_rolling_10_ppg,
        gf.home_rolling_10_win_pct,
        gf.away_rolling_10_win_pct,
        -- Feature differences
        gf.ppg_diff_5,
        gf.win_pct_diff_5,
        gf.ppg_diff_10,
        gf.win_pct_diff_10,
        -- Season-level features
        gf.home_season_win_pct,
        gf.away_season_win_pct,
        gf.season_win_pct_diff,
        -- Star player return features
        gf.home_star_players_returning,
        gf.home_key_players_returning,
        gf.home_extended_returns,
        gf.home_total_return_impact,
        gf.home_max_days_since_return,
        gf.away_star_players_returning,
        gf.away_key_players_returning,
        gf.away_extended_returns,
        gf.away_total_return_impact,
        gf.away_max_days_since_return,
        gf.return_impact_diff,
        -- Injury features
        gf.home_star_players_out,
        gf.home_key_players_out,
        gf.home_star_players_doubtful,
        gf.home_key_players_doubtful,
        gf.home_star_players_questionable,
        gf.home_injury_impact_score,
        gf.home_injured_players_count,
        gf.home_has_key_injury,
        gf.away_star_players_out,
        gf.away_key_players_out,
        gf.away_star_players_doubtful,
        gf.away_key_players_doubtful,
        gf.away_star_players_questionable,
        gf.away_injury_impact_score,
        gf.away_injured_players_count,
        gf.away_has_key_injury,
        gf.injury_impact_diff,
        gf.star_players_out_diff,
        -- Feature interactions
        gf.injury_impact_x_form_diff,
        gf.away_injury_x_form,
        gf.home_injury_x_form,
        gf.home_injury_impact_ratio,
        gf.away_injury_impact_ratio,
        -- Momentum features
        COALESCE(mf.home_win_streak, 0) AS home_win_streak,
        COALESCE(mf.home_loss_streak, 0) AS home_loss_streak,
        COALESCE(mf.away_win_streak, 0) AS away_win_streak,
        COALESCE(mf.away_loss_streak, 0) AS away_loss_streak,
        COALESCE(mf.home_momentum_score, 0) AS home_momentum_score,
        COALESCE(mf.away_momentum_score, 0) AS away_momentum_score,
        -- Rest days features
        COALESCE(mf.home_rest_days, 0) AS home_rest_days,
        COALESCE(mf.home_back_to_back, FALSE) AS home_back_to_back,
        COALESCE(mf.away_rest_days, 0) AS away_rest_days,
        COALESCE(mf.away_back_to_back, FALSE) AS away_back_to_back,
        COALESCE(mf.rest_advantage, 0) AS rest_advantage,
        -- Form divergence features
        COALESCE(mf.home_form_divergence, 0) AS home_form_divergence,
        COALESCE(mf.away_form_divergence, 0) AS away_form_divergence,
        -- Head-to-head features
        COALESCE(mf.home_h2h_win_pct, 0.5) AS home_h2h_win_pct,
        COALESCE(mf.home_h2h_recent_wins, 0) AS home_h2h_recent_wins,
        -- Opponent quality features
        COALESCE(mf.home_recent_opp_avg_win_pct, 0.5) AS home_recent_opp_avg_win_pct,
        COALESCE(mf.away_recent_opp_avg_win_pct, 0.5) AS away_recent_opp_avg_win_pct,
        COALESCE(mf.home_performance_vs_quality, 0.5) AS home_performance_vs_quality,
        COALESCE(mf.away_performance_vs_quality, 0.5) AS away_performance_vs_quality,
        -- Home/Road performance features
        COALESCE(mf.home_home_win_pct, 0.5) AS home_home_win_pct,
        COALESCE(mf.away_road_win_pct, 0.5) AS away_road_win_pct,
        COALESCE(mf.home_advantage, 0) AS home_advantage
    FROM marts.mart_game_features gf
    LEFT JOIN intermediate.int_game_momentum_features mf ON mf.game_id = gf.game_id
    WHERE gf.game_id = %s
""", (game['game_id'],))

features = cursor.fetchone()

# Convert to DataFrame for model prediction
feature_dict = {k: v for k, v in features.items() if k != 'game_id'}
df = pd.DataFrame([feature_dict])

# Convert all columns to numeric, handling boolean columns
for col in df.columns:
    if df[col].dtype == 'object':
        # Try to convert to numeric
        df[col] = pd.to_numeric(df[col], errors='coerce')
    elif df[col].dtype == 'bool':
        # Convert boolean to int
        df[col] = df[col].astype(int)

df = df.fillna(0)

# Prepare features in same order as training
X = df[feature_names].fillna(0).astype(float)

# Get prediction
prediction = model.predict(X)[0]
probabilities = model.predict_proba(X)[0]
confidence = max(probabilities)

print("=" * 80)
print(f"FEATURE ANALYSIS: {game['home_team']} vs {game['away_team']}")
print("=" * 80)
print(f"\nPrediction: {'HOME' if prediction == 1 else 'AWAY'} ({confidence:.1%})")
print(f"Actual: HOME (Mavericks won)")
print()

# Get feature importances
importances = model.feature_importances_
feature_importance_dict = dict(zip(feature_names, importances))

# Calculate feature contributions (approximate using feature values * importance)
# This is a simplified approach - for exact SHAP values we'd need shap library
print("=" * 80)
print("TOP FEATURES DRIVING PREDICTION (Feature Value * Importance)")
print("=" * 80)
print()

contributions = []
for feat in feature_names:
    if feat in X.columns:
        value = float(X[feat].iloc[0])
        importance = feature_importance_dict[feat]
        # Simplified contribution: value * importance
        # Note: This is approximate - XGBoost uses trees with splits, not linear combinations
        # Positive contribution means feature pushes toward prediction=1 (HOME win)
        # Negative contribution means feature pushes toward prediction=0 (AWAY win)
        contribution = value * importance
        contributions.append({
            'feature': feat,
            'value': value,
            'importance': importance,
            'contribution': contribution
        })

# Sort by absolute contribution
contributions.sort(key=lambda x: abs(x['contribution']), reverse=True)

print("Top 20 Features Contributing to Prediction:")
print("-" * 80)
for i, contrib in enumerate(contributions[:20], 1):
    direction = "-> HOME" if contrib['contribution'] > 0 else "-> AWAY"
    print(f"{i:2d}. {contrib['feature']:35s} | "
          f"Value: {contrib['value']:10.3f} | "
          f"Importance: {contrib['importance']:.6f} | "
          f"Contribution: {contrib['contribution']:10.6f} {direction}")

# Analyze injury vs form features
print("\n" + "=" * 80)
print("INJURY vs FORM FEATURE COMPARISON")
print("=" * 80)
print()

injury_features = [c for c in contributions if 'injury' in c['feature'].lower()]
form_features = [c for c in contributions if any(x in c['feature'].lower() for x in ['win_pct', 'streak', 'momentum', 'form'])]

print("Top Injury Features:")
total_injury_contribution = sum(c['contribution'] for c in injury_features)
for c in sorted(injury_features, key=lambda x: abs(x['contribution']), reverse=True)[:10]:
    print(f"  {c['feature']:35s} | Value: {c['value']:10.3f} | Contribution: {c['contribution']:10.6f}")

print(f"\nTotal Injury Contribution: {total_injury_contribution:.6f}")

print("\nTop Form/Momentum Features:")
total_form_contribution = sum(c['contribution'] for c in form_features)
for c in sorted(form_features, key=lambda x: abs(x['contribution']), reverse=True)[:10]:
    print(f"  {c['feature']:35s} | Value: {c['value']:10.3f} | Contribution: {c['contribution']:10.6f}")

print(f"\nTotal Form/Momentum Contribution: {total_form_contribution:.6f}")

# Compare to training data statistics
print("\n" + "=" * 80)
print("FEATURE VALUES vs TRAINING DATA STATISTICS")
print("=" * 80)
print()

cursor.execute("""
    SELECT 
        AVG(win_pct_diff_10) as avg_win_pct_diff_10,
        STDDEV(win_pct_diff_10) as std_win_pct_diff_10,
        AVG(injury_impact_diff) as avg_injury_impact_diff,
        STDDEV(injury_impact_diff) as std_injury_impact_diff,
        AVG(away_injury_impact_ratio) as avg_away_injury_ratio,
        STDDEV(away_injury_impact_ratio) as std_away_injury_ratio,
        AVG(away_rolling_10_win_pct) as avg_away_win_pct,
        STDDEV(away_rolling_10_win_pct) as std_away_win_pct
    FROM marts.mart_game_features
    WHERE game_date >= '2024-10-01'
""")
stats = cursor.fetchone()

game_win_pct_diff = float(X['win_pct_diff_10'].iloc[0])
game_injury_diff = float(X['injury_impact_diff'].iloc[0])
game_away_injury_ratio = float(X['away_injury_impact_ratio'].iloc[0])
game_away_win_pct = float(X['away_rolling_10_win_pct'].iloc[0])

# Convert stats to float
avg_win_pct_diff = float(stats['avg_win_pct_diff_10'] or 0)
std_win_pct_diff = float(stats['std_win_pct_diff_10'] or 1) if stats['std_win_pct_diff_10'] else 1.0
avg_injury_diff = float(stats['avg_injury_impact_diff'] or 0)
std_injury_diff = float(stats['std_injury_impact_diff'] or 1) if stats['std_injury_impact_diff'] else 1.0
avg_away_ratio = float(stats['avg_away_injury_ratio'] or 0)
std_away_ratio = float(stats['std_away_injury_ratio'] or 1) if stats['std_away_injury_ratio'] else 1.0
avg_away_win_pct = float(stats['avg_away_win_pct'] or 0)
std_away_win_pct = float(stats['std_away_win_pct'] or 1) if stats['std_away_win_pct'] else 1.0

print("win_pct_diff_10:")
print(f"  Game value: {game_win_pct_diff:.3f}")
print(f"  Training avg: {avg_win_pct_diff:.3f}, std: {std_win_pct_diff:.3f}")
if std_win_pct_diff > 0:
    z_score = (game_win_pct_diff - avg_win_pct_diff) / std_win_pct_diff
    print(f"  Z-score: {z_score:.2f} ({'EXTREME' if abs(z_score) > 2 else 'NORMAL'})")

print("\ninjury_impact_diff:")
print(f"  Game value: {game_injury_diff:.2f}")
print(f"  Training avg: {avg_injury_diff:.2f}, std: {std_injury_diff:.2f}")
if std_injury_diff > 0:
    z_score = (game_injury_diff - avg_injury_diff) / std_injury_diff
    print(f"  Z-score: {z_score:.2f} ({'EXTREME' if abs(z_score) > 2 else 'NORMAL'})")

print("\naway_injury_impact_ratio:")
print(f"  Game value: {game_away_injury_ratio:.2f}")
print(f"  Training avg: {avg_away_ratio:.2f}, std: {std_away_ratio:.2f}")
if std_away_ratio > 0:
    z_score = (game_away_injury_ratio - avg_away_ratio) / std_away_ratio
    print(f"  Z-score: {z_score:.2f} ({'EXTREME' if abs(z_score) > 2 else 'NORMAL'})")

print("\naway_rolling_10_win_pct:")
print(f"  Game value: {game_away_win_pct:.3f}")
print(f"  Training avg: {avg_away_win_pct:.3f}, std: {std_away_win_pct:.3f}")
if std_away_win_pct > 0:
    z_score = (game_away_win_pct - avg_away_win_pct) / std_away_win_pct
    print(f"  Z-score: {z_score:.2f} ({'EXTREME' if abs(z_score) > 2 else 'NORMAL'})")

cursor.close()
conn.close()
