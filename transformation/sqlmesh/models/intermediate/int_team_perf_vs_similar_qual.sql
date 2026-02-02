MODEL (
  name intermediate.int_team_perf_vs_similar_qual,
  description 'Team performance vs similar-quality opponents. Short name for Postgres 63-char limit.',
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate team performance against opponents with similar quality (win percentage or net rating)
-- This captures how teams perform against opponents at their level, which is different from:
-- - Matchup style performance (pace/off_rtg/def_rtg categories)
-- - Opponent similarity performance (pace/off_rtg/def_rtg similarity)
-- This uses team quality metrics (win_pct, net_rtg) to find similar-quality opponents
-- Refactored: LATERAL joins replaced with int_team_roll_lkp + window aggregation (opt iteration 9)

WITH team_games AS (
  -- Create team games view (each team appears once per game with their opponent)
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id AS team_id,
    g.away_team_id AS opponent_team_id,
    CASE WHEN g.winner_team_id = g.home_team_id THEN 1 ELSE 0 END AS is_win,
    g.home_score - g.away_score AS point_diff  -- From home team's perspective
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
    CASE WHEN g.winner_team_id = g.away_team_id THEN 1 ELSE 0 END AS is_win,
    g.away_score - g.home_score AS point_diff  -- From away team's perspective
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.winner_team_id IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Get team and opponent quality at time of game (JOIN to lookup; no LATERAL)
team_quality AS (
  SELECT 
    tg.game_id,
    tg.game_date,
    tg.team_id,
    tg.opponent_team_id,
    tg.is_win,
    tg.point_diff,
    -- Team's rolling quality metrics
    COALESCE(rs_team.rolling_10_win_pct, 0.5) AS team_win_pct,
    COALESCE(rs_team.rolling_10_net_rtg, 0.0) AS team_net_rtg,
    -- Opponent's rolling quality metrics
    COALESCE(rs_opp.rolling_10_win_pct, 0.5) AS opponent_win_pct,
    COALESCE(rs_opp.rolling_10_net_rtg, 0.0) AS opponent_net_rtg
  FROM team_games tg
  LEFT JOIN intermediate.int_team_roll_lkp rs_team
    ON rs_team.game_id = tg.game_id AND rs_team.team_id = tg.team_id
  LEFT JOIN intermediate.int_team_roll_lkp rs_opp
    ON rs_opp.game_id = tg.game_id AND rs_opp.team_id = tg.opponent_team_id
),

-- Calculate quality similarity: teams with similar win_pct (within 0.1) or similar net_rtg (within 3.0)
similar_quality_opponents AS (
  SELECT 
    tq.*,
    -- Similar win_pct: within 0.1 (10 percentage points)
    CASE WHEN ABS(tq.team_win_pct - tq.opponent_win_pct) <= 0.1 THEN 1 ELSE 0 END AS is_similar_win_pct,
    -- Similar net_rtg: within 3.0 points
    CASE WHEN ABS(tq.team_net_rtg - tq.opponent_net_rtg) <= 3.0 THEN 1 ELSE 0 END AS is_similar_net_rtg,
    -- Similar quality overall: similar win_pct OR similar net_rtg
    CASE 
      WHEN ABS(tq.team_win_pct - tq.opponent_win_pct) <= 0.1 
        OR ABS(tq.team_net_rtg - tq.opponent_net_rtg) <= 3.0 
      THEN 1 
      ELSE 0 
    END AS is_similar_quality
  FROM team_quality tq
),

-- Only similar-quality games for window aggregation (last 10 per team)
similar_only_qual AS (
  SELECT
    team_id,
    game_date,
    is_win,
    point_diff
  FROM similar_quality_opponents
  WHERE is_similar_quality = 1
),

-- Per-team, per-game-date: win_pct, avg_point_diff, cnt over last 10 similar-quality games (window, no LATERAL)
team_similar_qual_agg AS (
  SELECT
    team_id,
    game_date,
    AVG(is_win::float) OVER (PARTITION BY team_id ORDER BY game_date DESC ROWS BETWEEN 1 FOLLOWING AND 10 FOLLOWING) AS win_pct,
    AVG(point_diff) OVER (PARTITION BY team_id ORDER BY game_date DESC ROWS BETWEEN 1 FOLLOWING AND 10 FOLLOWING) AS avg_point_diff,
    COUNT(*) OVER (PARTITION BY team_id ORDER BY game_date DESC ROWS BETWEEN 1 FOLLOWING AND 10 FOLLOWING)::int AS cnt
  FROM similar_only_qual
),

-- One row per (team_id, game_date) for JOIN
team_similar_qual_dedup AS (
  SELECT DISTINCT ON (team_id, game_date)
    team_id,
    game_date,
    win_pct,
    avg_point_diff,
    cnt
  FROM team_similar_qual_agg
  ORDER BY team_id, game_date
)

-- Final: one row per game with home/away similar-quality metrics (JOINs only)
SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  COALESCE(home.win_pct, 0.5) AS home_win_pct_vs_similar_quality,
  COALESCE(home.avg_point_diff, 0.0) AS home_avg_point_diff_vs_similar_quality,
  COALESCE(home.cnt, 0) AS home_similar_quality_game_count,
  COALESCE(away.win_pct, 0.5) AS away_win_pct_vs_similar_quality,
  COALESCE(away.avg_point_diff, 0.0) AS away_avg_point_diff_vs_similar_quality,
  COALESCE(away.cnt, 0) AS away_similar_quality_game_count,
  COALESCE(sqo_curr.is_similar_quality, 0) AS is_current_game_similar_quality
FROM raw_dev.games g
LEFT JOIN team_similar_qual_dedup home
  ON home.team_id = g.home_team_id AND home.game_date = g.game_date::date
LEFT JOIN team_similar_qual_dedup away
  ON away.team_id = g.away_team_id AND away.game_date = g.game_date::date
LEFT JOIN similar_quality_opponents sqo_curr
  ON sqo_curr.game_id = g.game_id
  AND sqo_curr.team_id = g.home_team_id
  AND sqo_curr.opponent_team_id = g.away_team_id
WHERE g.home_score IS NOT NULL
  AND g.away_score IS NOT NULL
  AND g.game_date >= @start_ds
  AND g.game_date < @end_ds;
