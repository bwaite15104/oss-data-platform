MODEL (
    name staging.stg_games,
    kind VIEW,
    description 'Cleaned NBA games data with parsed dates'
);

SELECT 
    game_id,
    game_status,
    
    -- Parse game datetime
    game_date::DATE as game_date,
    season,
    season_type,
    
    -- Teams
    home_team_id::INTEGER as home_team_id,
    home_team_name,
    
    away_team_id::INTEGER as away_team_id,
    away_team_name,
    
    -- Scores
    home_score::INTEGER as home_score,
    away_score::INTEGER as away_score,
    
    -- Arena info
    venue as arena_name,
    arena_city,
    arena_state,
    
    -- Determine winner
    winner_team_id::INTEGER as winner_team_id,
    
    -- Is overtime (check status text)
    game_status LIKE '%OT%' as is_overtime,
    
    -- Is completed
    game_status IN ('Final', 'Final/OT', 'Final/OT2') as is_completed
    
FROM raw_dev.games
