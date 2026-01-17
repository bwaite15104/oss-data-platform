"""
NBA Data Pipeline Configuration.

Centralized configuration for NBA CDN endpoints, headers, and pipeline settings.
This keeps constants separate from business logic for easier maintenance.
"""

from typing import Dict

# =============================================================================
# NBA CDN ENDPOINTS
# =============================================================================
# These are the reliable NBA CDN endpoints that don't require authentication.
# The stats.nba.com API has aggressive bot protection - avoid using it.

NBA_CDN_ENDPOINTS = {
    # Schedule and team data
    "schedule": "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2.json",
    
    # Live scoreboard (today's games)
    "scoreboard": "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json",
    
    # Player index (all players)
    "players": "https://cdn.nba.com/static/json/staticData/playerIndex.json",
    
    # Betting odds
    "odds": "https://cdn.nba.com/static/json/liveData/odds/odds_todaysGames.json",
    
    # Boxscore template (requires game_id)
    "boxscore": "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game_id}.json",
}

# Convenience accessors
NBA_CDN_SCHEDULE = NBA_CDN_ENDPOINTS["schedule"]
NBA_CDN_SCOREBOARD = NBA_CDN_ENDPOINTS["scoreboard"]
NBA_CDN_PLAYERS = NBA_CDN_ENDPOINTS["players"]
NBA_CDN_ODDS = NBA_CDN_ENDPOINTS["odds"]
NBA_CDN_BOXSCORE = NBA_CDN_ENDPOINTS["boxscore"]


# =============================================================================
# REQUEST CONFIGURATION
# =============================================================================

# Headers to use for NBA CDN requests
CDN_HEADERS: Dict[str, str] = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

# Request timeout in seconds
REQUEST_TIMEOUT = 60

# Rate limiting - delay between requests (seconds)
RATE_LIMIT_DELAY = 0.2


# =============================================================================
# PIPELINE CONFIGURATION
# =============================================================================

# Default pipeline settings
PIPELINE_NAME = "nba_stats"
DESTINATION = "postgres"

# Environment-aware dataset naming
# DATA_ENV can be: dev, staging, prod
import os
DATA_ENV = os.getenv("DATA_ENV", "dev")

# Dataset name follows pattern: raw_{env} for ingestion
# Transformations go to staging_{env}, marts_{env}, etc.
DATASET_NAME = f"raw_{DATA_ENV}"

# Legacy dataset name (for backward compatibility)
LEGACY_DATASET_NAME = "nba"


# =============================================================================
# TEAM MAPPINGS
# =============================================================================
# Static mappings for team metadata not available in CDN

EAST_CONFERENCE_TEAMS = {
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

TEAM_DIVISIONS = {
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
