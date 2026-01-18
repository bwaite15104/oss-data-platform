"""
dlt pipeline for NBA Stats using official NBA CDN endpoints.

This pipeline extracts data from the NBA CDN (cdn.nba.com) which is more reliable
than the stats.nba.com API which has aggressive bot protection.

Schema definitions are driven by ODCS contracts in contracts/schemas/*.yml
"""

import dlt
from dlt.sources.helpers import requests
from typing import Iterator, Dict, Any, Optional, Set
from datetime import datetime
import time
import logging
import sys
import os
from pathlib import Path

# Optional database connection for incremental loading
try:
    import psycopg2
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

# Add parent directories to path for contract loader
_current_dir = Path(__file__).parent
_project_root = _current_dir.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Import contract loader for schema definitions
try:
    from ingestion.dlt.contract_loader import get_dlt_columns, get_primary_key
    CONTRACTS_AVAILABLE = True
except ImportError:
    CONTRACTS_AVAILABLE = False

# Import configuration from centralized config file
try:
    from ingestion.dlt.config import (
        NBA_CDN_SCHEDULE,
        NBA_CDN_SCOREBOARD,
        NBA_CDN_PLAYERS,
        NBA_CDN_ODDS,
        NBA_CDN_BOXSCORE,
        CDN_HEADERS,
        REQUEST_TIMEOUT,
        RATE_LIMIT_DELAY,
        EAST_CONFERENCE_TEAMS,
        TEAM_DIVISIONS,
    )
    CONFIG_AVAILABLE = True
except ImportError:
    # Fallback defaults if config not available
    CONFIG_AVAILABLE = False
    NBA_CDN_SCHEDULE = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
    NBA_CDN_SCOREBOARD = "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"
    NBA_CDN_PLAYERS = "https://cdn.nba.com/static/json/staticData/playerIndex.json"
    NBA_CDN_ODDS = "https://cdn.nba.com/static/json/liveData/odds/odds_todaysGames.json"
    NBA_CDN_BOXSCORE = "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game_id}.json"
    CDN_HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
    REQUEST_TIMEOUT = 60
    RATE_LIMIT_DELAY = 0.2
    EAST_CONFERENCE_TEAMS = set()
    TEAM_DIVISIONS = {}
    
# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _get_contract_columns(contract_name: str) -> Optional[Dict]:
    """Get columns from contract if available."""
    if CONTRACTS_AVAILABLE:
        try:
            return get_dlt_columns(contract_name)
        except Exception as e:
            logger.warning(f"Could not load contract {contract_name}: {e}")
    return None


def _get_contract_pk(contract_name: str):
    """Get primary key from contract if available."""
    if CONTRACTS_AVAILABLE:
        try:
            pk = get_primary_key(contract_name)
            if pk and len(pk) == 1:
                return pk[0]
            return pk
        except Exception:
            pass
    return None


def _get_existing_game_ids(table_name: str, schema_name: str = None) -> Set[str]:
    """
    Query database for existing game_ids to enable incremental loading.
    
    Returns set of game_ids that already exist in the database.
    Returns empty set if query fails (e.g., table doesn't exist yet).
    """
    if not PSYCOPG2_AVAILABLE:
        logger.warning("psycopg2 not available - cannot check existing games for incremental loading")
        return set()
    
    try:
        # Get connection from environment (same as db_query.py)
        database = (
            os.getenv("NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__DATABASE") or
            os.getenv("POSTGRES_DB") or
            "nba_analytics"
        )
        host = (
            os.getenv("NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__HOST") or
            os.getenv("POSTGRES_HOST") or
            "localhost"
        )
        port = int(os.getenv("POSTGRES_PORT", "5432"))
        user = os.getenv("POSTGRES_USER", "postgres")
        password = os.getenv("POSTGRES_PASSWORD", "postgres")
        
        # Determine schema (use DATA_ENV if available, default to raw_dev)
        if schema_name is None:
            data_env = os.getenv("DATA_ENV", "dev")
            schema_name = f"raw_{data_env}"
        
        # Connect and query
        conn = psycopg2.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
        )
        
        cursor = conn.cursor()
        
        # Query distinct game_ids from the table
        query = f"SELECT DISTINCT game_id FROM {schema_name}.{table_name}"
        cursor.execute(query)
        
        existing_ids = {str(row[0]) for row in cursor.fetchall()}
        
        cursor.close()
        conn.close()
        
        logger.info(f"Found {len(existing_ids)} existing game_ids in {schema_name}.{table_name}")
        return existing_ids
        
    except Exception as e:
        # Table might not exist yet (first run) - this is OK
        logger.debug(f"Could not query existing game_ids (this is OK on first run): {e}")
        return set()

