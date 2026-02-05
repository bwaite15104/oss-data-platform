# Orchestration

This directory contains Dagster orchestration configuration.

## Dagster

Dagster assets are defined in `dagster/definitions.py` (loaded from the repo root when using `dagster dev -f definitions.py`).

### Configuration for local runs

So that Dagster and the dlt ingestion assets can connect to Postgres (and create `raw_dev` tables when you run the ingestion assets):

1. Copy `.env.example` to `.env` in the **repo root**.
2. Set `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` to your Postgres instance. Optionally set `DATA_ENV=dev`.
3. The ingestion module reads these and configures dlt automatically; no separate dlt credential env vars are needed for local dev.

The `raw_dev` schema and tables (games, injuries, teams, players, boxscores, team_boxscores) are **created by dlt** when you materialize the ingestion assets (e.g. nba_teams, nba_games, nba_boxscores, nba_team_boxscores, nba_injuries). Do not create them with one-off scripts.

### Starting Dagster

From the **repo root**:

```bash
dagster dev -f definitions.py
```

Or from this directory: `dagster dev` (if workspace is set up). Access the UI at http://localhost:3000.

## Assets

Assets are organized by function:
- `assets/ingestion/` - Data ingestion assets
- `assets/transformation/` - Data transformation assets
- `assets/quality/` - Data quality assets (Baselinr)

## Configuration

- `dagster.yaml` - Dagster configuration
- `workspace.yaml` - Workspace definition
- `definitions.py` - Main definitions file

