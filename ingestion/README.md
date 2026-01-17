# Data Ingestion

This directory contains data ingestion pipelines using Airbyte and dlt.

## NBA Data Ingestion

### NBA Stats API Pipeline

The NBA Stats API pipeline (`dlt/pipelines/nba_stats.py`) extracts data from the official NBA Stats API (stats.nba.com).

#### Available Resources

1. **nba_teams** - Team information
2. **nba_players** - Player information and statistics
3. **nba_games** - Game results and basic information
4. **nba_player_game_stats** - Player statistics per game
5. **nba_team_game_stats** - Team statistics per game

#### Usage

```python
from ingestion.dlt.pipelines.nba_stats import nba_stats
import dlt

# Create pipeline
pipeline = dlt.pipeline(
    pipeline_name="nba_stats",
    destination="postgres",
    dataset_name="nba",
)

# Load data
load_info = pipeline.run(nba_stats(season="2023-24"))
```

#### Configuration

The pipeline accepts:
- `season`: NBA season (e.g., "2023-24")
- `game_date`: Specific game date (YYYY-MM-DD)
- `include_game_stats`: Whether to include detailed game statistics

#### NBA Stats API Notes

- No authentication required (public endpoints)
- Rate limiting: ~600ms between requests recommended
- Some endpoints may require specific headers (User-Agent, Referer)
- Data is returned in JSON format with specific structure

## Airbyte

Airbyte connection configurations are generated from ODCS and stored in `airbyte/connections/`.

## Integration with Dagster

Ingestion assets are defined in `orchestration/dagster/assets/ingestion/` and can be orchestrated via Dagster.

### NBA Ingestion Assets

- `nba_teams` - Ingest team data
- `nba_players` - Ingest player data
- `nba_games` - Ingest game data (depends on teams)
- `nba_player_game_stats` - Ingest player game stats (depends on games, players)
- `nba_team_game_stats` - Ingest team game stats (depends on games, teams)
