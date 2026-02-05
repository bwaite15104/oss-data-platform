# Data Transformation

This directory contains SQLMesh transformation models.

## SQLMesh

SQLMesh configuration is generated from ODCS contracts. Models are organized by layer:

- `models/staging/` - Staging models (raw data cleanup)
- `models/intermediate/` - Intermediate transformations
- `models/marts/` - Final marts for analytics
- `models/features/` - ML feature tables

## Running SQLMesh

```bash
cd sqlmesh
sqlmesh plan
sqlmesh apply
```

## Integration with Dagster

Transformation assets are defined in `orchestration/dagster/assets/transformation/`.

## Backfilling Heavy Models

Some models are computationally heavy (50+ JOINs, 1000+ columns) and cannot complete full historical backfills within Dagster's timeout. Use the chunked backfill script instead:

### Heavy Models List
- `intermediate.int_game_momentum_features` - Combined momentum features (50+ upstream models)
- `marts.mart_game_features` - ML-ready features (depends on all intermediates)

### Chunked Backfill Script

The `scripts/backfill_incremental_chunked.py` script processes backfills in manageable chunks:

```bash
# Backfill int_game_momentum_features for 2020-2026 in 30-day chunks
docker exec nba_analytics_dagster_webserver python /app/scripts/backfill_incremental_chunked.py \
    intermediate.int_game_momentum_features --start-year 2020 --end-year 2026 --chunk-days 30 --timeout 3600

# Backfill mart_game_features (after int_game_momentum_features is complete)
docker exec nba_analytics_dagster_webserver python /app/scripts/backfill_incremental_chunked.py \
    marts.mart_game_features --start-year 2020 --end-year 2026 --chunk-days 30 --timeout 3600

# Current year only (faster, for regular updates)
docker exec nba_analytics_dagster_webserver python /app/scripts/backfill_incremental_chunked.py \
    intermediate.int_game_momentum_features --start-year 2026 --end-year 2026 --chunk-days 7 --timeout 2400
```

### Script Options
- `--start-year` / `--end-year`: Date range for backfill
- `--chunk-days N`: Process N days per chunk (use for heavy models)
- `--chunk-years N`: Process N years per chunk (default, for lighter models)
- `--timeout`: Timeout per chunk in seconds
- `--dry-run`: Show chunks without executing
- `-v`: Verbose output

