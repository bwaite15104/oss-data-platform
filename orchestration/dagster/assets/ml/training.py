"""ML model training assets."""

from dagster import asset, AutomationCondition, Config
from pydantic import Field
from typing import Optional, Dict, Any
import sys
import os
from pathlib import Path
import logging

# Add project root to path
if Path("/app/ingestion").exists():
    project_root = Path("/app")
else:
    project_root = Path(__file__).parent.parent.parent.parent.parent.parent

if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

logger = logging.getLogger(__name__)


class ModelTrainingConfig(Config):
    """Configuration for model training."""
    test_size: float = Field(default=0.2, description="Test set size (0.0-1.0)")
    random_state: int = Field(default=42, description="Random seed for reproducibility")
    max_depth: int = Field(default=6, description="XGBoost max depth")
    n_estimators: int = Field(default=100, description="XGBoost number of estimators")
    model_version: Optional[str] = Field(default=None, description="Model version (auto-generated if None)")


@asset(
    group_name="ml_pipeline",
    description="Train XGBoost model for game winner prediction",
    automation_condition=AutomationCondition.on_cron("@daily"),  # Daily retrain
)
def train_game_winner_model(context, config: ModelTrainingConfig) -> dict:
    """
    Train XGBoost model to predict game winners.
    
    Process:
    1. Load training features from features_dev.game_features
    2. Split into train/test sets
    3. Train XGBoost classifier
    4. Evaluate performance
    5. Save model (pickle/joblib)
    6. Store metadata in ml_dev.model_registry
    
    Returns model metadata including version, accuracy, and metrics.
    """
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        import pandas as pd
        import joblib
        from datetime import datetime
        import json
    except ImportError as e:
        context.log.error(f"Missing dependency: {e}. Install: pip install scikit-learn xgboost pandas joblib")
        raise
    
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
        
        context.log.info("Loading training features from features_dev.game_features...")
        
        # Load features - only use completed games with actual results
        query = """
            SELECT 
                game_id,
                game_date,
                home_team_id,
                away_team_id,
                -- Rolling 5-game features
                home_rolling_5_ppg,
                away_rolling_5_ppg,
                home_rolling_5_win_pct,
                away_rolling_5_win_pct,
                home_rolling_5_opp_ppg,
                away_rolling_5_opp_ppg,
                -- Rolling 10-game features
                home_rolling_10_ppg,
                away_rolling_10_ppg,
                home_rolling_10_win_pct,
                away_rolling_10_win_pct,
                -- Feature differences
                ppg_diff_5,
                win_pct_diff_5,
                ppg_diff_10,
                win_pct_diff_10,
                -- Season-level features
                home_season_win_pct,
                away_season_win_pct,
                season_win_pct_diff,
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
                -- Target variable
                home_win
            FROM features_dev.game_features
            WHERE game_date IS NOT NULL
              AND home_score IS NOT NULL
              AND away_score IS NOT NULL
              AND home_win IS NOT NULL
              AND game_date::date < '2026-01-18'  -- Exclude 1/18 for prediction testing
            ORDER BY game_date
        """
        
        df = pd.read_sql_query(query, conn)
        
        if len(df) == 0:
            raise ValueError("No training data available in features_dev.game_features")
        
        context.log.info(f"Loaded {len(df)} training samples")
        
        # Prepare features and target (exclude non-feature columns)
        exclude_cols = ['game_id', 'game_date', 'home_team_id', 'away_team_id', 'home_win']
        feature_cols = [col for col in df.columns if col not in exclude_cols]
        
        # Filter to only columns that exist
        feature_cols = [col for col in feature_cols if col in df.columns]
        
        if len(feature_cols) == 0:
            raise ValueError("No valid feature columns found in game_features")
        
        X = df[feature_cols].fillna(0)
        y = df['home_win']
        
        # Train/test split
        from sklearn.model_selection import train_test_split
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=config.test_size, random_state=config.random_state, stratify=y
        )
        
        context.log.info(f"Training set: {len(X_train)}, Test set: {len(X_test)}")
        
        # Train XGBoost model
        try:
            import xgboost as xgb
        except ImportError:
            context.log.error("XGBoost not installed. Install: pip install xgboost")
            raise
        
        model = xgb.XGBClassifier(
            max_depth=config.max_depth,
            n_estimators=config.n_estimators,
            random_state=config.random_state,
            eval_metric='logloss',
        )
        
        context.log.info("Training XGBoost model...")
        model.fit(X_train, y_train)
        
        # Evaluate
        train_score = model.score(X_train, y_train)
        test_score = model.score(X_test, y_test)
        
        from sklearn.metrics import classification_report, confusion_matrix
        y_pred = model.predict(X_test)
        
        context.log.info(f"Training accuracy: {train_score:.4f}")
        context.log.info(f"Test accuracy: {test_score:.4f}")
        
        # Extract feature importances (basic importance from XGBoost)
        feature_importances = dict(zip(feature_cols, model.feature_importances_))
        importance_sorted = sorted(feature_importances.items(), key=lambda x: x[1], reverse=True)
        
        context.log.info(f"Top 5 features: {[f[0] for f in importance_sorted[:5]]}")
        
        # Generate model version
        model_version = config.model_version or f"v1.0.{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # Save model to disk (in Dagster storage or project directory)
        model_dir = project_root / "models"
        model_dir.mkdir(exist_ok=True)
        model_path = model_dir / f"game_winner_model_{model_version}.pkl"
        joblib.dump(model, model_path)
        
        context.log.info(f"Model saved to {model_path}")
        
        # Store metadata in ml_dev.model_registry
        cursor = conn.cursor()
        
        # Insert model metadata
        metadata_query = """
            INSERT INTO ml_dev.model_registry (
                model_name, model_version, model_type, hyperparameters,
                is_active, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (model_name, model_version) DO UPDATE SET
                hyperparameters = EXCLUDED.hyperparameters,
                created_at = EXCLUDED.created_at
            RETURNING model_id
        """
        
        hyperparams = {
            "max_depth": config.max_depth,
            "n_estimators": config.n_estimators,
            "test_size": config.test_size,
            "random_state": config.random_state,
            "train_accuracy": float(train_score),
            "test_accuracy": float(test_score),
            "model_path": str(model_path),
            "features": feature_cols,
            "training_samples": len(X_train),
            "test_samples": len(X_test),
        }
        
        cursor.execute(
            metadata_query,
            (
                "game_winner_model",
                model_version,
                "XGBoostClassifier",
                json.dumps(hyperparams),
                True,  # Mark as active
                datetime.now(),
            )
        )
        
        model_id = cursor.fetchone()[0]
        
        # Ensure feature_importances table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ml_dev.feature_importances (
                importance_id SERIAL PRIMARY KEY,
                model_id INTEGER NOT NULL,
                feature_name VARCHAR(255) NOT NULL,
                importance_score NUMERIC(10,6),
                importance_rank INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (model_id) REFERENCES ml_dev.model_registry(model_id),
                UNIQUE(model_id, feature_name)
            )
        """)
        
        # Store feature importances in dedicated table
        cursor.execute("""
            DELETE FROM ml_dev.feature_importances WHERE model_id = %s
        """, (model_id,))
        
        for rank, (feature_name, importance) in enumerate(importance_sorted, start=1):
            cursor.execute("""
                INSERT INTO ml_dev.feature_importances (
                    model_id, feature_name, importance_score, importance_rank, created_at
                ) VALUES (%s, %s, %s, %s, %s)
            """, (
                model_id,
                feature_name,
                float(importance),
                rank,
                datetime.now(),
            ))
        
        # Deactivate old models
        cursor.execute(
            "UPDATE ml_dev.model_registry SET is_active = false WHERE model_name = 'game_winner_model' AND model_id != %s",
            (model_id,)
        )
        
        conn.commit()
        cursor.close()
        conn.close()
        
        context.log.info(f"Model registered in ml_dev.model_registry with ID {model_id}")
        
        return {
            "status": "success",
            "model_id": model_id,
            "model_version": model_version,
            "model_path": str(model_path),
            "train_accuracy": float(train_score),
            "test_accuracy": float(test_score),
            "features": feature_cols,
            "training_samples": len(X_train),
            "test_samples": len(X_test),
        }
        
    except Exception as e:
        context.log.error(f"Model training failed: {e}")
        raise
