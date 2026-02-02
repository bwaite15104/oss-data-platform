MODEL (
  name intermediate.int_team_recent_perf_wt_opp_qual,
  description 'Recent performance weighted by opponent quality. Short name for Postgres 63-char limit.',
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate recent performance weighted by opponent quality
-- OPTIMIZED: Replaced 12 correlated subqueries with rank-based self-join and conditional aggregates.
WITH team_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id AS team_id,
    g.away_team_id AS opponent_team_id,
    CASE WHEN g.winner_team_id = g.home_team_id THEN 1 WHEN g.winner_team_id = g.away_team_id THEN 0 ELSE NULL END AS is_win,
    g.home_score - g.away_score AS point_diff,
    COALESCE((
      SELECT rs.rolling_10_win_pct FROM intermediate.int_team_rolling_stats rs
      WHERE rs.team_id = g.away_team_id AND rs.game_date < g.game_date::date ORDER BY rs.game_date DESC LIMIT 1
    ), 0.5) AS opponent_strength,
    COALESCE((
      SELECT rs.rolling_10_net_rtg FROM intermediate.int_team_rolling_stats rs
      WHERE rs.team_id = g.away_team_id AND rs.game_date < g.game_date::date ORDER BY rs.game_date DESC LIMIT 1
    ), 0.0) AS opponent_net_rtg
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL AND g.away_score IS NOT NULL AND g.winner_team_id IS NOT NULL AND g.game_date < @end_ds
  UNION ALL
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.away_team_id AS team_id,
    g.home_team_id AS opponent_team_id,
    CASE WHEN g.winner_team_id = g.away_team_id THEN 1 WHEN g.winner_team_id = g.home_team_id THEN 0 ELSE NULL END AS is_win,
    g.away_score - g.home_score AS point_diff,
    COALESCE((
      SELECT rs.rolling_10_win_pct FROM intermediate.int_team_rolling_stats rs
      WHERE rs.team_id = g.home_team_id AND rs.game_date < g.game_date::date ORDER BY rs.game_date DESC LIMIT 1
    ), 0.5) AS opponent_strength,
    COALESCE((
      SELECT rs.rolling_10_net_rtg FROM intermediate.int_team_rolling_stats rs
      WHERE rs.team_id = g.home_team_id AND rs.game_date < g.game_date::date ORDER BY rs.game_date DESC LIMIT 1
    ), 0.0) AS opponent_net_rtg
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL AND g.away_score IS NOT NULL AND g.winner_team_id IS NOT NULL AND g.game_date < @end_ds
),

team_games_ranked AS (
  SELECT 
    game_id,
    game_date,
    team_id,
    opponent_team_id,
    is_win,
    point_diff,
    opponent_strength,
    opponent_net_rtg,
    ROW_NUMBER() OVER (PARTITION BY team_id ORDER BY game_date DESC, game_id DESC) AS rank_
  FROM team_games
),

current_team_games AS (
  SELECT game_id, game_date, team_id, rank_
  FROM team_games_ranked
  WHERE game_date >= @start_ds AND game_date < @end_ds
),

