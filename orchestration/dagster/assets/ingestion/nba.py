"""NBA data ingestion assets using reliable NBA CDN endpoints."""

from dagster import asset, Config, AutomationCondition
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
from ingestion.dlt.pipelines.nba_injuries import nba_injuries_resource
from ingestion.dlt.config import DATASET_NAME, PIPELINE_NAME, DESTINATION

# Historical backfill is imported lazily inside nba_historical_backfill() only when that asset runs.
# This avoids loading basketball-reference-scraper (and Selenium) when running other ingestion assets,
# which would otherwise trigger Chrome/session errors in Docker for every worker.


class NBAIngestionConfig(Config):
    """Configuration for NBA data ingestion."""
    season: Optional[str] = Field(default=None, description="NBA season (e.g., 2024-25). Defaults to current season.")
    games_limit: Optional[int] = Field(default=None, description="Limit number of games (for testing)")


class NBABoxscoreConfig(Config):
    """Configuration for boxscore ingestion."""
    limit: Optional[int] = Field(default=None, description="Limit number of games (None = all completed games)")
    completed_only: bool = Field(default=True, description="Only fetch completed games")


@asset(
    group_name="nba_ingestion",
    description="Ingest NBA teams data from NBA CDN",
    automation_condition=AutomationCondition.on_cron("@daily"),  # Daily refresh
)
def nba_teams(context, config: NBAIngestionConfig) -> dict:
    """
    Ingest NBA teams data using dlt.
    
    Extracts all NBA teams from the season schedule CDN endpoint.
    This is reliable and fast (no bot protection issues).
    """
    try:
        # Create dlt pipeline
        # Credentials are provided via environment variables
        # Dataset name is environment-aware (raw_dev, raw_staging, raw_prod)
        try:
            pipeline = dlt.pipeline(
                pipeline_name=PIPELINE_NAME,
                destination=DESTINATION,
                dataset_name=DATASET_NAME,
            )
        except OSError as e:
            # Handle case where pipeline state directory already exists (concurrent execution)
            if "File exists" in str(e) or e.errno == 17:
                context.log.warning(f"Pipeline state directory already exists, reusing: {e}")
                pipeline = dlt.pipeline(
                    pipeline_name=PIPELINE_NAME,
                    destination=DESTINATION,
                    dataset_name=DATASET_NAME,
                )
            else:
                raise
        
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
    automation_condition=AutomationCondition.on_cron("@daily"),  # Daily refresh
)
def nba_players(context, config: NBAIngestionConfig) -> dict:
    """
    Ingest NBA players data using dlt.
    
    Note: The reliable CDN endpoints only include players from today's game leaders.
    For a complete player roster, you would need to use a third-party API.
    """
    try:
        # Create dlt pipeline - handle case where pipeline state directory already exists
        try:
            pipeline = dlt.pipeline(
                pipeline_name=PIPELINE_NAME,
                destination=DESTINATION,
                dataset_name=DATASET_NAME,
            )
        except OSError as e:
            if "File exists" in str(e) or e.errno == 17:
                context.log.warning(f"Pipeline state directory already exists, reusing: {e}")
                pipeline = dlt.pipeline(
                    pipeline_name=PIPELINE_NAME,
                    destination=DESTINATION,
                    dataset_name=DATASET_NAME,
                )
            else:
                raise
        
        context.log.info(f"Starting NBA players extraction to {DATASET_NAME}...")
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
    automation_condition=AutomationCondition.eager(),  # Run when teams update
)
def nba_games(context, config: NBAIngestionConfig) -> dict:
    """
    Ingest NBA games (full season schedule) using dlt.
    
    Extracts all games from the season schedule CDN endpoint.
    This includes ~1,300+ games for a full season.
    """
    try:
        # Create dlt pipeline - handle case where pipeline state directory already exists
        try:
            pipeline = dlt.pipeline(
                pipeline_name=PIPELINE_NAME,
                destination=DESTINATION,
                dataset_name=DATASET_NAME,
            )
        except OSError as e:
            if "File exists" in str(e) or e.errno == 17:
                context.log.warning(f"Pipeline state directory already exists, reusing: {e}")
                pipeline = dlt.pipeline(
                    pipeline_name=PIPELINE_NAME,
                    destination=DESTINATION,
                    dataset_name=DATASET_NAME,
                )
            else:
                raise
        
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
    automation_condition=AutomationCondition.on_cron("@daily"),  # Daily refresh
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
        # Create dlt pipeline - handle case where pipeline state directory already exists
        try:
            pipeline = dlt.pipeline(
                pipeline_name=PIPELINE_NAME,
                destination=DESTINATION,
                dataset_name=DATASET_NAME,
            )
        except OSError as e:
            if "File exists" in str(e) or e.errno == 17:
                context.log.warning(f"Pipeline state directory already exists, reusing: {e}")
                pipeline = dlt.pipeline(
                    pipeline_name=PIPELINE_NAME,
                    destination=DESTINATION,
                    dataset_name=DATASET_NAME,
                )
            else:
                raise
        
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
    automation_condition=AutomationCondition.on_cron("@daily"),  # Daily refresh
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
        # Create dlt pipeline - handle case where pipeline state directory already exists
        try:
            pipeline = dlt.pipeline(
                pipeline_name=PIPELINE_NAME,
                destination=DESTINATION,
                dataset_name=DATASET_NAME,
            )
        except OSError as e:
            if "File exists" in str(e) or e.errno == 17:
                context.log.warning(f"Pipeline state directory already exists, reusing: {e}")
                pipeline = dlt.pipeline(
                    pipeline_name=PIPELINE_NAME,
                    destination=DESTINATION,
                    dataset_name=DATASET_NAME,
                )
            else:
                raise
        
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
    automation_condition=AutomationCondition.eager(),  # Run when games update
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
        # Create dlt pipeline - handle case where pipeline state directory already exists
        try:
            pipeline = dlt.pipeline(
                pipeline_name=PIPELINE_NAME,
                destination=DESTINATION,
                dataset_name=DATASET_NAME,
            )
        except OSError as e:
            if "File exists" in str(e) or e.errno == 17:
                context.log.warning(f"Pipeline state directory already exists, reusing: {e}")
                pipeline = dlt.pipeline(
                    pipeline_name=PIPELINE_NAME,
                    destination=DESTINATION,
                    dataset_name=DATASET_NAME,
                )
            else:
                raise
        
        context.log.info(f"Starting boxscores extraction (limit={config.limit})...")
        
        # Handle case where incremental loading results in 0 games to process
        # dlt has issues normalizing empty resources, so we check first
        resource = nba_boxscores_resource(
            limit=config.limit,
            completed_only=config.completed_only,
        )
        
        # Try to get first item to see if resource has data
        # If it's empty (all games skipped), handle gracefully
        try:
            load_info = pipeline.run([resource])
            context.log.info(f"Boxscores ingested: {load_info}")
            return {
                "status": "success",
                "load_id": str(load_info.loads_ids[0]) if load_info.loads_ids else None,
            }
        except FileNotFoundError as e:
            # Handle dlt issue when resource yields no data (incremental loading skipped all games)
            # dlt can't normalize empty resources and raises FileNotFoundError for missing normalize directories
            error_str = str(e).lower()
            if "new_jobs" in error_str or "started_jobs" in error_str or "normalize" in error_str:
                context.log.info("No new games to process (all games already ingested). Skipping.")
                return {
                    "status": "success",
                    "load_id": None,
                    "message": "No new games - all already ingested (incremental loading)",
                }
            raise
    except Exception as e:
        context.log.error(f"Boxscores ingestion failed: {e}")
        raise


