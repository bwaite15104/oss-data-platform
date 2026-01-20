"""
Historical NBA data backfill using Basketball Reference scraping.

This pipeline backfills historical seasons (2023-24, 2022-23) to get more
star return training examples. Uses basketball-reference-scraper library
which is free and open-source.

Going forward, we use NBA CDN as source of truth. This is only for backfill.
"""

import dlt
from typing import Iterator, Dict, Any, Optional
from datetime import datetime
import time
import logging
import sys
import os
from pathlib import Path
import pandas as pd

# Add project root to path
_current_dir = Path(__file__).parent
_project_root = _current_dir.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

logger = logging.getLogger(__name__)

# Patch Selenium Chrome options BEFORE importing basketball-reference-scraper
# The library initializes Chrome at import time in request_utils, so we need to patch it first
try:
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium import webdriver
    
    # Monkey-patch ChromeOptions.add_argument to always add headless flags in Docker
    _original_add_argument = ChromeOptions.add_argument
    
    def _patched_add_argument(self, argument):
        # Always add headless options if in Docker, regardless of what the library passes
        if os.path.exists('/.dockerenv') or os.getenv('DISPLAY') == ':99':
            # Ensure headless flags are present
            required_flags = [
                '--headless=new',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--remote-debugging-port=9222',
                '--disable-extensions',
                '--window-size=1920,1080'
            ]
            # Get current arguments
            current_args = getattr(self, '_arguments', [])
            for flag in required_flags:
                if flag not in current_args:
                    _original_add_argument(self, flag)
        return _original_add_argument(self, argument)
    
    ChromeOptions.add_argument = _patched_add_argument
    
    # Also patch Chrome.__init__ as backup
    _original_chrome_init = webdriver.Chrome.__init__
    
    def _patched_chrome_init(self, options=None, service=None, keep_alive=True):
        # Check if running in Docker
        if os.path.exists('/.dockerenv') or os.getenv('DISPLAY') == ':99':
            if options is None:
                options = ChromeOptions()
            # Ensure headless options are set
            required_flags = [
                '--headless=new',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--remote-debugging-port=9222',
                '--disable-extensions',
                '--window-size=1920,1080'
            ]
            current_args = getattr(options, '_arguments', [])
            for flag in required_flags:
                if flag not in current_args:
                    options.add_argument(flag)
        return _original_chrome_init(self, options=options, service=service, keep_alive=keep_alive)
    
    webdriver.Chrome.__init__ = _patched_chrome_init
    logger.info("Patched Selenium Chrome for Docker/headless mode")
except Exception as e:
    logger.warning(f"Could not patch Selenium Chrome: {e}")

# Try to import basketball-reference-scraper (after patching Selenium)
try:
    from basketball_reference_scraper.box_scores import get_box_scores
    from basketball_reference_scraper.seasons import get_schedule
    BASKETBALL_REF_AVAILABLE = True
    logger.info("basketball-reference-scraper imported successfully")
except ImportError:
    BASKETBALL_REF_AVAILABLE = False
    logger.warning("basketball-reference-scraper not installed. Install with: pip install basketball-reference-scraper")
except Exception as e:
    BASKETBALL_REF_AVAILABLE = False
    logger.error(f"Error importing basketball-reference-scraper: {e}")
    # Don't raise - allow pipeline to continue and report the issue

# Import contract loader for schema definitions
try:
    from ingestion.dlt.contract_loader import get_dlt_columns, get_primary_key
    CONTRACTS_AVAILABLE = True
except ImportError:
    CONTRACTS_AVAILABLE = False


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


