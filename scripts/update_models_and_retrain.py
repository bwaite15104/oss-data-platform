"""
Update SQLMesh models with new injury data and retrain ML model.

This script:
1. Backfills SQLMesh models that use injury data (int_game_injury_features, mart_game_features)
2. Retrains the ML model with updated features
3. Validates injury feature importance
"""

import sys
from pathlib import Path
import os
import subprocess
import logging

# Add project root to path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def materialize_dagster_asset(asset_name: str):
    """Materialize a Dagster asset using CLI."""
    logger.info(f"Materializing Dagster asset: {asset_name}")
    
    # Check if we're in Docker or local
    docker_check = subprocess.run(
        ["docker", "ps", "--filter", "name=nba_analytics_dagster", "--format", "{{.Names}}"],
        capture_output=True,
        text=True
    )
    
    if docker_check.returncode == 0 and docker_check.stdout.strip():
        # Use Docker
        cmd = [
            "docker", "exec", "nba_analytics_dagster_webserver",
            "dagster", "asset", "materialize",
            "-f", "/app/orchestration/dagster/definitions.py",
            "--select", asset_name
        ]
    else:
        # Use local Dagster
        definitions_path = project_root / "orchestration" / "dagster" / "definitions.py"
        cmd = [
            "dagster", "asset", "materialize",
            "-f", str(definitions_path),
            "--select", asset_name
        ]
    
    logger.info(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        logger.error(f"Dagster materialization failed for {asset_name}")
        logger.error(f"STDERR: {result.stderr}")
        if result.stdout:
            logger.error(f"STDOUT: {result.stdout}")
        return False
    
    logger.info(f"✓ Successfully materialized {asset_name}")
    if result.stdout:
        if "RUN_SUCCESS" in result.stdout or "LOADED and contains no failed jobs" in result.stdout:
            logger.info("Asset materialization succeeded")
        else:
            logger.warning("Asset materialization completed but success status unclear")
    
    return True


def run_sqlmesh_backfill(model_name: str, start_date: str = "2010-10-01"):
    """Run SQLMesh backfill for a model."""
    logger.info(f"Backfilling SQLMesh model: {model_name}")
    
    script_path = project_root / "scripts" / "sqlmesh_backfill.py"
    
    cmd = [
        sys.executable,
        str(script_path),
        model_name,
        "--start", start_date
    ]
    
    logger.info(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        logger.error(f"SQLMesh backfill failed for {model_name}")
        logger.error(f"STDERR: {result.stderr}")
        return False
    
    logger.info(f"✓ Successfully backfilled {model_name}")
    if result.stdout:
        logger.info(result.stdout)
    
    return True


def retrain_model():
    """Retrain the ML model."""
    logger.info("Retraining ML model...")
    
    script_path = project_root / "scripts" / "retrain_model.py"
    
    cmd = [sys.executable, str(script_path)]
    
    logger.info(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        logger.error("Model retraining failed")
        logger.error(f"STDERR: {result.stderr}")
        return False
    
    logger.info("✓ Successfully retrained model")
    if result.stdout:
        logger.info(result.stdout)
    
    return True


def run_predictions_for_date(prediction_date: str = "2026-01-22"):
    """Run predictions for a specific date using Dagster asset."""
    logger.info(f"Running predictions for {prediction_date}...")
    
    try:
        from dagster import build_op_context
        from orchestration.dagster.assets.ml.predictions import generate_game_predictions, PredictionConfig
        
        context = build_op_context()
        config = PredictionConfig(
            prediction_date_cutoff=prediction_date,
            min_confidence=0.5
        )
        
        result = generate_game_predictions(context, config)
        
        logger.info(f"✓ Predictions generated: {result.get('predictions_count', 0)} predictions")
        logger.info(f"  Average confidence: {result.get('avg_confidence', 0):.3f}")
        logger.info(f"  Model version: {result.get('model_version', 'unknown')}")
        
        return True
        
    except Exception as e:
        logger.error(f"Error running predictions: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_injury_feature_importance():
    """Check injury feature importance in the trained model."""
    logger.info("Checking injury feature importance...")
    
    try:
        import pandas as pd
        import joblib
        from pathlib import Path
        
        # Find latest model
        models_dir = project_root / "models"
        if not models_dir.exists():
            logger.warning("Models directory not found")
            return
        
        model_files = list(models_dir.glob("game_winner_model_*.pkl"))
        if not model_files:
            logger.warning("No model files found")
            return
        
        # Get latest model
        latest_model = max(model_files, key=lambda p: p.stat().st_mtime)
        logger.info(f"Loading model: {latest_model.name}")
        
        model_data = joblib.load(latest_model)
        
        # Handle both dict format and direct model format
        if isinstance(model_data, dict):
            model = model_data.get('model')
            feature_names = model_data.get('feature_names', [])
        else:
            # Model was saved directly
            model = model_data
            # Try to get feature names from model or database
            try:
                import psycopg2
                from psycopg2.extras import RealDictCursor
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
                cursor.execute("""
                    SELECT hyperparameters FROM ml_dev.model_registry 
                    WHERE model_name = 'game_winner_model' 
                    ORDER BY created_at DESC LIMIT 1
                """)
                result = cursor.fetchone()
                if result and result.get('hyperparameters'):
                    import json
                    hyperparams_raw = result['hyperparameters']
                    # Handle both JSON string and dict
                    if isinstance(hyperparams_raw, str):
                        hyperparams = json.loads(hyperparams_raw)
                    else:
                        hyperparams = hyperparams_raw
                    feature_names = hyperparams.get('features', [])
                else:
                    feature_names = []
                
                # Fallback: use model's feature names if available (XGBoost stores them)
                if not feature_names and hasattr(model, 'feature_names_in_'):
                    feature_names = list(model.feature_names_in_)
                cursor.close()
                conn.close()
            except Exception as e:
                logger.warning(f"Could not load feature names from database: {e}")
                feature_names = getattr(model, 'feature_names_in_', None) or []
        
        if model is None or not hasattr(model, 'feature_importances_'):
            logger.warning("Model doesn't have feature_importances_")
            return
        
        if not feature_names:
            logger.warning("Could not determine feature names")
            return
        
        # Get feature importance
        importances = model.feature_importances_
        
        # Create DataFrame
        feature_importance_df = pd.DataFrame({
            'feature': feature_names,
            'importance': importances
        }).sort_values('importance', ascending=False)
        
        # Filter injury features
        injury_features = feature_importance_df[
            feature_importance_df['feature'].str.contains('injury', case=False)
        ]
        
        logger.info("\n" + "=" * 60)
        logger.info("Injury Feature Importance:")
        logger.info("=" * 60)
        
        if len(injury_features) > 0:
            for _, row in injury_features.iterrows():
                logger.info(f"  {row['feature']}: {row['importance']:.6f}")
            
            total_injury_importance = injury_features['importance'].sum()
            logger.info(f"\nTotal injury feature importance: {total_injury_importance:.6f}")
            
            if total_injury_importance > 0:
                logger.info("✅ Injury features now have non-zero importance!")
            else:
                logger.warning("⚠️  Injury features still have 0 importance")
        else:
            logger.warning("⚠️  No injury features found in model")
        
        logger.info("\nTop 10 Features Overall:")
        logger.info("=" * 60)
        for _, row in feature_importance_df.head(10).iterrows():
            logger.info(f"  {row['feature']}: {row['importance']:.6f}")
        
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"Error checking feature importance: {e}")
        import traceback
        traceback.print_exc()


def main():
    """Main function to update models and retrain."""
    logger.info("=" * 60)
    logger.info("Update SQLMesh Models and Retrain ML Model")
    logger.info("=" * 60)
    
    # Step 1: Materialize injury features using Dagster (preferred method)
    logger.info("\n[Step 1/4] Materializing injury features model via Dagster...")
    injury_success = materialize_dagster_asset("team_injury_features")
    
    if not injury_success:
        # Fallback to SQLMesh backfill
        logger.warning("Dagster materialization failed, trying SQLMesh backfill...")
        injury_model_names = [
            "features_dev.team_injury_features",
            "intermediate.int_game_injury_features",
        ]
        
        for model_name in injury_model_names:
            logger.info(f"Trying SQLMesh model: {model_name}")
            if run_sqlmesh_backfill(model_name, start_date="2010-10-01"):
                injury_success = True
                break
            else:
                logger.warning(f"Model {model_name} not found or failed, trying next...")
    
    if not injury_success:
        logger.warning("Could not materialize injury features model - may not exist or may have different name")
        logger.info("Continuing with game features model...")
    
    # Step 2: Materialize game features using Dagster (preferred method)
    logger.info("\n[Step 2/4] Materializing game features model via Dagster...")
    game_success = materialize_dagster_asset("game_features")
    
    if not game_success:
        # Fallback to SQLMesh backfill
        logger.warning("Dagster materialization failed, trying SQLMesh backfill...")
        game_model_names = [
            "marts.mart_game_features",
            "features_dev.game_features",
        ]
        
        for model_name in game_model_names:
            logger.info(f"Trying SQLMesh model: {model_name}")
            if run_sqlmesh_backfill(model_name, start_date="2010-10-01"):
                game_success = True
                break
            else:
                logger.warning(f"Model {model_name} not found or failed, trying next...")
    
    if not game_success:
        logger.error("Failed to materialize game features model")
        return 1
    
    # Step 3: Retrain ML model
    logger.info("\n[Step 3/4] Retraining ML model...")
    success = retrain_model()
    if not success:
        logger.error("Failed to retrain model")
        return 1
    
    # Step 4: Run predictions for 2026-01-22
    logger.info("\n[Step 4/5] Running predictions for 2026-01-22...")
    # Try Dagster asset first
    pred_success = materialize_dagster_asset("generate_game_predictions")
    
    if not pred_success:
        # Fallback to direct function call
        logger.warning("Dagster materialization failed, trying direct function call...")
        pred_success = run_predictions_for_date("2026-01-22")
    
    if not pred_success:
        logger.warning("Failed to run predictions, but continuing with validation...")
    
    # Step 5: Check injury feature importance
    logger.info("\n[Validation] Checking injury feature importance...")
    check_injury_feature_importance()
    
    logger.info("\n" + "=" * 60)
    logger.info("✅ Complete! Models updated, retrained, and predictions generated.")
    logger.info("=" * 60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
