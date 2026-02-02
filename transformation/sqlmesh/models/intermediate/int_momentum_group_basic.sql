MODEL (
  name intermediate.int_momentum_group_basic,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date,
    batch_size 90  -- 3 months per batch for efficient backfill
  ),
  start '1946-11-01',
  grains [game_id],
  cron '@daily',
  description 'Feature Group 1: Basic momentum features (streaks, rest, fatigue, weighted momentum)'
);

-- Basic momentum features: streaks, rest days, fatigue, weighted momentum
-- This group aggregates 7 upstream models into a single materialized table

WITH games_chunk AS NOT MATERIALIZED (
  SELECT game_id, game_date, home_team_id, away_team_id
  FROM raw_dev.games 
  WHERE game_date >= @start_ds AND game_date < @end_ds
),

streaks AS NOT MATERIALIZED (
  SELECT s.* FROM intermediate.int_team_streaks s 
  INNER JOIN games_chunk gc ON gc.game_id = s.game_id
),

rest_days AS NOT MATERIALIZED (
  SELECT r.* FROM intermediate.int_team_rest_days r 
  INNER JOIN games_chunk gc ON gc.game_id = r.game_id
),

cumulative_fatigue AS NOT MATERIALIZED (
  SELECT c.* FROM intermediate.int_team_cum_fatigue c 
  INNER JOIN games_chunk gc ON gc.game_id = c.game_id
),

weighted_momentum AS NOT MATERIALIZED (
  SELECT w.* FROM intermediate.int_team_weighted_momentum w 
  INNER JOIN games_chunk gc ON gc.game_id = w.game_id
),

win_streak_quality AS NOT MATERIALIZED (
  SELECT w.* FROM intermediate.int_team_streak_qual w 
  INNER JOIN games_chunk gc ON gc.game_id = w.game_id
),

contextualized_streaks AS NOT MATERIALIZED (
  SELECT c.* FROM intermediate.int_team_ctx_streaks c 
  INNER JOIN games_chunk gc ON gc.game_id = c.game_id
),

exponential_momentum AS NOT MATERIALIZED (
  SELECT e.* FROM intermediate.int_team_recent_mom_exp e 
  INNER JOIN games_chunk gc ON gc.game_id = e.game_id
)

SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  
  -- Streak features
  COALESCE(s.home_win_streak, 0) AS home_win_streak,
  COALESCE(s.home_loss_streak, 0) AS home_loss_streak,
  COALESCE(s.away_win_streak, 0) AS away_win_streak,
  COALESCE(s.away_loss_streak, 0) AS away_loss_streak,
  COALESCE(s.home_momentum_score, 0) AS home_momentum_score,
  COALESCE(s.away_momentum_score, 0) AS away_momentum_score,
  
  -- Win streak quality (weighted by opponent quality)
  COALESCE(wsq.home_win_streak_quality, 0.0) AS home_win_streak_quality,
  COALESCE(wsq.away_win_streak_quality, 0.0) AS away_win_streak_quality,
  COALESCE(wsq.win_streak_quality_diff, 0.0) AS win_streak_quality_diff,
  
  -- Rest day features
  COALESCE(rd.home_rest_days, 0) AS home_rest_days,
  COALESCE(rd.home_back_to_back, FALSE) AS home_back_to_back,
  COALESCE(rd.home_back_to_back_with_travel, FALSE) AS home_back_to_back_with_travel,
  COALESCE(rd.home_btb_travel_count_5, 0) AS home_btb_travel_count_5,
  COALESCE(rd.home_btb_travel_count_10, 0) AS home_btb_travel_count_10,
  COALESCE(rd.home_travel_fatigue_score_5, 0.0) AS home_travel_fatigue_score_5,
  COALESCE(rd.away_rest_days, 0) AS away_rest_days,
  COALESCE(rd.away_back_to_back, FALSE) AS away_back_to_back,
  COALESCE(rd.away_back_to_back_with_travel, FALSE) AS away_back_to_back_with_travel,
  COALESCE(rd.away_btb_travel_count_5, 0) AS away_btb_travel_count_5,
  COALESCE(rd.away_btb_travel_count_10, 0) AS away_btb_travel_count_10,
  COALESCE(rd.away_travel_fatigue_score_5, 0.0) AS away_travel_fatigue_score_5,
  COALESCE(rd.back_to_back_travel_advantage, 0) AS back_to_back_travel_advantage,
  COALESCE(rd.rest_advantage, 0) AS rest_advantage,
  COALESCE(rd.btb_travel_count_5_diff, 0) AS btb_travel_count_5_diff,
  COALESCE(rd.btb_travel_count_10_diff, 0) AS btb_travel_count_10_diff,
  COALESCE(rd.travel_fatigue_score_5_diff, 0.0) AS travel_fatigue_score_5_diff,
  
  -- Cumulative fatigue features
  COALESCE(cf.home_games_in_last_7_days, 0) AS home_games_in_last_7_days,
  COALESCE(cf.home_games_in_last_10_days, 0) AS home_games_in_last_10_days,
  COALESCE(cf.home_games_in_last_14_days, 0) AS home_games_in_last_14_days,
  COALESCE(cf.home_avg_rest_days_last_7_days, 0.0) AS home_avg_rest_days_last_7_days,
  COALESCE(cf.home_avg_rest_days_last_10_days, 0.0) AS home_avg_rest_days_last_10_days,
  COALESCE(cf.home_game_density_7_days, 0.0) AS home_game_density_7_days,
  COALESCE(cf.home_game_density_10_days, 0.0) AS home_game_density_10_days,
  COALESCE(cf.away_games_in_last_7_days, 0) AS away_games_in_last_7_days,
  COALESCE(cf.away_games_in_last_10_days, 0) AS away_games_in_last_10_days,
  COALESCE(cf.away_games_in_last_14_days, 0) AS away_games_in_last_14_days,
  COALESCE(cf.away_avg_rest_days_last_7_days, 0.0) AS away_avg_rest_days_last_7_days,
  COALESCE(cf.away_avg_rest_days_last_10_days, 0.0) AS away_avg_rest_days_last_10_days,
  COALESCE(cf.away_game_density_7_days, 0.0) AS away_game_density_7_days,
  COALESCE(cf.away_game_density_10_days, 0.0) AS away_game_density_10_days,
  COALESCE(cf.games_in_last_7_days_diff, 0) AS games_in_last_7_days_diff,
  COALESCE(cf.games_in_last_10_days_diff, 0) AS games_in_last_10_days_diff,
  COALESCE(cf.games_in_last_14_days_diff, 0) AS games_in_last_14_days_diff,
  COALESCE(cf.avg_rest_days_last_7_days_diff, 0.0) AS avg_rest_days_last_7_days_diff,
  COALESCE(cf.avg_rest_days_last_10_days_diff, 0.0) AS avg_rest_days_last_10_days_diff,
  COALESCE(cf.game_density_7_days_diff, 0.0) AS game_density_7_days_diff,
  COALESCE(cf.game_density_10_days_diff, 0.0) AS game_density_10_days_diff,
  
  -- Weighted momentum (by opponent quality)
  COALESCE(wm.home_weighted_momentum_5, 0.0) AS home_weighted_momentum_5,
  COALESCE(wm.home_weighted_momentum_10, 0.0) AS home_weighted_momentum_10,
  COALESCE(wm.away_weighted_momentum_5, 0.0) AS away_weighted_momentum_5,
  COALESCE(wm.away_weighted_momentum_10, 0.0) AS away_weighted_momentum_10,
  COALESCE(wm.weighted_momentum_diff_5, 0.0) AS weighted_momentum_diff_5,
  COALESCE(wm.weighted_momentum_diff_10, 0.0) AS weighted_momentum_diff_10,
  
  -- Contextualized streaks (weighted by opponent quality and context)
  COALESCE(cs.home_weighted_win_streak, 0.0) AS home_weighted_win_streak,
  COALESCE(cs.home_weighted_loss_streak, 0.0) AS home_weighted_loss_streak,
  COALESCE(cs.home_win_streak_avg_opponent_quality, 0.5) AS home_win_streak_avg_opponent_quality,
  COALESCE(cs.home_loss_streak_avg_opponent_quality, 0.5) AS home_loss_streak_avg_opponent_quality,
  COALESCE(cs.away_weighted_win_streak, 0.0) AS away_weighted_win_streak,
  COALESCE(cs.away_weighted_loss_streak, 0.0) AS away_weighted_loss_streak,
  COALESCE(cs.away_win_streak_avg_opponent_quality, 0.5) AS away_win_streak_avg_opponent_quality,
  COALESCE(cs.away_loss_streak_avg_opponent_quality, 0.5) AS away_loss_streak_avg_opponent_quality,
  
  -- Exponential momentum (exponentially weighted recent games)
  COALESCE(em.home_exp_weighted_win_pct_10, 0.5) AS home_exp_weighted_win_pct_10,
  COALESCE(em.home_exp_weighted_point_diff_10, 0.0) AS home_exp_weighted_point_diff_10,
  COALESCE(em.home_exp_weighted_ppg_10, 0.0) AS home_exp_weighted_ppg_10,
  COALESCE(em.home_exp_weighted_opp_ppg_10, 0.0) AS home_exp_weighted_opp_ppg_10,
  COALESCE(em.home_exp_weighted_win_pct_5, 0.5) AS home_exp_weighted_win_pct_5,
  COALESCE(em.home_exp_weighted_point_diff_5, 0.0) AS home_exp_weighted_point_diff_5,
  COALESCE(em.away_exp_weighted_win_pct_10, 0.5) AS away_exp_weighted_win_pct_10,
  COALESCE(em.away_exp_weighted_point_diff_10, 0.0) AS away_exp_weighted_point_diff_10,
  COALESCE(em.away_exp_weighted_ppg_10, 0.0) AS away_exp_weighted_ppg_10,
  COALESCE(em.away_exp_weighted_opp_ppg_10, 0.0) AS away_exp_weighted_opp_ppg_10,
  COALESCE(em.away_exp_weighted_win_pct_5, 0.5) AS away_exp_weighted_win_pct_5,
  COALESCE(em.away_exp_weighted_point_diff_5, 0.0) AS away_exp_weighted_point_diff_5,
  COALESCE(em.exp_weighted_win_pct_diff_10, 0.0) AS exp_weighted_win_pct_diff_10,
  COALESCE(em.exp_weighted_point_diff_diff_10, 0.0) AS exp_weighted_point_diff_diff_10,
  COALESCE(em.exp_weighted_ppg_diff_10, 0.0) AS exp_weighted_ppg_diff_10,
  COALESCE(em.exp_weighted_opp_ppg_diff_10, 0.0) AS exp_weighted_opp_ppg_diff_10,
  COALESCE(em.exp_weighted_win_pct_diff_5, 0.0) AS exp_weighted_win_pct_diff_5,
  COALESCE(em.exp_weighted_point_diff_diff_5, 0.0) AS exp_weighted_point_diff_diff_5

FROM games_chunk g
LEFT JOIN streaks s ON s.game_id = g.game_id
LEFT JOIN rest_days rd ON rd.game_id = g.game_id
LEFT JOIN cumulative_fatigue cf ON cf.game_id = g.game_id
LEFT JOIN weighted_momentum wm ON wm.game_id = g.game_id
LEFT JOIN win_streak_quality wsq ON wsq.game_id = g.game_id
LEFT JOIN contextualized_streaks cs ON cs.game_id = g.game_id
LEFT JOIN exponential_momentum em ON em.game_id = g.game_id;
