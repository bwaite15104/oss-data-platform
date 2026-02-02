# MLflow and Feast: Source of Truth for Models and Features

**When doing model improvements, evaluation, or historical review, the LLM must use MLflow and Feast as the authoritative sources—not chat context alone.**

---

## MLflow: Model Details and History

**MLflow is the source of truth for:**
- Experiments, runs, params, metrics, artifacts
- Which features were used per run
- Model Registry (`game_winner_model`) and versions

### Access

- **UI**: http://localhost:5000 — browse experiments, runs, params, metrics, artifacts, Model Registry
- **API** (e.g. from `nba_analytics_dagster_webserver` or any Python env with `tracking_uri` to `http://mlflow:5000` or `http://localhost:5000`):

```python
from mlflow.tracking import MlflowClient

client = MlflowClient(tracking_uri="http://localhost:5000")  # or http://mlflow:5000 in Docker

# Experiment
exp = client.get_experiment_by_name("nba_game_winner_prediction")

# Recent runs (params, metrics, run_id)
runs = client.search_runs(experiment_ids=[exp.experiment_id], order_by=["start_time DESC"], max_results=20)
for r in runs:
    print(r.info.run_id, r.data.params.get("model_version"), r.data.metrics.get("test_accuracy"))
    # Features: r.data.params.get("features_part0"), "features_part1", ... or "top_features"

# Single run artifacts (features, importances)
run = client.get_run(run_id)
artifacts = client.list_artifacts(run_id=run.info.run_id)
# Important: training_artifacts/features.json, training_artifacts/feature_importances.json

# Model Registry
from mlflow import pyfunc
model_uri = "models:/game_winner_model/Production"  # or /Staging, /None, or version number
# model = pyfunc.load_model(model_uri)
```

### Key Params and Artifacts (per run)

| Location | What |
|----------|------|
| **Params** | `features_part0`, `features_part1`, … (fallback for full feature list); `top_features` (top 10 with importances); `feature_count`; `algorithm`; `model_version`; hyperparams (`max_depth`, `n_estimators`, etc.) |
| **Metrics** | `train_accuracy`, `test_accuracy`, `training_samples`, `test_samples` |
| **Artifacts** | `training_artifacts/features.json` (ordered feature list), `training_artifacts/feature_importances.json`, `training_artifacts/run_info.txt`, `training_artifacts/calibrator.pkl`; `model/` (logged model); root `calibrator.pkl` |

### When to Use MLflow

- **Model improvements**: Compare new run params/metrics/artifacts to previous runs (same experiment).
- **Validating training/eval**: Confirm new run exists, metrics and `feature_count` are sane, artifacts present.
- **Historical work**: Which features, algorithm, and metrics each run used—read from runs/params/artifacts, not from chat.

---

## Feast: Feature Definitions and Schema

**Feast (and `feature_repo/`) is the source of truth for:**
- Current feature definitions, entities, and schema
- Data sources that back feature views

### Locations

- **Definitions**: `feature_repo/features.py` (FeatureViews, e.g. `game_features`), `feature_repo/data_sources.py`, `feature_repo/entities.py`
- **Apply**: `feast apply` (from `feature_repo/` or project root)
- **UI**: http://localhost:8080 — inspect feature views and metadata

### When to Use Feast / feature_repo

- **Feature schema and list**: What features exist and how they are defined — use `features.py` and `data_sources.py`.
- **Alignment with training**: Training reads from `marts__local.mart_game_features` and `intermediate__local.int_game_momentum_features` (see `orchestration/dagster/assets/ml/training.py` and `feature_repo/data_sources.py`). Ensure `feature_repo` definitions match what ML expects.
- **Versioning**: Feast state is in the store; use Git + tags for definition history. See `feature_repo/README.md`.

---

## LLM Instructions: Model Work and Historical Context

**For model improvements, evaluation, or understanding past work:**

1. **Query MLflow** for:
   - Recent runs in `nba_game_winner_prediction`: params (`features_part*`, `top_features`, `feature_count`, `algorithm`), metrics (`test_accuracy`, `train_accuracy`), artifacts (`training_artifacts/features.json`, `feature_importances.json`).
   - Model Registry `game_winner_model` for production/staging and versions.
2. **Check `feature_repo/`** for current feature definitions (`features.py`, `data_sources.py`) and align with what training uses.
3. **Do not rely only on chat** for “which features did we use?” or “what was the last test_accuracy?”—treat **MLflow** and **Feast/feature_repo** as authoritative.

---

## Validation: New/Modified ML Training or Model Changes

After changing training code, feature sets, or model config:

1. **Run training** (Dagster):

   ```bash
   docker exec nba_analytics_dagster_webserver dagster asset materialize -f /app/definitions.py --select train_game_winner_model
   ```

2. **Verify in MLflow** (UI or API):
   - New run in `nba_game_winner_prediction` with expected `model_version`, `feature_count`, `algorithm`
   - Params `features_part*` / `top_features` and metrics `test_accuracy`, `train_accuracy`
   - Artifacts: `training_artifacts/features.json`, `feature_importances.json`
   - New version in Model Registry `game_winner_model` if registration succeeded

3. **Verify feature alignment**:
   - `feature_repo/features.py` and `data_sources.py` match the data sources used in `orchestration/dagster/assets/ml/training.py` (marts + intermediate views).

4. **Predictions** (if needed): run `generate_game_predictions` and spot-check `ml_dev.predictions`.

See also: **AGENTS.md** → “Validation by Change Type” → **New/Modified ML training or model changes**.
