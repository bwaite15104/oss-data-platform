"""
dlt pipeline for NBA Stats using official NBA CDN endpoints.

This pipeline extracts data from the NBA CDN (cdn.nba.com) which is more reliable
than the stats.nba.com API which has aggressive bot protection.
"""

import dlt
from dlt.sources.helpers import requests
from typing import Iterator, Dict, Any, Optional, Set
from datetime import datetime
import time
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Reliable NBA CDN endpoints
NBA_CDN_SCOREBOARD = "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"
NBA_CDN_SCHEDULE = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json"
NBA_CDN_PLAYERS = "https://cdn.nba.com/static/json/staticData/playerIndex.json"

# Common headers for NBA CDN requests
CDN_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


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
    """Get team conference based on team ID."""
    east_teams = {
        1610612737,  # Hawks
        1610612738,  # Celtics
        1610612751,  # Nets
        1610612766,  # Hornets
        1610612741,  # Bulls
        1610612739,  # Cavaliers
        1610612765,  # Pistons
        1610612754,  # Pacers
        1610612748,  # Heat
        1610612749,  # Bucks
        1610612752,  # Knicks
        1610612753,  # Magic
        1610612755,  # 76ers
        1610612761,  # Raptors
        1610612764,  # Wizards
    }
    return "East" if team_id in east_teams else "West"


def _get_division(team_id: int) -> Optional[str]:
    """Get team division based on team ID."""
    divisions = {
        # Atlantic
        1610612738: "Atlantic", 1610612751: "Atlantic", 1610612752: "Atlantic",
        1610612755: "Atlantic", 1610612761: "Atlantic",
        # Central
        1610612741: "Central", 1610612739: "Central", 1610612765: "Central",
        1610612754: "Central", 1610612749: "Central",
        # Southeast
        1610612737: "Southeast", 1610612766: "Southeast", 1610612748: "Southeast",
        1610612753: "Southeast", 1610612764: "Southeast",
        # Northwest
        1610612743: "Northwest", 1610612750: "Northwest", 1610612760: "Northwest",
        1610612757: "Northwest", 1610612762: "Northwest",
        # Pacific
        1610612744: "Pacific", 1610612746: "Pacific", 1610612747: "Pacific",
        1610612756: "Pacific", 1610612758: "Pacific",
        # Southwest
        1610612742: "Southwest", 1610612745: "Southwest", 1610612763: "Southwest",
        1610612740: "Southwest", 1610612759: "Southwest",
    }
    return divisions.get(team_id, "Unknown")


@dlt.resource(
    name="teams",
    write_disposition="merge",
    primary_key="team_id",
)
def nba_teams_resource() -> Iterator[Dict[str, Any]]:
    """
    Extract NBA team data from the schedule endpoint.
    
    This extracts unique teams from the full season schedule, which is more reliable
    than the stats.nba.com API.
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
    primary_key="game_id",
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
    primary_key="game_id",
)
def nba_todays_games_resource() -> Iterator[Dict[str, Any]]:
    """
    Extract today's NBA games from the live scoreboard endpoint.
    
    This provides more real-time data including live scores during games.
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
    primary_key="player_id",
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
