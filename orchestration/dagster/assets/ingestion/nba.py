"""NBA data ingestion assets using reliable NBA CDN endpoints."""

from dagster import asset, Config
from pydantic import Field
from typing import Optional
import dlt
import sys
from pathlib import Path

# Import dlt pipeline
# The code is mounted at /app in the container
project_root = Path("/app")
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))
from ingestion.dlt.pipelines.nba_stats import (
    nba_teams_resource,
    nba_players_resource,
    nba_games_resource,
    nba_todays_games_resource,
)


class NBAIngestionConfig(Config):
    """Configuration for NBA data ingestion."""
    season: Optional[str] = Field(default=None, description="NBA season (e.g., 2024-25). Defaults to current season.")
    games_limit: Optional[int] = Field(default=None, description="Limit number of games (for testing)")


@asset(
    group_name="nba_ingestion",
    description="Ingest NBA teams data from NBA CDN",
)
def nba_teams(context, config: NBAIngestionConfig) -> dict:
    """
    Ingest NBA teams data using dlt.
    
    Extracts all NBA teams from the season schedule CDN endpoint.
    This is reliable and fast (no bot protection issues).
    """
    try:
        # Create dlt pipeline
        # Credentials are provided via environment variables in the format:
        # NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__HOST, etc.
        pipeline = dlt.pipeline(
            pipeline_name="nba_stats",
            destination="postgres",
            dataset_name="nba",
        )
        
        context.log.info("Starting NBA teams extraction from CDN...")
        load_info = pipeline.run([nba_teams_resource()])
        
        context.log.info(f"NBA teams ingested successfully: {load_info}")
        return {
            "status": "success",
            "load_id": str(load_info.loads_ids[0]) if load_info.loads_ids else None,
        }
    except Exception as e:
        context.log.error(f"NBA teams ingestion failed: {e}")
        raise


@asset(
    group_name="nba_ingestion",
    description="Ingest NBA players data from NBA CDN (limited to game leaders)",
)
def nba_players(context, config: NBAIngestionConfig) -> dict:
    """
    Ingest NBA players data using dlt.
    
    Note: The reliable CDN endpoints only include players from today's game leaders.
    For a complete player roster, you would need to use a third-party API.
    """
    try:
        pipeline = dlt.pipeline(
            pipeline_name="nba_stats",
            destination="postgres",
            dataset_name="nba",
        )
        
        context.log.info("Starting NBA players extraction from CDN game leaders...")
        load_info = pipeline.run([nba_players_resource(season=config.season)])
        
        context.log.info(f"NBA players ingested: {load_info}")
        return {
            "status": "success",
            "load_id": str(load_info.loads_ids[0]) if load_info.loads_ids else None,
            "note": "Players extracted from game leaders only (CDN limitation)",
        }
    except Exception as e:
        context.log.error(f"NBA players ingestion failed: {e}")
        raise


@asset(
    group_name="nba_ingestion",
    description="Ingest full NBA season schedule from NBA CDN",
    deps=[nba_teams],
)
def nba_games(context, config: NBAIngestionConfig) -> dict:
    """
    Ingest NBA games (full season schedule) using dlt.
    
    Extracts all games from the season schedule CDN endpoint.
    This includes ~1,300+ games for a full season.
    """
    try:
        pipeline = dlt.pipeline(
            pipeline_name="nba_stats",
            destination="postgres",
            dataset_name="nba",
        )
        
        limit_msg = f" (limited to {config.games_limit})" if config.games_limit else ""
        context.log.info(f"Starting NBA games extraction from CDN{limit_msg}...")
        
        load_info = pipeline.run([
            nba_games_resource(
                season=config.season,
                limit=config.games_limit,
            )
        ])
        
        context.log.info(f"NBA games ingested: {load_info}")
        return {
            "status": "success",
            "load_id": str(load_info.loads_ids[0]) if load_info.loads_ids else None,
        }
    except Exception as e:
        context.log.error(f"NBA games ingestion failed: {e}")
        raise


@asset(
    group_name="nba_ingestion",
    description="Ingest today's NBA games with live scores from NBA CDN",
)
def nba_todays_games(context) -> dict:
    """
    Ingest today's NBA games with live scores using dlt.
    
    This provides real-time scoreboard data including:
    - Current scores
    - Game status (scheduled, in progress, final)
    - Period and game clock
    """
    try:
        pipeline = dlt.pipeline(
            pipeline_name="nba_stats",
            destination="postgres",
            dataset_name="nba",
        )
        
        context.log.info("Starting today's NBA games extraction from CDN...")
        load_info = pipeline.run([nba_todays_games_resource()])
        
        context.log.info(f"Today's NBA games ingested: {load_info}")
        return {
            "status": "success",
            "load_id": str(load_info.loads_ids[0]) if load_info.loads_ids else None,
        }
    except Exception as e:
        context.log.error(f"Today's NBA games ingestion failed: {e}")
        raise