# Note: NBA CDN endpoints and headers are imported from ingestion.dlt.config
# See config.py for: NBA_CDN_SCHEDULE, NBA_CDN_SCOREBOARD, NBA_CDN_PLAYERS, 
# NBA_CDN_ODDS, NBA_CDN_BOXSCORE, CDN_HEADERS, REQUEST_TIMEOUT, RATE_LIMIT_DELAY


def _get_current_season() -> str:
    """Get current NBA season string."""
    now = datetime.now()
    year = now.year
    month = now.month
    
    # NBA season starts in October
    if month >= 10:
        return f"{year}-{str(year + 1)[2:]}"
    else:
        return f"{year - 1}-{str(year)[2:]}"


def _get_conference(team_id: int) -> Optional[str]:
    """Get team conference based on team ID. Uses EAST_CONFERENCE_TEAMS from config."""
    return "East" if team_id in EAST_CONFERENCE_TEAMS else "West"


def _get_division(team_id: int) -> Optional[str]:
    """Get team division based on team ID. Uses TEAM_DIVISIONS from config."""
    return TEAM_DIVISIONS.get(team_id, "Unknown")


@dlt.resource(
    name="teams",
    write_disposition="merge",
    primary_key=_get_contract_pk("nba_teams") or "team_id",
    columns=_get_contract_columns("nba_teams"),
)
def nba_teams_resource() -> Iterator[Dict[str, Any]]:
    """
    Extract NBA team data from the schedule endpoint.
    
    Schema defined in: contracts/schemas/nba_teams.yml
    """
    logger.info("Starting NBA teams extraction from CDN...")
    logger.info(f"Fetching schedule from: {NBA_CDN_SCHEDULE}")
    
    try:
        response = requests.get(NBA_CDN_SCHEDULE, headers=CDN_HEADERS, timeout=60)
        response.raise_for_status()
        logger.info(f"Schedule response status: {response.status_code}")
        
        data = response.json()
        logger.info(f"Schedule data keys: {list(data.keys())}")
        
        # Track unique teams
        teams_seen: Set[int] = set()
        teams_data: Dict[int, Dict[str, Any]] = {}
        
        # Extract teams from schedule games
        game_dates = data.get("leagueSchedule", {}).get("gameDates", [])
        logger.info(f"Processing {len(game_dates)} game dates...")
        
        for game_date in game_dates:
            for game in game_date.get("games", []):
                # Process home team
                home_team = game.get("homeTeam", {})
                team_id = home_team.get("teamId")
                if team_id and team_id not in teams_seen:
                    teams_seen.add(team_id)
                    teams_data[team_id] = {
                        "team_id": team_id,
                        "team_name": home_team.get("teamName"),
                        "team_abbreviation": home_team.get("teamTricode"),
                        "city": home_team.get("teamCity"),
                        "conference": _get_conference(team_id),
                        "division": _get_division(team_id),
                        "is_active": True,
                        "created_at": datetime.now().isoformat(),
                    }
                
                # Process away team
                away_team = game.get("awayTeam", {})
                team_id = away_team.get("teamId")
                if team_id and team_id not in teams_seen:
                    teams_seen.add(team_id)
                    teams_data[team_id] = {
                        "team_id": team_id,
                        "team_name": away_team.get("teamName"),
                        "team_abbreviation": away_team.get("teamTricode"),
                        "city": away_team.get("teamCity"),
                        "conference": _get_conference(team_id),
                        "division": _get_division(team_id),
                        "is_active": True,
                        "created_at": datetime.now().isoformat(),
                    }
        
        logger.info(f"Found {len(teams_data)} unique teams")
        
        for team in teams_data.values():
            logger.info(f"  Yielding team: {team['city']} {team['team_name']}")
            yield team
        
        logger.info("Teams extraction complete!")
        
    except Exception as e:
        logger.error(f"Error fetching teams: {e}")
        raise


