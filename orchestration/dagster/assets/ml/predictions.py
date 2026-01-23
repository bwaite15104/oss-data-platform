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

# Import training and transformation assets for dependency references
from .training import train_game_winner_model
from orchestration.dagster.assets.transformation import game_features

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
    deps=[train_game_winner_model, game_features],  # Depend on trained model and updated game features
    automation_condition=AutomationCondition.eager(),  # Run when model updates OR when new games available
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
        is_backtesting = config.prediction_date_cutoff is not None
        if is_backtesting:
            date_cutoff = config.prediction_date_cutoff
            context.log.info(f"Using backtesting mode: prediction_date_cutoff={date_cutoff}")
        else:
            date_cutoff = "CURRENT_DATE"
            context.log.info("Using production mode: predicting games >= CURRENT_DATE")
        
        # Load features for games to predict
        context.log.info("Loading features for games to predict...")
        
        # Build query - for backtesting, predict all games on/after the date
        # For production, only predict games without scores (future games)
        if is_backtesting:
            # Backtesting: predict games on the specified date (regardless of score status)
            date_filter = f"gf.game_date::date = '{date_cutoff}'::date"
            context.log.info(f"Backtesting: predicting games on {date_cutoff}")
        else:
            # Production: predict future games without scores
            date_filter = "gf.game_date >= CURRENT_DATE AND (gf.home_score IS NULL OR gf.away_score IS NULL)"
            context.log.info("Production: predicting future games without scores")
        
        query = f"""
            SELECT 
                gf.game_id,
                gf.game_date,
                gf.home_team_id,
                gf.away_team_id,
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
                -- NEW: Momentum/Streak features (Phase 1)
                COALESCE(mf.home_win_streak, 0) AS home_win_streak,
                COALESCE(mf.home_loss_streak, 0) AS home_loss_streak,
                COALESCE(mf.away_win_streak, 0) AS away_win_streak,
                COALESCE(mf.away_loss_streak, 0) AS away_loss_streak,
                COALESCE(mf.home_momentum_score, 0) AS home_momentum_score,
                COALESCE(mf.away_momentum_score, 0) AS away_momentum_score,
                -- NEW: Rest days features (Phase 1)
                COALESCE(mf.home_rest_days, 0) AS home_rest_days,
                COALESCE(mf.home_back_to_back, FALSE) AS home_back_to_back,
                COALESCE(mf.away_rest_days, 0) AS away_rest_days,
                COALESCE(mf.away_back_to_back, FALSE) AS away_back_to_back,
                COALESCE(mf.rest_advantage, 0) AS rest_advantage,
                -- NEW: Form divergence features (Phase 1)
                COALESCE(mf.home_form_divergence, 0) AS home_form_divergence,
                COALESCE(mf.away_form_divergence, 0) AS away_form_divergence,
                -- NEW: Head-to-head features (Phase 2)
                COALESCE(mf.home_h2h_win_pct, 0.5) AS home_h2h_win_pct,
                COALESCE(mf.home_h2h_recent_wins, 0) AS home_h2h_recent_wins,
                -- NEW: Opponent quality features (Phase 2)
                COALESCE(mf.home_recent_opp_avg_win_pct, 0.5) AS home_recent_opp_avg_win_pct,
                COALESCE(mf.away_recent_opp_avg_win_pct, 0.5) AS away_recent_opp_avg_win_pct,
                COALESCE(mf.home_performance_vs_quality, 0.5) AS home_performance_vs_quality,
                COALESCE(mf.away_performance_vs_quality, 0.5) AS away_performance_vs_quality,
                -- NEW: Home/Road performance features (Phase 2)
                COALESCE(mf.home_home_win_pct, 0.5) AS home_home_win_pct,
                COALESCE(mf.away_road_win_pct, 0.5) AS away_road_win_pct,
                COALESCE(mf.home_advantage, 0) AS home_advantage
            FROM marts.mart_game_features gf
            LEFT JOIN intermediate.int_game_momentum_features mf ON mf.game_id = gf.game_id
            WHERE {date_filter}
            ORDER BY gf.game_date
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
