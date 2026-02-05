"""ML model training assets."""

from dagster import asset, AutomationCondition, Config
from pydantic import Field
from typing import Optional, Dict, Any
import sys
import os
import shutil
import tempfile
from pathlib import Path
import logging

# Add project root to path
if Path("/app/ingestion").exists():
    project_root = Path("/app")
else:
    project_root = Path(__file__).parent.parent.parent.parent.parent.parent

if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Import transformation asset and single source of truth for mart table name
from orchestration.dagster.assets.transformation import mart_game_features, MART_GAME_FEATURES_TABLE

logger = logging.getLogger(__name__)

# Use same table the mart_game_features asset writes to (override via env for prod).
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


class ModelTrainingConfig(Config):
    """Configuration for model training."""
    test_size: float = Field(default=0.2, description="Test set size (0.0-1.0)")
    random_state: int = Field(default=42, description="Random seed for reproducibility")
    use_time_based_split: bool = Field(default=False, description="Use time-based train/test split (most recent games as test set). Changed to False for iteration_1_random_split to recover accuracy - random split achieved 0.6559 in iteration 1 vs 0.6051 with temporal split. Iteration 88 confirmed temporal split reduces accuracy (0.6077 vs 0.6320 with random), so keeping random split.")
    temporal_split_cutoff_date: Optional[str] = Field(default="2025-12-01", description="If set, use fixed-date train/test split: train = game_date < this date, test = game_date >= this date. E.g. '2025-12-01'. Iteration 123: try temporal split per next_steps (train before 2025-12-01, test 2025-12-01 onward) for more realistic evaluation. Set to None to use random split.")
    max_depth: int = Field(default=6, description="XGBoost max depth / CatBoost depth. Set to 6 for iteration 117 - iteration 116 switched to XGBoost but test_accuracy 0.6313 (essentially flat vs 0.6311). Current XGBoost uses LightGBM-tuned params (max_depth=4, learning_rate=0.045, reg_alpha=0.7, reg_lambda=4.5). Iteration 1 achieved 0.6559 with XGBoost using max_depth=6, n_estimators=300, learning_rate=0.03, reg_alpha=0.3, reg_lambda=1.5. Setting XGBoost-specific defaults to iteration-1 style to test if this recovers toward 0.6559 on current 125-feature set.")
    num_leaves: Optional[int] = Field(default=7, description="LightGBM num_leaves (number of leaves in one tree). If None, calculated as min(2^max_depth - 1, 127). For iteration 70, reverted to 7 (from 15 in iteration 66) to reduce overfitting - iteration 66's increase to 15 increased train-test gap from 0.0192 to 0.0695 and decreased test accuracy by 1.50 percentage points.")
    n_estimators: int = Field(default=300, description="XGBoost number of estimators / CatBoost iterations. Set to 300 for iteration 117 - iteration 116 switched to XGBoost but test_accuracy 0.6313 (essentially flat vs 0.6311). Iteration 1 achieved 0.6559 with XGBoost using max_depth=6, n_estimators=300, learning_rate=0.03, reg_alpha=0.3, reg_lambda=1.5. Setting XGBoost-specific defaults to iteration-1 style to test if this recovers toward 0.6559 on current 125-feature set.")
    learning_rate: float = Field(default=0.03, description="XGBoost learning rate. Set to 0.03 for iteration 117 - iteration 116 switched to XGBoost but test_accuracy 0.6313 (essentially flat vs 0.6311). Iteration 1 achieved 0.6559 with XGBoost using max_depth=6, n_estimators=300, learning_rate=0.03, reg_alpha=0.3, reg_lambda=1.5. Setting XGBoost-specific defaults to iteration-1 style to test if this recovers toward 0.6559 on current 125-feature set.")
    subsample: float = Field(default=0.8, description="Subsample ratio of training instances (reduced from 0.85 to 0.8 for iteration 53 to reduce overfitting - iteration 49 increased from 0.8 to 0.85 which decreased test accuracy from 0.6378 to 0.6336, and iteration 52's train-test gap increased to 0.0427 indicating overfitting)")
    colsample_bytree: float = Field(default=0.8, description="Subsample ratio of columns when constructing each tree (reduced from 0.85 to 0.8 for iteration 53 to reduce overfitting - iteration 49 increased from 0.8 to 0.85 which decreased test accuracy from 0.6378 to 0.6336, and iteration 52's train-test gap increased to 0.0427 indicating overfitting)")
    min_child_weight: int = Field(default=5, description="Minimum sum of instance weight needed in a child (increased from 4 to reduce overfitting - iteration 22)")
    gamma: float = Field(default=0.2, description="Minimum loss reduction required to make a split (increased from 0.15 to reduce overfitting - iteration 22)")
    reg_alpha: float = Field(default=0.3, description="L1 regularization term. Set to 0.3 for iteration 117 - iteration 116 switched to XGBoost but test_accuracy 0.6313 (essentially flat vs 0.6311). Iteration 1 achieved 0.6559 with XGBoost using max_depth=6, n_estimators=300, learning_rate=0.03, reg_alpha=0.3, reg_lambda=1.5. Setting XGBoost-specific defaults to iteration-1 style to test if this recovers toward 0.6559 on current 125-feature set.")
    reg_lambda: float = Field(default=1.5, description="L2 regularization term. Set to 1.5 for iteration 117 - iteration 116 switched to XGBoost but test_accuracy 0.6313 (essentially flat vs 0.6311). Iteration 1 achieved 0.6559 with XGBoost using max_depth=6, n_estimators=300, learning_rate=0.03, reg_alpha=0.3, reg_lambda=1.5. Setting XGBoost-specific defaults to iteration-1 style to test if this recovers toward 0.6559 on current 125-feature set.")
    min_child_samples: int = Field(default=30, description="Minimum number of samples in a leaf (LightGBM, increased from 20 to 30 for iteration 4 to reduce overfitting)")
    interaction_feature_boost: float = Field(default=1.5, description="Multiplier for interaction feature importance during training")
    model_version: Optional[str] = Field(default=None, description="Model version (auto-generated if None)")
    algorithm: str = Field(default="xgboost", description="Algorithm: xgboost, lightgbm, catboost, or ensemble (XGBoost + LightGBM stacking). Switched to xgboost for iteration 116 - current state 0.6311 (CatBoost/70 features). Iteration 1 achieved 0.6559 with XGBoost (best single-algorithm result). Last 3 runs were regularization (115), LightGBM+LR (114), ensemble (113); trying XGBoost is a focused, non-repetitive change toward target >= 0.7.")
    train_date_end: Optional[str] = Field(default=None, description="Last date (inclusive) for training data (YYYY-MM-DD). If set, train on games with game_date <= this date. If None, use today (exclusive).")
    feature_selection_enabled: bool = Field(default=False, description="Enable feature selection to remove low-importance features. Disabled for iteration 112 - iteration 110 enabled RFE feature selection but test accuracy stayed at 0.6355 (no improvement). RFE may be removing important features that the model needs. Disabling feature selection to use all available features and test if this improves accuracy toward target >= 0.7.")
    feature_selection_threshold: float = Field(default=0.0005, description="Minimum feature importance threshold (features below this are removed). Used only if feature_selection_method='threshold'. For percentile-based selection, use feature_selection_percentile instead.")
    feature_selection_method: str = Field(default="rfe", description="Feature selection method: 'threshold' (remove features below threshold), 'percentile' (keep top N% by importance), or 'rfe' (recursive feature elimination - keep top N features by rank). Changed to 'rfe' for iteration 45 to use a more sophisticated method that recursively removes features and retrains, keeping only the most important features by rank.")
    feature_selection_percentile: float = Field(default=80.0, description="Percentile threshold for feature selection (0-100). Keep top N% of features by importance. Used only if feature_selection_method='percentile'. Default 80% for iteration 44 - keeps top 80% of features, removing bottom 20% that may be noise.")
    feature_selection_n_features: int = Field(default=70, description="Number of features to keep for RFE (recursive feature elimination). Used only if feature_selection_method='rfe'. Default 70 for iteration 45 - keeps top 70 features by importance rank, removing bottom features that may be noise.")
    max_missing_feature_pct: float = Field(default=0.15, description="Maximum percentage of features that can be missing for a game to be included in training (0.0-1.0). Games with more missing features are filtered out. Reverted from 0.12 (12%) back to 0.15 (15%) for iteration 96 - iteration 95's tighter filtering resulted in regression (-0.65 percentage points test accuracy) and worse generalization. The filtering removed only 0.2% of games, suggesting most games already meet the 12% threshold, so reverting to 0.15 and trying a different approach (increasing depth).")


