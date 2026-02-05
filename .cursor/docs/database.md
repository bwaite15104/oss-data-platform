# Database Architecture

## Database Name
**`nba_analytics`** - Reflects the NBA betting/ML analytics purpose

## Connection Details
```
Host: localhost (or 'postgres' from Docker network)
Port: 5432
Database: nba_analytics
Username: postgres
Password: postgres
```

## Schema Architecture

### Layered Data Model
```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              nba_analytics                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │
│  │    raw      │    │   staging   │    │    marts    │    │  features   │  │
│  │  (bronze)   │───▶│  (silver)   │───▶│   (gold)    │───▶│   (ML)      │  │
│  └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘  │
│        │                                                         │          │
│        │                                                         ▼          │
│        │            ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │
│        │            │    ml       │◀───│ predictions │◀───│   models    │  │
│        │            │  training   │    │             │    │  (metadata) │  │
│        └───────────▶└─────────────┘    └─────────────┘    └─────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Environment Schemas
Each layer has dev/staging/prod variants:
```
raw_dev, raw_staging, raw_prod
staging_dev, staging_staging, staging_prod
marts_dev, marts_staging, marts_prod
features_dev, features_staging, features_prod
ml_dev, ml_staging, ml_prod
```

## Schema Definitions

### 1. RAW Schema (Bronze Layer)
**Purpose**: Raw data exactly as ingested from sources. No transformations.
**Naming**: `raw_{env}` (e.g., `raw_dev`, `raw_prod`)

| Table | Source | Description |
|-------|--------|-------------|
| `teams` | NBA CDN | Raw team data |
| `players` | NBA CDN | Raw player index |
| `games` | NBA CDN | Raw schedule data |
| `boxscores` | NBA CDN | Raw player game stats |
| `team_boxscores` | NBA CDN | Raw team game stats |
| `betting_odds` | NBA CDN | Raw odds data |
| `todays_games` | NBA CDN | Raw live scoreboard |

### 2. STAGING Schema (Silver Layer)
**Purpose**: Cleaned, validated, deduplicated data. Type conversions applied.
**Naming**: `staging_{env}` (e.g., `staging_dev`, `staging_prod`)

| Table | Description |
|-------|-------------|
| `stg_teams` | Cleaned teams with standardized fields |
| `stg_players` | Cleaned players with parsed height/weight |
| `stg_games` | Cleaned games with proper date types |
| `stg_boxscores` | Cleaned boxscores with calculated fields |
| `stg_odds` | Cleaned odds with implied probabilities |

### 3. MARTS Schema (Gold Layer)
**Purpose**: Business-ready aggregations and dimensional models.
**Naming**: `marts_{env}` (e.g., `marts_dev`, `marts_prod`)

| Table | Description |
|-------|-------------|
| `dim_teams` | Team dimension with current attributes |
| `dim_players` | Player dimension with current team |
| `dim_dates` | Date dimension for time analysis |
| `fct_games` | Game fact table with outcomes |
| `fct_player_performance` | Player performance metrics |
| `fct_team_performance` | Team performance metrics |
| `team_standings` | Current season standings |
| `player_averages` | Season averages per player |

### 4. FEATURES Schema (Feature Store)
**Purpose**: ML-ready features for model training and inference.
**Naming**: `features_{env}` (e.g., `features_dev`, `features_prod`)

| Table | Description |
|-------|-------------|
| `team_rolling_stats` | Rolling averages (5, 10, 20 games) |
| `player_rolling_stats` | Player rolling performance |
| `matchup_history` | Head-to-head historical features |
| `rest_days` | Days between games per team |
| `home_away_splits` | Home vs away performance |
| `pace_metrics` | Possessions and pace features |
| `odds_features` | Line movements, implied probs |
| `feature_registry` | Feature metadata and versions |

### 5. ML Schema (Training & Predictions)
**Purpose**: Model training data, predictions, and results tracking.
**Naming**: `ml_{env}` (e.g., `ml_dev`, `ml_prod`)

| Table | Description |
|-------|-------------|
| `training_datasets` | Versioned training data snapshots |
| `model_registry` | Model metadata and versions |
| `model_metrics` | Training/validation metrics |
| `predictions` | Model predictions per game |
| `prediction_results` | Actual vs predicted outcomes |
| `betting_results` | P&L tracking for bets |
| `experiments` | A/B test tracking |

## Environment Strategy

### Development (`*_dev`)
- Local development and testing
- Can be reset/rebuilt freely
- Uses sample/subset data

### Staging (`*_staging`)
- Pre-production validation
- Full data, production-like
- Integration testing

### Production (`*_prod`)
- Live predictions
- Protected from accidental changes
- Full audit logging

## Data Flow

```
NBA CDN APIs
     │
     ▼
