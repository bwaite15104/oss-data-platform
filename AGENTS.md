# OSS Data Platform - Cursor Rules

## Project Overview
NBA sports betting data platform. Goal: Build ML models to predict game outcomes for betting.

## ⚠️ CRITICAL: LLM Must Automatically Validate Changes

**When making ANY change to assets, jobs, pipelines, schemas, or tables, the LLM MUST automatically validate using available tools:**

1. **Run Dagster CLI commands** using `run_terminal_cmd` to execute assets/jobs
2. **Query warehouse** using `run_terminal_cmd` with `python scripts/db_query.py` to verify data
3. **Check execution output** for success indicators ("RUN_SUCCESS", "LOADED and contains no failed jobs")
4. **Only mark changes complete** after validation succeeds - if validation fails, fix issues before proceeding

**The LLM should NOT skip validation or assume changes work. Always run the validation commands after making changes.**

## 🚫 CONSTRAINT: Free Services Only
**All tools, APIs, and services MUST be free.** No paid subscriptions, API costs, or premium tiers.
- ✅ Free: NBA CDN, open-source tools, free API tiers, scraping (if legal)
- ❌ No: Paid APIs, premium subscriptions, usage-based pricing

## Quick Reference
- **Dagster UI**: http://localhost:3000
- **MLflow UI**: http://localhost:5000 (model registry, experiments, versioning)
- **Feast UI**: http://localhost:8080 (feature store, feature views)
- **Metabase**: http://localhost:3002
- **PostgreSQL**: localhost:5432 (postgres/postgres/nba_analytics)
- **Database**: `nba_analytics` with layered schemas (raw_dev → staging_dev → marts_dev → features_dev → ml_dev)

## Key Commands
```bash
make docker-up         # Start all services
make docker-down       # Stop services
make dagster-dev       # Start local Dagster (uses Docker Postgres)
make db-counts         # Show row counts in raw_dev
make db-schemas        # List all database schemas
make generate-configs  # Generate tool configs from ODCS
```

## Database Query Utility
```bash
# Quick validation queries (use this to check data)
python scripts/db_query.py --counts raw_dev
python scripts/db_query.py --schemas
python scripts/db_query.py "SELECT * FROM raw_dev.teams LIMIT 5"
python scripts/db_query.py "SELECT count(*) FROM raw_dev.games"
```

## Documentation
For detailed context, reference these docs in `.cursor/docs/`:

| Doc | When to Reference |
|-----|-------------------|
| `architecture.md` | Understanding system design, data flow |
| `contracts.md` | ODCS contracts, schema definitions, contract loader |
| `ingestion.md` | Adding new data sources, dlt pipelines |
| `database.md` | Schema structure, tables, useful queries |
| `ml-features.md` | ML model features, betting predictions |
| `mlflow-and-feast-context.md` | Model improvements, validating training/eval, understanding model and feature history |
| `commands.md` | Full list of make commands, docker ops |
| `sqlmesh-performance-optimization.md` | Slow materializations, incremental models, indexing, chunked backfill |
| `sqlmesh-troubleshooting-quick-reference.md` | Quick diagnosis and fixes for slow SQLMesh models |
| `game-features-backfill-strategy.md` | Partial vs full backfill when adding columns to game features |

## SQLMesh and Table Operational Best Practices

**When creating or modifying SQLMesh models and tables, the LLM MUST follow these operational best practices so materializations stay fast and maintainable.**

### 1. Prefer Incremental Refreshes for Large or Time-Series Data

- **Use `kind INCREMENTAL_BY_TIME_RANGE`** (with `time_column`, `start`, `cron`, `batch_size`) for models that:
  - Are time-series (e.g. game_date, created_at)
  - Would otherwise scan or join over full history (e.g. rolling stats, lookups)
  - Are large enough that a full refresh is slow (>5 min) or risky (temp space, timeouts)
- **Avoid `kind FULL`** for such models unless the dataset is small and will stay small.
- **Use `kind VIEW`** when the model is a simple projection over another model (e.g. `features_dev.game_features` over `marts.mart_game_features`) so there is no separate materialization to backfill.

**Reference:** `.cursor/docs/sqlmesh-performance-optimization.md` for conversion patterns and examples.

### 2. Indexing

**CRITICAL: Indexes must be created for ALL base tables, not just SQLMesh snapshots.**