@dlt.resource(
    name="games",
    write_disposition="merge",
    primary_key=_get_contract_pk("nba_games") or "game_id",
    columns=_get_contract_columns("nba_games"),
)
def nba_games_resource(
    season: Optional[str] = None,
    limit: Optional[int] = None,
) -> Iterator[Dict[str, Any]]:
    """
    Extract NBA game data from the schedule endpoint.
    
    Args:
        season: NBA season (e.g., "2024-25"). If None, uses current season.
        limit: Maximum number of games to return (for testing). None for all.
    """
    logger.info("Starting NBA games extraction from CDN...")
    logger.info(f"Fetching schedule from: {NBA_CDN_SCHEDULE}")
    
    try:
        response = requests.get(NBA_CDN_SCHEDULE, headers=CDN_HEADERS, timeout=60)
        response.raise_for_status()
        logger.info(f"Schedule response status: {response.status_code}")
        
        data = response.json()
        season = season or _get_current_season()
        logger.info(f"Processing games for season: {season}")
        
        game_count = 0
        game_dates = data.get("leagueSchedule", {}).get("gameDates", [])
        logger.info(f"Found {len(game_dates)} game dates")
        
        for game_date in game_dates:
            for game in game_date.get("games", []):
                if limit and game_count >= limit:
                    logger.info(f"Reached limit of {limit} games")
                    return
                
                home_team = game.get("homeTeam", {})
                away_team = game.get("awayTeam", {})
                
                # Determine winner (if game is finished)
                game_status = game.get("gameStatus", 1)
                winner_team_id = None
                if game_status == 3:  # Game finished
                    home_score = home_team.get("score", 0)
                    away_score = away_team.get("score", 0)
                    if home_score > away_score:
                        winner_team_id = home_team.get("teamId")
                    elif away_score > home_score:
                        winner_team_id = away_team.get("teamId")
                
                game_data = {
                    "game_id": game.get("gameId"),
                    "game_date": game.get("gameDateEst", "").split("T")[0] if game.get("gameDateEst") else None,
                    "season": season,
                    "season_type": "Regular Season",  # Could be enhanced to detect playoffs
                    "home_team_id": home_team.get("teamId"),
                    "away_team_id": away_team.get("teamId"),
                    "home_team_name": f"{home_team.get('teamCity', '')} {home_team.get('teamName', '')}".strip(),
                    "away_team_name": f"{away_team.get('teamCity', '')} {away_team.get('teamName', '')}".strip(),
                    "home_score": home_team.get("score"),
                    "away_score": away_team.get("score"),
                    "winner_team_id": winner_team_id,
                    "game_status": game.get("gameStatusText", "Scheduled"),
                    "venue": game.get("arenaName"),
                    "arena_city": game.get("arenaCity"),
                    "arena_state": game.get("arenaState"),
                    "created_at": datetime.now().isoformat(),
                }
                
                game_count += 1
                yield game_data
        
        logger.info(f"Games extraction complete! Processed {game_count} games")
        
    except Exception as e:
        logger.error(f"Error fetching games: {e}")
        raise


@dlt.resource(
    name="todays_games",
    write_disposition="merge",
    primary_key=_get_contract_pk("nba_todays_games") or "game_id",
    columns=_get_contract_columns("nba_todays_games"),
)
def nba_todays_games_resource() -> Iterator[Dict[str, Any]]:
    """
    Extract today's NBA games from the live scoreboard endpoint.
    
    Schema defined in: contracts/schemas/nba_todays_games.yml
    """
    logger.info("Starting today's games extraction from CDN...")
    logger.info(f"Fetching scoreboard from: {NBA_CDN_SCOREBOARD}")
    
    try:
        response = requests.get(NBA_CDN_SCOREBOARD, headers=CDN_HEADERS, timeout=60)
        response.raise_for_status()
        logger.info(f"Scoreboard response status: {response.status_code}")
        
        data = response.json()
        scoreboard = data.get("scoreboard", {})
        games = scoreboard.get("games", [])
        
        logger.info(f"Found {len(games)} games for {scoreboard.get('gameDate', 'today')}")
        
        for game in games:
            home_team = game.get("homeTeam", {})
            away_team = game.get("awayTeam", {})
            
            # Determine winner (if game is finished)
            game_status = game.get("gameStatus", 1)
            winner_team_id = None
            if game_status == 3:  # Game finished
                home_score = home_team.get("score", 0)
                away_score = away_team.get("score", 0)
                if home_score > away_score:
                    winner_team_id = home_team.get("teamId")
                elif away_score > home_score:
                    winner_team_id = away_team.get("teamId")
            
            game_data = {
                "game_id": game.get("gameId"),
                "game_date": scoreboard.get("gameDate"),
                "season": _get_current_season(),
                "season_type": "Regular Season",
                "home_team_id": home_team.get("teamId"),
                "away_team_id": away_team.get("teamId"),
                "home_team_name": f"{home_team.get('teamCity', '')} {home_team.get('teamName', '')}".strip(),
                "away_team_name": f"{away_team.get('teamCity', '')} {away_team.get('teamName', '')}".strip(),
                "home_score": home_team.get("score"),
                "away_score": away_team.get("score"),
                "winner_team_id": winner_team_id,
                "game_status": game.get("gameStatusText", "Scheduled"),
                "period": game.get("period", 0),
                "game_clock": game.get("gameClock", ""),
                "created_at": datetime.now().isoformat(),
            }
            
            logger.info(f"  Game: {game_data['away_team_name']} @ {game_data['home_team_name']}")
            yield game_data
        
        logger.info("Today's games extraction complete!")
        
    except Exception as e:
        logger.error(f"Error fetching today's games: {e}")
        raise


