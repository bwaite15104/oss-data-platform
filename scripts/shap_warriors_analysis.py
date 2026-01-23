"""Use SHAP values to understand actual feature contributions for Warriors game."""

import psycopg2
from psycopg2.extras import RealDictCursor
import pandas as pd
import joblib
from pathlib import Path
import json
import numpy as np

try:
    import shap
    SHAP_AVAILABLE = True
except ImportError:
    SHAP_AVAILABLE = False
    print("WARNING: SHAP not available. Install with: pip install shap")
    print("Falling back to simplified analysis...")

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
model_data = joblib.load(model_path)
model = model_data.get('model') if isinstance(model_data, dict) else model_data

# Get Warriors game features (same query as training)
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
        -- Explicit injury penalty features
        gf.home_injury_penalty_severe,
        gf.away_injury_penalty_severe,
        gf.home_injury_penalty_absolute,
        gf.away_injury_penalty_absolute,
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
    WHERE gf.game_id = (
        SELECT g.game_id
        FROM staging.stg_games g
        JOIN raw_dev.teams ht ON g.home_team_id = ht.team_id
        JOIN raw_dev.teams at ON g.away_team_id = at.team_id
        WHERE g.game_date::date = '2026-01-22'
          AND (ht.team_name = 'Mavericks' OR at.team_name = 'Mavericks')
          AND (ht.team_name = 'Warriors' OR at.team_name = 'Warriors')
        LIMIT 1
    )
""")

features = cursor.fetchone()

# Convert to DataFrame
feature_dict = {k: v for k, v in features.items() if k != 'game_id'}
df = pd.DataFrame([feature_dict])

# Convert all columns to numeric
for col in df.columns:
    if df[col].dtype == 'object':
        df[col] = pd.to_numeric(df[col], errors='coerce')
    elif df[col].dtype == 'bool':
        df[col] = df[col].astype(int)

df = df.fillna(0)
X = df[feature_names].fillna(0).astype(float)

# Get prediction
prediction = model.predict(X)[0]
probabilities = model.predict_proba(X)[0]
confidence = max(probabilities)

print("=" * 80)
print("SHAP FEATURE ANALYSIS: Mavericks vs Warriors")
print("=" * 80)
print(f"\nPrediction: {'HOME' if prediction == 1 else 'AWAY'} ({confidence:.1%})")
print(f"Actual: HOME (Mavericks won)")
print()

if SHAP_AVAILABLE:
    # Calculate SHAP values
    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(X)
    
    # For binary classification, shap_values might be a list
    if isinstance(shap_values, list):
        shap_values = shap_values[1]  # Use class 1 (HOME win) values
    
    shap_df = pd.DataFrame(shap_values, columns=feature_names)
    
    print("Top 20 Features by SHAP Value (contributing to prediction):")
    print("-" * 80)
    
    # Get SHAP values for this prediction
    shap_row = shap_df.iloc[0]
    shap_sorted = shap_row.abs().sort_values(ascending=False)
    
    for i, (feat, abs_shap) in enumerate(shap_sorted.head(20).items(), 1):
        actual_shap = shap_row[feat]
        direction = "-> HOME" if actual_shap > 0 else "-> AWAY"
        value = float(X[feat].iloc[0])
        print(f"{i:2d}. {feat:35s} | Value: {value:10.3f} | SHAP: {actual_shap:10.6f} {direction}")
    
    # Analyze injury vs form
    injury_features = [f for f in feature_names if 'injury' in f.lower()]
    form_features = [f for f in feature_names if any(x in f.lower() for x in ['win_pct', 'streak', 'momentum', 'form'])]
    
    injury_shap_total = shap_row[injury_features].sum()
    form_shap_total = shap_row[form_features].sum()
    
    print("\n" + "=" * 80)
    print("INJURY vs FORM SHAP CONTRIBUTION")
    print("=" * 80)
    print(f"\nTotal Injury Features SHAP: {injury_shap_total:.6f}")
    print(f"Total Form/Momentum Features SHAP: {form_shap_total:.6f}")
    print(f"\nNet Contribution: {'HOME' if (injury_shap_total + form_shap_total) > 0 else 'AWAY'}")
    
    print("\nTop Injury Features by SHAP:")
    injury_shap_sorted = shap_row[injury_features].abs().sort_values(ascending=False)
    for feat, abs_shap in injury_shap_sorted.head(10).items():
        actual_shap = shap_row[feat]
        direction = "-> HOME" if actual_shap > 0 else "-> AWAY"
        value = float(X[feat].iloc[0])
        print(f"  {feat:35s} | Value: {value:10.3f} | SHAP: {actual_shap:10.6f} {direction}")
    
    print("\nTop Form Features by SHAP:")
    form_shap_sorted = shap_row[form_features].abs().sort_values(ascending=False)
    for feat, abs_shap in form_shap_sorted.head(10).items():
        actual_shap = shap_row[feat]
        direction = "-> HOME" if actual_shap > 0 else "-> AWAY"
        value = float(X[feat].iloc[0])
        print(f"  {feat:35s} | Value: {value:10.3f} | SHAP: {actual_shap:10.6f} {direction}")
    
else:
    # Fallback: Analyze feature values and importance
    importances = model.feature_importances_
    feature_importance_dict = dict(zip(feature_names, importances))
    
    print("Top Features by Value * Importance (simplified):")
    print("-" * 80)
    
    contributions = []
    for feat in feature_names:
        if feat in X.columns:
            value = float(X[feat].iloc[0])
            importance = feature_importance_dict[feat]
            contribution = value * importance
            contributions.append({
                'feature': feat,
                'value': value,
                'importance': importance,
                'contribution': contribution
            })
    
    contributions.sort(key=lambda x: abs(x['contribution']), reverse=True)
    
    for i, contrib in enumerate(contributions[:20], 1):
        direction = "-> HOME" if contrib['contribution'] > 0 else "-> AWAY"
        print(f"{i:2d}. {contrib['feature']:35s} | "
              f"Value: {contrib['value']:10.3f} | "
              f"Contribution: {contrib['contribution']:10.6f} {direction}")

cursor.close()
conn.close()
