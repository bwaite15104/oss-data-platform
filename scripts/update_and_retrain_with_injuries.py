"""
Update SQLMesh models with new injury data, retrain model, and validate.

This script:
1. Backfills SQLMesh models that use injury data (using backfill to force rematerialization)
2. Retrains the ML model with updated features
3. Runs predictions for 1/22/2026
4. Validates injury feature importance
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


def run_sqlmesh_backfill(model_name: str, start_date: str = "2010-10-01"):
    """Run SQLMesh backfill for a model using the backfill script."""
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
        if result.stdout:
            logger.error(f"STDOUT: {result.stdout}")
        return False
    
    logger.info(f"✓ Successfully backfilled {model_name}")
    if result.stdout:
        logger.info(result.stdout)
    
    return True


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
        # Check for success indicators
        if "RUN_SUCCESS" in result.stdout or "LOADED and contains no failed jobs" in result.stdout:
            logger.info("Asset materialization succeeded")
        else:
            logger.warning("Asset materialization completed but success status unclear")
    
    return True


def query_database(query: str):
    """Query database using db_query.py script."""
    script_path = project_root / "scripts" / "db_query.py"
    cmd = [sys.executable, str(script_path), query]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        logger.error(f"Database query failed: {result.stderr}")
        return None
    
    return result.stdout


def main():
    """Main function to update models and retrain."""
    logger.info("=" * 60)
    logger.info("Update SQLMesh Models with Injury Data and Retrain")
    logger.info("=" * 60)
    
    # Step 1: Verify injury data is loaded
    logger.info("\n[Step 1/6] Verifying injury data...")
    injury_check = query_database(
        "SELECT COUNT(*) as total, COUNT(DISTINCT capture_date) as unique_dates, "
        "MIN(capture_date) as earliest, MAX(capture_date) as latest "
        "FROM raw_dev.injuries WHERE capture_date >= '2010-01-01'"
    )
    logger.info(f"Injury data check:\n{injury_check}")
    
    # Step 2: Backfill injury features model (if it exists)
    logger.info("\n[Step 2/6] Backfilling injury features model...")
    injury_model_names = [
        "features_dev.team_injury_features",
        "intermediate.int_game_injury_features",
    ]
    
    injury_success = False
    for model_name in injury_model_names:
        logger.info(f"Trying model: {model_name}")
        if run_sqlmesh_backfill(model_name, start_date="2010-10-01"):
            injury_success = True
            break
        else:
            logger.warning(f"Model {model_name} not found or failed, trying next...")
    
    if not injury_success:
        logger.warning("Could not backfill injury features model - may not exist or have different name")
        logger.info("Continuing with game features model...")
    
    # Step 3: Backfill game features model (depends on injury features)
    logger.info("\n[Step 3/6] Backfilling game features model...")
    game_model_names = [
        "marts.mart_game_features",
        "features_dev.game_features",
    ]
    
    game_success = False
    for model_name in game_model_names:
        logger.info(f"Trying model: {model_name}")
        if run_sqlmesh_backfill(model_name, start_date="2010-10-01"):
            game_success = True
            break
        else:
            logger.warning(f"Model {model_name} not found or failed, trying next...")
    
    if not game_success:
        logger.error("Failed to backfill game features model")
        return 1
    
    # Step 4: Materialize Dagster assets (if using Dagster for SQLMesh)
    logger.info("\n[Step 4/6] Materializing Dagster transformation assets...")
    dagster_assets = [
        "team_injury_features",
        "game_features",
    ]
    
    for asset_name in dagster_assets:
        logger.info(f"Materializing: {asset_name}")
        materialize_dagster_asset(asset_name)
    
    # Step 5: Retrain ML model
    logger.info("\n[Step 5/6] Retraining ML model...")
    train_success = materialize_dagster_asset("train_game_winner_model")
    if not train_success:
        logger.error("Failed to retrain model")
        return 1
    
    # Step 6: Run predictions for 1/22/2026
    logger.info("\n[Step 6/6] Running predictions for 1/22/2026...")
    pred_success = materialize_dagster_asset("predict_game_winners")
    if not pred_success:
        logger.error("Failed to run predictions")
        return 1
    
    # Step 7: Check injury feature importance
    logger.info("\n[Validation] Checking injury feature importance...")
    try:
        import pandas as pd
        import joblib
        
        models_dir = project_root / "models"
        if models_dir.exists():
            model_files = list(models_dir.glob("game_winner_model_*.pkl"))
            if model_files:
                latest_model = max(model_files, key=lambda p: p.stat().st_mtime)
                logger.info(f"Loading model: {latest_model.name}")
                
                model_data = joblib.load(latest_model)
                model = model_data.get('model')
                feature_names = model_data.get('feature_names', [])
                
                if model and hasattr(model, 'feature_importances_'):
                    importances = model.feature_importances_
                    
                    feature_importance_df = pd.DataFrame({
                        'feature': feature_names,
                        'importance': importances
                    }).sort_values('importance', ascending=False)
                    
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
    
    # Step 8: Query predictions for 1/22/2026
    logger.info("\n[Validation] Querying predictions for 1/22/2026...")
    predictions_query = """
        SELECT 
            game_id,
            home_team,
            away_team,
            predicted_winner,
            confidence,
            actual_winner,
            CASE 
                WHEN predicted_winner = actual_winner THEN 'CORRECT'
                ELSE 'WRONG'
            END as result
        FROM ml_dev.game_predictions
        WHERE game_date = '2026-01-22'
        ORDER BY game_id
    """
    
    predictions = query_database(predictions_query)
    if predictions:
        logger.info(f"\nPredictions for 1/22/2026:\n{predictions}")
    
    logger.info("\n" + "=" * 60)
    logger.info("✅ Complete! Models updated, retrained, and predictions generated.")
    logger.info("=" * 60)
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
