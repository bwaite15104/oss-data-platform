MODEL (
    name intermediate.int_star_player_avail,
    kind VIEW,
    description 'Tracks star player game appearances and detects return games after absences'
);

-- Track star player appearances in games
WITH star_players AS (
    SELECT player_id, team_id, player_tier, impact_score
    FROM intermediate.int_star_players
),
player_appearances AS (
    SELECT 
        b.game_id,
        b.player_id,
        b.team_id,
        g.game_date,
        sp.player_tier,
        sp.impact_score
    FROM staging.stg_player_boxscores b
    JOIN staging.stg_games g ON b.game_id = g.game_id
    JOIN star_players sp ON b.player_id = sp.player_id AND b.team_id = sp.team_id
    WHERE g.is_completed
      AND b.minutes_played > 0  -- Only count games where player actually played
),
-- Calculate days since last game for each star player
player_game_history AS (
    SELECT 
        pa.game_id,
        pa.game_date,
        pa.team_id,
        pa.player_id,
        pa.player_tier,
        pa.impact_score,
        LAG(pa.game_date) OVER (PARTITION BY pa.player_id, pa.team_id ORDER BY pa.game_date) as prev_game_date,
        pa.game_date - LAG(pa.game_date) OVER (PARTITION BY pa.player_id, pa.team_id ORDER BY pa.game_date) as days_since_last_game
    FROM player_appearances pa
)
SELECT 
    pgh.game_id,
    pgh.game_date,
    pgh.team_id,
    pgh.player_id,
    pgh.player_tier,
    pgh.impact_score,
    pgh.prev_game_date,
    pgh.days_since_last_game,
    -- Flag return games: playing after missing 7+ days
    CASE 
        WHEN pgh.days_since_last_game >= 7 THEN 1
        ELSE 0
    END as is_return_game,
    -- Flag extended absence return: playing after missing 14+ days
    CASE 
        WHEN pgh.days_since_last_game >= 14 THEN 1
        ELSE 0
    END as is_extended_return_game,
    -- Calculate return impact (weighted by player tier and days missed)
    CASE 
        WHEN pgh.days_since_last_game >= 14 AND pgh.player_tier = 'star' THEN pgh.impact_score * 1.5
        WHEN pgh.days_since_last_game >= 7 AND pgh.player_tier = 'star' THEN pgh.impact_score * 1.2
        WHEN pgh.days_since_last_game >= 14 AND pgh.player_tier = 'key_player' THEN pgh.impact_score * 1.3
        WHEN pgh.days_since_last_game >= 7 AND pgh.player_tier = 'key_player' THEN pgh.impact_score * 1.1
        ELSE 0
    END as return_impact_score
FROM player_game_history pgh