@dlt.resource(
    name="players",
    write_disposition="merge",
    primary_key=_get_contract_pk("nba_players") or "player_id",
    columns=_get_contract_columns("nba_players"),
)
def nba_players_resource(
    season: Optional[str] = None,
    active_only: bool = True,
) -> Iterator[Dict[str, Any]]:
    """
    Extract NBA player data from the player index CDN endpoint.
    
    This provides a complete roster of all NBA players with detailed info.
    
    Args:
        season: NBA season (not used currently, for API compatibility)
        active_only: If True, only return players with roster_status=1 (active)
    """
    logger.info("Starting NBA players extraction from CDN player index...")
    logger.info(f"Fetching players from: {NBA_CDN_PLAYERS}")
    
    try:
        response = requests.get(NBA_CDN_PLAYERS, headers=CDN_HEADERS, timeout=60)
        response.raise_for_status()
        logger.info(f"Players response status: {response.status_code}")
        
        data = response.json()
        result_sets = data.get("resultSets", [])
        
        if not result_sets:
            logger.warning("No resultSets found in player index response")
            return
        
        result_set = result_sets[0]
        headers = result_set.get("headers", [])
        rows = result_set.get("rowSet", [])
        
        logger.info(f"Found {len(rows)} total players in index")
        
        # Create header index map
        header_idx = {h: i for i, h in enumerate(headers)}
        
        player_count = 0
        for row in rows:
            # Check if active (roster_status = 1)
            roster_status = row[header_idx.get("ROSTER_STATUS", -1)] if "ROSTER_STATUS" in header_idx else None
            if active_only and roster_status != 1:
                continue
            
            player_data = {
                "player_id": row[header_idx["PERSON_ID"]],
                "first_name": row[header_idx["PLAYER_FIRST_NAME"]],
                "last_name": row[header_idx["PLAYER_LAST_NAME"]],
                "player_name": f"{row[header_idx['PLAYER_FIRST_NAME']]} {row[header_idx['PLAYER_LAST_NAME']]}",
                "player_slug": row[header_idx.get("PLAYER_SLUG", -1)] if "PLAYER_SLUG" in header_idx else None,
                "team_id": row[header_idx["TEAM_ID"]],
                "team_name": row[header_idx.get("TEAM_NAME", -1)] if "TEAM_NAME" in header_idx else None,
                "team_city": row[header_idx.get("TEAM_CITY", -1)] if "TEAM_CITY" in header_idx else None,
                "team_abbreviation": row[header_idx.get("TEAM_ABBREVIATION", -1)] if "TEAM_ABBREVIATION" in header_idx else None,
                "jersey_number": row[header_idx.get("JERSEY_NUMBER", -1)] if "JERSEY_NUMBER" in header_idx else None,
                "position": row[header_idx.get("POSITION", -1)] if "POSITION" in header_idx else None,
                "height": row[header_idx.get("HEIGHT", -1)] if "HEIGHT" in header_idx else None,
                "weight": row[header_idx.get("WEIGHT", -1)] if "WEIGHT" in header_idx else None,
                "college": row[header_idx.get("COLLEGE", -1)] if "COLLEGE" in header_idx else None,
                "country": row[header_idx.get("COUNTRY", -1)] if "COUNTRY" in header_idx else None,
                "draft_year": row[header_idx.get("DRAFT_YEAR", -1)] if "DRAFT_YEAR" in header_idx else None,
                "draft_round": row[header_idx.get("DRAFT_ROUND", -1)] if "DRAFT_ROUND" in header_idx else None,
                "draft_number": row[header_idx.get("DRAFT_NUMBER", -1)] if "DRAFT_NUMBER" in header_idx else None,
                "from_year": row[header_idx.get("FROM_YEAR", -1)] if "FROM_YEAR" in header_idx else None,
                "to_year": row[header_idx.get("TO_YEAR", -1)] if "TO_YEAR" in header_idx else None,
                "career_pts": row[header_idx.get("PTS", -1)] if "PTS" in header_idx else None,
                "career_reb": row[header_idx.get("REB", -1)] if "REB" in header_idx else None,
                "career_ast": row[header_idx.get("AST", -1)] if "AST" in header_idx else None,
                "created_at": datetime.now().isoformat(),
            }
            
            player_count += 1
            yield player_data
        
        logger.info(f"Players extraction complete! Yielded {player_count} active players")
        
    except Exception as e:
        logger.error(f"Error fetching players: {e}")
        raise


