MODEL (
    name intermediate.int_star_players,
    kind FULL,
    description 'Identifies star players (top players by PPG) - materialized for performance'
);

-- Identify star players: Top players by average PPG (minimum 10 games played)
WITH player_season_stats AS (
    SELECT 
        b.player_id,
        b.team_id,
        COUNT(DISTINCT b.game_id) as games_played,
        AVG(b.points::numeric) as avg_ppg,
        AVG(b.assists::numeric) as avg_apg,
        AVG(b.rebounds::numeric) as avg_rpg
    FROM staging.stg_player_boxscores b
    JOIN staging.stg_games g ON b.game_id = g.game_id
    WHERE g.is_completed
      AND b.minutes_played > 0  -- Only count games where player actually played
    GROUP BY b.player_id, b.team_id
    HAVING COUNT(DISTINCT b.game_id) >= 10  -- Minimum games threshold
),
star_players AS (
    SELECT 
        player_id,
        team_id,
        avg_ppg,
        avg_apg,
        avg_rpg,
        -- Star tier: 20+ PPG = star, 15-20 = key player
        CASE 
            WHEN avg_ppg >= 20 THEN 'star'
            WHEN avg_ppg >= 15 THEN 'key_player'
            ELSE 'role_player'
        END as player_tier,
        -- Calculate impact score (weighted combination)
        (avg_ppg * 1.0 + avg_apg * 0.5 + avg_rpg * 0.3) as impact_score
    FROM player_season_stats
    WHERE avg_ppg >= 15  -- Focus on key players and stars
)
SELECT 
    sp.player_id,
    sp.team_id,
    sp.avg_ppg,
    sp.avg_apg,
    sp.avg_rpg,
    sp.player_tier,
    sp.impact_score
FROM star_players sp
