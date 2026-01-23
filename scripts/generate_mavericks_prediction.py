#!/usr/bin/env python3
"""Generate prediction for Mavericks game using latest model."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from orchestration.dagster.assets.ml.predictions import generate_game_predictions, PredictionConfig
from dagster import build_op_context

if __name__ == "__main__":
    context = build_op_context()
    config = PredictionConfig(prediction_date_cutoff='2026-01-19')
    result = generate_game_predictions(context, config)
    
    print(f"Status: {result.get('status')}")
    print(f"Predictions count: {result.get('predictions_count', 0)}")
    print(f"Model ID: {result.get('model_id', 'N/A')}")
    if result.get('predictions_count', 0) > 0:
        print(f"Average confidence: {result.get('avg_confidence', 'N/A')}")
