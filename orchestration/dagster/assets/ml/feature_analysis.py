"""Feature correlation and similarity analysis for feature selection."""

from dagster import asset, AutomationCondition, Config
from pydantic import Field
from typing import Optional, Dict, Any, List
import sys
import os
from pathlib import Path
import logging
from datetime import datetime

# Add project root to path
if Path("/app/ingestion").exists():
    project_root = Path("/app")
else:
    project_root = Path(__file__).parent.parent.parent.parent.parent.parent

if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Import transformation asset for dependency
from orchestration.dagster.assets.transformation import mart_game_features, MART_GAME_FEATURES_TABLE

logger = logging.getLogger(__name__)

MART_FEATURES_TABLE = os.getenv("MART_FEATURES_TABLE", MART_GAME_FEATURES_TABLE)


def _is_docker() -> bool:
    """True when running inside a Docker container (e.g. Dagster worker)."""
    if os.getenv("DAGSTER_IN_DOCKER") == "1":
        return True
    try:
        return Path("/.dockerenv").exists() or (
            Path("/proc/1/cgroup").exists()
            and "docker" in Path("/proc/1/cgroup").read_text()
        )
    except Exception:
        return False


class FeatureAnalysisConfig(Config):
    """Configuration for feature analysis."""
    correlation_threshold: float = Field(default=0.95, description="Features with correlation above this threshold are flagged as highly similar")
    min_importance_threshold: float = Field(default=0.001, description="Minimum feature importance to consider (filters out near-zero importance features)")


