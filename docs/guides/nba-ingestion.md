# NBA Data Ingestion Guide

This guide explains how to ingest NBA data using the dlt pipeline and NBA Stats API.

## Overview

The NBA data ingestion pipeline extracts data from the official NBA Stats API (stats.nba.com) and loads it into PostgreSQL. The pipeline supports:

- **Teams** - Team information and metadata
- **Players** - Player information and statistics
- **Games** - Game results and basic information
- **Player Game Stats** - Detailed player statistics per game
- **Team Game Stats** - Detailed team statistics per game

## Setup

### 1. Install Dependencies

```bash
cd ingestion/dlt
pip install -r requirements.txt
```

### 2. Configure Database Connection

Set environment variables or update the dlt pipeline configuration:

```bash
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432
export POSTGRES_DB=oss_data_platform
export POSTGRES_USER=postgres
export POSTGRES_PASSWORD=postgres
```

### 3. Run Pipeline

```bash
# Basic usage
python ingestion/dlt/pipelines/nba_stats.py

# Or use Dagster to orchestrate the pipeline
# See orchestration/dagster/assets/ingestion/nba.py
```

## Pipeline Resources

### Teams Resource

Extracts team information:

```python
from ingestion.dlt.pipelines.nba_stats import nba_teams_resource

# Get all teams
teams = list(nba_teams_resource())
```

### Players Resource

Extracts player information for a season:

```python
from ingestion.dlt.pipelines.nba_stats import nba_players_resource

# Get players for 2023-24 season
players = list(nba_players_resource(season="2023-24"))
```

### Games Resource

Extracts game data:

```python
from ingestion.dlt.pipelines.nba_stats import nba_games_resource

# Get games for a specific date
games = list(nba_games_resource(game_date="2024-01-15"))
```

## NBA Stats API Notes

### Endpoints Used

- `scoreboard` - Daily game scores
- `leaguedashplayerstats` - Player statistics
- `leaguedashteamstats` - Team statistics
- `boxscoretraditionalv2` - Game boxscores

### Rate Limiting

The NBA Stats API doesn't have official rate limits, but:
- Use ~600ms delay between requests
- The pipeline includes automatic rate limiting
- Be respectful of the API

### Headers Required

The API requires specific headers:
- `User-Agent` - Browser user agent
- `Referer` - https://www.nba.com/
- `Accept` - application/json

These are handled automatically in the pipeline.

## Data Schema

Data is loaded into the `nba` schema in PostgreSQL:

- `nba.games` - Game results
- `nba.players` - Player information
- `nba.teams` - Team information
- `nba.player_game_stats` - Player game statistics
- `nba.team_game_stats` - Team game statistics

## Integration with Dagster

NBA ingestion assets are available in Dagster:

```python
from orchestration.dagster.assets.ingestion.nba import (
    nba_teams,
    nba_players,
    nba_games,
)
```

These assets can be scheduled to run daily or on-demand.

## Example Workflow

1. **Initial Load**: Load teams and players
   ```python
   pipeline.run(nba_stats(include_game_stats=False))
   ```

2. **Daily Games**: Load today's games
   ```python
   pipeline.run(nba_stats(game_date=None))
   ```

3. **Historical Games**: Load games for a date range
   ```python
   for date in date_range:
       pipeline.run(nba_stats(game_date=date))
   ```

4. **Game Statistics**: Load detailed stats for completed games
   ```python
   # After games are loaded, fetch detailed stats
   pipeline.run(nba_stats(include_game_stats=True))
   ```

## Troubleshooting

### API Errors

If you encounter API errors:
- Check your internet connection
- Verify the NBA Stats API is accessible
- Check rate limiting (add delays if needed)

### Data Quality

Use Baselinr to monitor data quality:

```bash
baselinr profile --config configs/generated/baselinr/baselinr_config.yml
```

## Next Steps

- Set up scheduled ingestion via Dagster
- Configure Baselinr for data quality monitoring
- Build transformation models in SQLMesh
- Create ML models for match prediction



