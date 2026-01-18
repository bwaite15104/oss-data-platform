MODEL (
    name features_dev.team_features,
    kind FULL,
    description 'Team season-level features for ML models',
    grain team_id
);

SELECT 
    team_id,
    games_played,
    wins,
    losses,
    win_pct,
    home_wins,
    home_losses,
    away_wins,
    away_losses,
    ppg,
    opp_ppg,
    point_diff,
    apg,
    rpg,
    spg,
    bpg,
    topg,
    fg_pct,
    fg3_pct,
    ft_pct,
    fga_pg,
    fg3a_pg,
    fta_pg,
    CURRENT_TIMESTAMP as updated_at
FROM intermediate.int_team_season_stats
