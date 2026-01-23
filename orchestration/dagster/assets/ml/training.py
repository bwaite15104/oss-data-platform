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

# Import transformation asset for dependency
from orchestration.dagster.assets.transformation import game_features

logger = logging.getLogger(__name__)


class ModelTrainingConfig(Config):
    """Configuration for model training."""
    test_size: float = Field(default=0.2, description="Test set size (0.0-1.0)")
    random_state: int = Field(default=42, description="Random seed for reproducibility")
    max_depth: int = Field(default=6, description="XGBoost max depth")
    n_estimators: int = Field(default=200, description="XGBoost number of estimators")
    learning_rate: float = Field(default=0.05, description="XGBoost learning rate")
    subsample: float = Field(default=0.8, description="Subsample ratio of training instances")
    colsample_bytree: float = Field(default=0.8, description="Subsample ratio of columns when constructing each tree")
    min_child_weight: int = Field(default=3, description="Minimum sum of instance weight needed in a child")
    gamma: float = Field(default=0.1, description="Minimum loss reduction required to make a split")
    reg_alpha: float = Field(default=0.1, description="L1 regularization term")
    reg_lambda: float = Field(default=1.0, description="L2 regularization term")
    interaction_feature_boost: float = Field(default=1.5, description="Multiplier for interaction feature importance during training")
    model_version: Optional[str] = Field(default=None, description="Model version (auto-generated if None)")


@asset(
    group_name="ml_pipeline",
    description="Train XGBoost model for game winner prediction",
    deps=[game_features],  # Depend on game features being ready
    automation_condition=AutomationCondition.on_cron("@daily"),  # Daily retrain (can be adjusted to weekly/monthly)
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
        import pandas as pd
        import joblib
        from datetime import datetime
        import json
        from sqlalchemy import create_engine
    except ImportError as e:
        context.log.error(f"Missing dependency: {e}. Install: pip install scikit-learn xgboost pandas joblib sqlalchemy")
        raise
    
    try:
        # Database connection using SQLAlchemy (better pandas compatibility)
        database = os.getenv("POSTGRES_DB", "nba_analytics")
        host = os.getenv("POSTGRES_HOST", "localhost")
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
        
        context.log.info("Loading training features from features_dev.game_features...")
        
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
        
        # Load features - only use completed games with actual results
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
                -- Star player return features (removed redundant: home_has_star_return, away_has_star_return, star_return_advantage)
                gf.home_star_players_returning,
                gf.home_key_players_returning,
                gf.home_extended_returns,
                gf.home_total_return_impact,
                gf.home_max_days_since_return,
                gf.away_star_players_returning,
                gf.away_key_players_returning,
                gf.away_extended_returns,
                gf.away_total_return_impact,
                gf.away_max_days_since_return,
                gf.return_impact_diff,
                -- Injury features
                gf.home_star_players_out,
                gf.home_key_players_out,
                gf.home_star_players_doubtful,
                gf.home_key_players_doubtful,
                gf.home_star_players_questionable,
                gf.home_injury_impact_score,
                gf.home_injured_players_count,
                gf.home_has_key_injury,
                gf.away_star_players_out,
                gf.away_key_players_out,
                gf.away_star_players_doubtful,
                gf.away_key_players_doubtful,
                gf.away_star_players_questionable,
                gf.away_injury_impact_score,
                gf.away_injured_players_count,
                gf.away_has_key_injury,
                gf.injury_impact_diff,
                gf.star_players_out_diff,
                -- Feature interactions: Injury impact with other key features
                gf.injury_impact_x_form_diff,
                gf.away_injury_x_form,
                gf.home_injury_x_form,
                gf.home_injury_impact_ratio,
                gf.away_injury_impact_ratio,
                -- Explicit injury penalty features (v3 - fixed encoding)
                gf.home_injury_penalty_severe,
                gf.away_injury_penalty_severe,
                gf.home_injury_penalty_absolute,
                gf.away_injury_penalty_absolute,
                -- NEW: Momentum/Streak features (Phase 1)
                COALESCE(mf.home_win_streak, 0) AS home_win_streak,
                COALESCE(mf.home_loss_streak, 0) AS home_loss_streak,
                COALESCE(mf.away_win_streak, 0) AS away_win_streak,
                COALESCE(mf.away_loss_streak, 0) AS away_loss_streak,
                -- NEW: Momentum score (weighted recent wins by recency)
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
                COALESCE(mf.home_advantage, 0) AS home_advantage,
                -- Target variable
                gf.home_win
            FROM marts__local.mart_game_features gf
            LEFT JOIN intermediate__local.int_game_momentum_features mf ON mf.game_id = gf.game_id
            WHERE gf.game_date IS NOT NULL
              AND gf.home_score IS NOT NULL
              AND gf.away_score IS NOT NULL
              AND gf.home_win IS NOT NULL
              AND gf.game_date::date >= '2010-10-01'::date  -- Only use last 15 years of data
              AND gf.game_date::date < '{today_str}'::date  -- Exclude today's games for prediction testing
              AND gf.game_date::date <= '2026-01-21'::date  -- Train on data through 1/21/2026
            ORDER BY gf.game_date
        """
        
        # Use SQLAlchemy engine for pandas (better compatibility)
        df = pd.read_sql_query(query, engine)
        
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
        
        # Identify interaction features for importance boosting
        interaction_features = [col for col in feature_cols if '_x_' in col or '_ratio' in col or 'injury' in col.lower()]
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
            tree_method='hist',  # Faster training
            early_stopping_rounds=20,  # Stop if no improvement for 20 rounds
        )
        
        context.log.info("Training XGBoost model with improved hyperparameters...")
        context.log.info(f"Hyperparameters: max_depth={config.max_depth}, n_estimators={config.n_estimators}, "
                        f"learning_rate={config.learning_rate}, subsample={config.subsample}, "
                        f"colsample_bytree={config.colsample_bytree}")
        
        # Use validation set for early stopping
        X_train_fit, X_val, y_train_fit, y_val = train_test_split(
            X_train, y_train, test_size=0.15, random_state=config.random_state, stratify=y_train
        )
        sample_weights_fit = sample_weights.loc[X_train_fit.index] if len(sample_weights) > 0 else None
        
        model.fit(
            X_train_fit, 
            y_train_fit,
            sample_weight=sample_weights_fit,
            eval_set=[(X_val, y_val)],
            verbose=False
        )
        
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
            "learning_rate": config.learning_rate,
            "subsample": config.subsample,
            "colsample_bytree": config.colsample_bytree,
            "min_child_weight": config.min_child_weight,
            "gamma": config.gamma,
            "reg_alpha": config.reg_alpha,
            "reg_lambda": config.reg_lambda,
            "interaction_feature_boost": config.interaction_feature_boost,
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
