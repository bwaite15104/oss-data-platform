"""Script to retrain the ML model with updated features."""

import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from orchestration.dagster.assets.ml.training import train_game_winner_model, ModelTrainingConfig
from dagster import build_op_context

def main():
    print("Retraining ML model with updated features...")
    
    # Create context and config
    context = build_op_context()
    config = ModelTrainingConfig(
        test_size=0.2,
        random_state=42,
        max_depth=6,
        n_estimators=100,
    )
    
    try:
        result = train_game_winner_model(context, config)
        
        print("\n" + "="*60)
        print("Training Results:")
        print("="*60)
        print(f"Status: {result['status']}")
        print(f"Model Version: {result['model_version']}")
        print(f"Train Accuracy: {result['train_accuracy']:.4f}")
        print(f"Test Accuracy: {result['test_accuracy']:.4f}")
        print(f"Training Samples: {result['training_samples']}")
        print(f"Test Samples: {result['test_samples']}")
        print(f"Features Count: {len(result['features'])}")
        print(f"Model Path: {result['model_path']}")
        print("="*60)
        
        return 0
    except Exception as e:
        print(f"Error training model: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