┌─────────┐     ┌───────────┐     ┌─────────┐     ┌──────────┐
│   raw   │────▶│  staging  │────▶│  marts  │────▶│ features │
│  (dlt)  │     │ (SQLMesh) │     │(SQLMesh)│     │ (SQLMesh)│
└─────────┘     └───────────┘     └─────────┘     └──────────┘
                                                       │
                                                       ▼
                                  ┌──────────┐    ┌─────────┐
                                  │predictions│◀──│   ml    │
                                  │           │   │(Python) │
                                  └──────────┘    └─────────┘
```

## Quick Reference

### Connection String
```
postgresql://postgres:postgres@localhost:5432/nba_analytics
```

### Schema Access by Environment
```sql
-- Development
SET search_path TO raw_dev, staging_dev, marts_dev, features_dev, ml_dev;

-- Staging
SET search_path TO raw_staging, staging_staging, marts_staging, features_staging, ml_staging;

-- Production
SET search_path TO raw_prod, staging_prod, marts_prod, features_prod, ml_prod;
```

### Create All Schemas
```sql
-- Run once to create schema structure
DO $$
DECLARE
    env TEXT;
    layer TEXT;
BEGIN
    FOREACH env IN ARRAY ARRAY['dev', 'staging', 'prod'] LOOP
        FOREACH layer IN ARRAY ARRAY['raw', 'staging', 'marts', 'features', 'ml'] LOOP
            EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I', layer || '_' || env);
        END LOOP;
    END LOOP;
END $$;
```

## Table Schemas

> **Note**: Detailed table schemas are defined in `contracts/schemas/*.yml`
> Use `from ingestion.dlt.contract_loader import get_dlt_columns` to load programmatically.

See individual contract files for column definitions:
- `contracts/schemas/nba_teams.yml`
- `contracts/schemas/nba_players.yml`
- `contracts/schemas/nba_games.yml`
- `contracts/schemas/nba_boxscores.yml`
- `contracts/schemas/nba_betting_odds.yml`

## Current Data Status (raw_dev)

Use `make db-counts` or `python scripts/db_query.py --counts raw_dev` to check current data:

| Table | Description | Current Count |
|-------|-------------|---------------|
| `teams` | NBA teams + international | 34 |
| `players` | All players from CDN | 527 |
| `games` | Full season schedule | 1,306 |
| `boxscores` | Player game stats (all completed games) | 24,468 |
| `team_boxscores` | Team game stats (all completed games) | 1,378 |
| `injuries` | Current injury report (ESPN) | 118 |
| `todays_games` | Today's live games | 0-15 |
| `betting_odds` | Today's odds | 280 |

### Transformation Views

| Layer | View | Description | Count |
|-------|------|-------------|-------|
| staging | `stg_*` | Cleaned raw data | 5 views |
| intermediate | `int_team_season_stats` | Season aggregates | 31 |
| intermediate | `int_team_rolling_stats` | Rolling 5/10 game stats | 1,219 |
| marts | `mart_game_features` | ML-ready game features | 584 |
| marts | `mart_team_standings` | Current standings | 31 |

### Feature Store (marts + features_dev)

| Schema | Table | Description | Count |
|--------|-------|-------------|-------|
| marts | `mart_game_features` | ML training features (used by training/predictions) | 584 |
| features_dev | `team_features` | Team season stats | 31 |
| features_dev | `team_injury_features` | Injury counts by team | 29 |
| features_dev | `feature_registry` | Feature metadata | 11 |

## Useful Queries

### Quick Data Checks
```sql
-- Row counts in raw_dev
SELECT 'teams' as tbl, count(*) FROM raw_dev.teams
UNION ALL SELECT 'games', count(*) FROM raw_dev.games
UNION ALL SELECT 'players', count(*) FROM raw_dev.players;

-- Schema overview
SELECT table_schema, count(*) as tables
FROM information_schema.tables 
WHERE table_schema LIKE 'raw_%' OR table_schema LIKE 'ml_%'
GROUP BY table_schema ORDER BY table_schema;
```

### Team Analysis
```sql
-- Teams by conference
SELECT conference, count(*) FROM raw_dev.teams 
WHERE team_id > 1610612700  -- NBA teams only
GROUP BY conference;

-- Team details
SELECT team_name, conference, division 
FROM raw_dev.teams 
WHERE team_id > 1610612700
ORDER BY conference, division, team_name;
```

### Game Analysis
```sql
-- Games by status
SELECT game_status, count(*) 
FROM raw_dev.games 
GROUP BY game_status;

-- Recent completed games
SELECT game_date, home_team_name, away_team_name, home_score, away_score
FROM raw_dev.games 
WHERE game_status = 'Final'
ORDER BY game_date DESC LIMIT 10;
```

### Using the Query Utility
```bash
# Quick commands
python scripts/db_query.py --schemas
python scripts/db_query.py --tables raw_dev
python scripts/db_query.py --counts raw_dev
python scripts/db_query.py "SELECT * FROM raw_dev.teams LIMIT 5"
```
