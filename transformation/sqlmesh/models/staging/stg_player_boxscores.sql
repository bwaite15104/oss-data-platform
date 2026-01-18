MODEL (
    name staging.stg_player_boxscores,
    kind VIEW,
    description 'Cleaned player boxscore data with parsed minutes'
);

SELECT 
    game_id,
    player_id::INTEGER as player_id,
    game_date::DATE as game_date,
    player_name,
    team_id::INTEGER as team_id,
    is_home,
    is_starter,
    
    -- Parse minutes (format: "PT32M45S" -> 32.75)
    COALESCE(
        CASE 
            WHEN minutes LIKE 'PT%' THEN
                COALESCE(NULLIF(REGEXP_REPLACE(minutes, '.*PT(\d+)M.*', '\1'), minutes)::FLOAT, 0) +
                COALESCE(NULLIF(REGEXP_REPLACE(minutes, '.*M(\d+)S.*', '\1'), minutes)::FLOAT / 60.0, 0)
            ELSE NULL
        END,
        0
    ) as minutes_played,
    
    -- Scoring stats
    COALESCE(points, 0) as points,
    COALESCE(assists, 0) as assists,
    
    -- Rebounds
    COALESCE(rebounds_total, 0) as rebounds,
    COALESCE(rebounds_offensive, 0) as rebounds_offensive,
    COALESCE(rebounds_defensive, 0) as rebounds_defensive,
    
    -- Other stats
    COALESCE(steals, 0) as steals,
    COALESCE(blocks, 0) as blocks,
    COALESCE(turnovers, 0) as turnovers,
    
    -- Fouls
    COALESCE(fouls_personal, 0) as fouls_personal,
    COALESCE(fouls_technical, 0) as fouls_technical,
    
    -- Shooting - made/attempted
    COALESCE(field_goals_made, 0) as fg_made,
    COALESCE(field_goals_attempted, 0) as fg_attempted,
    COALESCE(three_pointers_made, 0) as fg3_made,
    COALESCE(three_pointers_attempted, 0) as fg3_attempted,
    COALESCE(free_throws_made, 0) as ft_made,
    COALESCE(free_throws_attempted, 0) as ft_attempted,
    
    -- Shooting percentages (use raw or calculate)
    COALESCE(field_goal_pct, 0.0)::FLOAT as fg_pct,
    COALESCE(three_point_pct, 0.0)::FLOAT as fg3_pct,
    COALESCE(free_throw_pct, 0.0)::FLOAT as ft_pct,
    
    -- Plus/Minus
    COALESCE(plus_minus, 0) as plus_minus,
    
    -- Paint and second chance points
    COALESCE(points_fast_break, 0) as points_fastbreak,
    COALESCE(points_in_paint, 0) as points_in_paint,
    COALESCE(points_second_chance, 0) as points_second_chance
    
FROM raw_dev.boxscores
