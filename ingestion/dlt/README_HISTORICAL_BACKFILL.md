# Historical NBA Data Backfill

This dlt pipeline backfills historical NBA seasons from Basketball Reference (free, open-source).

## ⚠️ Important Note: Chrome/ChromeDriver Required

The `basketball-reference-scraper` library requires Chrome and ChromeDriver (via Selenium) to scrape Basketball Reference. 

**Current Status:**
- ✅ **Local execution**: Works well when running locally with Chrome installed
- ⚠️ **Docker setup**: Chrome/ChromeDriver installed but initialization can be challenging in headless mode
- 📖 **See [README_LOCAL_EXECUTION.md](README_LOCAL_EXECUTION.md)** for detailed local setup instructions

**Docker Configuration:**
- Chrome and ChromeDriver are installed in the Docker image
- Headless mode is configured via environment variables
- The library may still have issues in Docker due to Chrome initialization timing

**For reliable backfilling, use CSV-based approach:**
```bash
# Download CSV from free source (Kaggle, Opendatabay, etc.)
python scripts/backfill_historical_nba.py --file data/historical/boxscores.csv --table boxscores
```

## Usage

### Option 1: Direct dlt Usage (Requires Chrome)

```python
import dlt
from ingestion.dlt.pipelines.nba_historical_backfill import nba_historical_backfill

# Create pipeline
pipeline = dlt.pipeline(
    pipeline_name="nba_historical_backfill",
    destination="postgres",
    dataset_name="nba_analytics"
)

# Run backfill for a specific season
load_info = pipeline.run(
    nba_historical_backfill(
        season="2023-24",  # Season to backfill
        team=None,  # Optional: specific team (e.g., "LAL") or None for all teams
        skip_existing=True,  # Skip games already in database
        limit=None  # Optional: limit number of games (for testing)
    )
)

print(load_info)
```

### Option 2: Standalone Script with CSV Files (Recommended)

If you have CSV files from free sources (Kaggle, Opendatabay, etc.):

```bash
python scripts/backfill_historical_nba.py --file data/historical/boxscores.csv --table boxscores
```

Or with a direct download URL:

```bash
python scripts/backfill_historical_nba.py --url <direct_download_url> --table boxscores
```

### Option 3: Via Dagster Asset

```bash
dagster asset materialize -m orchestration.dagster.definitions --select nba_historical_backfill --config '{"ops": {"nba_historical_backfill": {"config": {"season": "2023-24", "team": "LAL", "limit": 10}}}}'
```

## Features

- ✅ **Automatic schema conversion**: Maps Basketball Reference format to our `raw_dev.boxscores` schema
- ✅ **Incremental loading**: Skips games that already exist in database
- ✅ **Player ID mapping**: Attempts to match player names to existing `player_id`s
- ✅ **Rate limiting**: Respects Basketball Reference with 2-second delays
- ✅ **Error handling**: Continues processing even if individual games fail

## Current Data Status

To check what historical data you already have:

```sql
SELECT 
    COUNT(*) as total_boxscores,
    COUNT(DISTINCT game_id) as unique_games,
    COUNT(DISTINCT player_id) as unique_players,
    MIN(game_date) as earliest_game,
    MAX(game_date) as latest_game
FROM raw_dev.boxscores;
```

## Notes

- **Free source**: Basketball Reference is free and open-source
- **Rate limits**: The scraper includes 2-second delays between games to be respectful
- **Player IDs**: If a player name isn't found in the database, a hash-based ID is generated
- **Missing stats**: Some advanced stats (points_in_paint, points_fast_break) aren't available from BR and default to 0
- **Chrome requirement**: The scraper uses Selenium/Chrome, which must be installed separately

## Example: Backfill 2023-24 Season

```python
import dlt
from ingestion.dlt.pipelines.nba_historical_backfill import nba_historical_backfill

pipeline = dlt.pipeline(
    pipeline_name="nba_historical_backfill",
    destination="postgres",
    dataset_name="nba_analytics"
)

# This will take a while (30 teams × ~82 games × 2 seconds = ~1.5 hours)
# Consider running for specific teams first to test
load_info = pipeline.run(
    nba_historical_backfill(season="2023-24", skip_existing=True)
)
```