@dlt.resource(
    name="betting_odds",
    write_disposition="merge",
    primary_key=_get_contract_pk("nba_betting_odds") or ["game_id", "book_name", "market_type"],
    columns=_get_contract_columns("nba_betting_odds"),
)
def nba_betting_odds_resource() -> Iterator[Dict[str, Any]]:
    """
    Extract betting odds for today's NBA games.
    
    Schema defined in: contracts/schemas/nba_betting_odds.yml
    Critical for ML betting models!
    """
    logger.info("Starting betting odds extraction from CDN...")
    logger.info(f"Fetching odds from: {NBA_CDN_ODDS}")
    
    try:
        response = requests.get(NBA_CDN_ODDS, headers=CDN_HEADERS, timeout=60)
        response.raise_for_status()
        logger.info(f"Odds response status: {response.status_code}")
        
        data = response.json()
        games = data.get("games", [])
        
        logger.info(f"Found odds for {len(games)} games")
        
        odds_count = 0
        for game in games:
            game_id = game.get("gameId")
            markets = game.get("markets", [])
            
            for market in markets:
                market_type = market.get("name", "unknown")
                books = market.get("books", [])
                
                for book in books:
                    book_name = book.get("name", "unknown")
                    outcomes = book.get("outcomes", [])
                    
                    # Parse outcomes into home/away odds
                    home_odds = None
                    away_odds = None
                    home_line = None
                    away_line = None
                    over_odds = None
                    under_odds = None
                    total_line = None
                    
                    for outcome in outcomes:
                        outcome_type = outcome.get("type", "")
                        odds = outcome.get("odds")
                        line = outcome.get("line")
                        
                        if outcome_type == "home":
                            home_odds = odds
                            home_line = line
                        elif outcome_type == "away":
                            away_odds = odds
                            away_line = line
                        elif outcome_type == "over":
                            over_odds = odds
                            total_line = line
                        elif outcome_type == "under":
                            under_odds = odds
                    
                    odds_data = {
                        "game_id": game_id,
                        "book_name": book_name,
                        "market_type": market_type,
                        "home_odds": home_odds,
                        "away_odds": away_odds,
                        "home_line": home_line,
                        "away_line": away_line,
                        "over_odds": over_odds,
                        "under_odds": under_odds,
                        "total_line": total_line,
                        "captured_at": datetime.now().isoformat(),
                    }
                    
                    odds_count += 1
                    yield odds_data
        
        logger.info(f"Betting odds extraction complete! Yielded {odds_count} odds records")
        
    except Exception as e:
        logger.error(f"Error fetching betting odds: {e}")
        raise