@asset(
    group_name="ml_pipeline",
    description="Analyze feature correlations and similarities to identify redundant features for feature selection",
    deps=[mart_game_features],  # Depend on mart game features being ready
    automation_condition=AutomationCondition.on_cron("@weekly"),  # Weekly analysis (less frequent than training)
)
def analyze_feature_correlations(context, config: FeatureAnalysisConfig) -> dict:
    """
    Analyze feature correlations and similarities to help decide which features to include/exclude.
    
    This analysis helps identify:
    1. Highly correlated features (potential redundancy)
    2. Features with very low importance (candidates for removal)
    3. Feature groups that might be redundant
    
    Process:
    1. Load features from MART_FEATURES_TABLE (default marts__local.mart_game_features)
    2. Calculate correlation matrix
    3. Identify highly correlated feature pairs
    4. Compare with feature importances from latest model
    5. Store results in ml_dev.feature_correlations table
    
    Returns summary of feature analysis including redundant features and recommendations.
    """
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        import pandas as pd
        import numpy as np
        import json
    except ImportError as e:
        context.log.error(f"Missing dependency: {e}")
        raise
    
    try:
        # Database connection (use host 'postgres' in Docker so worker can reach DB)
        database = os.getenv("POSTGRES_DB", "nba_analytics")
        host = "postgres" if _is_docker() else os.getenv("POSTGRES_HOST", "localhost")
        
        conn = psycopg2.connect(
            host=host,
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            database=database,
            user=os.getenv("POSTGRES_USER", "postgres"),
            password=os.getenv("POSTGRES_PASSWORD", "postgres"),
        )
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        context.log.info("Starting feature correlation analysis...")
        
        # Load all features from mart (SQLMesh local: marts__local.mart_game_features)
        query = f"""
            SELECT 
                game_id,
                game_date,
                -- Rolling 5-game features
                home_rolling_5_ppg,
                away_rolling_5_ppg,
                home_rolling_5_win_pct,
                away_rolling_5_win_pct,
                home_rolling_5_opp_ppg,
                away_rolling_5_opp_ppg,
                home_rolling_5_apg,
                away_rolling_5_apg,
                home_rolling_5_rpg,
                away_rolling_5_rpg,
                home_rolling_5_fg_pct,
                away_rolling_5_fg_pct,
                home_rolling_5_fg3_pct,
                away_rolling_5_fg3_pct,
                -- Rolling 10-game features
                home_rolling_10_ppg,
                away_rolling_10_ppg,
                home_rolling_10_win_pct,
                away_rolling_10_win_pct,
                -- Feature differences
                ppg_diff_5,
                win_pct_diff_5,
                fg_pct_diff_5,
                ppg_diff_10,
                win_pct_diff_10,
                -- Season-level features
                home_season_win_pct,
                away_season_win_pct,
                season_win_pct_diff,
                season_point_diff_diff,
                -- Star player return features
                home_has_star_return,
                home_star_players_returning,
                home_key_players_returning,
                home_extended_returns,
                home_total_return_impact,
                home_max_days_since_return,
                away_has_star_return,
                away_star_players_returning,
                away_key_players_returning,
                away_extended_returns,
                away_total_return_impact,
                away_max_days_since_return,
                star_return_advantage,
                return_impact_diff,
                -- Target variable (for correlation with target)
                home_win
            FROM {MART_FEATURES_TABLE}
            WHERE game_date IS NOT NULL
              AND home_score IS NOT NULL
              AND away_score IS NOT NULL
              AND home_win IS NOT NULL
        """
        
        df = pd.read_sql_query(query, conn)
        
        if len(df) == 0:
            raise ValueError("No data available for feature analysis")
        
        context.log.info(f"Loaded {len(df)} samples for feature analysis")
        
        # Exclude non-feature columns
        exclude_cols = ['game_id', 'game_date', 'home_win']
        feature_cols = [col for col in df.columns if col not in exclude_cols]
        
        # Filter to only numeric columns that exist
        feature_cols = [col for col in feature_cols if col in df.columns and df[col].dtype in ['float64', 'int64', 'float32', 'int32']]
        
        X = df[feature_cols].fillna(0)
        y = df['home_win']
        
        # Calculate correlation matrix
        context.log.info("Calculating correlation matrix...")
        corr_matrix = X.corr().abs()  # Use absolute correlation
        
        # Find highly correlated feature pairs
        high_corr_pairs = []
        for i in range(len(corr_matrix.columns)):
            for j in range(i+1, len(corr_matrix.columns)):
                feat1 = corr_matrix.columns[i]
                feat2 = corr_matrix.columns[j]
                corr_value = corr_matrix.iloc[i, j]
                
                if corr_value >= config.correlation_threshold:
                    high_corr_pairs.append({
                        'feature_1': feat1,
                        'feature_2': feat2,
                        'correlation': float(corr_value)
                    })
        
        high_corr_pairs.sort(key=lambda x: x['correlation'], reverse=True)
        
        context.log.info(f"Found {len(high_corr_pairs)} highly correlated feature pairs (threshold: {config.correlation_threshold})")
        
        # Get feature importances from latest model
        cursor.execute("""
            SELECT model_id, model_version, created_at
            FROM ml_dev.model_registry
            WHERE model_name = 'game_winner_model' AND is_active = true
            ORDER BY created_at DESC
            LIMIT 1
        """)
        
        model_row = cursor.fetchone()
        model_id = None
        feature_importances = {}
        
        if model_row:
            model_id = model_row['model_id']
            cursor.execute("""
                SELECT feature_name, importance_score, importance_rank
                FROM ml_dev.feature_importances
                WHERE model_id = %s
                ORDER BY importance_rank
            """, (model_id,))
            
            for row in cursor.fetchall():
                feature_importances[row['feature_name']] = {
                    'importance': float(row['importance_score']),
                    'rank': int(row['importance_rank'])
                }
        
        # Calculate correlation with target variable
        target_correlations = {}
        for feat in feature_cols:
            if feat in X.columns:
                corr_with_target = abs(X[feat].corr(y))
                target_correlations[feat] = float(corr_with_target)
        
        target_correlations_sorted = sorted(target_correlations.items(), key=lambda x: x[1], reverse=True)
        
        # Identify low-importance features
        low_importance_features = []
        if feature_importances:
            for feat, imp_data in feature_importances.items():
                if imp_data['importance'] < config.min_importance_threshold:
                    low_importance_features.append({
                        'feature': feat,
                        'importance': imp_data['importance'],
                        'rank': imp_data['rank']
                    })
        
        low_importance_features.sort(key=lambda x: x['importance'])
        
        # Create recommendations
        recommendations = []
        
        # Recommend removing one feature from highly correlated pairs
        for pair in high_corr_pairs[:10]:  # Top 10 most correlated pairs
            feat1_imp = feature_importances.get(pair['feature_1'], {}).get('importance', 0)
            feat2_imp = feature_importances.get(pair['feature_2'], {}).get('importance', 0)
            
            # Recommend keeping the feature with higher importance
            if feat1_imp > feat2_imp:
                keep_feat = pair['feature_1']
                remove_feat = pair['feature_2']
            else:
                keep_feat = pair['feature_2']
                remove_feat = pair['feature_1']
            
            recommendations.append({
                'type': 'high_correlation',
                'action': 'consider_removing',
                'feature': remove_feat,
                'reason': f"Highly correlated ({pair['correlation']:.3f}) with {keep_feat}",
                'correlation': pair['correlation'],
                'alternative': keep_feat
            })
        
        # Recommend removing low-importance features
        for feat_data in low_importance_features[:10]:  # Top 10 lowest importance
            recommendations.append({
                'type': 'low_importance',
                'action': 'consider_removing',
                'feature': feat_data['feature'],
                'reason': f"Very low importance ({feat_data['importance']:.6f})",
                'importance': feat_data['importance'],
                'rank': feat_data['rank']
            })
        
        # Store results in database
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ml_dev.feature_correlations (
                correlation_id SERIAL PRIMARY KEY,
                model_id INTEGER,
                feature_1 VARCHAR(255) NOT NULL,
                feature_2 VARCHAR(255) NOT NULL,
                correlation_value NUMERIC(10,6),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (model_id) REFERENCES ml_dev.model_registry(model_id) ON DELETE SET NULL
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ml_dev.feature_analysis_recommendations (
                recommendation_id SERIAL PRIMARY KEY,
                model_id INTEGER,
                recommendation_type VARCHAR(50),
                action VARCHAR(50),
                feature_name VARCHAR(255),
                reason TEXT,
                metadata JSONB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (model_id) REFERENCES ml_dev.model_registry(model_id) ON DELETE SET NULL
            )
        """)
        
        # Store correlations
        if model_id:
            cursor.execute("""
                DELETE FROM ml_dev.feature_correlations WHERE model_id = %s
            """, (model_id,))
            
            for pair in high_corr_pairs:
                cursor.execute("""
                    INSERT INTO ml_dev.feature_correlations (
                        model_id, feature_1, feature_2, correlation_value, created_at
                    ) VALUES (%s, %s, %s, %s, %s)
                """, (
                    model_id,
                    pair['feature_1'],
                    pair['feature_2'],
                    pair['correlation'],
                    datetime.now()
                ))
        
        # Store recommendations
        if model_id:
            cursor.execute("""
                DELETE FROM ml_dev.feature_analysis_recommendations WHERE model_id = %s
            """, (model_id,))
            
            for rec in recommendations:
                metadata = {k: v for k, v in rec.items() if k not in ['type', 'action', 'feature', 'reason']}
                cursor.execute("""
                    INSERT INTO ml_dev.feature_analysis_recommendations (
                        model_id, recommendation_type, action, feature_name, reason, metadata, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (
                    model_id,
                    rec['type'],
                    rec['action'],
                    rec['feature'],
                    rec['reason'],
                    json.dumps(metadata),
                    datetime.now()
                ))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        context.log.info(f"Feature analysis complete. Found {len(high_corr_pairs)} highly correlated pairs and {len(low_importance_features)} low-importance features.")
        context.log.info(f"Generated {len(recommendations)} recommendations for feature selection.")
        
        return {
            "status": "success",
            "model_id": model_id,
            "total_features": len(feature_cols),
            "high_correlation_pairs": len(high_corr_pairs),
            "low_importance_features": len(low_importance_features),
            "top_correlated_pairs": high_corr_pairs[:5],
            "top_target_correlations": dict(target_correlations_sorted[:10]),
            "recommendations_count": len(recommendations),
            "sample_recommendations": recommendations[:5]
        }
        
    except Exception as e:
        context.log.error(f"Feature analysis failed: {e}")
        raise