def _get_existing_game_ids(table_name: str, schema_name: str = None) -> set:
    """Get existing game IDs from database to avoid duplicates."""
    try:
        import psycopg2
    except ImportError:
        return set()
    
    try:
        database = os.getenv("POSTGRES_DB", "nba_analytics")
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = int(os.getenv("POSTGRES_PORT", "5432"))
        user = os.getenv("POSTGRES_USER", "postgres")
        password = os.getenv("POSTGRES_PASSWORD", "postgres")
        
        if schema_name is None:
            data_env = os.getenv("DATA_ENV", "dev")
            schema_name = f"raw_{data_env}"
        
        conn = psycopg2.connect(
            host=host, port=port, database=database, user=user, password=password
        )
        cursor = conn.cursor()
        query = f"SELECT DISTINCT game_id FROM {schema_name}.{table_name}"
        cursor.execute(query)
        existing_ids = {str(row[0]) for row in cursor.fetchall()}
        cursor.close()
        conn.close()
        return existing_ids
    except Exception as e:
        logger.debug(f"Could not query existing game_ids: {e}")
        return set()


def _map_br_team_to_nba_id(team_abbr: str) -> Optional[int]:
    """Map Basketball Reference team abbreviation to NBA team_id."""
    team_mapping = {
        "ATL": 1610612737, "BOS": 1610612738, "BRK": 1610612751, "CHA": 1610612766,
        "CHI": 1610612741, "CLE": 1610612739, "DAL": 1610612742, "DEN": 1610612743,
        "DET": 1610612765, "GSW": 1610612744, "HOU": 1610612745, "IND": 1610612754,
        "LAC": 1610612746, "LAL": 1610612747, "MEM": 1610612763, "MIA": 1610612748,
        "MIL": 1610612749, "MIN": 1610612750, "NOP": 1610612740, "NYK": 1610612752,
        "OKC": 1610612760, "ORL": 1610612753, "PHI": 1610612755, "PHO": 1610612756,
        "POR": 1610612757, "SAC": 1610612758, "SAS": 1610612759, "TOR": 1610612761,
        "UTA": 1610612762, "WAS": 1610612764,
    }
    return team_mapping.get(team_abbr.upper())


def _convert_date_to_game_id(date_str: str, game_num: int = 0) -> Optional[str]:
    """
    Convert date to NBA game ID format: 002YYMMDD0HH
    
    Args:
        date_str: Date in format "YYYY-MM-DD" or "10/24/2023"
        game_num: Game sequence number for the day (0-99)
    """
    try:
        if "/" in date_str:
            # Format: "10/24/2023"
            date_parts = date_str.split("/")
            if len(date_parts) == 3:
                date_obj = datetime(int(date_parts[2]), int(date_parts[0]), int(date_parts[1]))
            else:
                return None
        elif "-" in date_str:
            # Format: "2023-10-24"
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        else:
            return None
        
        year = date_obj.year
        if date_obj.month >= 10:
            season_year = year
        else:
            season_year = year - 1
        
        yy = str(season_year)[2:]
        mm = f"{date_obj.month:02d}"
        dd = f"{date_obj.day:02d}"
        hh = f"{game_num:02d}"
        
        return f"002{yy}{mm}{dd}0{hh}"
    except Exception as e:
        logger.debug(f"Could not convert date {date_str} to game_id: {e}")
        return None


def _parse_minutes(minutes_str: str) -> Optional[float]:
    """Parse minutes string (e.g., '36:00' or '36') to float."""
    if not minutes_str or pd.isna(minutes_str):
        return None
    
    try:
        minutes_str = str(minutes_str).strip()
        if ":" in minutes_str:
            parts = minutes_str.split(":")
            return float(parts[0]) + float(parts[1]) / 60.0
        else:
            return float(minutes_str)
    except:
        return None


