MODEL (
    name intermediate.int_team_rolling_stats,
    kind INCREMENTAL_BY_TIME_RANGE(
        time_column game_date,
        batch_size 1
    ),
    description 'Team rolling statistics (last 5 and 10 games). Incremental by game_date for efficient backfill.',
    start '1946-11-01',
    cron '@yearly'
);

-- Include all rows before @end_ds so window functions have prior games; output only [@start_ds, @end_ds)
WITH team_game_stats AS (
    SELECT 
        tb.team_id,
        tb.game_date,
        tb.game_id,
        
        -- Results
        tb.points,
        CASE WHEN tb.is_home THEN g.away_score ELSE g.home_score END as opponent_points,
        g.winner_team_id = tb.team_id as is_win,
        
        -- Stats
        tb.assists,
        tb.rebounds,
        tb.steals,
        tb.blocks,
        tb.turnovers,
        tb.fg_made,
        tb.fg_attempted,
        tb.fg_pct,
        tb.fg3_made,
        tb.fg3_attempted,
        tb.fg3_pct,
        tb.ft_made,
        tb.ft_attempted,
        tb.is_home,
        
        -- Advanced stats calculations (per game)
        -- eFG% = (FGM + 0.5*3PM) / FGA
        CASE 
            WHEN tb.fg_attempted > 0 THEN 
                (tb.fg_made + 0.5 * tb.fg3_made) / NULLIF(tb.fg_attempted, 0)
            ELSE 0.0
        END as efg_pct,
        
        -- TS% = PTS / (2 * (FGA + 0.44*FTA))
        CASE 
            WHEN (tb.fg_attempted + 0.44 * tb.ft_attempted) > 0 THEN
                tb.points / (2.0 * (tb.fg_attempted + 0.44 * tb.ft_attempted))
            ELSE 0.0
        END as ts_pct,
        
        -- Possessions (approximation: FGA + 0.44*FTA + TO)
        tb.fg_attempted + 0.44 * tb.ft_attempted + tb.turnovers as possessions,
        
        -- Row number for rolling calcs
        ROW_NUMBER() OVER (PARTITION BY tb.team_id ORDER BY tb.game_date, tb.game_id) as game_num
        
    FROM staging.stg_team_boxscores tb
    JOIN staging.stg_games g ON tb.game_id = g.game_id
    WHERE g.is_completed
      AND tb.game_date < @end_ds
),