@dlt.resource(
    name="boxscores",
    write_disposition="merge",
    primary_key=_get_contract_pk("nba_boxscores") or ["game_id", "player_id"],
    columns=_get_contract_columns("nba_boxscores"),
)
def nba_boxscores_resource(
    game_ids: Optional[list] = None,
    fetch_from_schedule: bool = True,
    completed_only: bool = True,
    limit: Optional[int] = None,
    skip_existing: bool = True,
) -> Iterator[Dict[str, Any]]:
    """
    Extract detailed boxscore data (player stats per game).
    
    This provides comprehensive player statistics for each game including:
    - Points, assists, rebounds
    - Field goal %, 3P%, FT%
    - Plus/minus, minutes played
    - And many more...
    
    Critical for ML player performance models!
    
    Args:
        game_ids: List of specific game IDs to fetch. If None, fetches from schedule.
        fetch_from_schedule: If True and game_ids is None, gets game IDs from schedule.
        completed_only: Only fetch boxscores for completed games (status=3).
        limit: Maximum number of games to process (for testing).
        skip_existing: If True, skip games that already exist in database (incremental loading).
    """
    logger.info("Starting boxscores extraction from CDN...")
    
    try:
        # Get game IDs to process
        if game_ids:
            games_to_process = game_ids
        elif fetch_from_schedule:
            # Fetch game IDs from schedule
            logger.info("Fetching game IDs from schedule...")
            response = requests.get(NBA_CDN_SCHEDULE, headers=CDN_HEADERS, timeout=60)
            response.raise_for_status()
            schedule_data = response.json()
            
            games_to_process = []
            for game_date in schedule_data.get("leagueSchedule", {}).get("gameDates", []):
                for game in game_date.get("games", []):
                    game_status = game.get("gameStatus", 1)
                    if completed_only and game_status != 3:
                        continue
                    games_to_process.append(game.get("gameId"))
                    
                    if limit and len(games_to_process) >= limit:
                        break
                if limit and len(games_to_process) >= limit:
                    break
            
            logger.info(f"Found {len(games_to_process)} completed games in schedule")
        else:
            logger.warning("No game IDs provided and fetch_from_schedule is False")
            return
        
        # Apply incremental loading: filter out existing games
        if skip_existing and games_to_process:
            existing_game_ids = _get_existing_game_ids("boxscores")
            initial_count = len(games_to_process)
            games_to_process = [gid for gid in games_to_process if str(gid) not in existing_game_ids]
            skipped_count = initial_count - len(games_to_process)
            if skipped_count > 0:
                logger.info(f"⏭️  Skipping {skipped_count} existing games (incremental loading). {len(games_to_process)} new games to fetch.")
            else:
                logger.info(f"✅ No existing games found. Will fetch all {len(games_to_process)} games.")
        
        # Apply limit (after filtering existing games)
        if limit:
            games_to_process = games_to_process[:limit]
        
        player_stats_count = 0
        for game_id in games_to_process:
            boxscore_url = NBA_CDN_BOXSCORE.format(game_id=game_id)
            
            try:
                response = requests.get(boxscore_url, headers=CDN_HEADERS, timeout=30)
                response.raise_for_status()
                boxscore_data = response.json()
                
                game = boxscore_data.get("game", {})
                game_date = game.get("gameTimeUTC", "").split("T")[0]
                
                # Process both home and away teams
                for team_type in ["homeTeam", "awayTeam"]:
                    team = game.get(team_type, {})
                    team_id = team.get("teamId")
                    is_home = team_type == "homeTeam"
                    
                    for player in team.get("players", []):
                        stats = player.get("statistics", {})
                        
                        player_stats = {
                            "game_id": game_id,
                            "game_date": game_date,
                            "player_id": player.get("personId"),
                            "player_name": player.get("name"),
                            "team_id": team_id,
                            "is_home": is_home,
                            "is_starter": player.get("starter") == "1",
                            "minutes": stats.get("minutes", "0:00"),
                            "minutes_calculated": stats.get("minutesCalculated", "0:00"),
                            "points": stats.get("points", 0),
                            "assists": stats.get("assists", 0),
                            "rebounds_total": stats.get("reboundsTotal", 0),
                            "rebounds_offensive": stats.get("reboundsOffensive", 0),
                            "rebounds_defensive": stats.get("reboundsDefensive", 0),
                            "steals": stats.get("steals", 0),
                            "blocks": stats.get("blocks", 0),
                            "turnovers": stats.get("turnovers", 0),
                            "fouls_personal": stats.get("foulsPersonal", 0),
                            "fouls_technical": stats.get("foulsTechnical", 0),
                            "field_goals_made": stats.get("fieldGoalsMade", 0),
                            "field_goals_attempted": stats.get("fieldGoalsAttempted", 0),
                            "field_goal_pct": stats.get("fieldGoalsPercentage", 0),
                            "three_pointers_made": stats.get("threePointersMade", 0),
                            "three_pointers_attempted": stats.get("threePointersAttempted", 0),
                            "three_point_pct": stats.get("threePointersPercentage", 0),
                            "free_throws_made": stats.get("freeThrowsMade", 0),
                            "free_throws_attempted": stats.get("freeThrowsAttempted", 0),
                            "free_throw_pct": stats.get("freeThrowsPercentage", 0),
                            "plus_minus": stats.get("plusMinusPoints", 0),
                            "points_fast_break": stats.get("pointsFastBreak", 0),
                            "points_in_paint": stats.get("pointsInThePaint", 0),
                            "points_second_chance": stats.get("pointsSecondChance", 0),
                            "created_at": datetime.now().isoformat(),
                        }
                        
                        player_stats_count += 1
                        yield player_stats
                
                # Rate limiting between games
                time.sleep(0.2)
                
            except Exception as e:
                logger.warning(f"Error fetching boxscore for game {game_id}: {e}")
                continue
        
        logger.info(f"Boxscores extraction complete! Yielded {player_stats_count} player stats records")
        
    except Exception as e:
        logger.error(f"Error in boxscores extraction: {e}")
        raise


