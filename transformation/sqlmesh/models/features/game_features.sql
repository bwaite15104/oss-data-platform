MODEL (
    name features_dev.game_features,
    kind FULL,
    description 'ML-ready game features for prediction models',
    grain game_id
);

SELECT 
    game_id,
    game_date,
    home_team_id,
    away_team_id,
    
    -- Target variables
    home_win,
    home_score,
    away_score,
    point_spread,
    total_points,
    is_overtime,
    
    -- Home team rolling features
    home_rolling_5_ppg,
    home_rolling_5_opp_ppg,
    home_rolling_5_win_pct,
    home_rolling_5_apg,
    home_rolling_5_rpg,
    home_rolling_5_fg_pct,
    home_rolling_5_fg3_pct,
    home_rolling_10_ppg,
    home_rolling_10_win_pct,
    
    -- Away team rolling features
    away_rolling_5_ppg,
    away_rolling_5_opp_ppg,
    away_rolling_5_win_pct,
    away_rolling_5_apg,
    away_rolling_5_rpg,
    away_rolling_5_fg_pct,
    away_rolling_5_fg3_pct,
    away_rolling_10_ppg,
    away_rolling_10_win_pct,
    
    -- Differential features
    ppg_diff_5,
    win_pct_diff_5,
    fg_pct_diff_5,
    ppg_diff_10,
    win_pct_diff_10,
    
    -- Season context
    home_season_win_pct,
    home_season_point_diff,
    home_wins_at_home,
    home_losses_at_home,
    away_season_win_pct,
    away_season_point_diff,
    away_wins_on_road,
    away_losses_on_road,
    
    -- Season differential
    season_win_pct_diff,
    season_point_diff_diff,
    
    -- Home team star player return features
    home_star_players_returning,
    home_key_players_returning,
    home_extended_returns,
    home_total_return_impact,
    home_max_days_since_return,
    home_avg_days_since_return,
    home_has_star_return,
    home_has_extended_return,
    
    -- Away team star player return features
    away_star_players_returning,
    away_key_players_returning,
    away_extended_returns,
    away_total_return_impact,
    away_max_days_since_return,
    away_avg_days_since_return,
    away_has_star_return,
    away_has_extended_return,
    
    -- Star return differential features
    star_return_advantage,
    return_impact_diff,
    
    CURRENT_TIMESTAMP as updated_at
    
FROM marts.mart_game_features
