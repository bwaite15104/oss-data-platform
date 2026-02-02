MODEL (
  name intermediate.int_team_opponent_quality,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- OPTIMIZED: Replaced correlated subqueries with rank-based self-joins and LATERALs.
WITH team_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id AS team_id,
    g.away_team_id AS opponent_team_id
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.game_date < @end_ds
  UNION ALL
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.away_team_id AS team_id,
    g.home_team_id AS opponent_team_id
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.game_date < @end_ds
),

team_games_ranked AS (
  SELECT 
    tg.*,
    ROW_NUMBER() OVER (PARTITION BY tg.team_id ORDER BY tg.game_date DESC, tg.game_id DESC) AS rank_
  FROM team_games tg
),

-- Opponent rolling win_pct at game time (one LATERAL per row; used for SOS)
team_games_with_opp_rolling AS (
  SELECT 
    tg.game_id,
    tg.game_date,
    tg.team_id,
    tg.opponent_team_id,
    tg.rank_,
    COALESCE(rs.rolling_10_win_pct, 0.5) AS opp_rolling_10_win_pct
  FROM team_games_ranked tg
  LEFT JOIN LATERAL (
    SELECT rolling_10_win_pct
    FROM intermediate.int_team_rolling_stats rs
    WHERE rs.team_id = tg.opponent_team_id AND rs.game_date < tg.game_date
    ORDER BY rs.game_date DESC
    LIMIT 1
  ) rs ON TRUE
),

top_team_games AS (
  SELECT 
    tg.game_id,
    tg.game_date,
    tg.team_id,
    tg.opponent_team_id,
    CASE WHEN g.winner_team_id = tg.team_id THEN 1 ELSE 0 END AS is_win,
    COALESCE(ss_opp.win_pct, 0.5) AS opponent_win_pct
  FROM team_games tg
  JOIN raw_dev.games g ON g.game_id = tg.game_id
  LEFT JOIN intermediate.int_team_season_stats ss_opp ON ss_opp.team_id = tg.opponent_team_id
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.winner_team_id IS NOT NULL
),

current_team_games AS (
  SELECT game_id, game_date, team_id, rank_
  FROM team_games_ranked
  WHERE game_date >= @start_ds AND game_date < @end_ds
),

-- Last 5 opponents' avg season win_pct (rank-based join)
recent_opp_avg AS (
  SELECT 
    c.game_id,
    c.team_id,
    AVG(COALESCE(ss.win_pct, 0.5)) AS recent_opp_avg_win_pct
  FROM current_team_games c
  JOIN team_games_ranked h ON h.team_id = c.team_id
    AND h.rank_ > c.rank_
    AND h.rank_ <= c.rank_ + 5
  LEFT JOIN intermediate.int_team_season_stats ss ON ss.team_id = h.opponent_team_id
  GROUP BY c.game_id, c.team_id
),

-- SOS 5/10 and weighted (rank-based join on team_games_with_opp_rolling)
sos_aggregated AS (
  SELECT 
    c.game_id,
    c.team_id,
    AVG(CASE WHEN h.rank_ <= c.rank_ + 5 THEN h.opp_rolling_10_win_pct END) AS sos_5_rolling,
    AVG(CASE WHEN h.rank_ <= c.rank_ + 10 THEN h.opp_rolling_10_win_pct END) AS sos_10_rolling,
    CASE 
      WHEN SUM(CASE WHEN h.rank_ <= c.rank_ + 5 THEN (c.rank_ + 6 - h.rank_) ELSE 0 END) > 0
      THEN SUM(CASE WHEN h.rank_ <= c.rank_ + 5 THEN h.opp_rolling_10_win_pct * (c.rank_ + 6 - h.rank_) ELSE 0 END)::float
           / SUM(CASE WHEN h.rank_ <= c.rank_ + 5 THEN (c.rank_ + 6 - h.rank_) ELSE 0 END)
      ELSE 0.5
    END AS sos_5_weighted
  FROM current_team_games c
  LEFT JOIN team_games_with_opp_rolling h ON h.team_id = c.team_id
    AND h.rank_ > c.rank_
    AND h.rank_ <= c.rank_ + 10
  GROUP BY c.game_id, c.team_id, c.rank_
)

SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  COALESCE(h_roa.recent_opp_avg_win_pct, 0.5) AS home_recent_opp_avg_win_pct,
  COALESCE(a_roa.recent_opp_avg_win_pct, 0.5) AS away_recent_opp_avg_win_pct,
  COALESCE(h_pvq.perf_vs_quality, 0.5) AS home_performance_vs_quality,
  COALESCE(a_pvq.perf_vs_quality, 0.5) AS away_performance_vs_quality,
  COALESCE(h_sos.sos_5_rolling, 0.5) AS home_sos_5_rolling,
  COALESCE(a_sos.sos_5_rolling, 0.5) AS away_sos_5_rolling,
  COALESCE(h_sos.sos_10_rolling, 0.5) AS home_sos_10_rolling,
  COALESCE(a_sos.sos_10_rolling, 0.5) AS away_sos_10_rolling,
  COALESCE(h_sos.sos_5_weighted, 0.5) AS home_sos_5_weighted,
  COALESCE(a_sos.sos_5_weighted, 0.5) AS away_sos_5_weighted
FROM raw_dev.games g
LEFT JOIN recent_opp_avg h_roa ON h_roa.game_id = g.game_id AND h_roa.team_id = g.home_team_id
LEFT JOIN recent_opp_avg a_roa ON a_roa.game_id = g.game_id AND a_roa.team_id = g.away_team_id
LEFT JOIN LATERAL (
  SELECT AVG(ttg.is_win::float) AS perf_vs_quality
  FROM top_team_games ttg
  WHERE ttg.team_id = g.home_team_id
    AND ttg.game_date < g.game_date::date
    AND ttg.opponent_win_pct > 0.6
) h_pvq ON TRUE
LEFT JOIN LATERAL (
  SELECT AVG(ttg.is_win::float) AS perf_vs_quality
  FROM top_team_games ttg
  WHERE ttg.team_id = g.away_team_id
    AND ttg.game_date < g.game_date::date
    AND ttg.opponent_win_pct > 0.6
) a_pvq ON TRUE
LEFT JOIN sos_aggregated h_sos ON h_sos.game_id = g.game_id AND h_sos.team_id = g.home_team_id
LEFT JOIN sos_aggregated a_sos ON a_sos.game_id = g.game_id AND a_sos.team_id = g.away_team_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds;
