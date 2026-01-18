MODEL (
    name intermediate.int_team_season_stats,
    kind VIEW,
    description 'Team season aggregated statistics'
);

-- Team season-level statistics aggregated from team boxscores
WITH team_game_stats AS (
    SELECT 
        tb.team_id,
        tb.game_date,
        g.game_id,
        
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
        tb.fg3_made,
        tb.fg3_attempted,
        tb.ft_made,
        tb.ft_attempted,
        
        -- Game context
        tb.is_home,
        g.is_overtime
        
    FROM staging.stg_team_boxscores tb
    JOIN staging.stg_games g ON tb.game_id = g.game_id
    WHERE g.is_completed
)

SELECT 
    team_id,
    
    -- Record
    COUNT(*) as games_played,
    SUM(CASE WHEN is_win THEN 1 ELSE 0 END) as wins,
    SUM(CASE WHEN NOT is_win THEN 1 ELSE 0 END) as losses,
    ROUND(SUM(CASE WHEN is_win THEN 1 ELSE 0 END)::DECIMAL / NULLIF(COUNT(*), 0), 3) as win_pct,
    
    -- Home/Away splits
    SUM(CASE WHEN is_home AND is_win THEN 1 ELSE 0 END) as home_wins,
    SUM(CASE WHEN is_home AND NOT is_win THEN 1 ELSE 0 END) as home_losses,
    SUM(CASE WHEN NOT is_home AND is_win THEN 1 ELSE 0 END) as away_wins,
    SUM(CASE WHEN NOT is_home AND NOT is_win THEN 1 ELSE 0 END) as away_losses,
    
    -- Scoring averages
    ROUND(AVG(points)::DECIMAL, 1) as ppg,
    ROUND(AVG(opponent_points)::DECIMAL, 1) as opp_ppg,
    ROUND(AVG(points)::DECIMAL - AVG(opponent_points)::DECIMAL, 1) as point_diff,
    
    -- Other averages
    ROUND(AVG(assists)::DECIMAL, 1) as apg,
    ROUND(AVG(rebounds)::DECIMAL, 1) as rpg,
    ROUND(AVG(steals)::DECIMAL, 1) as spg,
    ROUND(AVG(blocks)::DECIMAL, 1) as bpg,
    ROUND(AVG(turnovers)::DECIMAL, 1) as topg,
    
    -- Shooting averages
    ROUND(SUM(fg_made)::DECIMAL / NULLIF(SUM(fg_attempted), 0), 3) as fg_pct,
    ROUND(SUM(fg3_made)::DECIMAL / NULLIF(SUM(fg3_attempted), 0), 3) as fg3_pct,
    ROUND(SUM(ft_made)::DECIMAL / NULLIF(SUM(ft_attempted), 0), 3) as ft_pct,
    
    -- Shooting volume
    ROUND(AVG(fg_attempted)::DECIMAL, 1) as fga_pg,
    ROUND(AVG(fg3_attempted)::DECIMAL, 1) as fg3a_pg,
    ROUND(AVG(ft_attempted)::DECIMAL, 1) as fta_pg,
    
    -- Overtime games
    SUM(CASE WHEN is_overtime THEN 1 ELSE 0 END) as ot_games,
    
    -- Date range
    MIN(game_date) as first_game,
    MAX(game_date) as last_game
    
FROM team_game_stats
GROUP BY team_id
