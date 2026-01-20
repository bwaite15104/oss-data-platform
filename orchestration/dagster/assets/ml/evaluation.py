"""ML model evaluation assets for tracking prediction performance."""

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

# Import prediction and transformation assets for dependency references
from .predictions import generate_game_predictions
from orchestration.dagster.assets.transformation import game_features

logger = logging.getLogger(__name__)


class ModelEvaluationConfig(Config):
    """Configuration for model evaluation."""
    evaluation_date_cutoff: Optional[str] = Field(default=None, description="Date cutoff for evaluation (YYYY-MM-DD). None = evaluate all completed games with predictions.")
    min_predictions: int = Field(default=10, description="Minimum number of predictions required for evaluation")


@asset(
    group_name="ml_pipeline",
    description="Evaluate model performance by comparing predictions vs actual outcomes",
    deps=[generate_game_predictions, game_features],  # Depend on predictions and updated game results
    automation_condition=AutomationCondition.eager(),  # Run when predictions update OR when new game results available
)
def evaluate_model_performance(context, config: ModelEvaluationConfig) -> dict:
    """
    Evaluate model performance by comparing predictions to actual game outcomes.
    
    Calculates metrics:
    - Accuracy (overall correct predictions)
    - Precision (true positives / (true positives + false positives))
    - Recall (true positives / (true positives + false negatives))
    - F1 Score (harmonic mean of precision and recall)
    - Confusion matrix
    - Upset prediction accuracy
    
    Stores results in ml_dev.model_evaluations table.
    """
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, confusion_matrix
    except ImportError as e:
        context.log.error(f"Missing dependency: {e}. Install: pip install scikit-learn")
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
        
        context.log.info("Evaluating model performance...")
        
        # Get predictions with actual outcomes
        if config.evaluation_date_cutoff:
            date_filter = f"AND gf.game_date >= '{config.evaluation_date_cutoff}'::date"
        else:
            date_filter = ""
        
        query = f"""
            SELECT 
                p.prediction_id,
                p.model_id,
                p.game_id,
                p.predicted_value,
                p.confidence,
                gf.home_win as actual_value,
                gf.game_date
            FROM ml_dev.predictions p
            JOIN features_dev.game_features gf ON p.game_id = gf.game_id
            WHERE gf.home_win IS NOT NULL
              AND gf.home_score IS NOT NULL
              AND gf.away_score IS NOT NULL
              {date_filter}
            ORDER BY gf.game_date, p.game_id
        """
        
        cursor.execute(query)
        results = cursor.fetchall()
        
        if len(results) < config.min_predictions:
            context.log.warning(f"Only {len(results)} predictions with outcomes found. Minimum {config.min_predictions} required.")
            return {
                "status": "skipped",
                "message": f"Insufficient predictions ({len(results)} < {config.min_predictions})",
            }
        
        context.log.info(f"Evaluating {len(results)} predictions")
        
        # Extract predictions and actuals
        y_pred = [int(r['predicted_value']) for r in results]
        y_true = [int(r['actual_value']) for r in results]
        confidences = [float(r['confidence']) for r in results]
        
        # Calculate metrics
        accuracy = float(accuracy_score(y_true, y_pred))
        precision = float(precision_score(y_true, y_pred, zero_division=0))
        recall = float(recall_score(y_true, y_pred, zero_division=0))
        f1 = float(f1_score(y_true, y_pred, zero_division=0))
        
        # Confusion matrix: [TN, FP], [FN, TP]
        cm = confusion_matrix(y_true, y_pred)
        tn, fp, fn, tp = cm.ravel() if cm.size == 4 else (0, 0, 0, 0)
        
        # Upset analysis (away team wins = home_win = 0)
        upset_predictions = [i for i, (pred, actual) in enumerate(zip(y_pred, y_true)) if actual == 0]
        upset_correct = sum(1 for i in upset_predictions if y_pred[i] == y_true[i])
        upset_total = len(upset_predictions)
        upset_accuracy = float(upset_correct / upset_total) if upset_total > 0 else 0.0
        
        # Home win predictions
        home_win_predictions = [i for i, pred in enumerate(y_pred) if pred == 1]
        home_win_correct = sum(1 for i in home_win_predictions if y_pred[i] == y_true[i])
        home_win_total = len(home_win_predictions)
        home_win_accuracy = float(home_win_correct / home_win_total) if home_win_total > 0 else 0.0
        
        # Average confidence
        avg_confidence = float(sum(confidences) / len(confidences)) if confidences else 0.0
        
        # Get model info
        model_id = results[0]['model_id'] if results else None
        if model_id:
            cursor.execute(
                "SELECT model_name, model_version FROM ml_dev.model_registry WHERE model_id = %s",
                (model_id,)
            )
            model_info = cursor.fetchone()
            model_name = model_info['model_name'] if model_info else "unknown"
            model_version = model_info['model_version'] if model_info else "unknown"
        else:
            model_name = "unknown"
            model_version = "unknown"
        
        # Store evaluation results
        # First, ensure evaluation table exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ml_dev.model_evaluations (
                evaluation_id SERIAL PRIMARY KEY,
                model_id INTEGER,
                model_name VARCHAR(255),
                model_version VARCHAR(50),
                evaluation_date DATE DEFAULT CURRENT_DATE,
                total_predictions INTEGER,
                accuracy NUMERIC(5,4),
                precision NUMERIC(5,4),
                recall NUMERIC(5,4),
                f1_score NUMERIC(5,4),
                true_positives INTEGER,
                true_negatives INTEGER,
                false_positives INTEGER,
                false_negatives INTEGER,
                upset_accuracy NUMERIC(5,4),
                upset_total INTEGER,
                home_win_accuracy NUMERIC(5,4),
                home_win_total INTEGER,
                avg_confidence NUMERIC(5,4),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (model_id) REFERENCES ml_dev.model_registry(model_id)
            )
        """)
        
        # Insert evaluation
        cursor.execute("""
            INSERT INTO ml_dev.model_evaluations (
                model_id, model_name, model_version, evaluation_date,
                total_predictions, accuracy, precision, recall, f1_score,
                true_positives, true_negatives, false_positives, false_negatives,
                upset_accuracy, upset_total, home_win_accuracy, home_win_total,
                avg_confidence, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            model_id,
            model_name,
            model_version,
            datetime.now().date(),
            len(results),
            accuracy,
            precision,
            recall,
            f1,
            int(tp),
            int(tn),
            int(fp),
            int(fn),
            upset_accuracy,
            upset_total,
            home_win_accuracy,
            home_win_total,
            avg_confidence,
            datetime.now(),
        ))
        
        # Ensure prediction_results table has unique constraint
        cursor.execute("""
            DO $$ 
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint 
                    WHERE conname = 'prediction_results_prediction_id_key'
                ) THEN
                    ALTER TABLE ml_dev.prediction_results 
                    ADD CONSTRAINT prediction_results_prediction_id_key UNIQUE (prediction_id);
                END IF;
            END $$;
        """)
        
        # Update prediction_results table with actual outcomes
        for result in results:
            cursor.execute("""
                INSERT INTO ml_dev.prediction_results (
                    prediction_id, actual_value, is_correct, recorded_at
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT (prediction_id) DO UPDATE SET
                    actual_value = EXCLUDED.actual_value,
                    is_correct = EXCLUDED.is_correct,
                    recorded_at = EXCLUDED.recorded_at
            """, (
                result['prediction_id'],
                result['actual_value'],
                result['predicted_value'] == result['actual_value'],
                datetime.now(),
            ))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        context.log.info(f"Evaluation complete: Accuracy={accuracy:.4f}, Precision={precision:.4f}, Recall={recall:.4f}, F1={f1:.4f}")
        context.log.info(f"Upset accuracy: {upset_accuracy:.4f} ({upset_correct}/{upset_total})")
        
        return {
            "status": "success",
            "model_id": model_id,
            "model_version": model_version,
            "total_predictions": len(results),
            "accuracy": accuracy,
            "precision": precision,
            "recall": recall,
            "f1_score": f1,
            "confusion_matrix": {
                "true_positives": int(tp),
                "true_negatives": int(tn),
                "false_positives": int(fp),
                "false_negatives": int(fn),
            },
            "upset_accuracy": upset_accuracy,
            "upset_total": upset_total,
            "home_win_accuracy": home_win_accuracy,
            "home_win_total": home_win_total,
            "avg_confidence": avg_confidence,
        }
        
    except Exception as e:
        context.log.error(f"Model evaluation failed: {e}")
        raise
