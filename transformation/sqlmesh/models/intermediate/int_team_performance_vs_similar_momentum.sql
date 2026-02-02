MODEL (
  name intermediate.int_team_perf_vs_similar_mom,
  description 'Team performance vs similar-momentum opponents. Short name for Postgres 63-char limit.',
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate team performance against opponents with similar recent momentum
-- This captures how teams perform when facing opponents with similar recent form
-- (e.g., both teams on winning streaks, both teams struggling)
-- Refactored: LATERAL joins replaced with int_team_roll_lkp + window aggregation (opt iteration 10)

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

-- Get team and opponent momentum metrics at time of game (JOIN to lookup; no LATERAL)
team_momentum AS (
  SELECT 
    tg.game_id,
    tg.game_date,
    tg.team_id,
    tg.opponent_team_id,
    tg.is_win,
    tg.point_diff,
    -- Team's rolling momentum metrics
    COALESCE(rs_team.rolling_5_win_pct, 0.5) AS team_win_pct_5,
    COALESCE(rs_team.rolling_10_win_pct, 0.5) AS team_win_pct_10,
    COALESCE(rs_team.rolling_5_net_rtg, 0.0) AS team_net_rtg_5,
    COALESCE(rs_team.rolling_10_net_rtg, 0.0) AS team_net_rtg_10,
    -- Opponent's rolling momentum metrics
    COALESCE(rs_opp.rolling_5_win_pct, 0.5) AS opponent_win_pct_5,
    COALESCE(rs_opp.rolling_10_win_pct, 0.5) AS opponent_win_pct_10,
    COALESCE(rs_opp.rolling_5_net_rtg, 0.0) AS opponent_net_rtg_5,
    COALESCE(rs_opp.rolling_10_net_rtg, 0.0) AS opponent_net_rtg_10
  FROM team_games tg
  LEFT JOIN intermediate.int_team_roll_lkp rs_team
    ON rs_team.game_id = tg.game_id AND rs_team.team_id = tg.team_id
  LEFT JOIN intermediate.int_team_roll_lkp rs_opp
    ON rs_opp.game_id = tg.game_id AND rs_opp.team_id = tg.opponent_team_id
),

-- Calculate momentum similarity: teams with similar recent win_pct (within 0.15) or similar net_rtg (within 4.0)
similar_momentum_opponents AS (
  SELECT 
    tm.*,
    CASE WHEN ABS(tm.team_win_pct_5 - tm.opponent_win_pct_5) <= 0.15 THEN 1 ELSE 0 END AS is_similar_win_pct_5,
    CASE WHEN ABS(tm.team_net_rtg_5 - tm.opponent_net_rtg_5) <= 4.0 THEN 1 ELSE 0 END AS is_similar_net_rtg_5,
    CASE 
      WHEN ABS(tm.team_win_pct_5 - tm.opponent_win_pct_5) <= 0.15 
        OR ABS(tm.team_net_rtg_5 - tm.opponent_net_rtg_5) <= 4.0 
      THEN 1 
      ELSE 0 
    END AS is_similar_momentum
  FROM team_momentum tm
),

-- Only similar-momentum games for window aggregation (last 10 per team)
similar_only_mom AS (
  SELECT
    team_id,
    game_date,
    is_win,
    point_diff
  FROM similar_momentum_opponents
  WHERE is_similar_momentum = 1
),

-- Per-team, per-game-date: win_pct, avg_point_diff, cnt over last 10 similar-momentum games (window, no LATERAL)
team_similar_mom_agg AS (
  SELECT
    team_id,
    game_date,
    AVG(is_win::float) OVER (PARTITION BY team_id ORDER BY game_date DESC ROWS BETWEEN 1 FOLLOWING AND 10 FOLLOWING) AS win_pct,
    AVG(point_diff) OVER (PARTITION BY team_id ORDER BY game_date DESC ROWS BETWEEN 1 FOLLOWING AND 10 FOLLOWING) AS avg_point_diff,
    COUNT(*) OVER (PARTITION BY team_id ORDER BY game_date DESC ROWS BETWEEN 1 FOLLOWING AND 10 FOLLOWING)::int AS cnt
  FROM similar_only_mom
),

-- One row per (team_id, game_date) for JOIN
team_similar_mom_dedup AS (
  SELECT DISTINCT ON (team_id, game_date)
    team_id,
    game_date,
    win_pct,
    avg_point_diff,
    cnt
  FROM team_similar_mom_agg
  ORDER BY team_id, game_date
)

-- Final: one row per game with home/away similar-momentum metrics (JOINs only)
SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  COALESCE(home.win_pct, 0.5) AS home_win_pct_vs_similar_momentum,
  COALESCE(home.avg_point_diff, 0.0) AS home_avg_point_diff_vs_similar_momentum,
  COALESCE(home.cnt, 0) AS home_similar_momentum_game_count,
  COALESCE(away.win_pct, 0.5) AS away_win_pct_vs_similar_momentum,
  COALESCE(away.avg_point_diff, 0.0) AS away_avg_point_diff_vs_similar_momentum,
  COALESCE(away.cnt, 0) AS away_similar_momentum_game_count,
  COALESCE(smo_curr.is_similar_momentum, 0) AS is_current_game_similar_momentum
FROM raw_dev.games g
LEFT JOIN team_similar_mom_dedup home
  ON home.team_id = g.home_team_id AND home.game_date = g.game_date::date
LEFT JOIN team_similar_mom_dedup away
  ON away.team_id = g.away_team_id AND away.game_date = g.game_date::date
LEFT JOIN similar_momentum_opponents smo_curr
  ON smo_curr.game_id = g.game_id
  AND smo_curr.team_id = g.home_team_id
  AND smo_curr.opponent_team_id = g.away_team_id
WHERE g.home_score IS NOT NULL
  AND g.away_score IS NOT NULL
  AND g.game_date >= @start_ds
  AND g.game_date < @end_ds;
