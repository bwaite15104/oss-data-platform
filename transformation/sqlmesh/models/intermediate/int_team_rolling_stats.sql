MODEL (
    name intermediate.int_team_rolling_stats,
    kind FULL,
    description 'Team rolling statistics (last 5 and 10 games) - materialized for performance'
);

-- Calculate rolling averages per team per game
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
        
        -- Row number for rolling calcs
        ROW_NUMBER() OVER (PARTITION BY tb.team_id ORDER BY tb.game_date, tb.game_id) as game_num
        
    FROM staging.stg_team_boxscores tb
    JOIN staging.stg_games g ON tb.game_id = g.game_id
    WHERE g.is_completed
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
        
        -- Last 10 game rolling averages
        AVG(t.points) OVER w10_prior as rolling_10_ppg,
        AVG(t.opponent_points) OVER w10_prior as rolling_10_opp_ppg,
        AVG(CASE WHEN t.is_win THEN 1.0 ELSE 0.0 END) OVER w10_prior as rolling_10_win_pct,
        AVG(t.assists) OVER w10_prior as rolling_10_apg,
        AVG(t.rebounds) OVER w10_prior as rolling_10_rpg,
        AVG(t.fg_pct) OVER w10_prior as rolling_10_fg_pct,
        AVG(t.fg3_pct) OVER w10_prior as rolling_10_fg3_pct,
        
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
    wins_last_5,
    games_in_5_window,
    
    -- Rolling 10-game stats
    ROUND(rolling_10_ppg::DECIMAL, 1) as rolling_10_ppg,
    ROUND(rolling_10_opp_ppg::DECIMAL, 1) as rolling_10_opp_ppg,
    ROUND(rolling_10_win_pct::DECIMAL, 3) as rolling_10_win_pct,
    ROUND(rolling_10_apg::DECIMAL, 1) as rolling_10_apg,
    ROUND(rolling_10_rpg::DECIMAL, 1) as rolling_10_rpg,
    ROUND(rolling_10_fg_pct::DECIMAL, 3) as rolling_10_fg_pct,
    ROUND(rolling_10_fg3_pct::DECIMAL, 3) as rolling_10_fg3_pct,
    wins_last_10,
    games_in_10_window
    
FROM rolling_calcs
WHERE game_num > 5  -- Need at least 5 games for rolling stats to be meaningful
