MODEL (
    name intermediate.int_team_recent_momentum,
    kind INCREMENTAL_BY_TIME_RANGE (
        time_column game_date
    ),
    start '1946-11-01',
    grains [game_id],
    cron '@daily',
    description 'Team recent momentum - rate of change between 3-game and 5-game rolling windows. Captures immediate acceleration/deceleration in team performance (more recent than 5-game vs 10-game trends).'
);

-- Calculate recent momentum by comparing 3-game vs 5-game rolling stats
-- Positive momentum = improving (3-game > 5-game), Negative momentum = declining (3-game < 5-game)
-- This captures more immediate changes than the existing 5-game vs 10-game trends
WITH team_game_stats AS (
    SELECT 
        tb.team_id,
        tb.game_date,
        tb.game_id,
        tb.points,
        CASE WHEN tb.is_home THEN g.away_score ELSE g.home_score END as opponent_points,
        g.winner_team_id = tb.team_id as is_win,
        tb.fg_attempted + 0.44 * tb.ft_attempted + tb.turnovers as possessions,
        (tb.fg_made + 0.5 * tb.fg3_made) / NULLIF(tb.fg_attempted, 0) as efg_pct,
        CASE 
            WHEN (tb.fg_attempted + 0.44 * tb.ft_attempted) > 0 THEN
                tb.points / (2.0 * (tb.fg_attempted + 0.44 * tb.ft_attempted))
            ELSE 0.0
        END as ts_pct
    FROM staging.stg_team_boxscores tb
    JOIN staging.stg_games g ON tb.game_id = g.game_id
    WHERE g.winner_team_id IS NOT NULL  -- completed games only (avoids dependency on is_completed)
      AND tb.game_date < @end_ds  -- historical context for rolling windows
),

rolling_calcs AS (
    SELECT 
        t.*,
        -- 5-game rolling stats (from int_team_rolling_stats)
        rs.rolling_5_win_pct,
        rs.rolling_5_net_rtg,
        rs.rolling_5_ppg,
        rs.rolling_5_opp_ppg,
        rs.rolling_5_off_rtg,
        rs.rolling_5_def_rtg,
        rs.rolling_5_efg_pct,
        rs.rolling_5_ts_pct,
        -- 3-game rolling stats using window functions
        AVG(CASE WHEN t.is_win THEN 1.0 ELSE 0.0 END) OVER (
            PARTITION BY t.team_id 
            ORDER BY t.game_date, t.game_id 
            ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
        ) as rolling_3_win_pct,
        AVG(t.points - t.opponent_points) OVER (
            PARTITION BY t.team_id 
            ORDER BY t.game_date, t.game_id 
            ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
        ) as rolling_3_net_rtg,
        AVG(t.points) OVER (
            PARTITION BY t.team_id 
            ORDER BY t.game_date, t.game_id 
            ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
        ) as rolling_3_ppg,
        AVG(t.opponent_points) OVER (
            PARTITION BY t.team_id 
            ORDER BY t.game_date, t.game_id 
            ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
        ) as rolling_3_opp_ppg,
        -- Calculate 3-game offensive rating
        CASE 
            WHEN AVG(t.possessions) OVER (
                PARTITION BY t.team_id 
                ORDER BY t.game_date, t.game_id 
                ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
            ) > 0 THEN
                100.0 * AVG(t.points) OVER (
                    PARTITION BY t.team_id 
                    ORDER BY t.game_date, t.game_id 
                    ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
                ) / AVG(t.possessions) OVER (
                    PARTITION BY t.team_id 
                    ORDER BY t.game_date, t.game_id 
                    ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
                )
            ELSE 0.0
        END as rolling_3_off_rtg,
        -- Calculate 3-game defensive rating
        CASE 
            WHEN AVG(t.possessions) OVER (
                PARTITION BY t.team_id 
                ORDER BY t.game_date, t.game_id 
                ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
            ) > 0 THEN
                100.0 * AVG(t.opponent_points) OVER (
                    PARTITION BY t.team_id 
                    ORDER BY t.game_date, t.game_id 
                    ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
                ) / AVG(t.possessions) OVER (
                    PARTITION BY t.team_id 
                    ORDER BY t.game_date, t.game_id 
                    ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
                )
            ELSE 0.0
        END as rolling_3_def_rtg,
        AVG(t.efg_pct) OVER (
            PARTITION BY t.team_id 
            ORDER BY t.game_date, t.game_id 
            ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
        ) as rolling_3_efg_pct,
        AVG(t.ts_pct) OVER (
            PARTITION BY t.team_id 
            ORDER BY t.game_date, t.game_id 
            ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
        ) as rolling_3_ts_pct
    FROM team_game_stats t
    LEFT JOIN intermediate.int_team_rolling_stats rs ON rs.team_id = t.team_id AND rs.game_id = t.game_id
    WHERE rs.rolling_5_win_pct IS NOT NULL  -- Need at least 5 games for momentum calculation
)

SELECT 
    team_id,
    game_date,
    game_id,
    
    -- Win percentage recent momentum (3-game vs 5-game)
    -- Positive = improving (last 3 games better than last 5), Negative = declining
    COALESCE(rolling_3_win_pct - rolling_5_win_pct, 0.0) as recent_win_pct_momentum,
    
    -- Net rating recent momentum (strong predictor of team quality)
    -- Positive = improving, Negative = declining
    COALESCE(rolling_3_net_rtg - rolling_5_net_rtg, 0.0) as recent_net_rtg_momentum,
    
    -- Offensive performance recent momentum
    -- PPG momentum (scoring trend)
    COALESCE(rolling_3_ppg - rolling_5_ppg, 0.0) as recent_ppg_momentum,
    -- Offensive rating momentum
    COALESCE(rolling_3_off_rtg - rolling_5_off_rtg, 0.0) as recent_off_rtg_momentum,
    -- Shooting efficiency momentum
    COALESCE(rolling_3_efg_pct - rolling_5_efg_pct, 0.0) as recent_efg_pct_momentum,
    COALESCE(rolling_3_ts_pct - rolling_5_ts_pct, 0.0) as recent_ts_pct_momentum,
    
    -- Defensive performance recent momentum
    -- Opponent PPG momentum (negative = improving defense, positive = declining defense)
    -- Note: Lower opponent PPG is better, so we flip the sign
    COALESCE(rolling_5_opp_ppg - rolling_3_opp_ppg, 0.0) as recent_opp_ppg_momentum,
    -- Defensive rating momentum (negative = improving defense, positive = declining defense)
    -- Note: Lower defensive rating is better, so we flip the sign
    COALESCE(rolling_5_def_rtg - rolling_3_def_rtg, 0.0) as recent_def_rtg_momentum
    
FROM rolling_calcs
WHERE rolling_5_win_pct IS NOT NULL  -- Need at least 5 games for momentum calculation
  AND game_date >= @start_ds
  AND game_date < @end_ds