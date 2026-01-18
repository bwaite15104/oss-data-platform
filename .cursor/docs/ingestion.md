# Data Ingestion

## Contract-Driven Schema

All ingestion pipelines use **ODCS contracts** as the source of truth for schemas.

```
contracts/schemas/*.yml  →  contract_loader.py  →  dlt resource columns  →  PostgreSQL
```

### Contract Loader Usage
```python
from ingestion.dlt.contract_loader import get_dlt_columns, get_primary_key

# Load schema from contract
columns = get_dlt_columns("nba_teams")
pk = get_primary_key("nba_teams")

# Use in dlt resource
@dlt.resource(columns=columns, primary_key=pk)
def my_resource():
    ...
```

## NBA CDN Endpoints (Reliable, No Auth)
```python
NBA_CDN_SCHEDULE = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
NBA_CDN_SCOREBOARD = "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"
NBA_CDN_PLAYERS = "https://cdn.nba.com/static/json/staticData/playerIndex.json"
NBA_CDN_ODDS = "https://cdn.nba.com/static/json/liveData/odds/odds_todaysGames.json"
NBA_CDN_BOXSCORE = "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game_id}.json"
```

## Current Assets
| Asset | Table | Incremental? | Notes |
|-------|-------|--------------|-------|
| `nba_teams` | `raw_dev.teams` | ✅ Yes | Small dataset, merge dedupes |
| `nba_players` | `raw_dev.players` | ✅ Yes | Small dataset, merge dedupes |
| `nba_games` | `raw_dev.games` | ✅ Yes | Merge dedupes by game_id |
| `nba_todays_games` | `raw_dev.todays_games` | ✅ Yes | Today's games only, merge dedupes |
| `nba_betting_odds` | `raw_dev.betting_odds` | ✅ Yes | Today's odds, merge dedupes |
| `nba_boxscores` | `raw_dev.boxscores` | ⚠️ Partial | **Fetches ALL completed games each run** - merge prevents duplicates but still makes API calls |
| `nba_team_boxscores` | `raw_dev.team_boxscores` | ⚠️ Partial | **Fetches ALL completed games each run** - merge prevents duplicates but still makes API calls |
| `nba_injuries` | `raw_dev.injuries` | ❌ No | Point-in-time snapshot (replace each run) |

## Incremental Loading Status

### ✅ True Incremental (No Re-fetching)
- **`nba_teams`**, **`nba_players`**: Small datasets, `merge` prevents duplicates
- **`nba_games`**: Full schedule, `merge` updates existing games
- **`nba_todays_games`**: Only fetches today's games
- **`nba_betting_odds`**: Only fetches today's odds

### ✅ True Incremental (Query Existing Games Before Fetching)
**`nba_boxscores`** and **`nba_team_boxscores`**:
- **Behavior**: Queries database for existing `game_id`s before fetching
- **Skips**: Games that already exist in database (no API calls)
- **Fetches**: Only new games that don't exist yet
- **Protection**: `write_disposition="merge"` with `primary_key` as backup protection

**Example logs:**
```
INFO: Found 690 existing game_ids in raw_dev.boxscores
INFO: ⏭️  Skipping 690 existing games (incremental loading). 0 new games to fetch.
```

**Result**: Daily runs only fetch ~2-5 new games instead of all 1,300+ games!

### ❌ Replace (Not Incremental)
- **`nba_injuries`**: Point-in-time snapshot, replaces all rows each run

## Adding a New Data Source (Contract-First)

### 1. Define the contract schema
```yaml
# contracts/schemas/nba_new_data.yml
name: nba_new_data
description: Description of the data

columns:
  - name: id
    type: integer
    nullable: false
    primary_key: true
    description: Unique identifier
    
  - name: name
    type: string
    nullable: false
    description: Name field
    
  - name: created_at
    type: timestamp
    nullable: false
    description: Record creation timestamp

primary_key:
  - id
```

### 2. Add resource to dlt pipeline (uses contract)
```python
# ingestion/dlt/pipelines/nba_stats.py

@dlt.resource(
    name="new_data",
    write_disposition="merge",
    primary_key=_get_contract_pk("nba_new_data") or "id",
    columns=_get_contract_columns("nba_new_data"),
)
def nba_new_data_resource() -> Iterator[Dict[str, Any]]:
    """
    Extract new data.
    
    Schema defined in: contracts/schemas/nba_new_data.yml
    """
    response = requests.get(URL, headers=CDN_HEADERS, timeout=60)
    # ... process and yield records
```

### 3. Create Dagster asset
```python
# orchestration/dagster/assets/ingestion/nba.py

@asset(
    group_name="nba_ingestion",
    description="Description here",
)
def nba_new_data(context) -> dict:
    pipeline = dlt.pipeline(
        pipeline_name="nba_stats",
        destination="postgres",
        dataset_name="nba",
    )
    load_info = pipeline.run([nba_new_data_resource()])
    return {"status": "success"}
```

### 4. Export from __init__.py
```python
# orchestration/dagster/assets/ingestion/__init__.py
from .nba import nba_new_data
```

### 5. Restart Dagster
```bash
docker restart oss_data_platform_dagster_webserver
# Or locally:
make dagster-dev
```

## Environment Variables for dlt
dlt reads PostgreSQL credentials from environment variables:
```
NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__HOST=postgres
NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__PORT=5432
NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__DATABASE=oss_data_platform
NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__USERNAME=postgres
NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__PASSWORD=postgres
```