@dlt.resource(
    name="team_boxscores",
    write_disposition="merge",
    primary_key=_get_contract_pk("nba_team_boxscores") or ["game_id", "team_id"],
    columns=_get_contract_columns("nba_team_boxscores"),
)
def nba_team_boxscores_resource(
    game_ids: Optional[list] = None,
    fetch_from_schedule: bool = True,
    completed_only: bool = True,
    limit: Optional[int] = None,
    skip_existing: bool = True,
) -> Iterator[Dict[str, Any]]:
    """
    Extract team-level boxscore data (team stats per game).
    
    Aggregates team performance metrics for each game.
    Useful for team-based ML models.
    
    Args:
        game_ids: List of specific game IDs to fetch.
        fetch_from_schedule: If True, gets game IDs from schedule.
        completed_only: Only fetch boxscores for completed games.
        limit: Maximum number of games to process.
        skip_existing: If True, skip games that already exist in database (incremental loading).
    """
    logger.info("Starting team boxscores extraction from CDN...")
    
    try:
        # Get game IDs to process (same logic as player boxscores)
        if game_ids:
            games_to_process = game_ids
        elif fetch_from_schedule:
            logger.info("Fetching game IDs from schedule...")
            response = requests.get(NBA_CDN_SCHEDULE, headers=CDN_HEADERS, timeout=60)
            response.raise_for_status()
            schedule_data = response.json()
            
            games_to_process = []
            for game_date in schedule_data.get("leagueSchedule", {}).get("gameDates", []):
                for game in game_date.get("games", []):
                    game_status = game.get("gameStatus", 1)
                    if completed_only and game_status != 3:
                        continue
                    games_to_process.append(game.get("gameId"))
                    
                    if limit and len(games_to_process) >= limit:
                        break
                if limit and len(games_to_process) >= limit:
                    break
            
            logger.info(f"Found {len(games_to_process)} completed games in schedule")
        else:
            return
        
        # Apply incremental loading: filter out existing games
        if skip_existing and games_to_process:
            existing_game_ids = _get_existing_game_ids("team_boxscores")
            initial_count = len(games_to_process)
            games_to_process = [gid for gid in games_to_process if str(gid) not in existing_game_ids]
            skipped_count = initial_count - len(games_to_process)
            if skipped_count > 0:
                logger.info(f"⏭️  Skipping {skipped_count} existing games (incremental loading). {len(games_to_process)} new games to fetch.")
            else:
                logger.info(f"✅ No existing games found. Will fetch all {len(games_to_process)} games.")
        
        # Apply limit (after filtering existing games)
        if limit:
            games_to_process = games_to_process[:limit]
        
        team_stats_count = 0
        for game_id in games_to_process:
            boxscore_url = NBA_CDN_BOXSCORE.format(game_id=game_id)
            
            try:
                response = requests.get(boxscore_url, headers=CDN_HEADERS, timeout=30)
                response.raise_for_status()
                boxscore_data = response.json()
                
                game = boxscore_data.get("game", {})
                game_date = game.get("gameTimeUTC", "").split("T")[0]
                
                for team_type in ["homeTeam", "awayTeam"]:
                    team = game.get(team_type, {})
                    is_home = team_type == "homeTeam"
                    
                    # Aggregate player stats for team totals
                    team_totals = {
                        "points": 0, "assists": 0, "rebounds_total": 0,
                        "steals": 0, "blocks": 0, "turnovers": 0,
                        "field_goals_made": 0, "field_goals_attempted": 0,
                        "three_pointers_made": 0, "three_pointers_attempted": 0,
                        "free_throws_made": 0, "free_throws_attempted": 0,
                    }
                    
                    for player in team.get("players", []):
                        stats = player.get("statistics", {})
                        team_totals["points"] += stats.get("points", 0) or 0
                        team_totals["assists"] += stats.get("assists", 0) or 0
                        team_totals["rebounds_total"] += stats.get("reboundsTotal", 0) or 0
                        team_totals["steals"] += stats.get("steals", 0) or 0
                        team_totals["blocks"] += stats.get("blocks", 0) or 0
                        team_totals["turnovers"] += stats.get("turnovers", 0) or 0
                        team_totals["field_goals_made"] += stats.get("fieldGoalsMade", 0) or 0
                        team_totals["field_goals_attempted"] += stats.get("fieldGoalsAttempted", 0) or 0
                        team_totals["three_pointers_made"] += stats.get("threePointersMade", 0) or 0
                        team_totals["three_pointers_attempted"] += stats.get("threePointersAttempted", 0) or 0
                        team_totals["free_throws_made"] += stats.get("freeThrowsMade", 0) or 0
                        team_totals["free_throws_attempted"] += stats.get("freeThrowsAttempted", 0) or 0
                    
                    # Calculate percentages
                    fg_pct = (team_totals["field_goals_made"] / team_totals["field_goals_attempted"] * 100) if team_totals["field_goals_attempted"] > 0 else 0
                    three_pct = (team_totals["three_pointers_made"] / team_totals["three_pointers_attempted"] * 100) if team_totals["three_pointers_attempted"] > 0 else 0
                    ft_pct = (team_totals["free_throws_made"] / team_totals["free_throws_attempted"] * 100) if team_totals["free_throws_attempted"] > 0 else 0
                    
                    team_stats = {
                        "game_id": game_id,
                        "game_date": game_date,
                        "team_id": team.get("teamId"),
                        "team_name": team.get("teamName"),
                        "team_city": team.get("teamCity"),
                        "is_home": is_home,
                        "points": team_totals["points"],
                        "assists": team_totals["assists"],
                        "rebounds_total": team_totals["rebounds_total"],
                        "steals": team_totals["steals"],
                        "blocks": team_totals["blocks"],
                        "turnovers": team_totals["turnovers"],
                        "field_goals_made": team_totals["field_goals_made"],
                        "field_goals_attempted": team_totals["field_goals_attempted"],
                        "field_goal_pct": round(fg_pct, 1),
                        "three_pointers_made": team_totals["three_pointers_made"],
                        "three_pointers_attempted": team_totals["three_pointers_attempted"],
                        "three_point_pct": round(three_pct, 1),
                        "free_throws_made": team_totals["free_throws_made"],
                        "free_throws_attempted": team_totals["free_throws_attempted"],
                        "free_throw_pct": round(ft_pct, 1),
                        "created_at": datetime.now().isoformat(),
                    }
                    
                    team_stats_count += 1
                    yield team_stats
                
                time.sleep(0.2)
                
            except Exception as e:
                logger.warning(f"Error fetching boxscore for game {game_id}: {e}")
                continue
        
        logger.info(f"Team boxscores extraction complete! Yielded {team_stats_count} team stats records")
        
    except Exception as e:
        logger.error(f"Error in team boxscores extraction: {e}")
        raise


