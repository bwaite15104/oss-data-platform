MODEL (
    name intermediate.int_team_star_feats,
    kind VIEW,
    description 'Aggregates star player return features at the team-game level'
);

-- Aggregate star player return features for each team in each game
WITH star_returns AS (
    SELECT 
        game_id,
        game_date,
        team_id,
        -- Count of returning star players
        SUM(CASE WHEN is_return_game = 1 AND player_tier = 'star' THEN 1 ELSE 0 END) as star_players_returning,
        SUM(CASE WHEN is_return_game = 1 AND player_tier = 'key_player' THEN 1 ELSE 0 END) as key_players_returning,
        SUM(CASE WHEN is_extended_return_game = 1 THEN 1 ELSE 0 END) as extended_returns,
        
        -- Total return impact score
        SUM(return_impact_score) as total_return_impact,
        
        -- Max days since last game for any returning player
        MAX(days_since_last_game) FILTER (WHERE is_return_game = 1) as max_days_since_return,
        
        -- Average days since last game for returning players
        AVG(days_since_last_game) FILTER (WHERE is_return_game = 1) as avg_days_since_return,
        
        -- Count of star players playing (not returning, just playing)
        COUNT(DISTINCT player_id) FILTER (WHERE player_tier = 'star') as star_players_playing,
        COUNT(DISTINCT player_id) FILTER (WHERE player_tier = 'key_player') as key_players_playing
        
    FROM intermediate.int_star_player_avail
    GROUP BY game_id, game_date, team_id
)
SELECT 
    game_id,
    game_date,
    team_id,
    COALESCE(star_players_returning, 0) as star_players_returning,
    COALESCE(key_players_returning, 0) as key_players_returning,
    COALESCE(extended_returns, 0) as extended_returns,
    COALESCE(total_return_impact, 0) as total_return_impact,
    max_days_since_return,
    avg_days_since_return,
    COALESCE(star_players_playing, 0) as star_players_playing,
    COALESCE(key_players_playing, 0) as key_players_playing,
    -- Binary flag: any star player returning? (includes extended returns of key players)
    CASE 
        WHEN COALESCE(star_players_returning, 0) > 0 THEN 1
        WHEN COALESCE(extended_returns, 0) > 0 THEN 1  -- Extended returns count as star returns
        ELSE 0
    END as has_star_return,
    -- Binary flag: any extended return?
    CASE WHEN COALESCE(extended_returns, 0) > 0 THEN 1 ELSE 0 END as has_extended_return
FROM star_returns