aggregated AS (
  SELECT 
    c.game_id,
    c.team_id,
    -- 5-game weighted metrics
    SUM(CASE WHEN h.rank_ <= c.rank_ + 5 AND h.is_win = 1 THEN h.opponent_strength ELSE 0 END)::float
      / NULLIF(SUM(CASE WHEN h.rank_ <= c.rank_ + 5 THEN h.opponent_strength END), 0) AS weighted_win_pct_5,
    SUM(CASE WHEN h.rank_ <= c.rank_ + 5 AND h.point_diff IS NOT NULL THEN h.point_diff * h.opponent_strength END)::float
      / NULLIF(SUM(CASE WHEN h.rank_ <= c.rank_ + 5 AND h.point_diff IS NOT NULL THEN h.opponent_strength END), 0) AS weighted_point_diff_5,
    SUM(CASE WHEN h.rank_ <= c.rank_ + 5 AND h.point_diff IS NOT NULL THEN (h.point_diff / 100.0) * ABS(h.opponent_net_rtg) END)::float
      / NULLIF(SUM(CASE WHEN h.rank_ <= c.rank_ + 5 AND h.point_diff IS NOT NULL THEN ABS(h.opponent_net_rtg) END), 0) * 100.0 AS weighted_net_rtg_5,
    -- 10-game weighted metrics
    SUM(CASE WHEN h.rank_ <= c.rank_ + 10 AND h.is_win = 1 THEN h.opponent_strength ELSE 0 END)::float
      / NULLIF(SUM(CASE WHEN h.rank_ <= c.rank_ + 10 THEN h.opponent_strength END), 0) AS weighted_win_pct_10,
    SUM(CASE WHEN h.rank_ <= c.rank_ + 10 AND h.point_diff IS NOT NULL THEN h.point_diff * h.opponent_strength END)::float
      / NULLIF(SUM(CASE WHEN h.rank_ <= c.rank_ + 10 AND h.point_diff IS NOT NULL THEN h.opponent_strength END), 0) AS weighted_point_diff_10,
    SUM(CASE WHEN h.rank_ <= c.rank_ + 10 AND h.point_diff IS NOT NULL THEN (h.point_diff / 100.0) * ABS(h.opponent_net_rtg) END)::float
      / NULLIF(SUM(CASE WHEN h.rank_ <= c.rank_ + 10 AND h.point_diff IS NOT NULL THEN ABS(h.opponent_net_rtg) END), 0) * 100.0 AS weighted_net_rtg_10
  FROM current_team_games c
  LEFT JOIN team_games_ranked h ON h.team_id = c.team_id
    AND h.rank_ > c.rank_
    AND h.rank_ <= c.rank_ + 10
  GROUP BY c.game_id, c.team_id, c.rank_
)

SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  COALESCE(home.weighted_win_pct_5, 0.5) AS home_weighted_win_pct_5,
  COALESCE(home.weighted_win_pct_10, 0.5) AS home_weighted_win_pct_10,
  COALESCE(home.weighted_point_diff_5, 0.0) AS home_weighted_point_diff_5,
  COALESCE(home.weighted_point_diff_10, 0.0) AS home_weighted_point_diff_10,
  COALESCE(home.weighted_net_rtg_5, 0.0) AS home_weighted_net_rtg_5,
  COALESCE(home.weighted_net_rtg_10, 0.0) AS home_weighted_net_rtg_10,
  COALESCE(away.weighted_win_pct_5, 0.5) AS away_weighted_win_pct_5,
  COALESCE(away.weighted_win_pct_10, 0.5) AS away_weighted_win_pct_10,
  COALESCE(away.weighted_point_diff_5, 0.0) AS away_weighted_point_diff_5,
  COALESCE(away.weighted_point_diff_10, 0.0) AS away_weighted_point_diff_10,
  COALESCE(away.weighted_net_rtg_5, 0.0) AS away_weighted_net_rtg_5,
  COALESCE(away.weighted_net_rtg_10, 0.0) AS away_weighted_net_rtg_10,
  COALESCE(home.weighted_win_pct_5, 0.5) - COALESCE(away.weighted_win_pct_5, 0.5) AS weighted_win_pct_diff_5,
  COALESCE(home.weighted_win_pct_10, 0.5) - COALESCE(away.weighted_win_pct_10, 0.5) AS weighted_win_pct_diff_10,
  COALESCE(home.weighted_point_diff_5, 0.0) - COALESCE(away.weighted_point_diff_5, 0.0) AS weighted_point_diff_diff_5,
  COALESCE(home.weighted_point_diff_10, 0.0) - COALESCE(away.weighted_point_diff_10, 0.0) AS weighted_point_diff_diff_10,
  COALESCE(home.weighted_net_rtg_5, 0.0) - COALESCE(away.weighted_net_rtg_5, 0.0) AS weighted_net_rtg_diff_5,
  COALESCE(home.weighted_net_rtg_10, 0.0) - COALESCE(away.weighted_net_rtg_10, 0.0) AS weighted_net_rtg_diff_10
FROM raw_dev.games g
LEFT JOIN aggregated home ON home.game_id = g.game_id AND home.team_id = g.home_team_id
LEFT JOIN aggregated away ON away.game_id = g.game_id AND away.team_id = g.away_team_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds
  AND g.home_score IS NOT NULL
  AND g.away_score IS NOT NULL;
