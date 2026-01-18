MODEL (
    name staging.stg_team_boxscores,
    kind VIEW,
    description 'Cleaned team boxscore data'
);

SELECT 
    game_id,
    team_id::INTEGER as team_id,
    game_date::DATE as game_date,
    team_name,
    team_city,
    is_home,
    
    -- Scoring
    COALESCE(points, 0) as points,
    COALESCE(assists, 0) as assists,
    COALESCE(rebounds_total, 0) as rebounds,
    COALESCE(steals, 0) as steals,
    COALESCE(blocks, 0) as blocks,
    COALESCE(turnovers, 0) as turnovers,
    
    -- Shooting
    COALESCE(field_goals_made, 0) as fg_made,
    COALESCE(field_goals_attempted, 0) as fg_attempted,
    COALESCE(field_goal_pct, 0.0)::FLOAT as fg_pct,
    
    COALESCE(three_pointers_made, 0) as fg3_made,
    COALESCE(three_pointers_attempted, 0) as fg3_attempted,
    COALESCE(three_point_pct, 0.0)::FLOAT as fg3_pct,
    
    COALESCE(free_throws_made, 0) as ft_made,
    COALESCE(free_throws_attempted, 0) as ft_attempted,
    COALESCE(free_throw_pct, 0.0)::FLOAT as ft_pct
    
FROM raw_dev.team_boxscores
