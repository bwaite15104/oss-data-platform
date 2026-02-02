MODEL (
    name intermediate.int_team_perf_trends,
    kind FULL,
    description 'Team performance trends - rate of change between 5-game and 10-game rolling windows. Captures acceleration/deceleration in team performance.'
);

-- Calculate performance trends by comparing 5-game vs 10-game rolling stats
-- Positive trend = improving (5-game > 10-game), Negative trend = declining (5-game < 10-game)
WITH rolling_stats AS (
    SELECT 
        team_id,
        game_date,
        game_id,
        rolling_5_win_pct,
        rolling_10_win_pct,
        rolling_5_net_rtg,
        rolling_10_net_rtg,
        rolling_5_ppg,
        rolling_10_ppg,
        rolling_5_opp_ppg,
        rolling_10_opp_ppg,
        rolling_5_off_rtg,
        rolling_10_off_rtg,
        rolling_5_def_rtg,
        rolling_10_def_rtg,
        rolling_5_efg_pct,
        rolling_10_efg_pct,
        rolling_5_ts_pct,
        rolling_10_ts_pct
    FROM intermediate.int_team_rolling_stats
    WHERE rolling_10_win_pct IS NOT NULL  -- Need at least 10 games for trend calculation
)

SELECT 
    team_id,
    game_date,
    game_id,
    
    -- Win percentage trend (5-game vs 10-game)
    -- Positive = improving, Negative = declining
    COALESCE(rolling_5_win_pct - rolling_10_win_pct, 0.0) as win_pct_trend,
    
    -- Net rating trend (strong predictor of team quality)
    -- Positive = improving, Negative = declining
    COALESCE(rolling_5_net_rtg - rolling_10_net_rtg, 0.0) as net_rtg_trend,
    
    -- Offensive performance trends
    -- PPG trend (scoring trend)
    COALESCE(rolling_5_ppg - rolling_10_ppg, 0.0) as ppg_trend,
    -- Offensive rating trend
    COALESCE(rolling_5_off_rtg - rolling_10_off_rtg, 0.0) as off_rtg_trend,
    -- Shooting efficiency trends
    COALESCE(rolling_5_efg_pct - rolling_10_efg_pct, 0.0) as efg_pct_trend,
    COALESCE(rolling_5_ts_pct - rolling_10_ts_pct, 0.0) as ts_pct_trend,
    
    -- Defensive performance trends
    -- Opponent PPG trend (negative = improving defense, positive = declining defense)
    -- Note: Lower opponent PPG is better, so we flip the sign
    COALESCE(rolling_10_opp_ppg - rolling_5_opp_ppg, 0.0) as opp_ppg_trend,
    -- Defensive rating trend (negative = improving defense, positive = declining defense)
    -- Note: Lower defensive rating is better, so we flip the sign
    COALESCE(rolling_10_def_rtg - rolling_5_def_rtg, 0.0) as def_rtg_trend
    
FROM rolling_stats
