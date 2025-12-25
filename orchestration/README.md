# Orchestration

This directory contains Dagster orchestration configuration.

## Dagster

Dagster assets are generated from ODCS contracts and defined in `dagster/definitions.py`.

### Starting Dagster

```bash
cd dagster
dagster dev
```

Access the UI at http://localhost:3000

## Assets

Assets are organized by function:
- `assets/ingestion/` - Data ingestion assets
- `assets/transformation/` - Data transformation assets
- `assets/quality/` - Data quality assets (Baselinr)

## Configuration

- `dagster.yaml` - Dagster configuration
- `workspace.yaml` - Workspace definition
- `definitions.py` - Main definitions file

