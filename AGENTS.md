# OSS Data Platform - Cursor Rules

## Project Overview

NBA sports betting data platform. Goal: Build ML models to predict game outcomes for betting.

## Constraints

- **Free Services Only**: All tools, APIs, and services must be free. No paid subscriptions.
- **Automatic Validation**: After ANY change to assets/pipelines/schemas, validate using Dagster CLI and warehouse queries.

## Quick Reference

| Service | URL |
|---------|-----|
| Dagster UI | http://localhost:3000 |
| MLflow UI | http://localhost:5000 |
| Feast UI | http://localhost:8080 |
| Metabase | http://localhost:3002 |
| PostgreSQL | localhost:5432 (postgres/postgres/nba_analytics) |

## Key Commands

```bash
make docker-up         # Start all services
make docker-down       # Stop services
make dagster-dev       # Start local Dagster
make db-counts         # Show row counts
make db-schemas        # List schemas
make sqlmesh-plan      # Plan SQLMesh changes
```

## Database Query Utility

```bash
python scripts/db_query.py --counts raw_dev
python scripts/db_query.py --schemas
python scripts/db_query.py "SELECT * FROM raw_dev.teams LIMIT 5"
```

## Code Locations

| Location | Purpose |
|----------|---------|
| `ingestion/dlt/pipelines/` | dlt data pipelines |
| `orchestration/dagster/assets/` | Dagster asset definitions |
| `orchestration/dagster/assets/ml/` | ML training & predictions |
| `transformation/sqlmesh/` | SQL transformations |
| `contracts/schemas/` | ODCS data contracts |
| `feature_repo/` | Feast feature store |
| `scripts/` | Utility scripts |

## Documentation

**Core docs in `.cursor/docs/`:**

| Doc | When to Reference |
|-----|-------------------|
| `architecture.md` | System design, data flow |
| `commands.md` | Make commands, CLI usage |
| `database.md` | Schema structure, tables |
| `ingestion.md` | Data sources, pipelines |
| `ml-features.md` | ML features, model training |
| `mlflow-and-feast-context.md` | Model/feature tracking |
| `sqlmesh-guide.md` | Performance, troubleshooting |

**Cursor rules in `.cursor/rules/`:**

| Rule | Purpose |
|------|---------|
| `sqlmesh-model-design.mdc` | Avoid mega-models, use feature groups |
| `dagster-asset-partitioning.mdc` | One partitioned Dagster asset per table; new tables must use partitioning |
| `validation-workflow.mdc` | Validate changes automatically |
| `documentation-maintenance.mdc` | Keep docs in sync |

## SQLMesh Best Practices

1. **Prefer Incremental**: Use `INCREMENTAL_BY_TIME_RANGE` for time-series data
2. **Avoid Mega Models**: Max 10-15 JOINs per model; split into feature groups
3. **One partitioned asset per table**: Every new table gets an individual Dagster asset with daily partitioning for backfills (see `dagster-asset-partitioning.mdc`)
4. **Create Indexes**: Run `python scripts/create_sqlmesh_indexes.py` after direct backfills
5. **Chunked Backfill**: Use `scripts/backfill_incremental_chunked.py` for heavy models

See `.cursor/rules/sqlmesh-model-design.mdc` and `.cursor/docs/sqlmesh-guide.md` for details.

## ML Pipeline

**Source of truth:**
- **MLflow** (http://localhost:5000): Model runs, params, metrics, artifacts
- **Feast** (`feature_repo/`): Feature definitions, schemas

**Key assets:**
- `train_game_winner_model` - XGBoost model training
- `generate_game_predictions` - Generate predictions

## Validation Workflow

After making changes, validate using Dagster CLI:

```bash
# Run asset
docker exec nba_analytics_dagster_webserver dagster asset materialize -f /app/definitions.py --select <asset_name>

# Verify data
python scripts/db_query.py "SELECT count(*) FROM <schema>.<table>"
```

See `.cursor/rules/validation-workflow.mdc` for complete workflow.