def _convert_br_boxscore_to_schema(
    br_df: pd.DataFrame,
    game_id: str,
    game_date: str,
    team_id: int,
    is_home: bool,
    player_name_to_id_map: Dict[str, int],
) -> Iterator[Dict[str, Any]]:
    """
    Convert Basketball Reference boxscore DataFrame to our schema format.
    
    Args:
        br_df: DataFrame from basketball-reference-scraper
        game_id: NBA game ID
        game_date: Game date in YYYY-MM-DD format
        team_id: NBA team ID
        is_home: Whether this is the home team
        player_name_to_id_map: Map of player names to player_ids (for lookup)
    """
    if br_df is None or br_df.empty:
        return
    
    # Basketball Reference column names vary, but common patterns:
    # MP (minutes), PTS (points), TRB (total rebounds), AST (assists), etc.
    
    for idx, row in br_df.iterrows():
        # Get player name (common column names)
        player_name = None
        for col in ["PLAYER", "Name", "name", "Player"]:
            if col in row.index and pd.notna(row[col]):
                player_name = str(row[col]).strip()
                break
        
        if not player_name or player_name == "Team Totals" or player_name == "Reserves":
            continue
        
        # Look up player_id (use name as fallback if not found)
        player_id = player_name_to_id_map.get(player_name)
        if not player_id:
            # Generate a temporary ID based on name hash (not ideal, but works)
            import hashlib
            player_id = int(hashlib.md5(player_name.encode()).hexdigest()[:8], 16) % 1000000000
        
        # Determine if starter (usually first 5 players, or check "Starters" section)
        is_starter = idx < 5  # Simple heuristic: first 5 are usually starters
        
        # Parse stats with fallbacks for different column name formats
        def get_stat(row, *possible_names, default=0):
            for name in possible_names:
                if name in row.index and pd.notna(row[name]):
                    try:
                        val = row[name]
                        if isinstance(val, str):
                            val = val.strip()
                            if val == "" or val == "-":
                                return default
                        return float(val) if val != "" else default
                    except:
                        pass
            return default
        
        minutes_str = None
        for col in ["MP", "Min", "MIN", "minutes"]:
            if col in row.index:
                minutes_str = row[col]
                break
        
        minutes_float = _parse_minutes(minutes_str) if minutes_str else None
        
        player_stats = {
            "game_id": game_id,
            "game_date": game_date,
            "player_id": player_id,
            "player_name": player_name,
            "team_id": team_id,
            "is_home": is_home,
            "is_starter": is_starter,
            "minutes": minutes_str if minutes_str else "0:00",
            "minutes_calculated": f"{int(minutes_float * 60) if minutes_float else 0}:00" if minutes_float else "0:00",
            "points": get_stat(row, "PTS", "Points", "points", default=0),
            "assists": get_stat(row, "AST", "Assists", "assists", default=0),
            "rebounds_total": get_stat(row, "TRB", "TRB", "Rebounds", "rebounds_total", default=0),
            "rebounds_offensive": get_stat(row, "ORB", "ORB", "Offensive Rebounds", "rebounds_offensive", default=0),
            "rebounds_defensive": get_stat(row, "DRB", "DRB", "Defensive Rebounds", "rebounds_defensive", default=0),
            "steals": get_stat(row, "STL", "Steals", "steals", default=0),
            "blocks": get_stat(row, "BLK", "Blocks", "blocks", default=0),
            "turnovers": get_stat(row, "TOV", "TO", "Turnovers", "turnovers", default=0),
            "fouls_personal": get_stat(row, "PF", "Fouls", "fouls_personal", default=0),
            "fouls_technical": 0,  # BR doesn't always separate technical fouls
            "field_goals_made": get_stat(row, "FG", "FGM", "Field Goals Made", "field_goals_made", default=0),
            "field_goals_attempted": get_stat(row, "FGA", "Field Goals Attempted", "field_goals_attempted", default=0),
            "field_goal_pct": get_stat(row, "FG%", "FG_PCT", "Field Goal %", "field_goal_pct", default=0),
            "three_pointers_made": get_stat(row, "3P", "3PM", "Three Pointers Made", "three_pointers_made", default=0),
            "three_pointers_attempted": get_stat(row, "3PA", "Three Pointers Attempted", "three_pointers_attempted", default=0),
            "three_point_pct": get_stat(row, "3P%", "3P_PCT", "Three Point %", "three_point_pct", default=0),
            "free_throws_made": get_stat(row, "FT", "FTM", "Free Throws Made", "free_throws_made", default=0),
            "free_throws_attempted": get_stat(row, "FTA", "Free Throws Attempted", "free_throws_attempted", default=0),
            "free_throw_pct": get_stat(row, "FT%", "FT_PCT", "Free Throw %", "free_throw_pct", default=0),
            "plus_minus": get_stat(row, "+/-", "Plus/Minus", "plus_minus", default=0),
            "points_fast_break": 0,  # BR doesn't provide this
            "points_in_paint": 0,  # BR doesn't provide this
            "points_second_chance": 0,  # BR doesn't provide this
            "created_at": datetime.now().isoformat(),
        }
        
        yield player_stats


