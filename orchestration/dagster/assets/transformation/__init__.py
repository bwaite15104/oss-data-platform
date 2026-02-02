"""Transformation assets."""

from .sqlmesh_transforms import (
    # Intermediate models
    int_team_rolling_stats,
    int_team_rolling_stats_lookup,
    int_team_season_stats,
    int_star_players,
    int_star_player_availability,
    int_team_star_player_features,
    int_game_injury_features,
    int_away_team_upset_tendency,
    int_team_streaks,
    int_team_rest_days,
    int_team_h2h_stats,
    int_team_opponent_quality,
    int_team_home_road_performance,
    # Momentum feature groups (pre-computed for efficient backfill)
    int_momentum_group_basic,
    int_momentum_group_h2h,
    int_momentum_group_opponent,
    int_momentum_group_context,
    int_momentum_group_trends,
    # Combined momentum features (joins the 5 groups)
    int_game_momentum_features,
    # Feature models
    game_features,
    team_features,
    team_injury_features,
)

__all__ = [
    # Intermediate models
    "int_team_rolling_stats",
    "int_team_rolling_stats_lookup",
    "int_team_season_stats",
    "int_star_players",
    "int_star_player_availability",
    "int_team_star_player_features",
    "int_game_injury_features",
    "int_away_team_upset_tendency",
    "int_team_streaks",
    "int_team_rest_days",
    "int_team_h2h_stats",
    "int_team_opponent_quality",
    "int_team_home_road_performance",
    # Momentum feature groups (pre-computed for efficient backfill)
    "int_momentum_group_basic",
    "int_momentum_group_h2h",
    "int_momentum_group_opponent",
    "int_momentum_group_context",
    "int_momentum_group_trends",
    # Combined momentum features (joins the 5 groups)
    "int_game_momentum_features",
    # Feature models
    "game_features",
    "team_features",
    "team_injury_features",
]