-- Calculate rolling stats using LAG window functions
rolling_calcs AS (
    SELECT 
        t.*,
        
        -- Last 5 game rolling averages (shifted by 1 so we don't include current game)
        AVG(t.points) OVER w5_prior as rolling_5_ppg,
        AVG(t.opponent_points) OVER w5_prior as rolling_5_opp_ppg,
        AVG(CASE WHEN t.is_win THEN 1.0 ELSE 0.0 END) OVER w5_prior as rolling_5_win_pct,
        AVG(t.assists) OVER w5_prior as rolling_5_apg,
        AVG(t.rebounds) OVER w5_prior as rolling_5_rpg,
        AVG(t.fg_pct) OVER w5_prior as rolling_5_fg_pct,
        AVG(t.fg3_pct) OVER w5_prior as rolling_5_fg3_pct,
        AVG(t.efg_pct) OVER w5_prior as rolling_5_efg_pct,
        AVG(t.ts_pct) OVER w5_prior as rolling_5_ts_pct,
        AVG(t.possessions) OVER w5_prior as rolling_5_possessions,
        -- Consistency/variance features (5-game)
        STDDEV(t.points) OVER w5_prior as rolling_5_ppg_stddev,
        STDDEV(t.opponent_points) OVER w5_prior as rolling_5_opp_ppg_stddev,
        -- Coefficient of variation (stddev / mean) - lower = more consistent
        CASE 
            WHEN AVG(t.points) OVER w5_prior > 0 THEN
                STDDEV(t.points) OVER w5_prior / NULLIF(AVG(t.points) OVER w5_prior, 0)
            ELSE 0.0
        END as rolling_5_ppg_cv,
        CASE 
            WHEN AVG(t.opponent_points) OVER w5_prior > 0 THEN
                STDDEV(t.opponent_points) OVER w5_prior / NULLIF(AVG(t.opponent_points) OVER w5_prior, 0)
            ELSE 0.0
        END as rolling_5_opp_ppg_cv,
        -- OffRtg = 100 * (points / possessions)
        CASE 
            WHEN AVG(t.possessions) OVER w5_prior > 0 THEN
                100.0 * AVG(t.points) OVER w5_prior / AVG(t.possessions) OVER w5_prior
            ELSE 0.0
        END as rolling_5_off_rtg,
        -- DefRtg = 100 * (opp_points / possessions)
        CASE 
            WHEN AVG(t.possessions) OVER w5_prior > 0 THEN
                100.0 * AVG(t.opponent_points) OVER w5_prior / AVG(t.possessions) OVER w5_prior
            ELSE 0.0
        END as rolling_5_def_rtg,
        -- NetRtg = OffRtg - DefRtg
        CASE 
            WHEN AVG(t.possessions) OVER w5_prior > 0 THEN
                100.0 * (AVG(t.points) OVER w5_prior - AVG(t.opponent_points) OVER w5_prior) / AVG(t.possessions) OVER w5_prior
            ELSE 0.0
        END as rolling_5_net_rtg,
        -- Pace = 48 * (possessions / 48) = possessions per 48 minutes (simplified)
        AVG(t.possessions) OVER w5_prior as rolling_5_pace,
        -- Turnover rate = 100 * (turnovers / possessions) - turnovers per 100 possessions
        CASE 
            WHEN AVG(t.possessions) OVER w5_prior > 0 THEN
                100.0 * AVG(t.turnovers) OVER w5_prior / AVG(t.possessions) OVER w5_prior
            ELSE 0.0
        END as rolling_5_tov_rate,
        -- Assist rate = 100 * (assists / possessions) - assists per 100 possessions
        CASE 
            WHEN AVG(t.possessions) OVER w5_prior > 0 THEN
                100.0 * AVG(t.assists) OVER w5_prior / AVG(t.possessions) OVER w5_prior
            ELSE 0.0
        END as rolling_5_assist_rate,
        -- Steal rate = 100 * (steals / possessions) - steals per 100 possessions
        CASE 
            WHEN AVG(t.possessions) OVER w5_prior > 0 THEN
                100.0 * AVG(t.steals) OVER w5_prior / AVG(t.possessions) OVER w5_prior
            ELSE 0.0
        END as rolling_5_steal_rate,
        -- Block rate = 100 * (blocks / possessions) - blocks per 100 possessions
        CASE 
            WHEN AVG(t.possessions) OVER w5_prior > 0 THEN
                100.0 * AVG(t.blocks) OVER w5_prior / AVG(t.possessions) OVER w5_prior
            ELSE 0.0
        END as rolling_5_block_rate,
        
        -- Last 10 game rolling averages
        AVG(t.points) OVER w10_prior as rolling_10_ppg,
        AVG(t.opponent_points) OVER w10_prior as rolling_10_opp_ppg,
        AVG(CASE WHEN t.is_win THEN 1.0 ELSE 0.0 END) OVER w10_prior as rolling_10_win_pct,
        AVG(t.assists) OVER w10_prior as rolling_10_apg,
        AVG(t.rebounds) OVER w10_prior as rolling_10_rpg,
        AVG(t.fg_pct) OVER w10_prior as rolling_10_fg_pct,
        AVG(t.fg3_pct) OVER w10_prior as rolling_10_fg3_pct,
        AVG(t.efg_pct) OVER w10_prior as rolling_10_efg_pct,
        AVG(t.ts_pct) OVER w10_prior as rolling_10_ts_pct,
        AVG(t.possessions) OVER w10_prior as rolling_10_possessions,
        -- Consistency/variance features (10-game)
        STDDEV(t.points) OVER w10_prior as rolling_10_ppg_stddev,
        STDDEV(t.opponent_points) OVER w10_prior as rolling_10_opp_ppg_stddev,
        -- Coefficient of variation (stddev / mean) - lower = more consistent
        CASE 
            WHEN AVG(t.points) OVER w10_prior > 0 THEN
                STDDEV(t.points) OVER w10_prior / NULLIF(AVG(t.points) OVER w10_prior, 0)
            ELSE 0.0
        END as rolling_10_ppg_cv,
        CASE 
            WHEN AVG(t.opponent_points) OVER w10_prior > 0 THEN
                STDDEV(t.opponent_points) OVER w10_prior / NULLIF(AVG(t.opponent_points) OVER w10_prior, 0)
            ELSE 0.0
        END as rolling_10_opp_ppg_cv,
        -- OffRtg = 100 * (points / possessions)
        CASE 
            WHEN AVG(t.possessions) OVER w10_prior > 0 THEN
                100.0 * AVG(t.points) OVER w10_prior / AVG(t.possessions) OVER w10_prior
            ELSE 0.0
        END as rolling_10_off_rtg,
        -- DefRtg = 100 * (opp_points / possessions)
        CASE 
            WHEN AVG(t.possessions) OVER w10_prior > 0 THEN
                100.0 * AVG(t.opponent_points) OVER w10_prior / AVG(t.possessions) OVER w10_prior
            ELSE 0.0
        END as rolling_10_def_rtg,
        -- NetRtg = OffRtg - DefRtg
        CASE 
            WHEN AVG(t.possessions) OVER w10_prior > 0 THEN
                100.0 * (AVG(t.points) OVER w10_prior - AVG(t.opponent_points) OVER w10_prior) / AVG(t.possessions) OVER w10_prior
            ELSE 0.0
        END as rolling_10_net_rtg,
        -- Pace = possessions per 48 minutes (simplified)
        AVG(t.possessions) OVER w10_prior as rolling_10_pace,
        -- Turnover rate = 100 * (turnovers / possessions) - turnovers per 100 possessions
        CASE 
            WHEN AVG(t.possessions) OVER w10_prior > 0 THEN
                100.0 * AVG(t.turnovers) OVER w10_prior / AVG(t.possessions) OVER w10_prior
            ELSE 0.0
        END as rolling_10_tov_rate,
        -- Assist rate = 100 * (assists / possessions) - assists per 100 possessions
        CASE 
            WHEN AVG(t.possessions) OVER w10_prior > 0 THEN
                100.0 * AVG(t.assists) OVER w10_prior / AVG(t.possessions) OVER w10_prior
            ELSE 0.0
        END as rolling_10_assist_rate,
        -- Steal rate = 100 * (steals / possessions) - steals per 100 possessions
        CASE 
            WHEN AVG(t.possessions) OVER w10_prior > 0 THEN
                100.0 * AVG(t.steals) OVER w10_prior / AVG(t.possessions) OVER w10_prior
            ELSE 0.0
        END as rolling_10_steal_rate,
        -- Block rate = 100 * (blocks / possessions) - blocks per 100 possessions
        CASE 
            WHEN AVG(t.possessions) OVER w10_prior > 0 THEN
                100.0 * AVG(t.blocks) OVER w10_prior / AVG(t.possessions) OVER w10_prior
            ELSE 0.0
        END as rolling_10_block_rate,
        
        -- Streak calculations
        SUM(CASE WHEN t.is_win THEN 1 ELSE 0 END) OVER w5_prior as wins_last_5,
        SUM(CASE WHEN t.is_win THEN 1 ELSE 0 END) OVER w10_prior as wins_last_10,
        
        -- Count games in window (for validation)
        COUNT(*) OVER w5_prior as games_in_5_window,
        COUNT(*) OVER w10_prior as games_in_10_window
        
    FROM team_game_stats t
    WINDOW 
        w5_prior AS (PARTITION BY t.team_id ORDER BY t.game_date, t.game_id ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING),
        w10_prior AS (PARTITION BY t.team_id ORDER BY t.game_date, t.game_id ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING)
)

SELECT 
    team_id,
    game_date,
    game_id,
    game_num,
    is_home,
    
    -- Current game results
    points,
    opponent_points,
    is_win,
    
    -- Rolling 5-game stats (prior games only)
    ROUND(rolling_5_ppg::DECIMAL, 1) as rolling_5_ppg,
    ROUND(rolling_5_opp_ppg::DECIMAL, 1) as rolling_5_opp_ppg,
    ROUND(rolling_5_win_pct::DECIMAL, 3) as rolling_5_win_pct,
    ROUND(rolling_5_apg::DECIMAL, 1) as rolling_5_apg,
    ROUND(rolling_5_rpg::DECIMAL, 1) as rolling_5_rpg,
    ROUND(rolling_5_fg_pct::DECIMAL, 3) as rolling_5_fg_pct,
    ROUND(rolling_5_fg3_pct::DECIMAL, 3) as rolling_5_fg3_pct,
    ROUND(rolling_5_efg_pct::DECIMAL, 3) as rolling_5_efg_pct,
    ROUND(rolling_5_ts_pct::DECIMAL, 3) as rolling_5_ts_pct,
    ROUND(rolling_5_possessions::DECIMAL, 1) as rolling_5_possessions,
    ROUND(rolling_5_off_rtg::DECIMAL, 1) as rolling_5_off_rtg,
    ROUND(rolling_5_def_rtg::DECIMAL, 1) as rolling_5_def_rtg,
    ROUND(rolling_5_net_rtg::DECIMAL, 1) as rolling_5_net_rtg,
    ROUND(rolling_5_pace::DECIMAL, 1) as rolling_5_pace,
    ROUND(rolling_5_tov_rate::DECIMAL, 2) as rolling_5_tov_rate,
    ROUND(rolling_5_assist_rate::DECIMAL, 2) as rolling_5_assist_rate,
    ROUND(rolling_5_steal_rate::DECIMAL, 2) as rolling_5_steal_rate,
    ROUND(rolling_5_block_rate::DECIMAL, 2) as rolling_5_block_rate,
    wins_last_5,
    games_in_5_window,
    -- Consistency/variance features (5-game)
    ROUND(rolling_5_ppg_stddev::DECIMAL, 1) as rolling_5_ppg_stddev,
    ROUND(rolling_5_opp_ppg_stddev::DECIMAL, 1) as rolling_5_opp_ppg_stddev,
    ROUND(rolling_5_ppg_cv::DECIMAL, 3) as rolling_5_ppg_cv,
    ROUND(rolling_5_opp_ppg_cv::DECIMAL, 3) as rolling_5_opp_ppg_cv,
    
    -- Rolling 10-game stats
    ROUND(rolling_10_ppg::DECIMAL, 1) as rolling_10_ppg,
    ROUND(rolling_10_opp_ppg::DECIMAL, 1) as rolling_10_opp_ppg,
    ROUND(rolling_10_win_pct::DECIMAL, 3) as rolling_10_win_pct,
    ROUND(rolling_10_apg::DECIMAL, 1) as rolling_10_apg,
    ROUND(rolling_10_rpg::DECIMAL, 1) as rolling_10_rpg,
    ROUND(rolling_10_fg_pct::DECIMAL, 3) as rolling_10_fg_pct,
    ROUND(rolling_10_fg3_pct::DECIMAL, 3) as rolling_10_fg3_pct,
    ROUND(rolling_10_efg_pct::DECIMAL, 3) as rolling_10_efg_pct,
    ROUND(rolling_10_ts_pct::DECIMAL, 3) as rolling_10_ts_pct,
    ROUND(rolling_10_possessions::DECIMAL, 1) as rolling_10_possessions,
    ROUND(rolling_10_off_rtg::DECIMAL, 1) as rolling_10_off_rtg,
    ROUND(rolling_10_def_rtg::DECIMAL, 1) as rolling_10_def_rtg,
    ROUND(rolling_10_net_rtg::DECIMAL, 1) as rolling_10_net_rtg,
    ROUND(rolling_10_pace::DECIMAL, 1) as rolling_10_pace,
    ROUND(rolling_10_tov_rate::DECIMAL, 2) as rolling_10_tov_rate,
    ROUND(rolling_10_assist_rate::DECIMAL, 2) as rolling_10_assist_rate,
    ROUND(rolling_10_steal_rate::DECIMAL, 2) as rolling_10_steal_rate,
    ROUND(rolling_10_block_rate::DECIMAL, 2) as rolling_10_block_rate,
    wins_last_10,
    games_in_10_window,
    -- Consistency/variance features (10-game)
    ROUND(rolling_10_ppg_stddev::DECIMAL, 1) as rolling_10_ppg_stddev,
    ROUND(rolling_10_opp_ppg_stddev::DECIMAL, 1) as rolling_10_opp_ppg_stddev,
    ROUND(rolling_10_ppg_cv::DECIMAL, 3) as rolling_10_ppg_cv,
    ROUND(rolling_10_opp_ppg_cv::DECIMAL, 3) as rolling_10_opp_ppg_cv
    
FROM rolling_calcs
WHERE game_num > 5  -- Need at least 5 games for rolling stats to be meaningful
  AND game_date >= @start_ds
  AND game_date < @end_ds