@asset(
    group_name="nba_ingestion",
    description="Ingest team-level boxscore stats from completed games",
    deps=[nba_games],
    automation_condition=AutomationCondition.eager(),  # Run when games update
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
        # Create dlt pipeline - handle case where pipeline state directory already exists
        # This can happen when multiple runs execute concurrently or directory wasn't cleaned up
        try:
            pipeline = dlt.pipeline(
                pipeline_name=PIPELINE_NAME,
                destination=DESTINATION,
                dataset_name=DATASET_NAME,
            )
        except OSError as e:
            # Handle case where pipeline state directory already exists
            # This can happen in concurrent executions or if cleanup didn't happen
            if "File exists" in str(e) or e.errno == 17:
                context.log.warning(f"Pipeline state directory already exists, reusing: {e}")
                # Try to create pipeline with existing state
                pipeline = dlt.pipeline(
                    pipeline_name=PIPELINE_NAME,
                    destination=DESTINATION,
                    dataset_name=DATASET_NAME,
                )
            else:
                raise
        
        context.log.info(f"Starting team boxscores extraction (limit={config.limit})...")
        
        # Handle case where incremental loading results in 0 games to process
        resource = nba_team_boxscores_resource(
            limit=config.limit,
            completed_only=config.completed_only,
        )
        
        try:
            load_info = pipeline.run([resource])
            context.log.info(f"Team boxscores ingested: {load_info}")
            return {
                "status": "success",
                "load_id": str(load_info.loads_ids[0]) if load_info.loads_ids else None,
            }
        except FileNotFoundError as e:
            # Handle dlt issue when resource yields no data (incremental loading skipped all games)
            # dlt can't normalize empty resources and raises FileNotFoundError for missing normalize directories
            error_str = str(e).lower()
            if "new_jobs" in error_str or "started_jobs" in error_str or "normalize" in error_str:
                context.log.info("No new games to process (all games already ingested). Skipping.")
                return {
                    "status": "success",
                    "load_id": None,
                    "message": "No new games - all already ingested (incremental loading)",
                }
            raise
        except OSError as e:
            # Handle concurrent directory creation issues
            if "File exists" in str(e) or e.errno == 17:
                context.log.warning(f"Concurrent execution detected (directory already exists), retrying: {e}")
                # Retry the pipeline run - dlt should handle existing state
                try:
                    load_info = pipeline.run([resource])
                    context.log.info(f"Team boxscores ingested (after retry): {load_info}")
                    return {
                        "status": "success",
                        "load_id": str(load_info.loads_ids[0]) if load_info.loads_ids else None,
                    }
                except Exception as retry_error:
                    context.log.error(f"Retry also failed: {retry_error}")
                    raise
            raise
    except Exception as e:
        context.log.error(f"Team boxscores ingestion failed: {e}")
        raise


@asset(
    group_name="nba_ingestion",
    compute_kind="python",
    description="NBA player injury data from ESPN (point-in-time snapshot)",
    automation_condition=AutomationCondition.on_cron("@daily"),  # Daily refresh
)
def nba_injuries(context) -> dict:
    """
    Ingest current NBA injury data from ESPN.
    
    This is a point-in-time snapshot - should be run daily to build historical data.
    """
    context.log.info("Starting injuries extraction from ESPN...")
    
    # Create dlt pipeline - handle case where pipeline state directory already exists
    try:
        pipeline = dlt.pipeline(
            pipeline_name=PIPELINE_NAME,
            destination=DESTINATION,
            dataset_name=DATASET_NAME,
        )
    except OSError as e:
        if "File exists" in str(e) or e.errno == 17:
            context.log.warning(f"Pipeline state directory already exists, reusing: {e}")
            pipeline = dlt.pipeline(
                pipeline_name=PIPELINE_NAME,
                destination=DESTINATION,
                dataset_name=DATASET_NAME,
            )
        else:
            raise
    
    try:
        load_info = pipeline.run([nba_injuries_resource()])
        
        context.log.info(f"Injuries ingested: {load_info}")
        return {
            "status": "success",
            "load_id": str(load_info.loads_ids[0]) if load_info.loads_ids else None,
        }
    except Exception as e:
        context.log.error(f"Injuries ingestion failed: {e}")
        raise


class HistoricalBackfillConfig(Config):
    """Configuration for historical data backfill."""
    season: str = Field(description="NBA season to backfill (e.g., '2023-24')")
    team: Optional[str] = Field(default=None, description="Optional: specific team abbreviation (e.g., 'LAL') or None for all teams")
    limit: Optional[int] = Field(default=None, description="Optional: limit number of games (for testing)")


@asset(
    group_name="nba_ingestion",
    compute_kind="python",
    description="Backfill historical NBA boxscores from Basketball Reference (free source)",
    # No automation_condition - this is manual/occasional use only
)
def nba_historical_backfill(context, config: HistoricalBackfillConfig) -> dict:
    """
    Backfill historical NBA boxscores from Basketball Reference.
    
    This is for occasional use to backfill past seasons (e.g., 2023-24, 2022-23)
    to get more training data for star player return features.
    
    Uses basketball-reference-scraper (free, open-source).
    Automatically converts Basketball Reference format to our schema.
    
    Note: This can take a long time (30 teams × ~82 games × 2 seconds = ~1.5 hours per season).
    Consider using 'team' and 'limit' config for testing first.
    
    Args:
        season: NBA season (e.g., "2023-24")
        team: Optional team abbreviation (e.g., "LAL") - if None, processes all teams
        limit: Optional limit on number of games (for testing)
    """
    context.log.info(f"Starting historical backfill for season {config.season}...")
    if config.team:
        context.log.info(f"Processing team: {config.team}")
    if config.limit:
        context.log.info(f"Limit: {config.limit} games")
    
    # Create dlt pipeline - handle case where pipeline state directory already exists
    try:
        pipeline = dlt.pipeline(
            pipeline_name="nba_historical_backfill",
            destination=DESTINATION,
            dataset_name=DATASET_NAME,
        )
    except OSError as e:
        if "File exists" in str(e) or e.errno == 17:
            context.log.warning(f"Pipeline state directory already exists, reusing: {e}")
            pipeline = dlt.pipeline(
                pipeline_name="nba_historical_backfill",
                destination=DESTINATION,
                dataset_name=DATASET_NAME,
            )
        else:
            raise
    
    try:
        from ingestion.dlt.pipelines.nba_historical_backfill import nba_historical_backfill as historical_backfill_source
    except ImportError as e:
        raise ImportError(
            "basketball-reference-scraper not available. "
            "Install with: pip install basketball-reference-scraper"
        ) from e

    try:
        load_info = pipeline.run(
            historical_backfill_source(
                season=config.season,
                team=config.team,
                skip_existing=True,  # Always skip existing games
                limit=config.limit,
            )
        )
        
        context.log.info(f"Historical backfill complete: {load_info}")
        return {
            "status": "success",
            "season": config.season,
            "team": config.team,
            "load_id": str(load_info.loads_ids[0]) if load_info.loads_ids else None,
        }
    except Exception as e:
        context.log.error(f"Historical backfill failed: {e}")
        raise