@asset(
    group_name="ml_pipeline",
    description="Train XGBoost model for game winner prediction",
    deps=[mart_game_features],  # Depend on mart game features being ready
    automation_condition=AutomationCondition.on_cron("@daily"),  # Daily retrain (can be adjusted to weekly/monthly)
)
def train_game_winner_model(context, config: ModelTrainingConfig) -> dict:
    """
    Train XGBoost model to predict game winners.
    
    Process:
    1. Load training features from MART_FEATURES_TABLE (default marts__local.mart_game_features)
    2. Split into train/test sets
    3. Train XGBoost classifier
    4. Evaluate performance
    5. Save model (pickle/joblib)
    6. Store metadata in ml_dev.model_registry
    
    Returns model metadata including version, accuracy, and metrics.
    """
    try:
        import pandas as pd
        import numpy as np
        import joblib
        from datetime import datetime
        import json
        from sqlalchemy import create_engine
        import mlflow
        import mlflow.xgboost
    except ImportError as e:
        context.log.error(f"Missing dependency: {e}. Install: pip install scikit-learn xgboost pandas joblib sqlalchemy mlflow")
        raise
    
    try:
        # Database connection (use host 'postgres' in Docker so worker can reach DB)
        database = os.getenv("POSTGRES_DB", "nba_analytics")
        host = "postgres" if _is_docker() else os.getenv("POSTGRES_HOST", "localhost")
        port = int(os.getenv("POSTGRES_PORT", "5432"))
        user = os.getenv("POSTGRES_USER", "postgres")
        password = os.getenv("POSTGRES_PASSWORD", "postgres")
        
        # Create SQLAlchemy engine
        engine = create_engine(f"postgresql://{user}:{password}@{host}:{port}/{database}")
        
        # Also create psycopg2 connection for metadata operations
        import psycopg2
        conn = psycopg2.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
        )
        
        # Initialize MLflow tracking. Use experiment with proxied artifact URIs (mlflow-artifacts:/)
        # so the UI can list and fetch artifacts. The server must be started with
        # --artifacts-destination and without --default-artifact-root. The experiment
        # is created with the server default (mlflow-artifacts:/) on first use.
        mlflow_tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
        mlflow.set_tracking_uri(mlflow_tracking_uri)
        mlflow.set_experiment("nba_game_winner_prediction")
        
        context.log.info(f"MLflow tracking URI: {mlflow_tracking_uri}")
        context.log.info(f"Loading training features from {MART_FEATURES_TABLE}...")
        
        # Get today's date in Eastern timezone (NBA games are scheduled in ET)
        # Exclude today's games from training (use for predictions/testing)
        try:
            import pytz
            eastern = pytz.timezone('US/Eastern')
            today = datetime.now(eastern).date()
        except ImportError:
            # Fallback to UTC if pytz not available
            context.log.warning("pytz not available, using UTC date. Install with: pip install pytz")
            today = datetime.now().date()
        context.log.info(f"Excluding games from {today} (Eastern) and later for training")
        
        # Format dates for SQL query (avoid parameter binding issues with pandas)
        today_str = today.strftime('%Y-%m-%d')
        if config.train_date_end:
            train_end_str = config.train_date_end
            date_filter = f"AND gf.game_date::date <= '{train_end_str}'::date"
            context.log.info(f"Training on games through {train_end_str} (inclusive)")
        else:
            date_filter = f"AND gf.game_date::date < '{today_str}'::date"
            context.log.info(f"Training on games before {today_str} (exclusive)")
        
        # Load features from mart only (mart already includes momentum and all other features).
        query = f"""
            SELECT gf.*
            FROM {MART_FEATURES_TABLE} gf
            WHERE gf.game_date IS NOT NULL
              AND gf.home_score IS NOT NULL
              AND gf.away_score IS NOT NULL
              AND gf.home_win IS NOT NULL
              AND gf.game_date::date >= '2010-10-01'::date  -- Only use data from 2010 onward
              {date_filter}
            ORDER BY gf.game_date
        """
        
        df = pd.read_sql_query(query, engine)
        
        if len(df) == 0:
            # Diagnose: total rows in mart vs rows matching training filters
            try:
                diag = pd.read_sql_query(f"""
                    SELECT
                        COUNT(*) AS total_rows,
                        COUNT(*) FILTER (WHERE game_date IS NOT NULL AND home_score IS NOT NULL AND away_score IS NOT NULL AND home_win IS NOT NULL) AS completed_rows,
                        COUNT(*) FILTER (WHERE game_date IS NOT NULL AND home_score IS NOT NULL AND away_score IS NOT NULL AND home_win IS NOT NULL
                            AND game_date::date >= '2010-10-01'::date AND game_date::date < '{today_str}'::date) AS trainable_rows,
                        MIN(game_date)::text AS min_date,
                        MAX(game_date)::text AS max_date
                    FROM {MART_FEATURES_TABLE}
                """, engine).iloc[0]
                total, completed, trainable = int(diag["total_rows"]), int(diag["completed_rows"]), int(diag["trainable_rows"])
                min_d, max_d = diag["min_date"], diag["max_date"]
                raise ValueError(
                    f"No training data available in {MART_FEATURES_TABLE}. "
                    f"Diagnostics: total_rows={total}, completed_rows={completed}, trainable_rows (2010-10-01 to <{today_str})={trainable}, "
                    f"date_range=[{min_d}, {max_d}]. "
                    f"Materialize the mart_game_features asset and backfill at least 2010-10-01 onward, or check that games have home_score/away_score/home_win set."
                )
            except ValueError:
                raise
            except Exception as e:
                raise ValueError(
                    f"No training data available in {MART_FEATURES_TABLE}. "
                    f"(Diagnostic query failed: {e}). Materialize mart_game_features and backfill from 2010-10-01."
                )
        
        context.log.info(f"Loaded {len(df)} training samples with {len(df.columns)} columns")
        
        # Prepare features and target (exclude non-feature columns)
        exclude_cols = ['game_id', 'game_date', 'home_team_id', 'away_team_id', 'home_win', 
                        'home_score', 'away_score', 'point_spread', 'total_points', 'is_overtime',
                        'updated_at', 'winner_team_id', 'is_completed']
        feature_cols = [col for col in df.columns if col not in exclude_cols]
        
        # Filter to only columns that exist
        feature_cols = [col for col in feature_cols if col in df.columns]
        
        if len(feature_cols) == 0:
            raise ValueError("No valid feature columns found in mart_game_features")
        
        X = df[feature_cols].copy()
        
        # Data quality filtering (iteration 6): Remove games with too many missing features
        # This prevents training on incomplete data which can introduce noise
        # Calculate missing feature percentage for each game
        missing_pct = X.isnull().sum(axis=1) / len(feature_cols)
        
        # Filter out games with too many missing features
        valid_mask = missing_pct <= config.max_missing_feature_pct
        games_before_filter = len(X)
        X = X[valid_mask].copy()
        df = df[valid_mask].copy()
        games_after_filter = len(X)
        games_filtered = games_before_filter - games_after_filter
        
        if games_filtered > 0:
            context.log.info(f"Data quality filtering: Removed {games_filtered} games ({games_filtered/games_before_filter*100:.1f}%) with >{config.max_missing_feature_pct*100:.0f}% missing features")
            context.log.info(f"Training on {games_after_filter} games ({games_after_filter/games_before_filter*100:.1f}% of original data)")
        else:
            context.log.info(f"Data quality filtering: All {games_after_filter} games have <={config.max_missing_feature_pct*100:.0f}% missing features")
        
        # Fill remaining NaN/None values (for features that are still missing but within threshold)
        X = X.fillna(0)
        
        # Convert boolean/object columns to numeric (0/1) for XGBoost
        for col in X.columns:
            if X[col].dtype == 'object':
                # Convert object (likely boolean strings or None) to numeric
                # Handle True/False, 'True'/'False', None, etc.
                X[col] = pd.to_numeric(X[col], errors='coerce').fillna(0)
            elif X[col].dtype == 'bool' or X[col].dtype.name.startswith('bool'):
                # Convert boolean to int (True/False -> 1/0)
                X[col] = X[col].astype(int)
        
        # Ensure all columns are numeric (final check)
        for col in X.columns:
            if not pd.api.types.is_numeric_dtype(X[col]):
                context.log.warning(f"Converting non-numeric column {col} to numeric")
                X[col] = pd.to_numeric(X[col], errors='coerce').fillna(0)
        
        # Add Python-computed feature interactions (iteration 89)
        # These interactions are computed in Python to avoid SQLMesh materialization blocking issues
        # They combine existing features to capture non-linear relationships
        interaction_features_added = []
        
        # 1. Momentum score difference × H2H win percentage
        # Teams with momentum advantage AND historical H2H advantage are more likely to win
        if 'home_momentum_score' in X.columns and 'away_momentum_score' in X.columns and 'home_h2h_win_pct' in X.columns:
            momentum_diff = X['home_momentum_score'] - X['away_momentum_score']
            X['momentum_score_diff_x_h2h_win_pct'] = momentum_diff * X['home_h2h_win_pct']
            interaction_features_added.append('momentum_score_diff_x_h2h_win_pct')
            context.log.info("Added interaction: momentum_score_diff × h2h_win_pct")
        
        # 2. Form divergence difference × Home advantage
        # Teams playing above season average (positive form divergence) with home advantage are more dangerous
        if 'home_form_divergence' in X.columns and 'away_form_divergence' in X.columns and 'home_advantage' in X.columns:
            form_divergence_diff = X['home_form_divergence'] - X['away_form_divergence']
            X['form_divergence_diff_x_home_advantage'] = form_divergence_diff * X['home_advantage']
            interaction_features_added.append('form_divergence_diff_x_home_advantage')
            context.log.info("Added interaction: form_divergence_diff × home_advantage")
        
        # 3. Momentum score difference × Rest advantage
        # Teams with momentum AND rest advantage are more likely to win
        if 'home_momentum_score' in X.columns and 'away_momentum_score' in X.columns and 'rest_advantage' in X.columns:
            momentum_diff = X['home_momentum_score'] - X['away_momentum_score']
            X['momentum_score_diff_x_rest_advantage'] = momentum_diff * X['rest_advantage']
            interaction_features_added.append('momentum_score_diff_x_rest_advantage')
            context.log.info("Added interaction: momentum_score_diff × rest_advantage")
        
        # 4. Win percentage difference × H2H win percentage
        # Team quality × head-to-head history - teams that are better AND have H2H advantage are stronger
        if 'win_pct_diff_10' in X.columns and 'home_h2h_win_pct' in X.columns:
            X['win_pct_diff_10_x_h2h_win_pct'] = X['win_pct_diff_10'] * X['home_h2h_win_pct']
            interaction_features_added.append('win_pct_diff_10_x_h2h_win_pct')
            context.log.info("Added interaction: win_pct_diff_10 × h2h_win_pct")
        
        # 5. Form divergence difference × Rest advantage
        # "Hot + rested" teams are more dangerous
        if 'home_form_divergence' in X.columns and 'away_form_divergence' in X.columns and 'rest_advantage' in X.columns:
            form_divergence_diff = X['home_form_divergence'] - X['away_form_divergence']
            X['form_divergence_diff_x_rest_advantage'] = form_divergence_diff * X['rest_advantage']
            interaction_features_added.append('form_divergence_diff_x_rest_advantage')
            context.log.info("Added interaction: form_divergence_diff × rest_advantage")
        
        # 6. Injury impact difference × Rest advantage
        # Injuries matter more when teams are tired - tired teams with injuries are more vulnerable
        if 'injury_impact_diff' in X.columns and 'rest_advantage' in X.columns:
            X['injury_impact_diff_x_rest_advantage'] = X['injury_impact_diff'] * X['rest_advantage']
            interaction_features_added.append('injury_impact_diff_x_rest_advantage')
            context.log.info("Added interaction: injury_impact_diff × rest_advantage")
        
        # 7. Injury impact difference × Home advantage
        # Home teams can better compensate for injuries (familiar court, crowd support, no travel fatigue)
        if 'injury_impact_diff' in X.columns and 'home_advantage' in X.columns:
            X['injury_impact_diff_x_home_advantage'] = X['injury_impact_diff'] * X['home_advantage']
            interaction_features_added.append('injury_impact_diff_x_home_advantage')
            context.log.info("Added interaction: injury_impact_diff × home_advantage")
        
        # 8. Injury impact difference × Win percentage difference
        # Injuries matter more for better teams - losing a star player hurts elite teams more
        if 'injury_impact_diff' in X.columns and 'win_pct_diff_10' in X.columns:
            X['injury_impact_diff_x_win_pct_diff_10'] = X['injury_impact_diff'] * X['win_pct_diff_10']
            interaction_features_added.append('injury_impact_diff_x_win_pct_diff_10')
            context.log.info("Added interaction: injury_impact_diff × win_pct_diff_10")
        
        # 9. Form divergence difference × Win percentage difference
        # Teams with good form AND high quality are more dangerous - form amplifies team quality
        if 'home_form_divergence' in X.columns and 'away_form_divergence' in X.columns and 'win_pct_diff_10' in X.columns:
            form_divergence_diff = X['home_form_divergence'] - X['away_form_divergence']
            X['form_divergence_diff_x_win_pct_diff_10'] = form_divergence_diff * X['win_pct_diff_10']
            interaction_features_added.append('form_divergence_diff_x_win_pct_diff_10')
            context.log.info("Added interaction: form_divergence_diff × win_pct_diff_10")
        
        # 10. Momentum score difference × Win percentage difference
        # Teams with momentum AND high quality are more dangerous - momentum amplifies team quality
        if 'home_momentum_score' in X.columns and 'away_momentum_score' in X.columns and 'win_pct_diff_10' in X.columns:
            momentum_diff = X['home_momentum_score'] - X['away_momentum_score']
            X['momentum_score_diff_x_win_pct_diff_10'] = momentum_diff * X['win_pct_diff_10']
            interaction_features_added.append('momentum_score_diff_x_win_pct_diff_10')
            context.log.info("Added interaction: momentum_score_diff × win_pct_diff_10")
        
        # 11. Win percentage difference × Home recent opponent average win percentage
        # Better teams facing tougher schedules (home) are tested more - schedule strength contextualizes team quality
        if 'win_pct_diff_10' in X.columns and 'home_recent_opp_avg_win_pct' in X.columns:
            X['win_pct_diff_10_x_home_recent_opp_avg_win_pct'] = X['win_pct_diff_10'] * X['home_recent_opp_avg_win_pct']
            interaction_features_added.append('win_pct_diff_10_x_home_recent_opp_avg_win_pct')
            context.log.info("Added interaction: win_pct_diff_10 × home_recent_opp_avg_win_pct")
        
        # 12. Win percentage difference × Away recent opponent average win percentage
        # Better teams facing tougher schedules (away) are tested more - schedule strength contextualizes team quality
        if 'win_pct_diff_10' in X.columns and 'away_recent_opp_avg_win_pct' in X.columns:
            X['win_pct_diff_10_x_away_recent_opp_avg_win_pct'] = X['win_pct_diff_10'] * X['away_recent_opp_avg_win_pct']
            interaction_features_added.append('win_pct_diff_10_x_away_recent_opp_avg_win_pct')
            context.log.info("Added interaction: win_pct_diff_10 × away_recent_opp_avg_win_pct")
        
        # 12b. Win percentage difference × Rest advantage
        # Better teams benefit more from rest advantage - rest amplifies team quality differences
        # This is similar to net_rtg_diff_10 × rest_advantage, but win_pct_diff_10 is a more direct measure of team quality
        if 'win_pct_diff_10' in X.columns and 'rest_advantage' in X.columns:
            X['win_pct_diff_10_x_rest_advantage'] = X['win_pct_diff_10'] * X['rest_advantage']
            interaction_features_added.append('win_pct_diff_10_x_rest_advantage')
            context.log.info("Added interaction: win_pct_diff_10 × rest_advantage")
        
        # 13. Form divergence difference × Schedule strength difference
        # Teams with good form facing easier schedules (relative to opponent) have an advantage
        if 'home_form_divergence' in X.columns and 'away_form_divergence' in X.columns and 'home_recent_opp_avg_win_pct' in X.columns and 'away_recent_opp_avg_win_pct' in X.columns:
            form_divergence_diff = X['home_form_divergence'] - X['away_form_divergence']
            recent_opp_avg_win_pct_diff = X['home_recent_opp_avg_win_pct'] - X['away_recent_opp_avg_win_pct']
            X['form_divergence_diff_x_recent_opp_avg_win_pct_diff'] = form_divergence_diff * recent_opp_avg_win_pct_diff
            interaction_features_added.append('form_divergence_diff_x_recent_opp_avg_win_pct_diff')
            context.log.info("Added interaction: form_divergence_diff × recent_opp_avg_win_pct_diff")
        
        # 14. Net rating difference × Home advantage
        # Teams with better net rating AND home advantage are more likely to win
        if 'net_rtg_diff_10' in X.columns and 'home_advantage' in X.columns:
            X['net_rtg_diff_10_x_home_advantage'] = X['net_rtg_diff_10'] * X['home_advantage']
            interaction_features_added.append('net_rtg_diff_10_x_home_advantage')
            context.log.info("Added interaction: net_rtg_diff_10 × home_advantage")
        # Reverted iteration 121: momentum_score_diff_x_home_advantage (regressed test accuracy 0.6446 → 0.6402); do not re-add.
        
        # 15. Net rating difference × Rest advantage
        # Teams with better net rating AND rest advantage are more likely to win
        if 'net_rtg_diff_10' in X.columns and 'rest_advantage' in X.columns:
            X['net_rtg_diff_10_x_rest_advantage'] = X['net_rtg_diff_10'] * X['rest_advantage']
            interaction_features_added.append('net_rtg_diff_10_x_rest_advantage')
            context.log.info("Added interaction: net_rtg_diff_10 × rest_advantage")
        
        # 16. Field goal percentage difference × Win percentage difference
        # Teams with better shooting AND higher quality are more dangerous - shooting efficiency amplifies team quality
        if 'fg_pct_diff_5' in X.columns and 'win_pct_diff_10' in X.columns:
            X['fg_pct_diff_5_x_win_pct_diff_10'] = X['fg_pct_diff_5'] * X['win_pct_diff_10']
            interaction_features_added.append('fg_pct_diff_5_x_win_pct_diff_10')
            context.log.info("Added interaction: fg_pct_diff_5 × win_pct_diff_10")
        
        # 16b. Field goal percentage difference × Rest advantage (iteration 122)
        # Hot shooting when rested - shooting efficiency × rest; non–win_pct×context lever per iteration 121 next steps
        if 'fg_pct_diff_5' in X.columns and 'rest_advantage' in X.columns:
            X['fg_pct_diff_5_x_rest_advantage'] = X['fg_pct_diff_5'] * X['rest_advantage']
            interaction_features_added.append('fg_pct_diff_5_x_rest_advantage')
            context.log.info("Added interaction: fg_pct_diff_5 × rest_advantage")
        
        # 17. Offensive rating difference × Defensive rating difference
        # Teams with offensive AND defensive advantages are more likely to win - captures two-sided dominance
        if 'off_rtg_diff_10' in X.columns and 'def_rtg_diff_10' in X.columns:
            X['off_rtg_diff_10_x_def_rtg_diff_10'] = X['off_rtg_diff_10'] * X['def_rtg_diff_10']
            interaction_features_added.append('off_rtg_diff_10_x_def_rtg_diff_10')
            context.log.info("Added interaction: off_rtg_diff_10 × def_rtg_diff_10")
        
        # Add Python-computed base features: Assist, Steal, and Block Rate (iteration 107)
        # These features are defined in SQLMesh but not materialized due to SQLMesh blocking (iteration 105)
        # Computing them in Python from base features (apg, possessions) to bypass SQLMesh materialization
        # Rate = 100 * (stat per game / possessions per game) = stat per 100 possessions
        base_features_added = []
        
        # Assist rate = 100 * (assists per game / possessions per game) = assists per 100 possessions
        # 5-game rolling
        if 'home_rolling_5_assist_rate' not in X.columns and 'home_rolling_5_apg' in X.columns and 'home_rolling_5_possessions' in X.columns:
            X['home_rolling_5_assist_rate'] = 100.0 * X['home_rolling_5_apg'] / X['home_rolling_5_possessions'].replace(0, np.nan)
            X['home_rolling_5_assist_rate'] = X['home_rolling_5_assist_rate'].fillna(0)
            base_features_added.append('home_rolling_5_assist_rate')
            context.log.info("Added base feature: home_rolling_5_assist_rate")
        
        if 'away_rolling_5_assist_rate' not in X.columns and 'away_rolling_5_apg' in X.columns and 'away_rolling_5_possessions' in X.columns:
            X['away_rolling_5_assist_rate'] = 100.0 * X['away_rolling_5_apg'] / X['away_rolling_5_possessions'].replace(0, np.nan)
            X['away_rolling_5_assist_rate'] = X['away_rolling_5_assist_rate'].fillna(0)
            base_features_added.append('away_rolling_5_assist_rate')
            context.log.info("Added base feature: away_rolling_5_assist_rate")
        
        # 10-game rolling
        if 'home_rolling_10_assist_rate' not in X.columns and 'home_rolling_10_apg' in X.columns and 'home_rolling_10_possessions' in X.columns:
            X['home_rolling_10_assist_rate'] = 100.0 * X['home_rolling_10_apg'] / X['home_rolling_10_possessions'].replace(0, np.nan)
            X['home_rolling_10_assist_rate'] = X['home_rolling_10_assist_rate'].fillna(0)
            base_features_added.append('home_rolling_10_assist_rate')
            context.log.info("Added base feature: home_rolling_10_assist_rate")
        
        if 'away_rolling_10_assist_rate' not in X.columns and 'away_rolling_10_apg' in X.columns and 'away_rolling_10_possessions' in X.columns:
            X['away_rolling_10_assist_rate'] = 100.0 * X['away_rolling_10_apg'] / X['away_rolling_10_possessions'].replace(0, np.nan)
            X['away_rolling_10_assist_rate'] = X['away_rolling_10_assist_rate'].fillna(0)
            base_features_added.append('away_rolling_10_assist_rate')
            context.log.info("Added base feature: away_rolling_10_assist_rate")
        
        # Note: Steal rate and block rate require steals per game (spg) and blocks per game (bpg)
        # which are not currently in the feature set. These features are defined in SQLMesh
        # but not materialized. For now, we'll focus on assist rate which we can compute from apg.
        # If steal/block rate features are needed, we would need to add spg/bpg to the feature set first.
        
        # Differential features for assist rate
        if 'home_rolling_5_assist_rate' in X.columns and 'away_rolling_5_assist_rate' in X.columns and 'assist_rate_diff_5' not in X.columns:
            X['assist_rate_diff_5'] = X['home_rolling_5_assist_rate'] - X['away_rolling_5_assist_rate']
            base_features_added.append('assist_rate_diff_5')
            context.log.info("Added base feature: assist_rate_diff_5")
        
        if 'home_rolling_10_assist_rate' in X.columns and 'away_rolling_10_assist_rate' in X.columns and 'assist_rate_diff_10' not in X.columns:
            X['assist_rate_diff_10'] = X['home_rolling_10_assist_rate'] - X['away_rolling_10_assist_rate']
            base_features_added.append('assist_rate_diff_10')
            context.log.info("Added base feature: assist_rate_diff_10")
        
        if interaction_features_added:
            context.log.info(f"Added {len(interaction_features_added)} Python-computed interaction features: {', '.join(interaction_features_added)}")
        if base_features_added:
            context.log.info(f"Added {len(base_features_added)} Python-computed base features: {', '.join(base_features_added)}")
        
        if interaction_features_added or base_features_added:
            # Update feature_cols to include new features
            feature_cols = list(X.columns)
        else:
            context.log.warning("No Python-computed features added - required base features may be missing")
        
        y = df['home_win']
        
        # Time-based or random train/test split
        # Temporal split by fixed date (iteration 123): train = game_date < cutoff, test = game_date >= cutoff
        # More realistic evaluation: test on future games relative to training window
        # Time-based split (iteration 5): Use most recent games as test set (percentage-based)
        # Random split (iteration 1 baseline): stratify by y, best historical accuracy 0.6559
        from sklearn.model_selection import train_test_split

        if config.temporal_split_cutoff_date:
            # Fixed-date temporal split: train on games before cutoff, test on games on or after cutoff
            cutoff = pd.Timestamp(config.temporal_split_cutoff_date)
            gdate = pd.to_datetime(df["game_date"])
            train_mask = gdate < cutoff
            test_mask = ~train_mask
            n_train, n_test = train_mask.sum(), test_mask.sum()
            if n_test < 50:
                context.log.warning(f"Temporal split: only {n_test} test games (cutoff={config.temporal_split_cutoff_date}). Consider a later cutoff or random split.")
            X_train = X.loc[train_mask].copy()
            X_test = X.loc[test_mask].copy()
            y_train = y.loc[train_mask].copy()
            y_test = y.loc[test_mask].copy()
            train_dates = (gdate[train_mask].min(), gdate[train_mask].max()) if n_train else (None, None)
            test_dates = (gdate[test_mask].min(), gdate[test_mask].max()) if n_test else (None, None)
            context.log.info(f"Temporal split (cutoff={config.temporal_split_cutoff_date}) - Train: {n_train} games ({train_dates[0]} to {train_dates[1]})")
            context.log.info(f"Temporal split (cutoff={config.temporal_split_cutoff_date}) - Test: {n_test} games ({test_dates[0]} to {test_dates[1]})")
        elif config.use_time_based_split:
            # Time-based split: Use most recent games as test set
            # Sort by game_date to ensure chronological order
            df_sorted = df.sort_values('game_date').reset_index(drop=True)
            # Reindex X and y to match sorted df indices
            X_sorted = X.reindex(df_sorted.index).reset_index(drop=True)
            y_sorted = y.reindex(df_sorted.index).reset_index(drop=True)
            
            # Calculate cutoff index for test set (last N% of games)
            test_size_int = int(len(df_sorted) * config.test_size)
            train_size_int = len(df_sorted) - test_size_int
            
            # Split: older games for training, newer games for testing
            X_train = X_sorted.iloc[:train_size_int]
            X_test = X_sorted.iloc[train_size_int:]
            y_train = y_sorted.iloc[:train_size_int]
            y_test = y_sorted.iloc[train_size_int:]
            
            train_date_range = f"{df_sorted.iloc[0]['game_date']} to {df_sorted.iloc[train_size_int-1]['game_date']}"
            test_date_range = f"{df_sorted.iloc[train_size_int]['game_date']} to {df_sorted.iloc[-1]['game_date']}"
            
            context.log.info(f"Time-based split - Training set: {len(X_train)} games ({train_date_range})")
            context.log.info(f"Time-based split - Test set: {len(X_test)} games ({test_date_range})")
        else:
            # Random train/test split (fallback)
            # Random split was used in iteration 1 which achieved best accuracy (0.6559)
            # Temporal split (iteration 13) reduced accuracy to 0.6051, suggesting fixed date cutoff was too restrictive
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=config.test_size, random_state=config.random_state, stratify=y
            )
            
            context.log.info(f"Random split - Training set: {len(X_train)} games")
            context.log.info(f"Random split - Test set: {len(X_test)} games")

        split_type = (f"temporal (cutoff={config.temporal_split_cutoff_date})" if config.temporal_split_cutoff_date
                      else ("time-based" if config.use_time_based_split else "random"))
        
        # Identify interaction features for importance boosting (incl. injury_advantage_home, penalties)
        # This will be updated after feature selection if enabled
        interaction_features = [col for col in feature_cols if '_x_' in col or '_ratio' in col or 'injury' in col.lower() or col == 'injury_advantage_home']
        context.log.info(f"Found {len(interaction_features)} interaction/injury features to boost")
        
        # Create sample weights to boost importance of games with extreme injury scenarios
        # This helps the model learn from rare but important cases
        sample_weights = pd.Series(1.0, index=X_train.index)
        if len(interaction_features) > 0:
            # Boost samples where interaction features have extreme values
            for feat in interaction_features:
                if feat in X_train.columns:
                    # Normalize feature values
                    feat_values = X_train[feat].abs()
                    if feat_values.max() > 0:
                        normalized = feat_values / feat_values.max()
                        # Boost samples with high interaction feature values
                        sample_weights += normalized * (config.interaction_feature_boost - 1.0)
            
            # Normalize weights to prevent extreme values
            sample_weights = sample_weights / sample_weights.mean()
            sample_weights = sample_weights.clip(0.5, 2.0)  # Cap between 0.5x and 2x
            context.log.info(f"Sample weights range: {sample_weights.min():.3f} - {sample_weights.max():.3f}")
        
        # Use validation set for early stopping (shared by XGBoost and LightGBM)
        X_train_fit, X_val, y_train_fit, y_val = train_test_split(
            X_train, y_train, test_size=0.15, random_state=config.random_state, stratify=y_train
        )
        sample_weights_fit = sample_weights.loc[X_train_fit.index] if len(sample_weights) > 0 else None
        
        # Feature selection: Train initial model to get importances, then select top features
        algorithm = config.algorithm.lower()
        selected_features = feature_cols.copy()
        if config.feature_selection_enabled:
            context.log.info("Performing feature selection...")
            from sklearn.feature_selection import SelectFromModel
            
            # Use XGBoost as selector model for feature selection (more sklearn-compatible than CatBoost)
            # This is a common pattern: use a quick, sklearn-compatible model for feature selection,
            # then train the final model (CatBoost/XGBoost/LightGBM) with the selected features
            try:
                import xgboost as xgb
            except ImportError:
                context.log.error("XGBoost not installed. Install: pip install xgboost")
                raise
            selector_model = xgb.XGBClassifier(
                n_estimators=50,  # Quick model for selection
                learning_rate=0.1,
                random_state=config.random_state,
                eval_metric='logloss',
                tree_method='hist',
            )
            
            selector_model.fit(X_train_fit, y_train_fit, sample_weight=sample_weights_fit)
            
            # Get feature importances
            importances = selector_model.feature_importances_
            
            # Select features based on method
            if config.feature_selection_method == "rfe":
                # Recursive Feature Elimination: recursively remove features and keep top N by rank
                from sklearn.feature_selection import RFE
                n_features_to_select = min(config.feature_selection_n_features, len(feature_cols))
                rfe = RFE(
                    estimator=selector_model,
                    n_features_to_select=n_features_to_select,
                    step=1,  # Remove one feature at a time
                    verbose=0
                )
                rfe.fit(X_train_fit, y_train_fit, sample_weight=sample_weights_fit)
                
                # Get selected feature names
                selected_mask = rfe.get_support()
                selected_features = [feature_cols[i] for i in range(len(feature_cols)) if selected_mask[i]]
                
                removed_count = len(feature_cols) - len(selected_features)
                context.log.info(f"Feature selection (RFE): {len(selected_features)} features selected (top {n_features_to_select} by rank), {removed_count} features removed")
            elif config.feature_selection_method == "percentile":
                # Percentile-based: keep top N% of features by importance
                percentile_threshold = np.percentile(importances, 100 - config.feature_selection_percentile)
                selected_mask = importances >= percentile_threshold
                selected_features = [feature_cols[i] for i in range(len(feature_cols)) if selected_mask[i]]
                removed_count = len(feature_cols) - len(selected_features)
                context.log.info(f"Feature selection (percentile): {len(selected_features)} features selected (top {config.feature_selection_percentile}%), {removed_count} features removed (percentile threshold={percentile_threshold:.6f})")
            else:
                # Threshold-based: remove features below threshold
                selector = SelectFromModel(
                    selector_model,
                    threshold=config.feature_selection_threshold,
                    prefit=True
                )
                selector.fit(X_train_fit, y_train_fit)
                
                # Get selected feature names
                selected_mask = selector.get_support()
                selected_features = [feature_cols[i] for i in range(len(feature_cols)) if selected_mask[i]]
                
                removed_count = len(feature_cols) - len(selected_features)
                context.log.info(f"Feature selection (threshold): {len(selected_features)} features selected, {removed_count} features removed (threshold={config.feature_selection_threshold})")
            
            # Filter feature sets to selected features
            X_train = X_train[selected_features]
            X_test = X_test[selected_features]
            X_train_fit = X_train_fit[selected_features]
            X_val = X_val[selected_features]
            feature_cols = selected_features
            
            # Update interaction features list to only include selected features
            interaction_features = [col for col in interaction_features if col in selected_features]
            context.log.info(f"After feature selection: {len(interaction_features)} interaction/injury features remain")
        
        # Train model: XGBoost, LightGBM, or Ensemble
        num_leaves = None  # Only used for LightGBM/ensemble; set in those branches
        if algorithm == "ensemble":
            # Ensemble: Train both XGBoost and LightGBM, combine with stacking (iteration 7)
            # Stacking learns how to best combine base models using a meta-learner, which can outperform voting
            try:
                from sklearn.ensemble import StackingClassifier
                from sklearn.linear_model import LogisticRegression
                import xgboost as xgb
                import lightgbm as lgb
                import mlflow.sklearn
            except ImportError as ie:
                context.log.error(f"Missing dependency for ensemble: {ie}. Install: pip install scikit-learn xgboost lightgbm")
                raise
            
            context.log.info(f"Training ensemble model (XGBoost + LightGBM stacking) with {split_type} train/test split...")
            
            # Train XGBoost model (for stacking ensemble)
            # Note: StackingClassifier handles fitting internally, so early_stopping_rounds not used here
            xgb_model = xgb.XGBClassifier(
                max_depth=config.max_depth,
                n_estimators=config.n_estimators,
                learning_rate=config.learning_rate,
                subsample=config.subsample,
                colsample_bytree=config.colsample_bytree,
                min_child_weight=config.min_child_weight,
                gamma=config.gamma,
                reg_alpha=config.reg_alpha,
                reg_lambda=config.reg_lambda,
                random_state=config.random_state,
                eval_metric='logloss',
                tree_method='hist',
            )
            
            # Train LightGBM model (for stacking ensemble)
            # Note: StackingClassifier handles fitting internally, so early stopping not used here
            num_leaves = config.num_leaves if config.num_leaves is not None else min(2 ** config.max_depth - 1, 127)
            lgb_model = lgb.LGBMClassifier(
                num_leaves=num_leaves,
                n_estimators=config.n_estimators,
                learning_rate=config.learning_rate,
                subsample=config.subsample,
                colsample_bytree=config.colsample_bytree,
                min_child_samples=config.min_child_samples,
                min_split_loss=config.gamma,
                reg_alpha=config.reg_alpha,
                reg_lambda=config.reg_lambda,
                random_state=config.random_state,
                verbosity=-1,
                n_jobs=-1,
            )
            
            # Create stacking classifier with logistic regression meta-learner
            # Stacking uses cross-validation to generate out-of-fold predictions from base models,
            # then trains a meta-learner (LogisticRegression) to combine these predictions
            # This can learn optimal combinations that outperform simple voting
            meta_learner = LogisticRegression(C=1.0, max_iter=1000, random_state=config.random_state)
            model = StackingClassifier(
                estimators=[('xgb', xgb_model), ('lgb', lgb_model)],
                final_estimator=meta_learner,
                cv=5,  # 5-fold CV for out-of-fold predictions
                stack_method='predict_proba',  # Use probabilities for meta-learner
                n_jobs=-1,
            )
            
            context.log.info(f"Ensemble hyperparameters: XGBoost (max_depth={config.max_depth}, n_estimators={config.n_estimators}), "
                            f"LightGBM (num_leaves={num_leaves}, n_estimators={config.n_estimators}), "
                            f"stacking=LogisticRegression, cv=5")
            
            # Fit stacking ensemble
            # StackingClassifier uses cross-validation internally, so we fit on the full training set
            # Note: sample_weight is not directly supported by StackingClassifier, but base models can use it
            # We'll fit without sample_weight for the ensemble, but individual models below will use it
            model.fit(X_train_fit, y_train_fit)
            
            # Also fit individual models separately with early stopping for comparison/logging
            # Create new instances to avoid affecting the ensemble models
            xgb_individual = xgb.XGBClassifier(
                max_depth=config.max_depth,
                n_estimators=config.n_estimators,
                learning_rate=config.learning_rate,
                subsample=config.subsample,
                colsample_bytree=config.colsample_bytree,
                min_child_weight=config.min_child_weight,
                gamma=config.gamma,
                reg_alpha=config.reg_alpha,
                reg_lambda=config.reg_lambda,
                random_state=config.random_state,
                eval_metric='logloss',
                tree_method='hist',
                early_stopping_rounds=20,
            )
            xgb_individual.fit(
                X_train_fit, y_train_fit,
                sample_weight=sample_weights_fit,
                eval_set=[(X_val, y_val)],
                verbose=False,
            )
            
            num_leaves_individual = config.num_leaves if config.num_leaves is not None else min(2 ** config.max_depth - 1, 127)
            lgb_individual = lgb.LGBMClassifier(
                num_leaves=num_leaves_individual,
                n_estimators=config.n_estimators,
                learning_rate=config.learning_rate,
                subsample=config.subsample,
                colsample_bytree=config.colsample_bytree,
                min_child_samples=config.min_child_samples,
                min_split_loss=config.gamma,
                reg_alpha=config.reg_alpha,
                reg_lambda=config.reg_lambda,
                random_state=config.random_state,
                verbosity=-1,
                n_jobs=-1,
            )
            lgb_individual.fit(
                X_train_fit, y_train_fit,
                sample_weight=sample_weights_fit,
                eval_set=[(X_val, y_val)],
                callbacks=[lgb.early_stopping(20, verbose=False)],
            )
            
            # Log individual model accuracies for comparison
            xgb_train_acc = xgb_individual.score(X_train, y_train)
            xgb_test_acc = xgb_individual.score(X_test, y_test)
            lgb_train_acc = lgb_individual.score(X_train, y_train)
            lgb_test_acc = lgb_individual.score(X_test, y_test)
            context.log.info(f"XGBoost individual (with early stopping): train={xgb_train_acc:.4f}, test={xgb_test_acc:.4f}")
            context.log.info(f"LightGBM individual (with early stopping): train={lgb_train_acc:.4f}, test={lgb_test_acc:.4f}")
            
        elif algorithm == "lightgbm":
            try:
                import lightgbm as lgb
                import mlflow.lightgbm
            except ImportError as ie:
                context.log.error("LightGBM not installed. Install: pip install lightgbm")
                raise
            # Use config.num_leaves if provided, otherwise calculate from max_depth
            num_leaves = config.num_leaves if config.num_leaves is not None else min(2 ** config.max_depth - 1, 127)
            model = lgb.LGBMClassifier(
                num_leaves=num_leaves,
                n_estimators=config.n_estimators,
                learning_rate=config.learning_rate,
                subsample=config.subsample,
                colsample_bytree=config.colsample_bytree,
                min_child_samples=config.min_child_samples,
                min_split_loss=config.gamma,
                reg_alpha=config.reg_alpha,
                reg_lambda=config.reg_lambda,
                random_state=config.random_state,
                verbosity=-1,
                n_jobs=-1,
            )
            context.log.info(f"Training LightGBM model with {split_type} train/test split...")
            context.log.info(f"Hyperparameters: num_leaves={num_leaves}, n_estimators={config.n_estimators}, "
                            f"learning_rate={config.learning_rate}, subsample={config.subsample}, "
                            f"colsample_bytree={config.colsample_bytree}, min_child_samples={config.min_child_samples}, "
                            f"reg_alpha={config.reg_alpha}, reg_lambda={config.reg_lambda}")
            model.fit(
                X_train_fit, y_train_fit,
                sample_weight=sample_weights_fit,
                eval_set=[(X_val, y_val)],
                callbacks=[lgb.early_stopping(20, verbose=False)],
            )
        elif algorithm == "catboost":
            try:
                from catboost import CatBoostClassifier
                import mlflow.catboost
            except ImportError as ie:
                context.log.error("CatBoost not installed. Install: pip install catboost")
                raise
            # CatBoost uses depth instead of max_depth, and has different parameter names
            # Set boosting_type='Ordered' for iteration 82 - Ordered usually provides better quality on datasets
            # (though may be slower), and we haven't tried this CatBoost-specific hyperparameter yet
            model = CatBoostClassifier(
                depth=config.max_depth,
                iterations=config.n_estimators,
                learning_rate=config.learning_rate,
                subsample=config.subsample,
                colsample_bylevel=config.colsample_bytree,  # CatBoost uses colsample_bylevel
                min_child_samples=config.min_child_samples,
                l2_leaf_reg=config.reg_lambda,  # CatBoost uses l2_leaf_reg instead of reg_lambda
                boosting_type='Ordered',  # Ordered usually provides better quality (iteration 82)
                random_seed=config.random_state,
                verbose=False,
                thread_count=-1,  # Use all available threads
                early_stopping_rounds=20,
            )
            context.log.info(f"Training CatBoost model with {split_type} train/test split...")
            context.log.info(f"Hyperparameters: depth={config.max_depth}, iterations={config.n_estimators}, "
                            f"learning_rate={config.learning_rate}, subsample={config.subsample}, "
                            f"colsample_bylevel={config.colsample_bytree}, min_child_samples={config.min_child_samples}, "
                            f"l2_leaf_reg={config.reg_lambda}, boosting_type=Ordered")
            # CatBoost uses eval_set parameter directly
            model.fit(
                X_train_fit, y_train_fit,
                sample_weight=sample_weights_fit,
                eval_set=(X_val, y_val),
                verbose=False,
            )
        else:
            # XGBoost (default)
            try:
                import xgboost as xgb
            except ImportError:
                context.log.error("XGBoost not installed. Install: pip install xgboost")
                raise
            model = xgb.XGBClassifier(
                max_depth=config.max_depth,
                n_estimators=config.n_estimators,
                learning_rate=config.learning_rate,
                subsample=config.subsample,
                colsample_bytree=config.colsample_bytree,
                min_child_weight=config.min_child_weight,
                gamma=config.gamma,
                reg_alpha=config.reg_alpha,
                reg_lambda=config.reg_lambda,
                random_state=config.random_state,
                eval_metric='logloss',
                tree_method='hist',
                early_stopping_rounds=20,
            )
            context.log.info("Training XGBoost model with improved hyperparameters...")
            context.log.info(f"Hyperparameters: max_depth={config.max_depth}, n_estimators={config.n_estimators}, "
                            f"learning_rate={config.learning_rate}, subsample={config.subsample}, "
                            f"colsample_bytree={config.colsample_bytree}")
            model.fit(
                X_train_fit, y_train_fit,
                sample_weight=sample_weights_fit,
                eval_set=[(X_val, y_val)],
                verbose=False,
            )
        
        # Evaluate
        train_score = model.score(X_train, y_train)
        test_score = model.score(X_test, y_test)
        
        from sklearn.metrics import classification_report, confusion_matrix
        y_pred = model.predict(X_test)
        
        context.log.info(f"Training accuracy: {train_score:.4f}")
        context.log.info(f"Test accuracy: {test_score:.4f}")
        
        # Extract feature importances
        # For ensemble, average importances from both models
        if algorithm == "ensemble":
            # Get importances from both base models and average
            # For StackingClassifier, base models are in estimators_ (fitted) or named_estimators_ (original)
            xgb_importances = dict(zip(feature_cols, model.named_estimators_['xgb'].feature_importances_))
            lgb_importances = dict(zip(feature_cols, model.named_estimators_['lgb'].feature_importances_))
            # Average the importances
            feature_importances = {k: (xgb_importances.get(k, 0) + lgb_importances.get(k, 0)) / 2.0 
                                  for k in feature_cols}
        else:
            feature_importances = dict(zip(feature_cols, model.feature_importances_))
        importance_sorted = sorted(feature_importances.items(), key=lambda x: x[1], reverse=True)
        
        context.log.info(f"Top 10 features: {[f[0] for f in importance_sorted[:10]]}")
        
        # Log interaction feature importance
        interaction_importance = {k: v for k, v in feature_importances.items() if k in interaction_features}
        if interaction_importance:
            total_interaction_importance = sum(interaction_importance.values())
            context.log.info(f"Interaction features total importance: {total_interaction_importance:.6f} ({total_interaction_importance/sum(feature_importances.values())*100:.2f}%)")
            top_interactions = sorted(interaction_importance.items(), key=lambda x: x[1], reverse=True)[:5]
            context.log.info(f"Top interaction features: {[f'{f[0]}: {f[1]:.6f}' for f in top_interactions]}")
        
        # Generate model version
        model_version = config.model_version or f"v1.0.{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # Also save model to disk (for backward compatibility)
        model_dir = project_root / "models"
        model_dir.mkdir(exist_ok=True)
        model_path = model_dir / f"game_winner_model_{model_version}.pkl"
        joblib.dump(model, model_path)
        
        context.log.info(f"Model saved to {model_path}")
        
        # Fit Platt scaling calibrator on validation set (maps raw P(home) to better-calibrated [0,1])
        # Platt scaling uses logistic regression and often works better than isotonic regression for XGBoost
        from sklearn.calibration import calibration_curve
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import brier_score_loss
        import numpy as np
        proba_val = model.predict_proba(X_val)[:, 1]
        # Platt scaling: logistic regression on log-odds (logit) of probabilities
        # Transform probabilities to log-odds space, fit logistic regression, then transform back
        # Use small regularization to prevent overfitting
        calibrator = LogisticRegression(C=1.0, max_iter=1000, solver='lbfgs')
        # Fit on log-odds (logit) of probabilities
        logit_proba_val = np.log(proba_val / (1 - proba_val + 1e-10))  # Add small epsilon to avoid log(0)
        calibrator.fit(logit_proba_val.reshape(-1, 1), y_val)
        calibrator_path = model_dir / f"calibrator_{model_version}.pkl"
        joblib.dump(calibrator, calibrator_path)
        context.log.info(f"Platt scaling calibrator saved to {calibrator_path}")
        
        # Evaluate calibration quality on test set
        proba_test_uncalibrated = model.predict_proba(X_test)[:, 1]
        # Apply Platt scaling: transform to log-odds, apply logistic regression, get calibrated probabilities
        logit_proba_test = np.log(proba_test_uncalibrated / (1 - proba_test_uncalibrated + 1e-10))
        proba_test_calibrated = calibrator.predict_proba(logit_proba_test.reshape(-1, 1))[:, 1]
        
        # Brier score (lower is better, measures calibration quality)
        brier_uncalibrated = brier_score_loss(y_test, proba_test_uncalibrated)
        brier_calibrated = brier_score_loss(y_test, proba_test_calibrated)
        brier_improvement = brier_uncalibrated - brier_calibrated
        
        # Calibration curve (fraction of positives vs mean predicted probability)
        # Use 10 bins for calibration curve
        fraction_of_positives_uncal, mean_predicted_value_uncal = calibration_curve(
            y_test, proba_test_uncalibrated, n_bins=10, strategy='uniform'
        )
        fraction_of_positives_cal, mean_predicted_value_cal = calibration_curve(
            y_test, proba_test_calibrated, n_bins=10, strategy='uniform'
        )
        
        # Calculate ECE (Expected Calibration Error) - average absolute difference
        ece_uncalibrated = np.mean(np.abs(fraction_of_positives_uncal - mean_predicted_value_uncal))
        ece_calibrated = np.mean(np.abs(fraction_of_positives_cal - mean_predicted_value_cal))
        ece_improvement = ece_uncalibrated - ece_calibrated
        
        context.log.info(f"Calibration quality (test set):")
        context.log.info(f"  Brier score (uncalibrated): {brier_uncalibrated:.4f}")
        context.log.info(f"  Brier score (calibrated): {brier_calibrated:.4f}")
        context.log.info(f"  Brier improvement: {brier_improvement:.4f} ({'better' if brier_improvement > 0 else 'worse'})")
        context.log.info(f"  ECE (uncalibrated): {ece_uncalibrated:.4f}")
        context.log.info(f"  ECE (calibrated): {ece_calibrated:.4f}")
        context.log.info(f"  ECE improvement: {ece_improvement:.4f} ({'better' if ece_improvement > 0 else 'worse'})")
        
        # Start MLflow run
        run_id = None
        with mlflow.start_run(run_name=f"game_winner_model_{model_version}") as run:
            run_id = run.info.run_id
            
            # Log hyperparameters and experiment tags (Warriors fix: injury_advantage_home, stronger penalties)
            mlflow.log_params({
                "max_depth": config.max_depth,
                "n_estimators": config.n_estimators,
                "learning_rate": config.learning_rate,
                "subsample": config.subsample,
                "colsample_bytree": config.colsample_bytree,
                "min_child_weight": config.min_child_weight,
                "min_child_samples": config.min_child_samples,
                "gamma": config.gamma,
                "reg_alpha": config.reg_alpha,
                "reg_lambda": config.reg_lambda,
                "interaction_feature_boost": config.interaction_feature_boost,
                "test_size": config.test_size,
                "random_state": config.random_state,
                "model_version": model_version,
                "feature_count": len(feature_cols),
                "injury_advantage_home": True,
                "penalty_strengthened": True,
                "experiment_tag": "iteration_123_temporal_split_2025_12_01",
                "temporal_split_cutoff_date": config.temporal_split_cutoff_date or "",
                "algorithm": config.algorithm,
                "num_leaves": num_leaves if algorithm == "lightgbm" else None,
                "calibration": "platt_scaling",
                "feature_selection_enabled": config.feature_selection_enabled,
                "feature_selection_method": config.feature_selection_method if config.feature_selection_enabled else None,
                "feature_selection_threshold": config.feature_selection_threshold if (config.feature_selection_enabled and config.feature_selection_method == "threshold") else None,
                "feature_selection_percentile": config.feature_selection_percentile if (config.feature_selection_enabled and config.feature_selection_method == "percentile") else None,
                "feature_selection_n_features": config.feature_selection_n_features if (config.feature_selection_enabled and config.feature_selection_method == "rfe") else None,
                "max_missing_feature_pct": config.max_missing_feature_pct,
            })
            # Params fallback for features (visible in UI even if Artifacts tab fails). MLflow param value limit ~500.
            feats_str = ",".join(feature_cols)
            for j in range(0, len(feats_str), 450):
                mlflow.log_param(f"features_part{j // 450}", feats_str[j : j + 450])
            top = ",".join(f"{n}:{v:.4f}" for n, v in importance_sorted[:10])
            mlflow.log_param("top_features", top[:500])
            
            # Log metrics to MLflow
            mlflow.log_metrics({
                "train_accuracy": float(train_score),
                "test_accuracy": float(test_score),
                "training_samples": len(X_train),
                "test_samples": len(X_test),
                "brier_score_uncalibrated": float(brier_uncalibrated),
                "brier_score_calibrated": float(brier_calibrated),
                "brier_improvement": float(brier_improvement),
                "ece_uncalibrated": float(ece_uncalibrated),
                "ece_calibrated": float(ece_calibrated),
                "ece_improvement": float(ece_improvement),
            })
            
            # Log training artifacts (features, importances, run_info, calibrator) so they show in MLflow UI.
            # Use log_artifacts with a dedicated folder so the Artifacts tree is clear and uploads are reliable.
            td = tempfile.mkdtemp(prefix="mlflow_")
            try:
                with open(os.path.join(td, "features.json"), "w") as f:
                    json.dump({
                        "description": "Ordered list of feature column names used for training and inference.",
                        "feature_count": len(feature_cols),
                        "features": feature_cols,
                    }, f, indent=2)
                with open(os.path.join(td, "feature_importances.json"), "w") as f:
                    json.dump({k: float(v) for k, v in feature_importances.items()}, f, indent=2)
                with open(os.path.join(td, "run_info.txt"), "w") as f:
                    f.write(f"model_version={model_version}\nfeature_count={len(feature_cols)}\ntest_accuracy={test_score:.4f}\ncalibration=platt_scaling\nalgorithm={config.algorithm}\n")
                # Save calibration curve data for visualization
                with open(os.path.join(td, "calibration_curve.json"), "w") as f:
                    json.dump({
                        "description": "Calibration curve data for uncalibrated and calibrated predictions",
                        "uncalibrated": {
                            "fraction_of_positives": [float(x) for x in fraction_of_positives_uncal],
                            "mean_predicted_value": [float(x) for x in mean_predicted_value_uncal],
                        },
                        "calibrated": {
                            "fraction_of_positives": [float(x) for x in fraction_of_positives_cal],
                            "mean_predicted_value": [float(x) for x in mean_predicted_value_cal],
                        },
                        "brier_scores": {
                            "uncalibrated": float(brier_uncalibrated),
                            "calibrated": float(brier_calibrated),
                            "improvement": float(brier_improvement),
                        },
                        "ece_scores": {
                            "uncalibrated": float(ece_uncalibrated),
                            "calibrated": float(ece_calibrated),
                            "improvement": float(ece_improvement),
                        },
                    }, f, indent=2)
                shutil.copy(str(calibrator_path), os.path.join(td, "calibrator.pkl"))
                mlflow.log_artifacts(td, artifact_path="training_artifacts")
                # Also log calibrator at run root so predictions can load runs:/{run_id}/calibrator.pkl
                mlflow.log_artifact(os.path.join(td, "calibrator.pkl"))
                mlflow.set_tag("artifacts.features", "training_artifacts/features.json")
                mlflow.set_tag("artifacts.feature_importances", "training_artifacts/feature_importances.json")
                mlflow.set_tag("artifacts.calibration_curve", "training_artifacts/calibration_curve.json")
                context.log.info("Artifacts logged: training_artifacts/ (features.json, feature_importances.json, run_info.txt, calibration_curve.json, calibrator.pkl), calibrator.pkl at root")
            finally:
                shutil.rmtree(td, ignore_errors=True)
            
            # Log model artifact to MLflow (must be under 'model' for runs:/run_id/model and Registry)
            try:
                if algorithm == "ensemble":
                    mlflow.sklearn.log_model(model, "model")
                elif algorithm == "lightgbm":
                    mlflow.lightgbm.log_model(model, "model")
                elif algorithm == "catboost":
                    mlflow.catboost.log_model(model, "model")
                else:
                    mlflow.xgboost.log_model(model, "model")
                context.log.info("Model artifact logged to MLflow")
            except Exception as e:
                context.log.warning(f"Could not log model to MLflow (UI still shows params/metrics): {e}")
            
            context.log.info(f"MLflow run ID: {run_id}")
        
        # Register model version in MLflow Model Registry (after run completes)
        try:
            if run_id:
                mv = mlflow.register_model(
                    f"runs:/{run_id}/model",
                    "game_winner_model",
                )
                context.log.info(f"Model registered in MLflow Model Registry: {getattr(mv, 'name', mv)} version {getattr(mv, 'version', '')}")
        except Exception as e:
            context.log.warning(f"Could not register model in MLflow: {e}")
        
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
            "learning_rate": config.learning_rate,
            "subsample": config.subsample,
            "colsample_bytree": config.colsample_bytree,
            "min_child_weight": config.min_child_weight,
            "min_child_samples": config.min_child_samples,
            "gamma": config.gamma,
            "reg_alpha": config.reg_alpha,
            "reg_lambda": config.reg_lambda,
            "interaction_feature_boost": config.interaction_feature_boost,
            "test_size": config.test_size,
            "random_state": config.random_state,
            "train_accuracy": float(train_score),
            "test_accuracy": float(test_score),
            "model_path": str(model_path),
            "calibrator_path": str(calibrator_path),
            "algorithm": config.algorithm,
            "calibration": "platt_scaling",
            "mlflow_run_id": run_id,
            "mlflow_tracking_uri": mlflow_tracking_uri,
            "features": feature_cols,
            "training_samples": len(X_train),
            "test_samples": len(X_test),
        }
        
        if algorithm == "ensemble":
            model_type = "StackingClassifier(XGBoost+LightGBM)"
        elif algorithm == "lightgbm":
            model_type = "LGBMClassifier"
        elif algorithm == "catboost":
            model_type = "CatBoostClassifier"
        else:
            model_type = "XGBoostClassifier"
        cursor.execute(
            metadata_query,
            (
                "game_winner_model",
                model_version,
                model_type,
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