- **Base tables (raw_dev, staging, etc.)**: 
  - **ODCS contracts define indexes** in `contracts/schemas/*.yml` files (e.g., `nba_games.yml` has an `indexes:` section).
  - **Ensure indexes are created** when tables are first created. Run `python scripts/ensure_contract_indexes.py` to create all indexes defined in contracts. If DLT/ODCS doesn't automatically create indexes from contracts, add them manually via migration scripts or `storage/postgres/init.sql`.
  - **Common index patterns**: Create indexes on `game_id`, `team_id`, `player_id`, `game_date`, and composite indexes like `(team_id, game_date DESC)` for time-series queries.
  - **After adding indexes**, run `ANALYZE <schema>.<table>` to update query planner statistics.
- **SQLMesh snapshot tables**:
  - **Dagster path**: When materializing via Dagster assets, `create_indexes_for_sqlmesh_snapshot` runs automatically after each model (see `orchestration/dagster/assets/transformation/sqlmesh_transforms.py`).
  - **Direct sqlmesh plan path**: When running `sqlmesh plan` directly (e.g. `scripts/backfill_all_models_chunked.py`), indexes are **NOT** created by SQLMesh. You **must** either: (1) run `python scripts/create_sqlmesh_indexes.py` after materialization, or (2) use the chunked backfill script (it runs the index script after each chunk by default; do **not** use `--no-create-indexes` unless you have a reason). Without indexes, queries do sequential scans and can take 30+ minutes per model.
- **If snapshot tables already exist without indexes**: Run `python scripts/create_sqlmesh_indexes.py` once (locally or `docker exec ... python /app/scripts/create_sqlmesh_indexes.py`) to create indexes on all existing snapshot tables.
- **Staging views**: Note that `staging.stg_*` tables are often **views** (e.g., `stg_games` is a view over `raw_dev.games`). Index the underlying base tables in `raw_dev`, not the views themselves.
- **Check for missing indexes** when materializations are slow: `python scripts/db_query.py "SELECT indexname FROM pg_indexes WHERE schemaname = '<schema>'"`. Add indexes on join and time columns, then run `ANALYZE <schema>.<table>`.

**Reference:** `.cursor/docs/sqlmesh-troubleshooting-quick-reference.md` (index creation examples).

### 3. Avoid Query Patterns That Cause Slow Materializations

- **Avoid range joins over full history** (e.g. joining all prior rows per entity without a time bound). Prefer:
  - **INCREMENTAL_BY_TIME_RANGE** so each chunk only processes a time window (`@start_ds` / `@end_ds`), and
  - **LATERAL joins or window functions within that chunk** (e.g. “latest previous row” per game in one year).
- **Avoid unbounded LATERAL/CROSS JOIN** over the full table. If you need “latest previous record” per row, either:
  - Pre-compute a **lookup table** (incremental, chunked by time) and join to it, or
  - Make the model **incremental** and use LATERAL only within the current interval.
- **Monitor temp space** when testing: large `pg_stat_database.temp_bytes` or long runtimes indicate inefficient patterns. See `.cursor/docs/sqlmesh-performance-optimization.md` for diagnosis.

### 4. Chunked Backfill When There Are Known Speed Limitations

- **If a model is incremental but needs full-history backfill**, provide or use a **chunked backfill** (e.g. by year or 2-year windows) so that:
  - Progress is visible (chunk by chunk).
  - Failures are isolated to one chunk and can be retried or skipped.
  - Temp space and runtime stay bounded per chunk.
- **Use existing scripts** where they apply:
  - `scripts/backfill_incremental_chunked.py <model>` for any INCREMENTAL_BY_TIME_RANGE model (full or custom range).
  - `scripts/backfill_game_features_range.py` for partial backfill of `marts.mart_game_features` (e.g. after adding a column; default last 5 years).
- **When adding a new incremental model** that may need full-history backfill, add or document a chunked backfill approach (e.g. in `.cursor/docs/` or a script similar to `backfill_incremental_chunked.py`).

**Reference:** `.cursor/docs/game-features-backfill-strategy.md`, `.cursor/docs/lookup-table-chunked-backfill.md`.

### 5. Checklist for New or Heavily Modified Tables/Models

Before considering a new or heavily modified SQLMesh model complete, the LLM should ensure:

