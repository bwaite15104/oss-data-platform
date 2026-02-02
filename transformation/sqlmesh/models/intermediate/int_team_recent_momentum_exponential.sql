MODEL (
    name intermediate.int_team_recent_mom_exp,
    kind INCREMENTAL_BY_TIME_RANGE (
        time_column game_date
    ),
    start '1946-11-01',  -- Updated for full history backfill,
    grains [
        game_id
    ],
    cron '@daily',
    description 'Recent team performance momentum with exponential decay weighting - weights most recent games exponentially more than older games to capture current form better than simple rolling averages'
);

-- Calculate recent performance momentum with exponential decay
-- This weights the most recent games exponentially more than older games
-- Formula: weighted_avg = sum(value * exp(-decay * age)) / sum(exp(-decay * age))
-- Where age = number of games ago (0 = most recent, 1 = 1 game ago, etc.)
-- decay_factor = 0.2 means: game 0 (most recent) gets weight 1.0, game 1 gets weight 0.82, game 2 gets weight 0.67, etc.
--
-- OPTIMIZED: Replaced 8 correlated subqueries with a single rank-based self-join and conditional aggregates.
WITH team_game_stats AS (
    SELECT 
        tb.team_id,
        tb.game_date,
        tb.game_id,
        tb.points,
        CASE WHEN tb.is_home THEN g.away_score ELSE g.home_score END AS opponent_points,
        (g.winner_team_id = tb.team_id) AS is_win,
        ROW_NUMBER() OVER (PARTITION BY tb.team_id ORDER BY tb.game_date DESC) AS rank_recent_first
    FROM staging.stg_team_boxscores tb
    JOIN staging.stg_games g ON tb.game_id = g.game_id
    WHERE g.is_completed
      AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Current chunk games only (one row per team per game in chunk)
current_team_games AS (
    SELECT 
        team_id,
        game_id,
        game_date,
        points,
        opponent_points,
        is_win,
        rank_recent_first AS rank_
    FROM team_game_stats
    WHERE game_date >= @start_ds
      AND game_date < @end_ds
),

-- One join to get preceding 10 games per (team, game); compute all 8 exponential-weighted metrics with conditional aggregates for 5 vs 10
exponential_momentum_per_team AS (
    SELECT 
        c.game_id,
        c.game_date,
        c.team_id,
        -- Exponential-weighted win % (last 10 games)
        SUM(CASE WHEN h.rank_recent_first > c.rank_ AND h.rank_recent_first <= c.rank_ + 10 AND h.is_win THEN EXP(-0.2 * (h.rank_recent_first - c.rank_ - 1)) ELSE 0 END)::double precision
            / NULLIF(SUM(CASE WHEN h.rank_recent_first > c.rank_ AND h.rank_recent_first <= c.rank_ + 10 THEN EXP(-0.2 * (h.rank_recent_first - c.rank_ - 1)) ELSE 0 END), 0) AS exp_weighted_win_pct_10,
        -- Exponential-weighted point differential (last 10)
        SUM(CASE WHEN h.rank_recent_first > c.rank_ AND h.rank_recent_first <= c.rank_ + 10 THEN (h.points - h.opponent_points) * EXP(-0.2 * (h.rank_recent_first - c.rank_ - 1)) ELSE 0 END)::double precision
            / NULLIF(SUM(CASE WHEN h.rank_recent_first > c.rank_ AND h.rank_recent_first <= c.rank_ + 10 THEN EXP(-0.2 * (h.rank_recent_first - c.rank_ - 1)) ELSE 0 END), 0) AS exp_weighted_point_diff_10,
        -- Exponential-weighted PPG (last 10)
        SUM(CASE WHEN h.rank_recent_first > c.rank_ AND h.rank_recent_first <= c.rank_ + 10 THEN h.points * EXP(-0.2 * (h.rank_recent_first - c.rank_ - 1)) ELSE 0 END)::double precision
            / NULLIF(SUM(CASE WHEN h.rank_recent_first > c.rank_ AND h.rank_recent_first <= c.rank_ + 10 THEN EXP(-0.2 * (h.rank_recent_first - c.rank_ - 1)) ELSE 0 END), 0) AS exp_weighted_ppg_10,
        -- Exponential-weighted opp PPG (last 10)
        SUM(CASE WHEN h.rank_recent_first > c.rank_ AND h.rank_recent_first <= c.rank_ + 10 THEN h.opponent_points * EXP(-0.2 * (h.rank_recent_first - c.rank_ - 1)) ELSE 0 END)::double precision
            / NULLIF(SUM(CASE WHEN h.rank_recent_first > c.rank_ AND h.rank_recent_first <= c.rank_ + 10 THEN EXP(-0.2 * (h.rank_recent_first - c.rank_ - 1)) ELSE 0 END), 0) AS exp_weighted_opp_ppg_10,
        -- Exponential-weighted win % (last 5 games)
        SUM(CASE WHEN h.rank_recent_first > c.rank_ AND h.rank_recent_first <= c.rank_ + 5 AND h.is_win THEN EXP(-0.2 * (h.rank_recent_first - c.rank_ - 1)) ELSE 0 END)::double precision
            / NULLIF(SUM(CASE WHEN h.rank_recent_first > c.rank_ AND h.rank_recent_first <= c.rank_ + 5 THEN EXP(-0.2 * (h.rank_recent_first - c.rank_ - 1)) ELSE 0 END), 0) AS exp_weighted_win_pct_5,
        -- Exponential-weighted point differential (last 5)
        SUM(CASE WHEN h.rank_recent_first > c.rank_ AND h.rank_recent_first <= c.rank_ + 5 THEN (h.points - h.opponent_points) * EXP(-0.2 * (h.rank_recent_first - c.rank_ - 1)) ELSE 0 END)::double precision
            / NULLIF(SUM(CASE WHEN h.rank_recent_first > c.rank_ AND h.rank_recent_first <= c.rank_ + 5 THEN EXP(-0.2 * (h.rank_recent_first - c.rank_ - 1)) ELSE 0 END), 0) AS exp_weighted_point_diff_5
    FROM current_team_games c
    LEFT JOIN team_game_stats h
        ON h.team_id = c.team_id
       AND h.rank_recent_first > c.rank_
       AND h.rank_recent_first <= c.rank_ + 10
    GROUP BY c.game_id, c.game_date, c.team_id, c.rank_
)

-- Join with games to get home/away features per game
SELECT 
    g.game_id,
    g.game_date,
    -- Home team exponential momentum features
    COALESCE(home.exp_weighted_win_pct_10, 0.5) AS home_exp_weighted_win_pct_10,
    COALESCE(home.exp_weighted_point_diff_10, 0.0) AS home_exp_weighted_point_diff_10,
    COALESCE(home.exp_weighted_ppg_10, 0.0) AS home_exp_weighted_ppg_10,
    COALESCE(home.exp_weighted_opp_ppg_10, 0.0) AS home_exp_weighted_opp_ppg_10,
    COALESCE(home.exp_weighted_win_pct_5, 0.5) AS home_exp_weighted_win_pct_5,
    COALESCE(home.exp_weighted_point_diff_5, 0.0) AS home_exp_weighted_point_diff_5,
    -- Away team exponential momentum features
    COALESCE(away.exp_weighted_win_pct_10, 0.5) AS away_exp_weighted_win_pct_10,
    COALESCE(away.exp_weighted_point_diff_10, 0.0) AS away_exp_weighted_point_diff_10,
    COALESCE(away.exp_weighted_ppg_10, 0.0) AS away_exp_weighted_ppg_10,
    COALESCE(away.exp_weighted_opp_ppg_10, 0.0) AS away_exp_weighted_opp_ppg_10,
    COALESCE(away.exp_weighted_win_pct_5, 0.5) AS away_exp_weighted_win_pct_5,
    COALESCE(away.exp_weighted_point_diff_5, 0.0) AS away_exp_weighted_point_diff_5,
    -- Differential features (home - away)
    COALESCE(home.exp_weighted_win_pct_10, 0.5) - COALESCE(away.exp_weighted_win_pct_10, 0.5) AS exp_weighted_win_pct_diff_10,
    COALESCE(home.exp_weighted_point_diff_10, 0.0) - COALESCE(away.exp_weighted_point_diff_10, 0.0) AS exp_weighted_point_diff_diff_10,
    COALESCE(home.exp_weighted_ppg_10, 0.0) - COALESCE(away.exp_weighted_ppg_10, 0.0) AS exp_weighted_ppg_diff_10,
    COALESCE(home.exp_weighted_opp_ppg_10, 0.0) - COALESCE(away.exp_weighted_opp_ppg_10, 0.0) AS exp_weighted_opp_ppg_diff_10,
    COALESCE(home.exp_weighted_win_pct_5, 0.5) - COALESCE(away.exp_weighted_win_pct_5, 0.5) AS exp_weighted_win_pct_diff_5,
    COALESCE(home.exp_weighted_point_diff_5, 0.0) - COALESCE(away.exp_weighted_point_diff_5, 0.0) AS exp_weighted_point_diff_diff_5
FROM staging.stg_games g
LEFT JOIN exponential_momentum_per_team home 
    ON home.game_id = g.game_id AND home.team_id = g.home_team_id
LEFT JOIN exponential_momentum_per_team away 
    ON away.game_id = g.game_id AND away.team_id = g.away_team_id
WHERE g.is_completed
  AND g.game_date >= @start_ds
  AND g.game_date < @end_ds
