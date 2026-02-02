MODEL (
    name intermediate.int_team_form_divergence,
    kind INCREMENTAL_BY_TIME_RANGE (
        time_column game_date
    ),
    start '1946-11-01',  -- Updated for full history backfill,
    grains [
        team_id,
        game_date
    ],
    cron '@daily',
    description 'Team form divergence - compares recent performance (last 5/10 games) to season average to capture teams trending above/below their baseline'
);

-- Calculate form divergence: how much is recent form (last 5/10 games) diverging from season average?
-- Teams playing above season average recently = trending up
-- Teams playing below season average recently = trending down
WITH rolling_stats AS (
    SELECT 
        team_id,
        game_date,
        game_id,
        rolling_5_win_pct,
        rolling_5_ppg,
        rolling_5_opp_ppg,
        rolling_5_net_rtg,
        rolling_10_win_pct,
        rolling_10_ppg,
        rolling_10_opp_ppg,
        rolling_10_net_rtg
    FROM intermediate.int_team_rolling_stats
),

-- Calculate season-to-date stats at each game (cumulative average up to that point)
-- This gives us the "season average" at the time of each game
season_to_date AS (
    SELECT 
        tb.team_id,
        tb.game_date,
        tb.game_id,
        -- Cumulative season stats up to this game (excluding current game)
        AVG(CASE WHEN tb.is_home THEN g.home_score ELSE g.away_score END) 
            OVER (PARTITION BY tb.team_id ORDER BY tb.game_date ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) 
            as season_to_date_ppg,
        AVG(CASE WHEN tb.is_home THEN g.away_score ELSE g.home_score END) 
            OVER (PARTITION BY tb.team_id ORDER BY tb.game_date ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) 
            as season_to_date_opp_ppg,
        AVG(CASE WHEN g.winner_team_id = tb.team_id THEN 1.0 ELSE 0.0 END) 
            OVER (PARTITION BY tb.team_id ORDER BY tb.game_date ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) 
            as season_to_date_win_pct,
        COUNT(*) OVER (PARTITION BY tb.team_id ORDER BY tb.game_date ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) 
            as games_played_before
    FROM staging.stg_team_boxscores tb
    JOIN staging.stg_games g ON tb.game_id = g.game_id
    WHERE g.is_completed
      AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Join with season-to-date stats to calculate form divergence
form_divergence_with_game AS (
    SELECT 
        r.game_id,
        r.team_id,
        r.game_date,
        
        -- 5-game form divergence (recent vs season-to-date)
        -- Positive = playing above season average (trending up)
        -- Negative = playing below season average (trending down)
        COALESCE(r.rolling_5_win_pct - s.season_to_date_win_pct, 0.0) as form_divergence_win_pct_5,
        COALESCE(r.rolling_5_ppg - s.season_to_date_ppg, 0.0) as form_divergence_ppg_5,
        COALESCE(r.rolling_5_opp_ppg - s.season_to_date_opp_ppg, 0.0) as form_divergence_opp_ppg_5,
        COALESCE((r.rolling_5_ppg - r.rolling_5_opp_ppg) - (s.season_to_date_ppg - s.season_to_date_opp_ppg), 0.0) as form_divergence_net_rtg_5,
        
        -- 10-game form divergence
        COALESCE(r.rolling_10_win_pct - s.season_to_date_win_pct, 0.0) as form_divergence_win_pct_10,
        COALESCE(r.rolling_10_ppg - s.season_to_date_ppg, 0.0) as form_divergence_ppg_10,
        COALESCE(r.rolling_10_opp_ppg - s.season_to_date_opp_ppg, 0.0) as form_divergence_opp_ppg_10,
        COALESCE((r.rolling_10_ppg - r.rolling_10_opp_ppg) - (s.season_to_date_ppg - s.season_to_date_opp_ppg), 0.0) as form_divergence_net_rtg_10,
        
        -- Magnitude indicators (absolute divergence - how much is form diverging?)
        ABS(COALESCE(r.rolling_5_win_pct - s.season_to_date_win_pct, 0.0)) as form_divergence_magnitude_win_pct_5,
        ABS(COALESCE(r.rolling_10_win_pct - s.season_to_date_win_pct, 0.0)) as form_divergence_magnitude_win_pct_10,
        
        -- Sample size indicator (need at least 5 games before calculating divergence)
        CASE WHEN s.games_played_before >= 5 THEN 1 ELSE 0 END as has_sufficient_games_5,
        CASE WHEN s.games_played_before >= 10 THEN 1 ELSE 0 END as has_sufficient_games_10

    FROM rolling_stats r
    LEFT JOIN season_to_date s 
        ON r.team_id = s.team_id 
        AND r.game_date = s.game_date
    WHERE s.games_played_before >= 5  -- Only include games where we have enough history
)

SELECT 
    game_id,
    team_id,
    game_date,
    form_divergence_win_pct_5,
    form_divergence_ppg_5,
    form_divergence_opp_ppg_5,
    form_divergence_net_rtg_5,
    form_divergence_win_pct_10,
    form_divergence_ppg_10,
    form_divergence_opp_ppg_10,
    form_divergence_net_rtg_10,
    form_divergence_magnitude_win_pct_5,
    form_divergence_magnitude_win_pct_10,
    has_sufficient_games_5,
    has_sufficient_games_10
FROM form_divergence_with_game
WHERE game_date >= @start_ds
  AND game_date < @end_ds