@dlt.source
def nba_stats(
    season: Optional[str] = None,
    include_players: bool = False,
    include_teams: bool = True,
    include_games: bool = True,
    include_todays_games: bool = False,
    games_limit: Optional[int] = None,
):
    """
    NBA Stats source using reliable CDN endpoints.
    
    Args:
        season: NBA season (e.g., "2024-25"). Defaults to current season.
        include_players: Whether to include player data (limited from game leaders).
        include_teams: Whether to include team data.
        include_games: Whether to include full season game schedule.
        include_todays_games: Whether to include today's live games.
        games_limit: Limit number of games (for testing).
    """
    resources = []
    
    if include_teams:
        resources.append(nba_teams_resource())
    
    if include_players:
        resources.append(nba_players_resource(season=season))
    
    if include_games:
        resources.append(nba_games_resource(season=season, limit=games_limit))
    
    if include_todays_games:
        resources.append(nba_todays_games_resource())
    
    return resources


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="NBA Stats CDN dlt pipeline")
    parser.add_argument("--season", type=str, default=None, help="NBA season (e.g., 2024-25)")
    parser.add_argument("--teams", action="store_true", default=True, help="Include teams")
    parser.add_argument("--players", action="store_true", default=False, help="Include players")
    parser.add_argument("--games", action="store_true", default=True, help="Include games")
    parser.add_argument("--todays-games", action="store_true", default=False, help="Include today's games")
    parser.add_argument("--games-limit", type=int, default=None, help="Limit games (for testing)")
    parser.add_argument("--dry-run", action="store_true", help="Print data without loading to DB")
    
    args = parser.parse_args()
    
    if args.dry_run:
        # Just print data for testing
        print("=== DRY RUN - Testing NBA CDN endpoints ===\n")
        
        if args.teams:
            print("--- Teams ---")
            for team in nba_teams_resource():
                print(f"  {team['city']} {team['team_name']} ({team['team_abbreviation']})")
        
        if args.todays_games:
            print("\n--- Today's Games ---")
            for game in nba_todays_games_resource():
                print(f"  {game['away_team_name']} @ {game['home_team_name']} - {game['game_status']}")
    else:
        # Create and run pipeline
        pipeline = dlt.pipeline(
            pipeline_name="nba_stats",
            destination="postgres",
            dataset_name="nba",
        )
        
        load_info = pipeline.run(
            nba_stats(
                season=args.season,
                include_teams=args.teams,
                include_players=args.players,
                include_games=args.games,
                include_todays_games=args.todays_games,
                games_limit=args.games_limit,
            )
        )
        
        print(f"✅ Load complete: {load_info}")
