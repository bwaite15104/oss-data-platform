# Cursor Documentation

Project context documentation for AI assistants.

## Core Documentation

| Doc | Purpose |
|-----|---------|
| `architecture.md` | System design, data flow |
| `commands.md` | Make commands, CLI usage |
| `contracts.md` | ODCS schema definitions |
| `database.md` | DB schema, tables, queries |
| `ingestion.md` | Data sources, dlt pipelines |
| `ml-features.md` | ML features, model training |
| `mlflow-and-feast-context.md` | Model/feature tracking |
| `sqlmesh-guide.md` | SQLMesh performance & troubleshooting |

## Cursor Rules

Rules in `.cursor/rules/` provide actionable patterns:

| Rule | Trigger |
|------|---------|
| `sqlmesh-model-design.mdc` | SQLMesh model files |
| `validation-workflow.mdc` | Asset/pipeline changes |
| `documentation-maintenance.mdc` | Documentation updates |

## Quick Reference

- **Dagster UI**: http://localhost:3000
- **MLflow UI**: http://localhost:5000
- **Feast UI**: http://localhost:8080
- **PostgreSQL**: localhost:5432 (postgres/postgres/nba_analytics)

## When to Update

| Change | Update |
|--------|--------|
| Database schema | `database.md` |
| New commands | `commands.md` |
| Data sources | `ingestion.md` |
| Contracts | `contracts.md` |
| ML features | `ml-features.md` |
| SQLMesh patterns | `sqlmesh-guide.md` |
