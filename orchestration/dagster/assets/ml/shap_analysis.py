"""SHAP (SHapley Additive exPlanations) analysis for model interpretability."""

from dagster import asset, AutomationCondition, Config
from pydantic import Field
from typing import Optional, Dict, Any
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

# Import training asset for dependency reference
from .training import train_game_winner_model

logger = logging.getLogger(__name__)


class SHAPAnalysisConfig(Config):
    """Configuration for SHAP analysis."""
    sample_size: int = Field(default=100, description="Number of samples to compute SHAP values for (reduces computation time)")
    explain_predictions_only: bool = Field(default=False, description="If True, only explain recent predictions. If False, explain training samples.")


@asset(
    group_name="ml_pipeline",
    description="Compute SHAP values for model interpretability and feature contribution analysis",
    deps=[train_game_winner_model],  # Depends on training
    automation_condition=AutomationCondition.on_cron("@weekly"),  # Weekly SHAP analysis (computationally expensive, less frequent)
)
def analyze_feature_importance_shap(context, config: SHAPAnalysisConfig) -> dict:
    """
    Compute SHAP (SHapley Additive exPlanations) values for model interpretability.
    
    SHAP values explain the contribution of each feature to each prediction:
    - Positive SHAP value: Feature pushes prediction towards home win
    - Negative SHAP value: Feature pushes prediction towards away win
    - Magnitude: Strength of the contribution
    
    Process:
    1. Load latest trained model
    2. Sample training data or recent predictions
    3. Compute SHAP values using TreeExplainer (fast for XGBoost)
    4. Store SHAP values in ml_dev.feature_shap_values
    
    Returns summary statistics of SHAP analysis.
    """
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        import pandas as pd
        import joblib
        import json
    except ImportError as e:
        context.log.error(f"Missing dependency: {e}")
        raise
    
    try:
        import shap
    except ImportError:
        context.log.warning("SHAP not installed. Install with: pip install shap")
        return {
            "status": "skipped",
            "message": "SHAP library not installed. Install with: pip install shap",
        }
    
    try:
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
        
        context.log.info("Starting SHAP analysis...")
        
        # Get latest active model
        cursor.execute("""
            SELECT model_id, model_version, hyperparameters, created_at
            FROM ml_dev.model_registry
            WHERE model_name = 'game_winner_model' AND is_active = true
            ORDER BY created_at DESC
            LIMIT 1
        """)
        
        model_row = cursor.fetchone()
        
        if not model_row:
            raise ValueError("No active model found. Train a model first.")
        
        model_id = model_row['model_id']
        model_version = model_row['model_version']
        hyperparams = model_row['hyperparameters']
        if isinstance(hyperparams, str):
            hyperparams = json.loads(hyperparams)
        elif hasattr(hyperparams, '__dict__'):
            hyperparams = dict(hyperparams)
        
        model_path = hyperparams.get('model_path')
        feature_cols = hyperparams.get('features', [])
        
        if not model_path or not Path(model_path).exists():
            raise FileNotFoundError(f"Model file not found: {model_path}")
        
        context.log.info(f"Loading model {model_version} from {model_path}")
        model = joblib.load(model_path)
        
        # Get data for SHAP analysis
        if config.explain_predictions_only:
            # Use recent predictions (limited data, but more relevant)
            context.log.info("Using recent predictions for SHAP analysis...")
            query = f"""
                SELECT 
                    p.prediction_id,
                    p.game_id,
                    {', '.join([f'gf.{col}' for col in feature_cols])}
                FROM ml_dev.predictions p
                JOIN features_dev.game_features gf ON p.game_id = gf.game_id
                ORDER BY p.predicted_at DESC
                LIMIT %s
            """
            df = pd.read_sql_query(query, conn, params=(config.sample_size,))
            game_ids = df['game_id'].tolist()
            prediction_ids = df['prediction_id'].tolist()
            X_shap = df[feature_cols].fillna(0)
        else:
            # Use training data (better for global explanations)
            context.log.info("Using training data for SHAP analysis...")
            query = f"""
                SELECT 
                    game_id,
                    {', '.join([f'{col}' for col in feature_cols])}
                FROM features_dev.game_features
                WHERE home_score IS NOT NULL
                  AND away_score IS NOT NULL
                  AND home_win IS NOT NULL
                ORDER BY game_date DESC
                LIMIT %s
            """
            df = pd.read_sql_query(query, conn, params=(config.sample_size,))
            game_ids = df['game_id'].tolist()
            prediction_ids = [None] * len(game_ids)
            X_shap = df[feature_cols].fillna(0)
        
        if len(X_shap) == 0:
            raise ValueError("No data available for SHAP analysis")
        
        context.log.info(f"Computing SHAP values for {len(X_shap)} samples...")
        
        # Create SHAP explainer (TreeExplainer is fast for XGBoost)
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X_shap)
        base_value = explainer.expected_value
        
        # For binary classification, shap_values might be 2D array [class_0, class_1]
        # Use the positive class (home win = 1)
        import numpy as np
        if isinstance(shap_values, list) and len(shap_values) == 2:
            shap_values = np.array(shap_values[1])  # Use positive class
        elif hasattr(shap_values, 'shape') and len(shap_values.shape) > 2:
            shap_values = shap_values[:, :, 1]  # Positive class
        elif not isinstance(shap_values, np.ndarray):
            shap_values = np.array(shap_values)
        
        # Handle base_value (can be scalar or array)
        if isinstance(base_value, (list, np.ndarray)) and len(base_value) > 1:
            base_value = float(base_value[1])  # Positive class
        else:
            base_value = float(base_value) if isinstance(base_value, (int, float, np.number)) else 0.0
        
        context.log.info(f"SHAP values computed. Shape: {shap_values.shape}, Base value: {base_value}")
        
        # Ensure feature_shap_values table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ml_dev.feature_shap_values (
                shap_id SERIAL PRIMARY KEY,
                model_id INTEGER NOT NULL,
                game_id VARCHAR(50),
                feature_name VARCHAR(255) NOT NULL,
                shap_value NUMERIC(10,6),
                base_value NUMERIC(10,6),
                prediction_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (model_id) REFERENCES ml_dev.model_registry(model_id)
            )
        """)
        
        # Store SHAP values
        cursor.execute("""
            DELETE FROM ml_dev.feature_shap_values WHERE model_id = %s
        """, (model_id,))
        
        shap_inserted = 0
        for idx in range(len(X_shap)):
            game_id = game_ids[idx] if idx < len(game_ids) else None
            prediction_id = prediction_ids[idx] if idx < len(prediction_ids) and prediction_ids[idx] else None
            
            for feat_idx, feature_name in enumerate(feature_cols):
                shap_value = float(shap_values[idx][feat_idx])
                cursor.execute("""
                    INSERT INTO ml_dev.feature_shap_values (
                        model_id, game_id, feature_name, shap_value, base_value, prediction_id, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (
                    model_id,
                    str(game_id) if game_id else None,
                    feature_name,
                    shap_value,
                    base_value,
                    prediction_id,
                    datetime.now(),
                ))
                shap_inserted += 1
        
        conn.commit()
        cursor.close()
        conn.close()
        
        # Calculate summary statistics
        import numpy as np
        mean_shap_by_feature = {}
        for feat_idx, feature_name in enumerate(feature_cols):
            mean_shap_by_feature[feature_name] = float(np.mean(np.abs(shap_values[:, feat_idx])))
        
        top_features_by_impact = sorted(mean_shap_by_feature.items(), key=lambda x: x[1], reverse=True)[:10]
        
        context.log.info(f"SHAP analysis complete. Inserted {shap_inserted} SHAP value records.")
        context.log.info(f"Top 5 features by average SHAP impact: {[f[0] for f in top_features_by_impact[:5]]}")
        
        return {
            "status": "success",
            "model_id": model_id,
            "model_version": model_version,
            "samples_analyzed": len(X_shap),
            "shap_values_inserted": shap_inserted,
            "top_features_by_impact": dict(top_features_by_impact[:10]),
            "base_value": base_value,
        }
        
    except Exception as e:
        context.log.error(f"SHAP analysis failed: {e}")
        raise
