# SQLMesh Guide

## Quick Reference

### Commands
```bash
make sqlmesh-plan      # Plan and apply all models
make sqlmesh-run       # Run incremental models
make sqlmesh-info      # Show project info

# Direct SQLMesh
cd transformation/sqlmesh
sqlmesh plan --auto-apply
sqlmesh run
sqlmesh ui             # Web UI
```

### Model Types
- `staging.*` - Views cleaning raw data
- `intermediate.*` - Rolling stats and aggregations
- `marts.*` - Business-ready data
- `features_dev.*` - ML feature tables

---

## Performance Optimization

### When to Use Incremental Models

Use `INCREMENTAL_BY_TIME_RANGE` for:
- Time-series data (game_date, created_at)
- Large datasets (>5 min full refresh)
- Models with "latest previous record" patterns

```sql
MODEL (
    name intermediate.your_model,
    kind INCREMENTAL_BY_TIME_RANGE(
        time_column game_date,
        batch_size 30  -- days per chunk
    ),
    start '1946-11-01',
    cron '@daily'
);

-- Filter by date range
WHERE game_date >= @start_ds AND game_date < @end_ds
```

### Avoid Slow Query Patterns

**Bad: Range joins over full history**
```sql
-- Creates massive intermediate result
SELECT g.*, r.rolling_5_ppg
FROM games g
LEFT JOIN rolling_stats r ON r.team_id = g.team_id AND r.game_date < g.game_date
```

**Good: Use LATERAL within incremental chunks**
```sql
LEFT JOIN LATERAL (
    SELECT rolling_5_ppg
    FROM rolling_stats r
    WHERE r.team_id = ag.team_id AND r.game_date < ag.game_date
    ORDER BY r.game_date DESC LIMIT 1
) r ON TRUE
```

**Good: Pre-compute lookup tables**
```sql
-- Simple JOIN instead of correlated subquery
LEFT JOIN intermediate.int_team_rolling_stats_lookup l
    ON l.game_id = g.game_id AND l.team_id = g.home_team_id
```

---

## Indexing

### Critical: Indexes Must Be Created

SQLMesh does NOT create indexes on snapshot tables.

**After direct `sqlmesh plan`:**
```bash
python scripts/create_sqlmesh_indexes.py
```

**Via Dagster:** Indexes are created automatically after each model.

**Check for missing indexes:**
```bash
python scripts/db_query.py "SELECT indexname FROM pg_indexes WHERE schemaname = '<schema>'"
```

---

## Backfilling

### Chunked Backfill for Heavy Models

For models with 50+ JOINs or full history:

```bash
# Feature groups (run in order)
python scripts/backfill_incremental_chunked.py intermediate.int_momentum_group_basic --start-year 2020 --end-year 2026
python scripts/backfill_incremental_chunked.py intermediate.int_momentum_group_h2h --start-year 2020 --end-year 2026
python scripts/backfill_incremental_chunked.py intermediate.int_momentum_group_opponent --start-year 2020 --end-year 2026
python scripts/backfill_incremental_chunked.py intermediate.int_momentum_group_context --start-year 2020 --end-year 2026
python scripts/backfill_incremental_chunked.py intermediate.int_momentum_group_trends --start-year 2020 --end-year 2026

# Then the combined model
python scripts/backfill_incremental_chunked.py intermediate.int_game_momentum_features --start-year 2020 --end-year 2026

# Finally the mart
python scripts/backfill_incremental_chunked.py marts.mart_game_features --start-year 2020 --end-year 2026
```

### Partial Backfill (Adding Columns)

When adding features, backfill only recent data:

```bash
python scripts/backfill_game_features_range.py --years 5  # Last 5 years
```

---

## Troubleshooting

### Slow Materialization (>5 minutes)

```bash
# Check for long-running queries
docker exec nba_analytics_postgres psql -U postgres -d nba_analytics -c "
SELECT pid, state, now() - query_start as duration, LEFT(query, 100)
FROM pg_stat_activity WHERE state = 'active' AND now() - query_start > INTERVAL '2 minutes';"

# Check temp space (>10GB = range join problem)
docker exec nba_analytics_postgres psql -U postgres -d nba_analytics -c "
SELECT pg_size_pretty(temp_bytes) FROM pg_stat_database WHERE datname = 'nba_analytics';"
```

### PostgreSQL 63-Character Identifier Limit

**Error:** `Identifier name '...' (length 70) exceeds Postgres's max identifier limit`

**Fix:** Already configured in `config.yaml`:
```yaml
physical_table_naming_convention: hash_md5
```

### Dagster Asset Times Out

**Cause:** SQLMesh tries to backfill missing intervals for all upstream models.

**Fix:** Backfill the model directly first:
```bash
python scripts/backfill_incremental_chunked.py intermediate.your_model --start-year 2020
```

---

## Design Guidelines

See `.cursor/rules/sqlmesh-model-design.mdc` for:
- Max 10-15 JOINs per model
- Grouping by domain
- Incremental batch sizing
- Dagster asset exports
