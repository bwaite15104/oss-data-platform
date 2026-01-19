"""ML prediction assets for generating game predictions."""

from dagster import asset, AutomationCondition, Config
from pydantic import Field
from typing import Optional, Dict, Any, List
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

# Import training asset for dependency reference
from .training import train_game_winner_model

logger = logging.getLogger(__name__)


class PredictionConfig(Config):
    """Configuration for predictions."""
    prediction_type: str = Field(default="game_winner", description="Type of prediction (game_winner, spread, over_under)")
    min_confidence: float = Field(default=0.5, description="Minimum confidence threshold for predictions")
    model_version: Optional[str] = Field(default=None, description="Model version to use (uses latest if None)")
    prediction_date_cutoff: Optional[str] = Field(default=None, description="Date cutoff for predictions (YYYY-MM-DD). None = CURRENT_DATE (production). Set to past date for backtesting.")


@asset(
    group_name="ml_pipeline",
    description="Generate predictions for upcoming NBA games using trained model",
    deps=[train_game_winner_model],  # Depends on training asset
    automation_condition=AutomationCondition.eager(),  # Run when model updates
)
def generate_game_predictions(context, config: PredictionConfig) -> dict:
    """
    Generate predictions for upcoming games using the latest trained model.
    
    Process:
    1. Load latest trained model from ml_dev.model_registry
    2. Load features for upcoming games from features_dev.game_features
    3. Generate predictions
    4. Store predictions in ml_dev.predictions
    
    Returns prediction metadata including count and confidence stats.
    """
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        import pandas as pd
        import joblib
        from datetime import datetime
        import json
    except ImportError as e:
        context.log.error(f"Missing dependency: {e}")
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
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Get latest active model
        if config.model_version:
            cursor.execute(
                """
                SELECT model_id, model_version, hyperparameters, created_at
                FROM ml_dev.model_registry
                WHERE model_name = 'game_winner_model' AND model_version = %s AND is_active = true
                """,
                (config.model_version,)
            )
        else:
            cursor.execute(
                """
                SELECT model_id, model_version, hyperparameters, created_at
                FROM ml_dev.model_registry
                WHERE model_name = 'game_winner_model' AND is_active = true
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
        
        model_row = cursor.fetchone()
        
        if not model_row:
            raise ValueError("No active model found in ml_dev.model_registry. Train a model first.")
        
        model_id = model_row['model_id']
        model_version = model_row['model_version']
        # PostgreSQL JSONB returns dict directly in psycopg2, not a string
        hyperparams = model_row['hyperparameters']
        if isinstance(hyperparams, str):
            hyperparams = json.loads(hyperparams)
        model_path = hyperparams.get('model_path')
        feature_cols = hyperparams.get('features', [])
        
        # Handle both Docker (/app/models) and local paths
        if model_path and Path(model_path).exists():
            # Use stored path if it exists
            pass
        else:
            # Try local models directory
            local_model_path = project_root / "models" / f"game_winner_model_{model_version}.pkl"
            if local_model_path.exists():
                model_path = str(local_model_path)
                context.log.info(f"Using local model path: {model_path}")
            else:
                raise FileNotFoundError(
                    f"Model file not found. Tried:\n"
                    f"  - Stored path: {hyperparams.get('model_path')}\n"
                    f"  - Local path: {local_model_path}"
                )
        
        context.log.info(f"Loading model {model_version} from {model_path}")
        
        # Load model
        model = joblib.load(model_path)
        
        # Determine date cutoff for predictions
        if config.prediction_date_cutoff:
            date_cutoff = config.prediction_date_cutoff
            context.log.info(f"Using backtesting mode: prediction_date_cutoff={date_cutoff}")
        else:
            date_cutoff = "CURRENT_DATE"
            context.log.info("Using production mode: predicting games >= CURRENT_DATE")
        
        # Load features for upcoming games (games without final scores)
        context.log.info("Loading features for games to predict...")
        
        # Use parameterized date cutoff (either fixed date for backtesting or CURRENT_DATE for production)
        if date_cutoff == "CURRENT_DATE":
            date_filter = "game_date >= CURRENT_DATE"
        else:
            date_filter = f"game_date >= '{date_cutoff}'::date"
        
        query = f"""
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
                return_impact_diff
            FROM features_dev.game_features
            WHERE (home_score IS NULL OR away_score IS NULL)
               OR {date_filter}
            ORDER BY game_date
        """
        
        df = pd.read_sql_query(query, conn)
        
        if len(df) == 0:
            context.log.warning("No upcoming games found to predict")
            return {
                "status": "success",
                "predictions_count": 0,
                "message": "No upcoming games to predict",
            }
        
        context.log.info(f"Found {len(df)} games to predict")
        
        # Prepare features (match training feature columns)
        X_pred = df[feature_cols].fillna(0)
        
        # Generate predictions
        predictions = model.predict(X_pred)
        probabilities = model.predict_proba(X_pred)
        
        # Store predictions
        predictions_inserted = 0
        for idx, row in df.iterrows():
            game_id = row['game_id']
            pred_value = int(predictions[idx])
            confidence = float(max(probabilities[idx]))  # Max probability
            
            # Only insert if confidence meets threshold
            if confidence >= config.min_confidence:
                cursor.execute(
                    """
                    INSERT INTO ml_dev.predictions (
                        model_id, game_id, prediction_type, predicted_value, confidence, predicted_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        model_id,
                        str(game_id),
                        config.prediction_type,
                        pred_value,
                        confidence,
                        datetime.now(),
                    )
                )
                predictions_inserted += 1
        
        conn.commit()
        cursor.close()
        conn.close()
        
        avg_confidence = float(probabilities.max(axis=1).mean())
        
        context.log.info(f"Generated {predictions_inserted} predictions (avg confidence: {avg_confidence:.3f})")
        
        return {
            "status": "success",
            "model_version": model_version,
            "model_id": model_id,
            "predictions_count": predictions_inserted,
            "games_processed": len(df),
            "avg_confidence": avg_confidence,
            "prediction_type": config.prediction_type,
        }
        
    except Exception as e:
        context.log.error(f"Prediction generation failed: {e}")
        raise
