"""
Analyze feature correlations to identify overfitting and redundant features.

This script:
1. Loads feature importance data from ml_dev.feature_importances
2. Calculates correlations between features using actual feature values
3. Identifies highly correlated features (potential redundancy)
4. Generates a correlation heatmap
"""

import sys
from pathlib import Path
import os

# Add project root to path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import psycopg2
import pandas as pd
import numpy as np
from sqlalchemy import create_engine
import json

def get_db_connection():
    """Get database connection."""
    database = os.getenv("POSTGRES_DB", "nba_analytics")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    
    return psycopg2.connect(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
    )

def get_feature_importances():
    """Get feature importances from the latest model."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    query = """
        SELECT 
            fi.feature_name,
            fi.importance_score,
            fi.importance_rank
        FROM ml_dev.feature_importances fi
        JOIN ml_dev.model_registry mr ON fi.model_id = mr.model_id
        WHERE mr.is_active = true
        ORDER BY fi.importance_rank
    """
    
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    return df

def get_feature_values_sample(n_samples=10000):
    """Get sample of feature values from game_features for correlation analysis."""
    database = os.getenv("POSTGRES_DB", "nba_analytics")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    
    engine = create_engine(f"postgresql://{user}:{password}@{host}:{port}/{database}")
    
    # Get list of feature columns from training query
    feature_cols_query = """
        SELECT 
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
            gf.home_has_star_return,
            gf.home_star_players_returning,
            gf.home_key_players_returning,
            gf.home_extended_returns,
            gf.home_total_return_impact,
            gf.home_max_days_since_return,
            gf.away_has_star_return,
            gf.away_star_players_returning,
            gf.away_key_players_returning,
            gf.away_extended_returns,
            gf.away_total_return_impact,
            gf.away_max_days_since_return,
            gf.star_return_advantage,
            gf.return_impact_diff,
            -- Momentum/Streak features
            COALESCE(mf.home_win_streak, 0) AS home_win_streak,
            COALESCE(mf.home_loss_streak, 0) AS home_loss_streak,
            COALESCE(mf.away_win_streak, 0) AS away_win_streak,
            COALESCE(mf.away_loss_streak, 0) AS away_loss_streak,
            COALESCE(mf.home_momentum_score, 0) AS home_momentum_score,
            COALESCE(mf.away_momentum_score, 0) AS away_momentum_score,
            -- Rest days features
            COALESCE(mf.home_rest_days, 0) AS home_rest_days,
            COALESCE(mf.home_back_to_back, FALSE)::int AS home_back_to_back,
            COALESCE(mf.away_rest_days, 0) AS away_rest_days,
            COALESCE(mf.away_back_to_back, FALSE)::int AS away_back_to_back,
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
        FROM features_dev.game_features gf
        LEFT JOIN intermediate.int_game_momentum_features mf ON mf.game_id = gf.game_id
        WHERE gf.game_date IS NOT NULL
          AND gf.home_score IS NOT NULL
          AND gf.away_score IS NOT NULL
          AND gf.home_win IS NOT NULL
          AND gf.game_date::date >= '2010-10-01'::date
          AND gf.game_date::date < '2026-01-22'::date
        LIMIT %s
    """
    
    df = pd.read_sql_query(feature_cols_query, engine, params=(n_samples,))
    return df

def analyze_correlations():
    """Analyze feature correlations."""
    print("Loading feature importances...")
    importance_df = get_feature_importances()
    
    print(f"Found {len(importance_df)} features")
    print("\nTop 10 Features by Importance:")
    print(importance_df.head(10).to_string(index=False))
    
    print("\nLoading feature values for correlation analysis...")
    try:
        feature_df = get_feature_values_sample(n_samples=10000)
        print(f"Loaded {len(feature_df)} samples")
        
        # Calculate correlation matrix
        print("\nCalculating correlation matrix...")
        corr_matrix = feature_df.corr()
        
        # Find highly correlated pairs (|correlation| > 0.8)
        high_corr_pairs = []
        for i in range(len(corr_matrix.columns)):
            for j in range(i+1, len(corr_matrix.columns)):
                corr_val = corr_matrix.iloc[i, j]
                if abs(corr_val) > 0.8:
                    high_corr_pairs.append({
                        'feature1': corr_matrix.columns[i],
                        'feature2': corr_matrix.columns[j],
                        'correlation': corr_val
                    })
        
        print(f"\nFound {len(high_corr_pairs)} highly correlated pairs (|r| > 0.8):")
        if high_corr_pairs:
            high_corr_df = pd.DataFrame(high_corr_pairs)
            high_corr_df = high_corr_df.sort_values('correlation', key=abs, ascending=False)
            print(high_corr_df.to_string(index=False))
        else:
            print("No highly correlated pairs found (good - less redundancy)")
        
        # Save correlation matrix to CSV
        output_file = project_root / "analysis" / "feature_correlations.csv"
        output_file.parent.mkdir(exist_ok=True)
        corr_matrix.to_csv(output_file)
        print(f"\nCorrelation matrix saved to: {output_file}")
        
        # Generate summary statistics
        print("\nCorrelation Statistics:")
        print(f"  Mean absolute correlation: {corr_matrix.abs().values[np.triu_indices_from(corr_matrix.values, k=1)].mean():.3f}")
        print(f"  Max correlation: {corr_matrix.abs().values[np.triu_indices_from(corr_matrix.values, k=1)].max():.3f}")
        print(f"  Min correlation: {corr_matrix.abs().values[np.triu_indices_from(corr_matrix.values, k=1)].min():.3f}")
        
    except Exception as e:
        print(f"Error loading feature values: {e}")
        print("Skipping correlation analysis")

if __name__ == "__main__":
    analyze_correlations()