- [ ] **Model kind** is appropriate: INCREMENTAL_BY_TIME_RANGE for large/time-series, VIEW for simple projections, FULL only if small and not time-series.
- [ ] **Indexes** exist (or are created by the asset) on join and time columns; run `ANALYZE` after adding indexes.
- [ ] **Query pattern** does not rely on unbounded range joins or full-table LATERAL over the whole history; use chunking or a lookup table if needed.
- [ ] **Chunked backfill** is available or documented if the model is incremental and may need full or partial backfill (e.g. after adding columns).
- [ ] **Docs** in `.cursor/docs/` are updated if you introduce a new backfill script or a new performance pattern (e.g. in `sqlmesh-performance-optimization.md` or `sqlmesh-troubleshooting-quick-reference.md`).

**Reference:** `.cursor/docs/sqlmesh-performance-optimization.md`, `.cursor/docs/sqlmesh-troubleshooting-quick-reference.md`.

---

## Model and Feature Context: Use MLflow + Feast

**For model-related work (improvements, evaluation, historical review), the LLM must use:**
- **MLflow** (http://localhost:5000) as the source of truth for model state: runs, params, metrics, artifacts (`training_artifacts/features.json`, `feature_importances.json`), and Model Registry `game_winner_model`.
- **Feast** / **`feature_repo/`** (`features.py`, `data_sources.py`) and Feast UI (http://localhost:8080) as the source of truth for current feature definitions and schema.

**Do not rely only on chat context** for "which features were used," "what was the last test_accuracy," or "what we changed last"—query MLflow and inspect `feature_repo/` instead. See `.cursor/docs/mlflow-and-feast-context.md` for how to inspect MLflow (UI + `MlflowClient`) and Feast, and for validation steps.

## Code Locations
- `ingestion/dlt/pipelines/` - dlt data pipelines
- `ingestion/dlt/config.py` - NBA CDN endpoints, constants
- `orchestration/dagster/assets/` - Dagster asset definitions
- `orchestration/dagster/assets/ml/` - ML assets (training, predictions); **MLflow** for model versioning
- `feature_repo/` - **Feast** feature store (entities, data sources, feature views). See `feature_repo/README.md` for **versioning** (edit → `feast apply`; use Git + tags for history).
- `transformation/sqlmesh/` - SQL transformations
- `contracts/schemas/` - ODCS data contracts
- `scripts/db_query.py` - Database query utility

---

## ✅ REQUIRED: LLM Automatic Validation

**⚠️ CRITICAL: The LLM MUST automatically validate changes using available tools (run_terminal_cmd). Do not skip this step.**

**After making ANY change that impacts tables, assets, jobs, or warehouse data, the LLM MUST:**
1. Execute Dagster CLI commands to run the changed asset/job
2. Query the warehouse to verify data loaded correctly
3. Check execution logs for success/failure
4. Only proceed if validation succeeds - otherwise fix issues and re-validate

**Validation should happen automatically as part of the change, not as a separate manual step.**

This includes (but not limited to):
- **New or modified Dagster assets**
- **New or modified Dagster jobs/schedules**
- **Changes to dlt pipelines or resources**
- **Database schema/table changes**
- **Contract/schema modifications**
- **Config changes affecting data ingestion/transformation**
- **SQLMesh model changes**

### Standard Validation Workflow (LLM Should Execute Automatically)

**The LLM must execute these steps automatically after making changes:**

**1. Run the change using Dagster CLI (via run_terminal_cmd):**
```bash
# For assets:
docker exec nba_analytics_dagster_webserver dagster asset materialize -f /app/definitions.py --select <asset_name>

# For jobs:
docker exec nba_analytics_dagster_webserver dagster job execute -f /app/definitions.py -j <job_name>

# Local Dagster (if using make dagster-dev):
dagster asset materialize -f definitions.py --select <asset_name>
dagster job execute -f definitions.py -j <job_name>
```

**2. Verify data in warehouse (via run_terminal_cmd with db_query.py):**
```bash
# Check row counts
python scripts/db_query.py --counts raw_dev
python scripts/db_query.py "SELECT count(*) FROM raw_dev.<table_name>"

# Sample data
python scripts/db_query.py "SELECT * FROM raw_dev.<table_name> LIMIT 5"

# Verify structure
python scripts/db_query.py --schemas
python scripts/db_query.py --tables raw_dev
```

**3. Check execution logs in terminal output:**
- Verify presence of: `"LOADED and contains no failed jobs"` or `"RUN_SUCCESS"`
- Confirm no errors in Dagster output
- Verify expected rows were processed (counts should be reasonable, not zero unless expected)
- **If validation fails, the LLM must fix issues and re-run validation before proceeding**

### Validation by Change Type (LLM Must Execute These)

**The LLM should automatically run these validation commands after making the corresponding changes:**

#### New/Modified Ingestion Asset
**After creating/modifying an ingestion asset, LLM must run:**
```bash
# 1. Run asset via Dagster CLI
docker exec nba_analytics_dagster_webserver dagster asset materialize -f /app/definitions.py --select <asset_name>

# 2. Verify table exists and has data
python scripts/db_query.py "SELECT count(*) FROM raw_dev.<table_name>"
python scripts/db_query.py "SELECT * FROM raw_dev.<table_name> LIMIT 5"

# 3. Check for errors (should see "RUN_SUCCESS" or "LOADED and contains no failed jobs")
```

#### New/Modified Dagster Job or Schedule
**After creating/modifying a job or schedule, LLM must run:**
```bash
# 1. Verify job exists
docker exec nba_analytics_dagster_webserver dagster job list -f /app/definitions.py

# 2. Execute the job
docker exec nba_analytics_dagster_webserver dagster job execute -f /app/definitions.py -j <job_name>

# 3. Verify all assets in job completed successfully
# Check logs for each asset materialization

# 4. Validate affected tables
python scripts/db_query.py --counts raw_dev
```

#### New/Modified dlt Resource or Pipeline
**After creating/modifying a dlt resource or pipeline, LLM must run:**
```bash
# 1. Test extraction locally (optional, for debugging)
python -c "from ingestion.dlt.pipelines.nba_stats import <resource_name>; print(list(<resource_name>())[:3])"

# 2. Run full pipeline via Dagster asset
docker exec nba_analytics_dagster_webserver dagster asset materialize -f /app/definitions.py --select <asset_name>

# 3. Validate data loaded correctly
python scripts/db_query.py --counts raw_dev
python scripts/db_query.py "SELECT * FROM raw_dev.<table_name> LIMIT 5"
```

#### Schema/Contract Changes
**After modifying schemas or contracts, LLM must run:**
```bash
# 1. Run affected asset to create/update table
docker exec nba_analytics_dagster_webserver dagster asset materialize -f /app/definitions.py --select <asset_name>

# 2. Verify schema exists
python scripts/db_query.py --schemas

# 3. Check table structure matches contract
python scripts/db_query.py "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = 'raw_dev' AND table_name = '<table>' ORDER BY ordinal_position"
```

#### Database Changes (init.sql, schema changes)
**After database changes, LLM must run:**
```bash
# 1. Restart services to apply changes
docker-compose down -v && docker-compose up -d

# 2. Verify schemas created
python scripts/db_query.py --schemas

# 3. Verify tables exist
python scripts/db_query.py --tables raw_dev
python scripts/db_query.py --tables staging_dev
python scripts/db_query.py --tables marts_dev
python scripts/db_query.py --tables features_dev
python scripts/db_query.py --tables ml_dev

# 4. If schema changed, run affected assets
docker exec nba_analytics_dagster_webserver dagster asset materialize -f /app/definitions.py --select <affected_assets>
```

#### SQLMesh Model Changes
**After SQLMesh model changes, LLM must run:**
```bash
# 1. Plan changes
make sqlmesh-plan

# 2. Apply changes
make sqlmesh-run

# 3. Verify data in target schemas
python scripts/db_query.py "SELECT count(*) FROM staging_dev.<table>"
python scripts/db_query.py "SELECT count(*) FROM marts_dev.<table>"
```

#### New/Modified ML Training or Model Changes
**After creating/modifying ML training assets, feature sets, or model config, LLM must run:**
```bash
# 1. Run training via Dagster
docker exec nba_analytics_dagster_webserver dagster asset materialize -f /app/definitions.py --select train_game_winner_model

# 2. Verify in MLflow (UI http://localhost:5000 or MlflowClient): new run in nba_game_winner_prediction,
#    params (features_part*, top_features, feature_count, algorithm), metrics (test_accuracy, train_accuracy),
#    artifacts (training_artifacts/features.json, feature_importances.json), Model Registry game_winner_model

# 3. Verify feature alignment: feature_repo/features.py and data_sources.py match sources used in
#    orchestration/dagster/assets/ml/training.py (marts + intermediate views)

# 4. If needed, run predictions and spot-check ml_dev.predictions
docker exec nba_analytics_dagster_webserver dagster asset materialize -f /app/definitions.py --select generate_game_predictions
python scripts/db_query.py "SELECT * FROM ml_dev.predictions ORDER BY created_at DESC LIMIT 5"
```
See `.cursor/docs/mlflow-and-feast-context.md` for detailed MLflow/Feast usage and validation.

### LLM Validation Checklist (Must Complete Automatically)

**The LLM MUST automatically verify these items after making changes (using run_terminal_cmd):**

- [ ] Execute Dagster CLI command - check output shows success (no errors)
- [ ] Query warehouse to verify tables exist in expected schemas
- [ ] Query warehouse to verify row counts are reasonable (not zero unless expected)
- [ ] Query warehouse to verify data structure matches contracts/schemas
- [ ] Query warehouse to sample data and verify it looks correct
- [ ] Parse terminal output to confirm no errors in Dagster logs
- [ ] If job/schedule changed, execute job and verify it completes successfully

**If ANY validation fails, the LLM must:**
1. Identify the issue from terminal output
2. Fix the problem
3. Re-run validation commands
4. Only proceed once all validations pass

**The LLM should NOT skip validation steps or assume changes work without testing.**

---

## 📋 REQUIRED: Keep Contracts Updated

**When adding new schemas or modifying existing ones, ALWAYS recompose contracts:**

```bash
# After ANY change to contracts/schemas/*.yml:
make compose-contracts

# Verify all contracts exist
ls contracts/contracts/
```

### Contract Workflow
1. Edit schema in `contracts/schemas/<name>.yml`
2. Run `make compose-contracts` to generate composed contract
3. Verify `contracts/contracts/<name>.yml` was created/updated
4. If adding new asset, also update `configs/odcs/datasets.yml`

### Current Contracts (must match schemas/)
```
contracts/schemas/           contracts/contracts/
├── nba_betting_odds.yml  → ├── nba_betting_odds.yml
├── nba_boxscores.yml     → ├── nba_boxscores.yml
├── nba_games.yml         → ├── nba_games.yml
├── nba_players.yml       → ├── nba_players.yml
├── nba_teams.yml         → ├── nba_teams.yml
├── nba_todays_games.yml  → ├── nba_todays_games.yml
└── ...                   → └── ...
```

---

## ⚠️ IMPORTANT: Documentation Maintenance

**After making changes, UPDATE THE RELEVANT DOCS in `.cursor/docs/`:**

| Change Type | Docs to Update |
|-------------|----------------|
| Database schema changes | `database.md` |
| New tables/schemas | `database.md`, `architecture.md` |
| New make commands | `commands.md`, this file |
| New data sources/endpoints | `ingestion.md`, `config.py` |
| New contracts/schemas | `contracts.md` |
| New assets/pipelines | `ingestion.md`, `architecture.md` |
| ML features/models | `ml-features.md` |
| New SQLMesh models / slow tables / backfill scripts | `sqlmesh-performance-optimization.md`, `sqlmesh-troubleshooting-quick-reference.md`, `game-features-backfill-strategy.md` as needed |
| Config changes | `commands.md` (env vars), relevant doc |

**Checklist after significant changes:**
1. [ ] Update relevant `.cursor/docs/*.md` files
2. [ ] Update this `.cursorrules` quick reference if needed
3. [ ] Update `README.md` if user-facing workflow changed
4. [ ] Add/update docstrings in code

**Keep docs in sync with reality - outdated docs are worse than no docs.**

---

## 🗺️ Development Roadmap

### ✅ Phase 1: ML Pipeline (Current Focus)

**Status**: ML training and prediction assets created. Next: Validate and test.

**Completed:**
- ✅ Training asset (`train_game_winner_model`) - XGBoost model training
- ✅ Prediction asset (`generate_game_predictions`) - Generate predictions for upcoming games
- ✅ ML infrastructure - `ml_dev` schema with model_registry, predictions, betting_results tables

**Next Immediate Steps:**
1. [ ] Validate ML assets - Run training asset, verify model saved and registered in `ml_dev.model_registry`
2. [ ] Test predictions - Generate predictions for upcoming games, verify stored in `ml_dev.predictions`
3. [ ] Model evaluation - Track accuracy, compare predictions vs. actuals

**Reference**: See `.cursor/docs/ml-features.md` for detailed ML feature documentation.

### 🔮 Phase 2: Expand ML Pipeline (Future Enhancements)

**Priority**: After Phase 1 validation is complete.

**Multiple Models:**
- [ ] **Spread prediction model** - Predict point spread outcomes (requires historical odds)
- [ ] **Over/Under model** - Predict total points over/under (requires historical odds)
- [ ] **Player prop models** - Individual player performance predictions

**Model Evaluation & Comparison:**
- [x] **Model evaluation asset** - Compare predictions vs. actual outcomes, calculate metrics (accuracy, precision, recall, F1) ✅ COMPLETE
- [ ] **A/B testing** - Compare different algorithms (XGBoost vs. Neural Networks vs. Logistic Regression)
- [ ] **Feature importance analysis** - Understand which features drive predictions (SHAP values)
- [x] **Backtesting framework** - Historical performance simulation, P&L tracking ✅ COMPLETE (via prediction_date_cutoff parameter)

**Advanced MLOps:**
- [ ] **Hyperparameter tuning** - Automated optimization using Optuna/Hyperopt
- [ ] **Model monitoring** - Track prediction drift, feature drift over time
- [ ] **Automated retraining triggers** - Retrain when accuracy drops below threshold
- [ ] **Model explainability** - SHAP values, feature contributions per prediction

**Reference**: See `.cursor/docs/ml-features.md` section "Future ML Enhancements".

### 📊 Phase 3: Data Quality Integration (After ML Pipeline)

**Priority**: After ML pipeline is validated and running.

**Baselinr Integration:**
- [ ] **Regenerate Baselinr config** - Fix `configs/generated/baselinr/baselinr_config.yml`
  - Update database name (`oss_data_platform` → `nba_analytics`)
  - Update table references to `raw_dev.*` schemas (currently references wrong tables)
  - Remove outdated `customer_orders` table references
  - Add quality rules for NBA data (nullability, ranges, uniqueness)
- [ ] **Wire quality assets** - Ensure `quality` assets load correctly in Dagster definitions
- [ ] **Quality checks on features** - Monitor `features_dev.*` tables for drift, anomalies
- [ ] **Quality checks on predictions** - Validate prediction confidence, feature completeness

**Data Validation:**
- [ ] **Schema validation** - Ensure features match training schema before model input
- [ ] **Data freshness checks** - Alert when features are stale (games older than X days)
- [ ] **Missing feature detection** - Identify games with incomplete features

**Reference**: See `.cursor/docs/ml-features.md` section "Data Quality Integration".

### 📋 Ongoing: Data Gaps (Free Sources Only)

**Constraint**: All data sources must be FREE. No paid APIs.

**Remaining Gaps:**
- [ ] **Historical betting odds** - Building daily via `nba_betting_odds` asset (long-term accumulation)
- [ ] **Advanced stats** - Calculate from existing data (Pace, OffRtg, DefRtg, eFG%, TS%) - add to SQLMesh transformations
- [ ] **Rest/travel data** - Calculate from schedule (back-to-backs, travel distance) - add to feature engineering

**Reference**: See `.cursor/docs/ml-features.md` section "Remaining Data Gaps".

### 📊 Phase 4: Observability & Dashboards

**Priority**: After model evaluation is working. Provides visibility into model performance and data quality.

**Metabase Dashboards:**
- [ ] **Model Performance Dashboard** - Track accuracy, precision, recall, F1 over time
- [ ] **Prediction Analysis Dashboard** - Predictions vs actuals, confidence distributions, upset analysis
- [ ] **Feature Quality Dashboard** - Feature completeness, data freshness, missing values
- [ ] **Data Ingestion Dashboard** - Ingestion status, row counts, last update times
- [ ] **Betting Results Dashboard** - P&L tracking, win rates by confidence level, ROI analysis

**Grafana Dashboards (if needed for real-time monitoring):**
- [ ] **Real-time prediction metrics** - Live accuracy, prediction rate
- [ ] **Data pipeline health** - Dagster run status, asset materialization times
- [ ] **Database performance** - Query times, connection pool status

**Setup Steps:**
1. Connect Metabase to `nba_analytics` database (PostgreSQL)
2. Create dashboards for key metrics from `ml_dev.model_evaluations`, `ml_dev.predictions`
3. Set up scheduled reports/alerts for model performance degradation
4. Document dashboard access and usage in `.cursor/docs/`

**Reference**: Metabase available at http://localhost:3002, Grafana at http://localhost:3001

---

**Note**: When starting work on any phase, check `.cursor/docs/ml-features.md` for current status and detailed requirements.
