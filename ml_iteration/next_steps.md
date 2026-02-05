# ML Iteration - Next Steps

## Current Status

**Starting fresh** after data infrastructure refactor (momentum feature groups).

- **Best historical accuracy**: ~0.66 (test), ~0.75 target
- **Current feature count**: 800+ columns in `marts.mart_game_features`
- **Data infrastructure**: Fully materialized with new feature group architecture

## Next Steps (Prioritized)

### 1. Baseline Model Training (HIGH PRIORITY)
- Train model with current feature set to establish new baseline
- Command: `docker exec nba_analytics_dagster_webserver dagster asset materialize -f /app/definitions.py --select train_game_winner_model`
- Check MLflow (http://localhost:5000) for results
- Record: test_accuracy, train_accuracy, feature_count, top_features

### 2. Feature Importance Analysis (HIGH PRIORITY)
- Review feature importances from MLflow artifacts
- Identify which of the 800+ features are actually predictive
- Consider feature selection to reduce noise

### 3. Algorithm Comparison (MEDIUM PRIORITY)
- Current: XGBoost (default)
- Try: LightGBM, CatBoost, ensemble methods
- Compare accuracy and training time

### 4. Hyperparameter Tuning (MEDIUM PRIORITY)
- After baseline established, tune key parameters
- Consider Optuna for automated search

---

## Key Learnings from Previous Iterations

Archived in `ml_iteration/archive_2026-02-02/`. Summary:

1. **Feature engineering** had more impact than hyperparameter tuning
2. **Regularization** helps reduce overfitting but can hurt test accuracy
3. **Feature selection** with threshold 0.001 removed useful features
4. **Algorithm**: XGBoost and LightGBM performed similarly (~0.65 test accuracy)
5. **Data quality**: Injury data had quality issues (zeros, missing values)
6. **Infrastructure**: SQLMesh materialization issues blocked feature availability

## Constraints

- All data sources must be FREE (no paid APIs)
- Target: test_accuracy >= 0.75
- One focused change per iteration