@dlt.resource(
    name="historical_boxscores",
    write_disposition="merge",
    primary_key=_get_contract_pk("nba_boxscores") or ["game_id", "player_id"],
    columns=_get_contract_columns("nba_boxscores"),
)
def historical_boxscores_resource(
    season: str,
    team: Optional[str] = None,
    skip_existing: bool = True,
    limit: Optional[int] = None,
) -> Iterator[Dict[str, Any]]:
    """
    Backfill historical boxscores from Basketball Reference.
    
    Args:
        season: NBA season (e.g., "2023-24")
        team: Team abbreviation (e.g., "LAL") - if None, processes all teams
        skip_existing: Skip games that already exist in database
        limit: Maximum number of games to process (for testing)
    """
    if not BASKETBALL_REF_AVAILABLE:
        logger.error("basketball-reference-scraper not available. Install with: pip install basketball-reference-scraper")
        return
    
    logger.info(f"Starting historical boxscores backfill for season {season}...")
    
    # Get existing game IDs
    existing_game_ids = set()
    if skip_existing:
        existing_game_ids = _get_existing_game_ids("boxscores")
        logger.info(f"Found {len(existing_game_ids)} existing games in database")
    
    # Build player name to ID map (query from database)
    player_name_to_id_map = {}
    try:
        import psycopg2
        database = os.getenv("POSTGRES_DB", "nba_analytics")
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = int(os.getenv("POSTGRES_PORT", "5432"))
        user = os.getenv("POSTGRES_USER", "postgres")
        password = os.getenv("POSTGRES_PASSWORD", "postgres")
        data_env = os.getenv("DATA_ENV", "dev")
        schema_name = f"raw_{data_env}"
        
        conn = psycopg2.connect(host=host, port=port, database=database, user=user, password=password)
        cursor = conn.cursor()
        cursor.execute(f"SELECT DISTINCT player_id, player_name FROM {schema_name}.players WHERE player_name IS NOT NULL")
        for row in cursor.fetchall():
            player_name_to_id_map[str(row[1]).strip()] = row[0]
        cursor.close()
        conn.close()
        logger.info(f"Loaded {len(player_name_to_id_map)} player name mappings")
    except Exception as e:
        logger.warning(f"Could not load player mappings: {e}. Will use name-based IDs.")
    
    try:
        # Parse season to get year
        season_year = int(season.split("-")[0])
        
        # Get schedule for the season
        teams = ["ATL", "BOS", "BRK", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW",
                 "HOU", "IND", "LAC", "LAL", "MEM", "MIA", "MIL", "MIN", "NOP", "NYK",
                 "OKC", "ORL", "PHI", "PHO", "POR", "SAC", "SAS", "TOR", "UTA", "WAS"]
        
        if team:
            teams = [team]
        
        total_games = 0
        processed_games = set()
        
        for team_abbr in teams:
            logger.info(f"Processing {team_abbr} for season {season_year}...")
            
            try:
                # Get schedule for this team
                # Note: get_schedule expects team abbreviation and year (e.g., 'LAL', 2023)
                # The year should be the start year of the season (2023 for 2023-24)
                schedule_df = get_schedule(team_abbr, season_year)
                
                if schedule_df is None:
                    logger.warning(f"Schedule returned None for {team_abbr} {season_year}")
                    continue
                    
                if schedule_df.empty:
                    logger.warning(f"Schedule is empty for {team_abbr} {season_year}. This may indicate a scraping issue.")
                    continue
                
                # Process each game
                for idx, row in schedule_df.iterrows():
                    if limit and total_games >= limit:
                        break
                    
                    try:
                        date_str = row.get('DATE', '')
                        opponent = row.get('OPPONENT', '')
                        home_away = row.get('HOME/AWAY', '')
                        
                        if not date_str or pd.isna(date_str) or not opponent or pd.isna(opponent):
                            continue
                        
                        # Convert date to game_id
                        game_id = _convert_date_to_game_id(date_str, 0)
                        if not game_id:
                            continue
                        
                        # Skip if already processed or exists
                        if game_id in processed_games:
                            continue
                        if skip_existing and game_id in existing_game_ids:
                            continue
                        
                        # Get box scores for this game
                        # basketball-reference-scraper needs date in "YYYY-MM-DD" format
                        if isinstance(date_str, str) and "/" in date_str:
                            # Convert "10/24/2023" to "2023-10-24"
                            date_parts = date_str.split("/")
                            if len(date_parts) == 3:
                                date_obj = datetime(int(date_parts[2]), int(date_parts[0]), int(date_parts[1]))
                                date_iso = date_obj.strftime("%Y-%m-%d")
                            else:
                                continue
                        else:
                            date_iso = date_str
                        
                        # Get box scores
                        home_team = team_abbr if home_away == "HOME" else opponent
                        away_team = opponent if home_away == "HOME" else team_abbr
                        
                        try:
                            box_score = get_box_scores(date_iso, home_team, away_team)
                            
                            if box_score is None:
                                continue
                            
                            # basketball-reference-scraper returns dict with DataFrames
                            # Format: {'home_stats': DataFrame, 'away_stats': DataFrame}
                            if isinstance(box_score, dict):
                                home_stats_df = box_score.get('home_stats', pd.DataFrame())
                                away_stats_df = box_score.get('away_stats', pd.DataFrame())
                                
                                # Convert home team stats
                                home_team_id = _map_br_team_to_nba_id(home_team)
                                if home_team_id:
                                    for player_stat in _convert_br_boxscore_to_schema(
                                        home_stats_df, game_id, date_iso, home_team_id, True, player_name_to_id_map
                                    ):
                                        yield player_stat
                                
                                # Convert away team stats
                                away_team_id = _map_br_team_to_nba_id(away_team)
                                if away_team_id:
                                    for player_stat in _convert_br_boxscore_to_schema(
                                        away_stats_df, game_id, date_iso, away_team_id, False, player_name_to_id_map
                                    ):
                                        yield player_stat
                            
                            processed_games.add(game_id)
                            total_games += 1
                            
                            if total_games % 10 == 0:
                                logger.info(f"Processed {total_games} games so far...")
                            
                            # Rate limiting - be respectful to Basketball Reference
                            time.sleep(2)
                            
                        except Exception as e:
                            logger.warning(f"Error fetching boxscore for {date_iso} {home_team} vs {away_team}: {e}")
                            continue
                        
                    except Exception as e:
                        logger.warning(f"Error processing game row: {e}")
                        continue
                
            except Exception as e:
                logger.warning(f"Error processing {team_abbr}: {e}")
                continue
        
        logger.info(f"Historical boxscores backfill complete! Processed {total_games} games")
        
    except Exception as e:
        logger.error(f"Error in historical boxscores backfill: {e}")
        raise


# dlt pipeline definition
@dlt.source
def nba_historical_backfill(
    season: str,
    team: Optional[str] = None,
    skip_existing: bool = True,
    limit: Optional[int] = None,
):
    """
    dlt source for historical NBA data backfill from Basketball Reference.
    
    Usage:
        pipeline = dlt.pipeline(
            pipeline_name="nba_historical_backfill",
            destination="postgres",
            dataset_name="nba_analytics"
        )
        load_info = pipeline.run(
            nba_historical_backfill(season="2023-24", team="LAL", limit=10)
        )
    """
    return historical_boxscores_resource(season, team, skip_existing, limit)
