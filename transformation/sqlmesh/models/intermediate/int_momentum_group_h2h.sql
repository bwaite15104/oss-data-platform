MODEL (
  name intermediate.int_momentum_group_h2h,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date,
    batch_size 90  -- 3 months per batch for efficient backfill
  ),
  start '1946-11-01',
  grains [game_id],
  cron '@daily',
  description 'Feature Group 2: Head-to-head and rivalry features'
);

-- H2H and rivalry features: historical matchup performance, rivalry intensity
-- This group aggregates 3 upstream models into a single materialized table

WITH games_chunk AS NOT MATERIALIZED (
  SELECT game_id, game_date, home_team_id, away_team_id
  FROM raw_dev.games 
  WHERE game_date >= @start_ds AND game_date < @end_ds
),

h2h_stats AS NOT MATERIALIZED (
  SELECT h.* FROM intermediate.int_team_h2h_stats h 
  INNER JOIN games_chunk gc ON gc.game_id = h.game_id
),

rivalry_indicators AS NOT MATERIALIZED (
  SELECT r.* FROM intermediate.int_team_rivalry_ind r 
  INNER JOIN games_chunk gc ON gc.game_id = r.game_id
),

opponent_specific_performance AS NOT MATERIALIZED (
  SELECT o.* FROM intermediate.int_team_opp_specific_perf o 
  INNER JOIN games_chunk gc ON gc.game_id = o.game_id
)

SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  
  -- Head-to-head stats
  COALESCE(h2h.home_h2h_win_pct, 0.5) AS home_h2h_win_pct,
  COALESCE(h2h.home_h2h_recent_wins, 0) AS home_h2h_recent_wins,
  COALESCE(h2h.home_h2h_win_pct_3, 0.5) AS home_h2h_win_pct_3,
  COALESCE(h2h.home_h2h_win_pct_5, 0.5) AS home_h2h_win_pct_5,
  COALESCE(h2h.home_h2h_avg_point_diff_3, 0.0) AS home_h2h_avg_point_diff_3,
  COALESCE(h2h.home_h2h_avg_point_diff_5, 0.0) AS home_h2h_avg_point_diff_5,
  COALESCE(h2h.home_h2h_momentum, 0.0) AS home_h2h_momentum,
  
  -- Rivalry indicators
  COALESCE(ri.rivalry_total_matchups, 0) AS rivalry_total_matchups,
  COALESCE(ri.rivalry_recent_matchups, 0) AS rivalry_recent_matchups,
  COALESCE(ri.rivalry_avg_point_margin, 0.0) AS rivalry_avg_point_margin,
  COALESCE(ri.rivalry_close_game_pct, 0.0) AS rivalry_close_game_pct,
  COALESCE(ri.rivalry_very_close_game_pct, 0.0) AS rivalry_very_close_game_pct,
  COALESCE(ri.rivalry_recent_close_game_pct, 0.0) AS rivalry_recent_close_game_pct,
  COALESCE(ri.rivalry_recent_avg_margin, 0.0) AS rivalry_recent_avg_margin,
  
  -- Opponent-specific performance (how teams perform vs this specific opponent)
  COALESCE(osp.home_vs_opponent_home_win_pct, 0.5) AS home_vs_opponent_home_win_pct,
  COALESCE(osp.home_vs_opponent_home_games, 0) AS home_vs_opponent_home_games,
  COALESCE(osp.home_vs_opponent_home_avg_point_diff, 0.0) AS home_vs_opponent_home_avg_point_diff,
  COALESCE(osp.home_vs_opponent_away_win_pct, 0.5) AS home_vs_opponent_away_win_pct,
  COALESCE(osp.home_vs_opponent_away_games, 0) AS home_vs_opponent_away_games,
  COALESCE(osp.home_vs_opponent_away_avg_point_diff, 0.0) AS home_vs_opponent_away_avg_point_diff,
  COALESCE(osp.away_vs_opponent_home_win_pct, 0.5) AS away_vs_opponent_home_win_pct,
  COALESCE(osp.away_vs_opponent_home_games, 0) AS away_vs_opponent_home_games,
  COALESCE(osp.away_vs_opponent_home_avg_point_diff, 0.0) AS away_vs_opponent_home_avg_point_diff,
  COALESCE(osp.away_vs_opponent_away_win_pct, 0.5) AS away_vs_opponent_away_win_pct,
  COALESCE(osp.away_vs_opponent_away_games, 0) AS away_vs_opponent_away_games,
  COALESCE(osp.away_vs_opponent_away_avg_point_diff, 0.0) AS away_vs_opponent_away_avg_point_diff,
  COALESCE(osp.opponent_specific_win_pct_diff, 0.0) AS opponent_specific_win_pct_diff,
  COALESCE(osp.opponent_specific_avg_point_diff_diff, 0.0) AS opponent_specific_avg_point_diff_diff

FROM games_chunk g
LEFT JOIN h2h_stats h2h ON h2h.game_id = g.game_id
LEFT JOIN rivalry_indicators ri ON ri.game_id = g.game_id
LEFT JOIN opponent_specific_performance osp ON osp.game_id = g.game_id;
