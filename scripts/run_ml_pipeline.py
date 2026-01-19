"""Script to run ML pipeline steps: training, predictions, evaluation, and feature analysis."""

import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from orchestration.dagster.assets.ml.training import train_game_winner_model, ModelTrainingConfig
from orchestration.dagster.assets.ml.predictions import generate_game_predictions, PredictionConfig
from orchestration.dagster.assets.ml.evaluation import evaluate_model_performance
from orchestration.dagster.assets.ml.feature_analysis import analyze_feature_correlations, FeatureAnalysisConfig
from dagster import build_op_context

def main():
    context = build_op_context()
    
    print("=" * 60)
    print("ML Pipeline Execution")
    print("=" * 60)
    
    # Step 1: Train model (excludes 1/18 data)
    print("\n[1/4] Training model (excluding 1/18 data)...")
    try:
        train_config = ModelTrainingConfig()
        train_result = train_game_winner_model(context, train_config)
        print(f"✅ Model trained: {train_result['model_version']}")
        print(f"   Train accuracy: {train_result['train_accuracy']:.4f}")
        print(f"   Test accuracy: {train_result['test_accuracy']:.4f}")
        print(f"   Features: {len(train_result['features'])}")
    except Exception as e:
        print(f"❌ Training failed: {e}")
        return
    
    # Step 2: Feature correlation analysis
    print("\n[2/4] Running feature correlation analysis...")
    try:
        feature_config = FeatureAnalysisConfig()
        feature_result = analyze_feature_correlations(context, feature_config)
        print(f"✅ Feature analysis complete")
        print(f"   Total features: {feature_result['total_features']}")
        print(f"   High correlation pairs: {feature_result['high_correlation_pairs']}")
        print(f"   Low importance features: {feature_result['low_importance_features']}")
        print(f"   Recommendations: {feature_result['recommendations_count']}")
        if feature_result['top_correlated_pairs']:
            print("\n   Top correlated pairs:")
            for pair in feature_result['top_correlated_pairs'][:5]:
                print(f"     {pair['feature_1']} <-> {pair['feature_2']}: {pair['correlation']:.3f}")
    except Exception as e:
        print(f"⚠️  Feature analysis failed: {e}")
        # Continue anyway
    
    # Step 3: Generate predictions for 1/18
    print("\n[3/4] Generating predictions for 1/18 game...")
    try:
        pred_config = PredictionConfig(prediction_date_cutoff="2026-01-18")
        pred_result = generate_game_predictions(context, pred_config)
        print(f"✅ Predictions generated: {pred_result['predictions_count']} games")
        print(f"   Model version: {pred_result['model_version']}")
    except Exception as e:
        print(f"❌ Prediction generation failed: {e}")
        return
    
    # Step 4: Evaluate predictions
    print("\n[4/4] Evaluating predictions...")
    try:
        eval_result = evaluate_model_performance(context)
        print(f"✅ Evaluation complete")
        print(f"   Accuracy: {eval_result.get('accuracy', 'N/A')}")
        print(f"   Precision: {eval_result.get('precision', 'N/A')}")
        print(f"   Recall: {eval_result.get('recall', 'N/A')}")
        print(f"   F1 Score: {eval_result.get('f1_score', 'N/A')}")
        if 'upset_accuracy' in eval_result:
            print(f"   Upset accuracy: {eval_result['upset_accuracy']}")
        if 'correct_predictions' in eval_result:
            print(f"   Correct predictions: {eval_result['correct_predictions']}/{eval_result.get('total_predictions', 'N/A')}")
    except Exception as e:
        print(f"⚠️  Evaluation failed: {e}")
    
    print("\n" + "=" * 60)
    print("Pipeline execution complete!")
    print("=" * 60)

if __name__ == "__main__":
    main()
