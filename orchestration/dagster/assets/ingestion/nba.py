"""NBA data ingestion assets using reliable NBA CDN endpoints."""

from dagster import asset, Config
from pydantic import Field
from typing import Optional
import dlt
import sys
import os
from pathlib import Path

# Import dlt pipeline - works both in Docker (/app) and locally
# Check if running in Docker or locally
if Path("/app/ingestion").exists():
    # Docker environment
    project_root = Path("/app")
else:
    # Local environment - find project root
    project_root = Path(__file__).parent.parent.parent.parent.parent

if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from ingestion.dlt.pipelines.nba_stats import (
    nba_teams_resource,
    nba_players_resource,
    nba_games_resource,
    nba_todays_games_resource,
    nba_betting_odds_resource,
    nba_boxscores_resource,
    nba_team_boxscores_resource,
)


class NBAIngestionConfig(Config):
    """Configuration for NBA data ingestion."""
    season: Optional[str] = Field(default=None, description="NBA season (e.g., 2024-25). Defaults to current season.")
    games_limit: Optional[int] = Field(default=None, description="Limit number of games (for testing)")


class NBABoxscoreConfig(Config):
    """Configuration for boxscore ingestion."""
    limit: Optional[int] = Field(default=10, description="Limit number of games to fetch boxscores for")
    completed_only: bool = Field(default=True, description="Only fetch completed games")


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


@asset(
    group_name="nba_ingestion",
    description="Ingest betting odds for today's NBA games - CRITICAL FOR ML BETTING MODELS",
)
def nba_betting_odds(context) -> dict:
    """
    Ingest betting odds for today's NBA games using dlt.
    
    This provides real odds data from sportsbooks including:
    - Moneyline odds (home/away win probability)
    - Spread/handicap lines
    - Over/under totals
    
    CRITICAL for ML betting prediction models!
    """
    try:
        pipeline = dlt.pipeline(
            pipeline_name="nba_stats",
            destination="postgres",
            dataset_name="nba",
        )
        
        context.log.info("Starting betting odds extraction from CDN...")
        load_info = pipeline.run([nba_betting_odds_resource()])
        
        context.log.info(f"Betting odds ingested: {load_info}")
        return {
            "status": "success",
            "load_id": str(load_info.loads_ids[0]) if load_info.loads_ids else None,
        }
    except Exception as e:
        context.log.error(f"Betting odds ingestion failed: {e}")
        raise


@asset(
    group_name="nba_ingestion",
    description="Ingest player-level boxscore stats from completed games",
    deps=[nba_games],
)
def nba_boxscores(context, config: NBABoxscoreConfig) -> dict:
    """
    Ingest detailed player boxscore statistics using dlt.
    
    This provides comprehensive per-game player stats including:
    - Points, assists, rebounds, steals, blocks
    - Shooting percentages (FG%, 3P%, FT%)
    - Plus/minus, minutes played
    - And many more...
    
    CRITICAL for ML player performance prediction models!
    
    Note: This fetches data for completed games only.
    Use 'limit' config to control how many games to process.
    """
    try:
        pipeline = dlt.pipeline(
            pipeline_name="nba_stats",
            destination="postgres",
            dataset_name="nba",
        )
        
        context.log.info(f"Starting boxscores extraction (limit={config.limit})...")
        load_info = pipeline.run([
            nba_boxscores_resource(
                limit=config.limit,
                completed_only=config.completed_only,
            )
        ])
        
        context.log.info(f"Boxscores ingested: {load_info}")
        return {
            "status": "success",
            "load_id": str(load_info.loads_ids[0]) if load_info.loads_ids else None,
        }
    except Exception as e:
        context.log.error(f"Boxscores ingestion failed: {e}")
        raise


@asset(
    group_name="nba_ingestion",
    description="Ingest team-level boxscore stats from completed games",
    deps=[nba_games],
)
def nba_team_boxscores(context, config: NBABoxscoreConfig) -> dict:
    """
    Ingest team-level boxscore statistics using dlt.
    
    Aggregates team performance metrics per game:
    - Total points, assists, rebounds
    - Team shooting percentages
    - Turnovers, steals, blocks
    
    Useful for team-based ML prediction models!
    """
    try:
        pipeline = dlt.pipeline(
            pipeline_name="nba_stats",
            destination="postgres",
            dataset_name="nba",
        )
        
        context.log.info(f"Starting team boxscores extraction (limit={config.limit})...")
        load_info = pipeline.run([
            nba_team_boxscores_resource(
                limit=config.limit,
                completed_only=config.completed_only,
            )
        ])
        
        context.log.info(f"Team boxscores ingested: {load_info}")
        return {
            "status": "success",
            "load_id": str(load_info.loads_ids[0]) if load_info.loads_ids else None,
        }
    except Exception as e:
        context.log.error(f"Team boxscores ingestion failed: {e}")
        raise
