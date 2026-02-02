MODEL (
    name intermediate.int_team_perf_accel,
    kind INCREMENTAL_BY_TIME_RANGE (
        time_column game_date
    ),
    start '1946-11-01',  -- Updated for full history backfill,
    grains [
        team_id,
        game_date
    ],
    cron '@daily',
    description 'Team performance acceleration features - comparing recent 3 games to previous 3 games (games 4-6) to capture if teams are improving or declining'
);

-- Calculate performance acceleration by comparing recent 3 games to previous 3 games
WITH team_game_stats AS (
    SELECT 
        tb.team_id,
        tb.game_date,
        tb.game_id,
        tb.points,
        CASE WHEN tb.is_home THEN g.away_score ELSE g.home_score END as opponent_points,
        tb.points - (CASE WHEN tb.is_home THEN g.away_score ELSE g.home_score END) as point_diff,
        g.winner_team_id = tb.team_id as is_win,
        
        -- Advanced stats for net rating calculation
        tb.fg_attempted + 0.44 * tb.ft_attempted + tb.turnovers as possessions,
        
        -- Row number for window functions
        ROW_NUMBER() OVER (PARTITION BY tb.team_id ORDER BY tb.game_date, tb.game_id) as game_num
        
    FROM staging.stg_team_boxscores tb
    JOIN staging.stg_games g ON tb.game_id = g.game_id
    WHERE g.is_completed
      AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Calculate rolling stats for recent 3 games and previous 3 games (games 4-6)
rolling_performance AS (
    SELECT 
        t.*,
        
        -- Recent 3 games (games 1-3, most recent)
        AVG(CASE WHEN t.is_win THEN 1.0 ELSE 0.0 END) 
            OVER (PARTITION BY t.team_id ORDER BY t.game_date, t.game_id 
                  ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING) as recent_3_win_pct,
        AVG(t.point_diff) 
            OVER (PARTITION BY t.team_id ORDER BY t.game_date, t.game_id 
                  ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING) as recent_3_point_diff,
        AVG(t.points) 
            OVER (PARTITION BY t.team_id ORDER BY t.game_date, t.game_id 
                  ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING) as recent_3_ppg,
        AVG(t.opponent_points) 
            OVER (PARTITION BY t.team_id ORDER BY t.game_date, t.game_id 
                  ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING) as recent_3_opp_ppg,
        AVG(t.possessions) 
            OVER (PARTITION BY t.team_id ORDER BY t.game_date, t.game_id 
                  ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING) as recent_3_possessions,
        
        -- Previous 3 games (games 4-6, before recent 3)
        AVG(CASE WHEN t.is_win THEN 1.0 ELSE 0.0 END) 
            OVER (PARTITION BY t.team_id ORDER BY t.game_date, t.game_id 
                  ROWS BETWEEN 6 PRECEDING AND 4 PRECEDING) as prev_3_win_pct,
        AVG(t.point_diff) 
            OVER (PARTITION BY t.team_id ORDER BY t.game_date, t.game_id 
                  ROWS BETWEEN 6 PRECEDING AND 4 PRECEDING) as prev_3_point_diff,
        AVG(t.points) 
            OVER (PARTITION BY t.team_id ORDER BY t.game_date, t.game_id 
                  ROWS BETWEEN 6 PRECEDING AND 4 PRECEDING) as prev_3_ppg,
        AVG(t.opponent_points) 
            OVER (PARTITION BY t.team_id ORDER BY t.game_date, t.game_id 
                  ROWS BETWEEN 6 PRECEDING AND 4 PRECEDING) as prev_3_opp_ppg,
        AVG(t.possessions) 
            OVER (PARTITION BY t.team_id ORDER BY t.game_date, t.game_id 
                  ROWS BETWEEN 6 PRECEDING AND 4 PRECEDING) as prev_3_possessions
        
    FROM team_game_stats t
),

-- Calculate acceleration metrics (recent - previous)
acceleration_metrics AS (
    SELECT 
        team_id,
        game_date,
        game_id,
        
        -- Win percentage acceleration (positive = improving, negative = declining)
        COALESCE(recent_3_win_pct - prev_3_win_pct, 0.0) as win_pct_acceleration,
        
        -- Point differential acceleration (positive = improving, negative = declining)
        COALESCE(recent_3_point_diff - prev_3_point_diff, 0.0) as point_diff_acceleration,
        
        -- Offensive acceleration (recent PPG - previous PPG)
        COALESCE(recent_3_ppg - prev_3_ppg, 0.0) as ppg_acceleration,
        
        -- Defensive acceleration (negative is better - allowing fewer points)
        -- Flip sign so positive = improving defense (allowing fewer points)
        COALESCE(prev_3_opp_ppg - recent_3_opp_ppg, 0.0) as defensive_acceleration,
        
        -- Net rating acceleration (offensive rating - defensive rating)
        -- Offensive rating = 100 * points / possessions
        -- Defensive rating = 100 * opponent_points / possessions
        COALESCE(
            (100.0 * recent_3_ppg / NULLIF(recent_3_possessions, 0)) - 
            (100.0 * recent_3_opp_ppg / NULLIF(recent_3_possessions, 0))
            , 0.0
        ) - COALESCE(
            (100.0 * prev_3_ppg / NULLIF(prev_3_possessions, 0)) - 
            (100.0 * prev_3_opp_ppg / NULLIF(prev_3_possessions, 0))
            , 0.0
        ) as net_rtg_acceleration,
        
        -- Sample size indicators (need at least 6 games for acceleration calculation)
        CASE 
            WHEN recent_3_win_pct IS NOT NULL AND prev_3_win_pct IS NOT NULL THEN 1 
            ELSE 0 
        END as has_acceleration_data
        
    FROM rolling_performance
)

SELECT 
    team_id,
    game_date,
    game_id,
    win_pct_acceleration,
    point_diff_acceleration,
    ppg_acceleration,
    defensive_acceleration,
    net_rtg_acceleration,
    has_acceleration_data
FROM acceleration_metrics
WHERE game_date >= @start_ds
  AND game_date < @end_ds
