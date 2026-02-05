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
from orchestration.dagster.assets.transformation import mart_game_features, MART_GAME_FEATURES_TABLE

logger = logging.getLogger(__name__)

MART_FEATURES_TABLE = os.getenv("MART_FEATURES_TABLE", MART_GAME_FEATURES_TABLE)


class PredictionConfig(Config):
    """Configuration for predictions."""
    prediction_type: str = Field(default="game_winner", description="Type of prediction (game_winner, spread, over_under)")
    min_confidence: float = Field(default=0.5, description="Minimum confidence threshold for predictions")
    model_version: Optional[str] = Field(default=None, description="Model version to use (uses latest if None)")
    prediction_date_cutoff: Optional[str] = Field(default=None, description="Date cutoff for predictions (YYYY-MM-DD). None = CURRENT_DATE (production). Set to past date for backtesting.")
    prediction_date_end: Optional[str] = Field(default=None, description="For backtesting: end date inclusive (YYYY-MM-DD). When set with prediction_date_cutoff, predict all completed games in [cutoff, end].")


@asset(
    group_name="ml_pipeline",
    description="Generate predictions for upcoming NBA games using trained model",
    deps=[train_game_winner_model, mart_game_features],  # Depend on trained model and updated game features
    automation_condition=AutomationCondition.eager(),  # Run when model updates OR when new games available
)
def generate_game_predictions(context, config: PredictionConfig) -> dict:
    """
    Generate predictions for upcoming games using the latest trained model.
    
    Process:
    1. Load latest trained model from ml_dev.model_registry
    2. Load features for upcoming games from MART_FEATURES_TABLE (default marts__local.mart_game_features)
    3. Generate predictions
    4. Store predictions in ml_dev.predictions
    
    Returns prediction metadata including count and confidence stats.
    """
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        import pandas as pd
        import numpy as np
        import joblib
        import tempfile
        import shutil
        from datetime import datetime
        import json
        try:
            import mlflow
            import mlflow.xgboost
            import mlflow.artifacts
            MLFLOW_AVAILABLE = True
        except ImportError:
            MLFLOW_AVAILABLE = False
            context.log.warning("MLflow not available, using local model files")
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
        feature_cols = hyperparams.get('features', [])
        mlflow_run_id = hyperparams.get('mlflow_run_id')
        mlflow_tracking_uri = hyperparams.get('mlflow_tracking_uri', os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000"))
        
        # Try to load model from MLflow first, fall back to local file
        algo = (hyperparams.get("algorithm") or "xgboost").lower()
        model = None
        if MLFLOW_AVAILABLE and mlflow_run_id:
            try:
                mlflow.set_tracking_uri(mlflow_tracking_uri)
                context.log.info(f"Loading model {model_version} from MLflow (run_id: {mlflow_run_id})")
                if algo == "lightgbm":
                    import mlflow.lightgbm
                    model = mlflow.lightgbm.load_model(f"runs:/{mlflow_run_id}/model")
                else:
                    model = mlflow.xgboost.load_model(f"runs:/{mlflow_run_id}/model")
                context.log.info("Successfully loaded model from MLflow")
            except Exception as e:
                context.log.warning(f"Failed to load model from MLflow: {e}. Falling back to local file.")
                model = None
        
        # Fall back to local file if MLflow failed or not available
        if model is None:
            model_path = hyperparams.get('model_path')
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
                        f"  - MLflow run_id: {mlflow_run_id}\n"
                        f"  - Stored path: {hyperparams.get('model_path')}\n"
                        f"  - Local path: {local_model_path}"
                    )
            
            context.log.info(f"Loading model {model_version} from {model_path}")
            model = joblib.load(model_path)
        
        # Load calibrator if present (apply before injury heuristic)
        # Supports both isotonic regression and Platt scaling calibration methods
        calibrator = None
        calibration_method = hyperparams.get("calibration", "isotonic")  # Default to isotonic for backward compatibility
        cal_path = hyperparams.get("calibrator_path")
        if cal_path and Path(cal_path).exists():
            try:
                calibrator = joblib.load(cal_path)
                context.log.info(f"Loaded {calibration_method} calibrator from {cal_path}")
            except Exception as e:
                context.log.warning(f"Could not load calibrator from {cal_path}: {e}")
        elif MLFLOW_AVAILABLE and mlflow_run_id and calibration_method in ["isotonic", "platt_scaling"]:
            try:
                td = tempfile.mkdtemp(prefix="mlflow_cal_")
                dl = mlflow.artifacts.download_artifacts(artifact_uri=f"runs:/{mlflow_run_id}/calibrator.pkl", dst_path=td)
                p = dl if os.path.isfile(dl) else os.path.join(dl, "calibrator.pkl")
                if os.path.isfile(p):
                    calibrator = joblib.load(p)
                    context.log.info(f"Loaded {calibration_method} calibrator from MLflow run artifacts")
                shutil.rmtree(td, ignore_errors=True)
            except Exception as e:
                context.log.warning(f"Could not load calibrator from MLflow: {e}")
        
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
        
        # Build query - for backtesting, predict all games on/after the date (or in range)
        # For production, only predict games without scores (future games)
        if is_backtesting and config.prediction_date_end:
            # Backtest range: all completed games in [cutoff, end]
            date_filter = (
                f"gf.game_date::date >= '{date_cutoff}'::date "
                f"AND gf.game_date::date <= '{config.prediction_date_end}'::date "
                f"AND gf.home_score IS NOT NULL AND gf.away_score IS NOT NULL"
            )
            context.log.info(f"Backtesting range: predicting completed games from {date_cutoff} through {config.prediction_date_end}")
        elif is_backtesting:
            # Backtesting single day: predict games on the specified date (regardless of score status)
            date_filter = f"gf.game_date::date = '{date_cutoff}'::date"
            context.log.info(f"Backtesting: predicting games on {date_cutoff}")
        else:
            # Production: predict future games without scores
            date_filter = "gf.game_date >= CURRENT_DATE AND (gf.home_score IS NULL OR gf.away_score IS NULL)"
            context.log.info("Production: predicting future games without scores")
        
        # Mart already includes all momentum and other features; select same shape as training (gf.*).
        query = f"""
            SELECT gf.*
            FROM {MART_FEATURES_TABLE} gf
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
        
        # Compute Python-computed interaction features (must match training pipeline)
        # fg_pct_diff_5 × rest_advantage (iteration 122) - shooting efficiency × rest; hot shooting when rested
        if 'fg_pct_diff_5' in df.columns and 'rest_advantage' in df.columns:
            df['fg_pct_diff_5_x_rest_advantage'] = df['fg_pct_diff_5'].fillna(0) * df['rest_advantage'].fillna(0)
        
        # For backtest range, avoid duplicate rows when re-running: remove existing predictions for this model and these games
        if is_backtesting and config.prediction_date_end and len(df) > 0:
            gids = [str(row['game_id']) for _, row in df.iterrows()]
            if gids:
                cursor.execute(
                    "DELETE FROM ml_dev.predictions WHERE model_id = %s AND game_id IN %s",
                    (model_id, tuple(gids)),
                )
                context.log.info(f"Cleared {cursor.rowcount} existing backtest predictions for this model and date range")
        
        # Prepare features (match training feature columns)
        X_pred = df[feature_cols].fillna(0)
        
        # Generate predictions
        predictions = model.predict(X_pred)
        probabilities = model.predict_proba(X_pred)
        
        # Confidence calibration: reduce confidence when injury impact ratios are extreme
        # This helps flag uncertain predictions when injuries significantly impact team strength
        def calibrate_confidence(base_confidence, home_ratio, away_ratio, injury_advantage_home):
            """
            Reduce confidence when injury impact ratios are extreme.
            
            Args:
                base_confidence: Raw model confidence (0-1)
                home_ratio: home_injury_impact_ratio
                away_ratio: away_injury_impact_ratio
                injury_advantage_home: injury_advantage_home (|value| = injury diff magnitude)
            
            Returns:
                Calibrated confidence (0-1)
            """
            calibrated = base_confidence
            
            # Penalize extreme injury impact ratios (>20 = injury is 20x team's form)
            # Higher ratio = more uncertainty
            max_ratio = max(abs(home_ratio) if pd.notna(home_ratio) else 0,
                          abs(away_ratio) if pd.notna(away_ratio) else 0)
            
            if max_ratio > 20:
                # Severe penalty: reduce confidence by up to 40% for ratios > 30
                penalty = min(0.4, (max_ratio - 20) / 50)  # 0.4 penalty at ratio=45
                calibrated = calibrated * (1 - penalty)
            elif max_ratio > 10:
                # Moderate penalty: reduce confidence by up to 20% for ratios 10-20
                penalty = (max_ratio - 10) / 50  # 0.2 penalty at ratio=20
                calibrated = calibrated * (1 - penalty)
            
            # Also penalize very large absolute injury advantage (>1000)
            # Indicates one team is significantly more injured
            adv_abs = abs(injury_advantage_home) if pd.notna(injury_advantage_home) else 0
            if adv_abs > 1000:
                additional_penalty = min(0.15, (adv_abs - 1000) / 10000)
                calibrated = calibrated * (1 - additional_penalty)
            
            return max(0.1, min(1.0, calibrated))  # Clamp between 0.1 and 1.0
        
        # Store predictions
        predictions_inserted = 0
        for idx, row in df.iterrows():
            game_id = row['game_id']
            pred_value = int(predictions[idx])
            base_confidence = float(max(probabilities[idx]))  # Max probability
            if calibrator is not None:
                # Apply calibration based on method (isotonic or Platt scaling)
                calibration_method = hyperparams.get("calibration", "isotonic")
                if calibration_method == "platt_scaling":
                    # Platt scaling: transform to log-odds, apply logistic regression, get calibrated probability
                    logit_proba = np.log(base_confidence / (1 - base_confidence + 1e-10))
                    calibrated_proba = calibrator.predict_proba([[logit_proba]])[0, 1]
                    base_confidence = float(np.clip(calibrated_proba, 0.0, 1.0))
                else:
                    # Isotonic regression (default)
                    base_confidence = float(np.clip(calibrator.predict([[base_confidence]])[0], 0.0, 1.0))
            
            # Get injury impact ratios and advantage for heuristic calibration
            home_ratio = row.get('home_injury_impact_ratio', 0) if 'home_injury_impact_ratio' in row else 0
            away_ratio = row.get('away_injury_impact_ratio', 0) if 'away_injury_impact_ratio' in row else 0
            injury_adv = row.get('injury_advantage_home', 0) if 'injury_advantage_home' in row else 0
            
            home_ratio_val = float(home_ratio) if pd.notna(home_ratio) else 0.0
            away_ratio_val = float(away_ratio) if pd.notna(away_ratio) else 0.0
            injury_adv_val = float(injury_adv) if pd.notna(injury_adv) else 0.0
            
            confidence = calibrate_confidence(
                base_confidence,
                home_ratio_val,
                away_ratio_val,
                injury_adv_val
            )
            
            if abs(base_confidence - confidence) > 0.1:
                context.log.debug(
                    f"Game {game_id}: Confidence calibrated from {base_confidence:.3f} to {confidence:.3f} "
                    f"(home_ratio={home_ratio_val:.2f}, away_ratio={away_ratio_val:.2f}, injury_advantage_home={injury_adv_val:.2f})"
                )
            
            # Only insert if confidence meets threshold (use base confidence for threshold check, 
            # but store calibrated confidence to reflect uncertainty from injuries)
            if base_confidence >= config.min_confidence:
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
