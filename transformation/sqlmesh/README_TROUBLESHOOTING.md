# SQLMesh Troubleshooting Guide

This guide addresses common SQLMesh state management issues and provides solutions.

## Problem: SQLMesh Not Detecting Upstream Data Changes

### Symptoms
- SQLMesh reports "No changes to plan" even when upstream data has changed
- Models don't rematerialize after manual table creation
- `sqlmesh plan` shows no changes when it should

### Root Cause

SQLMesh uses a **content-based fingerprinting system** to detect changes:
- Model fingerprints are based on SQL query text, configuration, and upstream dependencies
- SQLMesh does **not** track changes to raw data tables
- Manual table creation bypasses SQLMesh's snapshot system, causing state mismatches

### Solutions

#### Option 1: Force Backfill (Recommended)

Use the backfill command to force rematerialization:

**Using the script:**
```bash
# Backfill a single model
python scripts/sqlmesh_backfill.py marts.mart_game_features

# Backfill with custom start date
python scripts/sqlmesh_backfill.py marts.mart_game_features --start 2015-01-01

# Backfill a chain of models (upstream first)
python scripts/sqlmesh_backfill.py marts.mart_game_features features_dev.game_features

# From Docker container
docker exec nba_analytics_dagster_webserver python /app/scripts/sqlmesh_backfill.py marts.mart_game_features
```

**Using SQLMesh directly:**
```bash
# Force backfill from a specific date
sqlmesh plan local --backfill --start 2010-10-01 --select-model marts.mart_game_features --auto-apply

# Then rematerialize downstream
sqlmesh plan local --backfill --start 2010-10-01 --select-model features_dev.game_features --auto-apply
```

**Using Dagster (with backfill support):**
The Dagster integration now supports backfill mode. Update your asset configuration:
```python
config = SQLMeshConfig(backfill=True, backfill_start_date="2010-10-01")
```

#### Option 2: Investigate State with table_diff

Use the table_diff script to compare expected vs actual state:

```bash
# Compare state for a model
python scripts/sqlmesh_table_diff.py features_dev.game_features

# From Docker container
docker exec nba_analytics_dagster_webserver python /app/scripts/sqlmesh_table_diff.py features_dev.game_features
```

Or use SQLMesh directly:
```bash
sqlmesh table_diff local features_dev.game_features
```

#### Option 3: Drop and Recreate Snapshots (Last Resort)

**Warning**: This loses snapshot history and should only be used if other options fail.

1. Identify problematic snapshots in SQLMesh's state database (`sqlmesh__*` tables)
2. Drop the snapshots manually
3. Run `sqlmesh plan` again - SQLMesh will detect missing tables and recreate them

## Prevention

To avoid state management issues in the future:

1. **Always use SQLMesh commands** to create/modify tables, never manual SQL
2. **Use backfill commands** when expanding historical data ranges
3. **Update model `start` dates** in `config.yaml` and model definitions when changing date ranges
4. **Let SQLMesh manage snapshots** - don't manually create snapshot tables
5. **Use the backfill script** proactively when you know upstream data has changed significantly

## SQLMesh State Database

SQLMesh stores its state in the database:
- Look for `sqlmesh__*` tables in your database
- These tables track which snapshots exist and their fingerprints
- State is environment-specific (e.g., `local`, `prod`)

## Quick Reference

### Check SQLMesh State
```bash
# List all models
sqlmesh info

# Check specific model
sqlmesh info --select-model features_dev.game_features

# Compare expected vs actual
sqlmesh table_diff local features_dev.game_features
```

### Force Rematerialization
```bash
# Single model
python scripts/sqlmesh_backfill.py features_dev.game_features

# Multiple models (dependency order)
python scripts/sqlmesh_backfill.py marts.mart_game_features features_dev.game_features
```

### Monitor Execution
```bash
# Monitor SQLMesh queries and locks
python scripts/monitor_sqlmesh.py --watch
```

## Related Documentation

- [SQLMesh Change Detection Investigation](../../.cursor/docs/sqlmesh-change-detection-investigation.md)
- [SQLMesh Snapshots Documentation](https://sqlmesh.readthedocs.io/en/stable/concepts/architecture/snapshots/)
- [SQLMesh Backfill Guide](https://sqlmesh.readthedocs.io/en/stable/guides/backfill/)
- [SQLMesh Table Diff Guide](https://sqlmesh.readthedocs.io/en/stable/guides/tablediff/)
