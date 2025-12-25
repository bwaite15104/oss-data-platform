# Data Transformation

This directory contains SQLMesh transformation models.

## SQLMesh

SQLMesh configuration is generated from ODCS contracts. Models are organized by layer:

- `models/staging/` - Staging models (raw data cleanup)
- `models/intermediate/` - Intermediate transformations
- `models/marts/` - Final marts for analytics

## Running SQLMesh

```bash
cd sqlmesh
sqlmesh plan
sqlmesh apply
```

## Integration with Dagster

Transformation assets are defined in `orchestration/dagster/assets/transformation/`.

