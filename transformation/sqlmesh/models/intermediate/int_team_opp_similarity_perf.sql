MODEL (
  name intermediate.int_team_opp_similarity_perf,
  description 'Team performance vs similar opponents. Short name for Postgres 63-char limit.',
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate team performance against opponents with similar characteristics
-- This captures matchup-specific patterns by finding opponents with similar pace, offensive/defensive ratings
-- Different from matchup style performance: instead of categorizing opponents into styles, we find similar opponents

WITH team_games AS (
  -- Create team games view (each team appears once per game with their opponent)
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id AS team_id,
    g.away_team_id AS opponent_team_id,
    CASE WHEN g.winner_team_id = g.home_team_id THEN 1 ELSE 0 END AS is_win
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.winner_team_id IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
  
  UNION ALL
  
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.away_team_id AS team_id,
    g.home_team_id AS opponent_team_id,
    CASE WHEN g.winner_team_id = g.away_team_id THEN 1 ELSE 0 END AS is_win
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.winner_team_id IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Get team and opponent characteristics at time of game (JOIN to lookup; no LATERAL)
team_characteristics AS (
  SELECT 
    tg.game_id,
    tg.game_date,
    tg.team_id,
    tg.opponent_team_id,
    tg.is_win,
    -- Team's rolling stats (from lookup: one row per game_id, team_id)
    COALESCE(rs_team.rolling_10_pace, 95.0) AS team_pace,
    COALESCE(rs_team.rolling_10_off_rtg, 110.0) AS team_off_rtg,
    COALESCE(rs_team.rolling_10_def_rtg, 110.0) AS team_def_rtg,
    -- Opponent's rolling stats
    COALESCE(rs_opp.rolling_10_pace, 95.0) AS opponent_pace,
    COALESCE(rs_opp.rolling_10_off_rtg, 110.0) AS opponent_off_rtg,
    COALESCE(rs_opp.rolling_10_def_rtg, 110.0) AS opponent_def_rtg
  FROM team_games tg
  LEFT JOIN intermediate.int_team_roll_lkp rs_team
    ON rs_team.game_id = tg.game_id AND rs_team.team_id = tg.team_id
  LEFT JOIN intermediate.int_team_roll_lkp rs_opp
    ON rs_opp.game_id = tg.game_id AND rs_opp.team_id = tg.opponent_team_id
),

-- Calculate similarity score between team and opponent characteristics
-- Similarity = 1 / (1 + normalized_distance) where distance is Euclidean distance in normalized space
similarity_scores AS (
  SELECT 
    tc.*,
    -- Normalize characteristics to 0-1 scale (using reasonable NBA ranges)
    -- Pace: 85-105 (normalize to 0-1)
    (tc.team_pace - 85.0) / 20.0 AS team_pace_norm,
    (tc.opponent_pace - 85.0) / 20.0 AS opponent_pace_norm,
    -- Offensive rating: 100-120 (normalize to 0-1)
    (tc.team_off_rtg - 100.0) / 20.0 AS team_off_rtg_norm,
    (tc.opponent_off_rtg - 100.0) / 20.0 AS opponent_off_rtg_norm,
    -- Defensive rating: 100-120 (normalize to 0-1)
    (tc.team_def_rtg - 100.0) / 20.0 AS team_def_rtg_norm,
    (tc.opponent_def_rtg - 100.0) / 20.0 AS opponent_def_rtg_norm
  FROM team_characteristics tc
),

-- Calculate similarity distance (Euclidean distance in normalized 3D space: pace, off_rtg, def_rtg)
similarity_with_distance AS (
  SELECT 
    ss.*,
    SQRT(
      POWER(ss.team_pace_norm - ss.opponent_pace_norm, 2) +
      POWER(ss.team_off_rtg_norm - ss.opponent_off_rtg_norm, 2) +
      POWER(ss.team_def_rtg_norm - ss.opponent_def_rtg_norm, 2)
    ) AS characteristic_distance,
    -- Similarity score: higher = more similar (inverse of distance, capped at 1.0)
    LEAST(1.0 / (1.0 + SQRT(
      POWER(ss.team_pace_norm - ss.opponent_pace_norm, 2) +
      POWER(ss.team_off_rtg_norm - ss.opponent_off_rtg_norm, 2) +
      POWER(ss.team_def_rtg_norm - ss.opponent_def_rtg_norm, 2)
    )), 1.0) AS similarity_score
  FROM similarity_scores ss
),

-- Only similar-opponent games (similarity > 0.7) for window aggregation
similar_only AS (
  SELECT
    team_id,
    game_date,
    is_win,
    CASE WHEN is_win = 1 THEN 5.0 ELSE -5.0 END AS pd
  FROM similarity_with_distance
  WHERE similarity_score > 0.7
),

-- Per-team, per-game-date: win_pct, count, avg_pd over last 15 similar games (window, no LATERAL)
team_similar_agg AS (
  SELECT
    team_id,
    game_date,
    AVG(is_win::float) OVER (PARTITION BY team_id ORDER BY game_date DESC ROWS BETWEEN 1 FOLLOWING AND 15 FOLLOWING) AS win_pct_vs_similar_opponents,
    COUNT(*) OVER (PARTITION BY team_id ORDER BY game_date DESC ROWS BETWEEN 1 FOLLOWING AND 15 FOLLOWING)::int AS similar_opponent_game_count,
    AVG(pd) OVER (PARTITION BY team_id ORDER BY game_date DESC ROWS BETWEEN 1 FOLLOWING AND 15 FOLLOWING) AS avg_point_diff_vs_similar_opponents
  FROM similar_only
),

-- One row per (team_id, game_date) for JOIN
team_similar_agg_dedup AS (
  SELECT DISTINCT ON (team_id, game_date)
    team_id,
    game_date,
    win_pct_vs_similar_opponents,
    similar_opponent_game_count,
    avg_point_diff_vs_similar_opponents
  FROM team_similar_agg
  ORDER BY team_id, game_date
)

-- Final: one row per game with home/away similar-opponent metrics (JOINs only)
SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  COALESCE(home.win_pct_vs_similar_opponents, 0.5) AS home_win_pct_vs_similar_opponents,
  COALESCE(home.similar_opponent_game_count, 0) AS home_similar_opponent_game_count,
  COALESCE(home.avg_point_diff_vs_similar_opponents, 0.0) AS home_avg_point_diff_vs_similar_opponents,
  COALESCE(away.win_pct_vs_similar_opponents, 0.5) AS away_win_pct_vs_similar_opponents,
  COALESCE(away.similar_opponent_game_count, 0) AS away_similar_opponent_game_count,
  COALESCE(away.avg_point_diff_vs_similar_opponents, 0.0) AS away_avg_point_diff_vs_similar_opponents,
  COALESCE(swd_curr.similarity_score, 0.5) AS current_game_similarity_score
FROM raw_dev.games g
LEFT JOIN team_similar_agg_dedup home
  ON home.team_id = g.home_team_id AND home.game_date = g.game_date::date
LEFT JOIN team_similar_agg_dedup away
  ON away.team_id = g.away_team_id AND away.game_date = g.game_date::date
LEFT JOIN similarity_with_distance swd_curr
  ON swd_curr.game_id = g.game_id
  AND swd_curr.team_id = g.home_team_id
  AND swd_curr.opponent_team_id = g.away_team_id
WHERE g.home_score IS NOT NULL
  AND g.away_score IS NOT NULL
  AND g.game_date >= @start_ds
  AND g.game_date < @end_ds;
