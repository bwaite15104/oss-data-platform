MODEL (
  name intermediate.int_momentum_group_trends,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date,
    batch_size 90  -- 3 months per batch for efficient backfill
  ),
  start '1946-11-01',
  grains [game_id],
  cron '@daily',
  description 'Feature Group 5: Trends, acceleration, consistency, and matchup features'
);

-- Trends and matchup features: form trends, acceleration, consistency, matchup style
-- This group aggregates 14 upstream models into a single materialized table

WITH games_chunk AS NOT MATERIALIZED (
  SELECT game_id, game_date, home_team_id, away_team_id
  FROM raw_dev.games 
  WHERE game_date >= @start_ds AND game_date < @end_ds
),

form_divergence AS NOT MATERIALIZED (
  SELECT f.* FROM intermediate.int_team_form_divergence f 
  INNER JOIN games_chunk gc ON gc.game_id = f.game_id
),

performance_trends AS NOT MATERIALIZED (
  SELECT p.* FROM intermediate.int_team_perf_trends p 
  INNER JOIN games_chunk gc ON gc.game_id = p.game_id
),

recent_momentum AS NOT MATERIALIZED (
  SELECT r.* FROM intermediate.int_team_recent_momentum r 
  INNER JOIN games_chunk gc ON gc.game_id = r.game_id
),

momentum_acceleration AS NOT MATERIALIZED (
  SELECT m.* FROM intermediate.int_team_mom_accel m 
  INNER JOIN games_chunk gc ON gc.game_id = m.game_id
),

performance_acceleration AS NOT MATERIALIZED (
  SELECT p.* FROM intermediate.int_team_perf_accel p 
  INNER JOIN games_chunk gc ON gc.game_id = p.game_id
),

recent_form_trend AS NOT MATERIALIZED (
  SELECT r.* FROM intermediate.int_team_recent_form_trend r 
  INNER JOIN games_chunk gc ON gc.game_id = r.game_id
),

performance_consistency AS NOT MATERIALIZED (
  SELECT p.* FROM intermediate.int_team_perf_consistency p 
  INNER JOIN games_chunk gc ON gc.game_id = p.game_id
),

matchup_style_performance AS NOT MATERIALIZED (
  SELECT m.* FROM intermediate.int_team_matchup_perf m 
  INNER JOIN games_chunk gc ON gc.game_id = m.game_id
),

similar_momentum_performance AS NOT MATERIALIZED (
  SELECT s.* FROM intermediate.int_team_perf_vs_similar_mom s 
  INNER JOIN games_chunk gc ON gc.game_id = s.game_id
),

similar_rest_performance AS NOT MATERIALIZED (
  SELECT s.* FROM intermediate.int_team_perf_vs_rest_ctx s 
  INNER JOIN games_chunk gc ON gc.game_id = s.game_id
),

rest_advantage_scenarios AS NOT MATERIALIZED (
  SELECT r.* FROM intermediate.int_team_perf_rest_adv r 
  INNER JOIN games_chunk gc ON gc.game_id = r.game_id
),

rest_combination_performance AS NOT MATERIALIZED (
  SELECT r.* FROM intermediate.int_team_perf_by_rest_combo r 
  INNER JOIN games_chunk gc ON gc.game_id = r.game_id
),

matchup_compatibility AS NOT MATERIALIZED (
  SELECT m.* FROM intermediate.int_team_matchup_compat m 
  INNER JOIN games_chunk gc ON gc.game_id = m.game_id
),

pace_context_performance AS NOT MATERIALIZED (
  SELECT p.* FROM intermediate.int_team_perf_by_pace_ctx p 
  INNER JOIN games_chunk gc ON gc.game_id = p.game_id
)

SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  
  -- Form divergence (recent vs season average)
  COALESCE(fd_home.form_divergence_win_pct_5, 0.0) AS home_win_pct_divergence_5,
  COALESCE(fd_home.form_divergence_win_pct_10, 0.0) AS home_win_pct_divergence_10,
  COALESCE(fd_away.form_divergence_win_pct_5, 0.0) AS away_win_pct_divergence_5,
  COALESCE(fd_away.form_divergence_win_pct_10, 0.0) AS away_win_pct_divergence_10,
  COALESCE(fd_home.form_divergence_net_rtg_5, 0.0) AS home_net_rtg_divergence_5,
  COALESCE(fd_home.form_divergence_net_rtg_10, 0.0) AS home_net_rtg_divergence_10,
  COALESCE(fd_away.form_divergence_net_rtg_5, 0.0) AS away_net_rtg_divergence_5,
  COALESCE(fd_away.form_divergence_net_rtg_10, 0.0) AS away_net_rtg_divergence_10,
  COALESCE(fd_home.form_divergence_ppg_5, 0.0) AS home_ppg_divergence_5,
  COALESCE(fd_home.form_divergence_ppg_10, 0.0) AS home_ppg_divergence_10,
  COALESCE(fd_away.form_divergence_ppg_5, 0.0) AS away_ppg_divergence_5,
  COALESCE(fd_away.form_divergence_ppg_10, 0.0) AS away_ppg_divergence_10,
  COALESCE(fd_home.form_divergence_opp_ppg_5, 0.0) AS home_opp_ppg_divergence_5,
  COALESCE(fd_home.form_divergence_opp_ppg_10, 0.0) AS home_opp_ppg_divergence_10,
  COALESCE(fd_away.form_divergence_opp_ppg_5, 0.0) AS away_opp_ppg_divergence_5,
  COALESCE(fd_away.form_divergence_opp_ppg_10, 0.0) AS away_opp_ppg_divergence_10,
  COALESCE(fd_home.form_divergence_magnitude_win_pct_5, 0.0) AS home_form_divergence_magnitude_win_pct_5,
  COALESCE(fd_home.form_divergence_magnitude_win_pct_10, 0.0) AS home_form_divergence_magnitude_win_pct_10,
  COALESCE(fd_away.form_divergence_magnitude_win_pct_5, 0.0) AS away_form_divergence_magnitude_win_pct_5,
  COALESCE(fd_away.form_divergence_magnitude_win_pct_10, 0.0) AS away_form_divergence_magnitude_win_pct_10,
  
  -- Performance trends (rate of change)
  COALESCE(pt_home.win_pct_trend, 0.0) AS home_win_pct_trend,
  COALESCE(pt_home.net_rtg_trend, 0.0) AS home_net_rtg_trend,
  COALESCE(pt_home.ppg_trend, 0.0) AS home_ppg_trend,
  COALESCE(pt_home.off_rtg_trend, 0.0) AS home_off_rtg_trend,
  COALESCE(pt_home.efg_pct_trend, 0.0) AS home_efg_pct_trend,
  COALESCE(pt_home.ts_pct_trend, 0.0) AS home_ts_pct_trend,
  COALESCE(pt_home.opp_ppg_trend, 0.0) AS home_opp_ppg_trend,
  COALESCE(pt_home.def_rtg_trend, 0.0) AS home_def_rtg_trend,
  COALESCE(pt_away.win_pct_trend, 0.0) AS away_win_pct_trend,
  COALESCE(pt_away.net_rtg_trend, 0.0) AS away_net_rtg_trend,
  COALESCE(pt_away.ppg_trend, 0.0) AS away_ppg_trend,
  COALESCE(pt_away.off_rtg_trend, 0.0) AS away_off_rtg_trend,
  COALESCE(pt_away.efg_pct_trend, 0.0) AS away_efg_pct_trend,
  COALESCE(pt_away.ts_pct_trend, 0.0) AS away_ts_pct_trend,
  COALESCE(pt_away.opp_ppg_trend, 0.0) AS away_opp_ppg_trend,
  COALESCE(pt_away.def_rtg_trend, 0.0) AS away_def_rtg_trend,
  
  -- Recent momentum (3-game vs 5-game)
  COALESCE(rm_home.recent_win_pct_momentum, 0.0) AS home_recent_win_pct_momentum,
  COALESCE(rm_home.recent_net_rtg_momentum, 0.0) AS home_recent_net_rtg_momentum,
  COALESCE(rm_home.recent_ppg_momentum, 0.0) AS home_recent_ppg_momentum,
  COALESCE(rm_home.recent_off_rtg_momentum, 0.0) AS home_recent_off_rtg_momentum,
  COALESCE(rm_home.recent_efg_pct_momentum, 0.0) AS home_recent_efg_pct_momentum,
  COALESCE(rm_home.recent_ts_pct_momentum, 0.0) AS home_recent_ts_pct_momentum,
  COALESCE(rm_home.recent_opp_ppg_momentum, 0.0) AS home_recent_opp_ppg_momentum,
  COALESCE(rm_home.recent_def_rtg_momentum, 0.0) AS home_recent_def_rtg_momentum,
  COALESCE(rm_away.recent_win_pct_momentum, 0.0) AS away_recent_win_pct_momentum,
  COALESCE(rm_away.recent_net_rtg_momentum, 0.0) AS away_recent_net_rtg_momentum,
  COALESCE(rm_away.recent_ppg_momentum, 0.0) AS away_recent_ppg_momentum,
  COALESCE(rm_away.recent_off_rtg_momentum, 0.0) AS away_recent_off_rtg_momentum,
  COALESCE(rm_away.recent_efg_pct_momentum, 0.0) AS away_recent_efg_pct_momentum,
  COALESCE(rm_away.recent_ts_pct_momentum, 0.0) AS away_recent_ts_pct_momentum,
  COALESCE(rm_away.recent_opp_ppg_momentum, 0.0) AS away_recent_opp_ppg_momentum,
  COALESCE(rm_away.recent_def_rtg_momentum, 0.0) AS away_recent_def_rtg_momentum,
  
  -- Recent form trend (comparing recent 3 vs recent 5)
  COALESCE(rft.home_form_trend_win_pct, 0.0) AS home_form_trend_win_pct,
  COALESCE(rft.home_form_trend_point_diff, 0.0) AS home_form_trend_point_diff,
  COALESCE(rft.away_form_trend_win_pct, 0.0) AS away_form_trend_win_pct,
  COALESCE(rft.away_form_trend_point_diff, 0.0) AS away_form_trend_point_diff,
  COALESCE(rft.form_trend_win_pct_diff, 0.0) AS form_trend_win_pct_diff,
  COALESCE(rft.form_trend_point_diff_diff, 0.0) AS form_trend_point_diff_diff,
  
  -- Momentum acceleration
  COALESCE(ma.home_momentum_acceleration, 0.0) AS home_momentum_acceleration,
  COALESCE(ma.away_momentum_acceleration, 0.0) AS away_momentum_acceleration,
  COALESCE(ma.momentum_acceleration_diff, 0.0) AS momentum_acceleration_diff,
  
  -- Performance acceleration
  COALESCE(pa_home.win_pct_acceleration, 0.0) AS home_win_pct_acceleration,
  COALESCE(pa_home.point_diff_acceleration, 0.0) AS home_point_diff_acceleration,
  COALESCE(pa_home.ppg_acceleration, 0.0) AS home_ppg_acceleration,
  COALESCE(pa_home.defensive_acceleration, 0.0) AS home_defensive_acceleration,
  COALESCE(pa_home.net_rtg_acceleration, 0.0) AS home_net_rtg_acceleration,
  COALESCE(pa_away.win_pct_acceleration, 0.0) AS away_win_pct_acceleration,
  COALESCE(pa_away.point_diff_acceleration, 0.0) AS away_point_diff_acceleration,
  COALESCE(pa_away.ppg_acceleration, 0.0) AS away_ppg_acceleration,
  COALESCE(pa_away.defensive_acceleration, 0.0) AS away_defensive_acceleration,
  COALESCE(pa_away.net_rtg_acceleration, 0.0) AS away_net_rtg_acceleration,
  COALESCE(pa_home.win_pct_acceleration, 0.0) - COALESCE(pa_away.win_pct_acceleration, 0.0) AS win_pct_acceleration_diff,
  COALESCE(pa_home.point_diff_acceleration, 0.0) - COALESCE(pa_away.point_diff_acceleration, 0.0) AS point_diff_acceleration_diff,
  COALESCE(pa_home.ppg_acceleration, 0.0) - COALESCE(pa_away.ppg_acceleration, 0.0) AS ppg_acceleration_diff,
  COALESCE(pa_home.defensive_acceleration, 0.0) - COALESCE(pa_away.defensive_acceleration, 0.0) AS defensive_acceleration_diff,
  COALESCE(pa_home.net_rtg_acceleration, 0.0) - COALESCE(pa_away.net_rtg_acceleration, 0.0) AS net_rtg_acceleration_diff,
  
  -- Performance consistency
  COALESCE(pc.home_win_consistency_10, 0.0) AS home_win_consistency_10,
  COALESCE(pc.home_point_diff_consistency_10, 0.0) AS home_point_diff_consistency_10,
  COALESCE(pc.home_points_consistency_10, 0.0) AS home_points_consistency_10,
  COALESCE(pc.home_opp_points_consistency_10, 0.0) AS home_opp_points_consistency_10,
  COALESCE(pc.home_points_cv_10, 0.0) AS home_points_cv_10,
  COALESCE(pc.home_opp_points_cv_10, 0.0) AS home_opp_points_cv_10,
  COALESCE(pc.home_win_consistency_5, 0.0) AS home_win_consistency_5,
  COALESCE(pc.home_point_diff_consistency_5, 0.0) AS home_point_diff_consistency_5,
  COALESCE(pc.home_points_consistency_5, 0.0) AS home_points_consistency_5,
  COALESCE(pc.home_opp_points_consistency_5, 0.0) AS home_opp_points_consistency_5,
  COALESCE(pc.home_points_cv_5, 0.0) AS home_points_cv_5,
  COALESCE(pc.home_opp_points_cv_5, 0.0) AS home_opp_points_cv_5,
  COALESCE(pc.away_win_consistency_10, 0.0) AS away_win_consistency_10,
  COALESCE(pc.away_point_diff_consistency_10, 0.0) AS away_point_diff_consistency_10,
  COALESCE(pc.away_points_consistency_10, 0.0) AS away_points_consistency_10,
  COALESCE(pc.away_opp_points_consistency_10, 0.0) AS away_opp_points_consistency_10,
  COALESCE(pc.away_points_cv_10, 0.0) AS away_points_cv_10,
  COALESCE(pc.away_opp_points_cv_10, 0.0) AS away_opp_points_cv_10,
  COALESCE(pc.away_win_consistency_5, 0.0) AS away_win_consistency_5,
  COALESCE(pc.away_point_diff_consistency_5, 0.0) AS away_point_diff_consistency_5,
  COALESCE(pc.away_points_consistency_5, 0.0) AS away_points_consistency_5,
  COALESCE(pc.away_opp_points_consistency_5, 0.0) AS away_opp_points_consistency_5,
  COALESCE(pc.away_points_cv_5, 0.0) AS away_points_cv_5,
  COALESCE(pc.away_opp_points_cv_5, 0.0) AS away_opp_points_cv_5,
  COALESCE(pc.win_consistency_10_diff, 0.0) AS win_consistency_10_diff,
  COALESCE(pc.point_diff_consistency_10_diff, 0.0) AS point_diff_consistency_10_diff,
  COALESCE(pc.points_consistency_10_diff, 0.0) AS points_consistency_10_diff,
  COALESCE(pc.opp_points_consistency_10_diff, 0.0) AS opp_points_consistency_10_diff,
  COALESCE(pc.points_cv_10_diff, 0.0) AS points_cv_10_diff,
  COALESCE(pc.opp_points_cv_10_diff, 0.0) AS opp_points_cv_10_diff,
  COALESCE(pc.win_consistency_5_diff, 0.0) AS win_consistency_5_diff,
  COALESCE(pc.point_diff_consistency_5_diff, 0.0) AS point_diff_consistency_5_diff,
  COALESCE(pc.points_consistency_5_diff, 0.0) AS points_consistency_5_diff,
  COALESCE(pc.opp_points_consistency_5_diff, 0.0) AS opp_points_consistency_5_diff,
  COALESCE(pc.points_cv_5_diff, 0.0) AS points_cv_5_diff,
  COALESCE(pc.opp_points_cv_5_diff, 0.0) AS opp_points_cv_5_diff,
  
  -- Matchup style performance
  COALESCE(msp.home_win_pct_vs_fast_paced, 0.5) AS home_win_pct_vs_fast_paced,
  COALESCE(msp.home_win_pct_vs_slow_paced, 0.5) AS home_win_pct_vs_slow_paced,
  COALESCE(msp.home_win_pct_vs_high_scoring, 0.5) AS home_win_pct_vs_high_scoring,
  COALESCE(msp.home_win_pct_vs_defensive, 0.5) AS home_win_pct_vs_defensive,
  COALESCE(msp.away_win_pct_vs_fast_paced, 0.5) AS away_win_pct_vs_fast_paced,
  COALESCE(msp.away_win_pct_vs_slow_paced, 0.5) AS away_win_pct_vs_slow_paced,
  COALESCE(msp.away_win_pct_vs_high_scoring, 0.5) AS away_win_pct_vs_high_scoring,
  COALESCE(msp.away_win_pct_vs_defensive, 0.5) AS away_win_pct_vs_defensive,
  
  -- Similar momentum performance
  COALESCE(smp.home_win_pct_vs_similar_momentum, 0.5) AS home_win_pct_vs_similar_momentum,
  COALESCE(smp.home_avg_point_diff_vs_similar_momentum, 0.0) AS home_avg_point_diff_vs_similar_momentum,
  COALESCE(smp.home_similar_momentum_game_count, 0) AS home_similar_momentum_game_count,
  COALESCE(smp.away_win_pct_vs_similar_momentum, 0.5) AS away_win_pct_vs_similar_momentum,
  COALESCE(smp.away_avg_point_diff_vs_similar_momentum, 0.0) AS away_avg_point_diff_vs_similar_momentum,
  COALESCE(smp.away_similar_momentum_game_count, 0) AS away_similar_momentum_game_count,
  COALESCE(smp.is_current_game_similar_momentum, 0) AS is_current_game_similar_momentum,
  COALESCE(smp.home_win_pct_vs_similar_momentum, 0.5) - COALESCE(smp.away_win_pct_vs_similar_momentum, 0.5) AS win_pct_vs_similar_momentum_diff,
  COALESCE(smp.home_avg_point_diff_vs_similar_momentum, 0.0) - COALESCE(smp.away_avg_point_diff_vs_similar_momentum, 0.0) AS avg_point_diff_vs_similar_momentum_diff,
  
  -- Similar rest performance
  COALESCE(srp.home_win_pct_vs_similar_rest, 0.5) AS home_win_pct_vs_similar_rest,
  COALESCE(srp.home_avg_point_diff_vs_similar_rest, 0.0) AS home_avg_point_diff_vs_similar_rest,
  COALESCE(srp.home_similar_rest_game_count, 0) AS home_similar_rest_game_count,
  COALESCE(srp.away_win_pct_vs_similar_rest, 0.5) AS away_win_pct_vs_similar_rest,
  COALESCE(srp.away_avg_point_diff_vs_similar_rest, 0.0) AS away_avg_point_diff_vs_similar_rest,
  COALESCE(srp.away_similar_rest_game_count, 0) AS away_similar_rest_game_count,
  COALESCE(srp.is_current_game_similar_rest_context, FALSE) AS is_current_game_similar_rest_context,
  COALESCE(srp.win_pct_vs_similar_rest_diff, 0.0) AS win_pct_vs_similar_rest_diff,
  COALESCE(srp.avg_point_diff_vs_similar_rest_diff, 0.0) AS avg_point_diff_vs_similar_rest_diff,
  
  -- Rest advantage scenarios
  COALESCE(ras.home_win_pct_well_rested_vs_tired, 0.5) AS home_win_pct_well_rested_vs_tired,
  COALESCE(ras.home_avg_point_diff_well_rested_vs_tired, 0.0) AS home_avg_point_diff_well_rested_vs_tired,
  COALESCE(ras.home_well_rested_vs_tired_count, 0) AS home_well_rested_vs_tired_count,
  COALESCE(ras.home_win_pct_well_rested_vs_moderate, 0.5) AS home_win_pct_well_rested_vs_moderate,
  COALESCE(ras.home_avg_point_diff_well_rested_vs_moderate, 0.0) AS home_avg_point_diff_well_rested_vs_moderate,
  COALESCE(ras.home_well_rested_vs_moderate_count, 0) AS home_well_rested_vs_moderate_count,
  COALESCE(ras.home_win_pct_moderate_vs_tired, 0.5) AS home_win_pct_moderate_vs_tired,
  COALESCE(ras.home_avg_point_diff_moderate_vs_tired, 0.0) AS home_avg_point_diff_moderate_vs_tired,
  COALESCE(ras.home_moderate_vs_tired_count, 0) AS home_moderate_vs_tired_count,
  COALESCE(ras.home_win_pct_with_rest_advantage, 0.5) AS home_win_pct_with_rest_advantage,
  COALESCE(ras.home_avg_point_diff_with_rest_advantage, 0.0) AS home_avg_point_diff_with_rest_advantage,
  COALESCE(ras.home_with_rest_advantage_count, 0) AS home_with_rest_advantage_count,
  COALESCE(ras.away_win_pct_well_rested_vs_tired, 0.5) AS away_win_pct_well_rested_vs_tired,
  COALESCE(ras.away_avg_point_diff_well_rested_vs_tired, 0.0) AS away_avg_point_diff_well_rested_vs_tired,
  COALESCE(ras.away_well_rested_vs_tired_count, 0) AS away_well_rested_vs_tired_count,
  COALESCE(ras.away_win_pct_well_rested_vs_moderate, 0.5) AS away_win_pct_well_rested_vs_moderate,
  COALESCE(ras.away_avg_point_diff_well_rested_vs_moderate, 0.0) AS away_avg_point_diff_well_rested_vs_moderate,
  COALESCE(ras.away_well_rested_vs_moderate_count, 0) AS away_well_rested_vs_moderate_count,
  COALESCE(ras.away_win_pct_moderate_vs_tired, 0.5) AS away_win_pct_moderate_vs_tired,
  COALESCE(ras.away_avg_point_diff_moderate_vs_tired, 0.0) AS away_avg_point_diff_moderate_vs_tired,
  COALESCE(ras.away_moderate_vs_tired_count, 0) AS away_moderate_vs_tired_count,
  COALESCE(ras.away_win_pct_with_rest_advantage, 0.5) AS away_win_pct_with_rest_advantage,
  COALESCE(ras.away_avg_point_diff_with_rest_advantage, 0.0) AS away_avg_point_diff_with_rest_advantage,
  COALESCE(ras.away_with_rest_advantage_count, 0) AS away_with_rest_advantage_count,
  COALESCE(ras.is_well_rested_vs_tired, 0) AS is_well_rested_vs_tired,
  COALESCE(ras.is_well_rested_vs_moderate, 0) AS is_well_rested_vs_moderate,
  COALESCE(ras.is_moderate_vs_tired, 0) AS is_moderate_vs_tired,
  COALESCE(ras.is_rest_advantage_scenario, 0) AS is_rest_advantage_scenario,
  COALESCE(ras.win_pct_well_rested_vs_tired_diff, 0.0) AS win_pct_well_rested_vs_tired_diff,
  COALESCE(ras.win_pct_well_rested_vs_moderate_diff, 0.0) AS win_pct_well_rested_vs_moderate_diff,
  COALESCE(ras.win_pct_moderate_vs_tired_diff, 0.0) AS win_pct_moderate_vs_tired_diff,
  COALESCE(ras.win_pct_with_rest_advantage_diff, 0.0) AS win_pct_with_rest_advantage_diff,
  
  -- Rest combination performance
  COALESCE(rcp.home_win_pct_by_rest_combination, 0.5) AS home_win_pct_by_rest_combination,
  COALESCE(rcp.home_avg_point_diff_by_rest_combination, 0.0) AS home_avg_point_diff_by_rest_combination,
  COALESCE(rcp.home_rest_combination_count, 0) AS home_rest_combination_count,
  COALESCE(rcp.away_win_pct_by_rest_combination, 0.5) AS away_win_pct_by_rest_combination,
  COALESCE(rcp.away_avg_point_diff_by_rest_combination, 0.0) AS away_avg_point_diff_by_rest_combination,
  COALESCE(rcp.away_rest_combination_count, 0) AS away_rest_combination_count,
  COALESCE(rcp.rest_combination_win_pct_diff, 0.0) AS rest_combination_win_pct_diff,
  COALESCE(rcp.rest_combination_point_diff_diff, 0.0) AS rest_combination_point_diff_diff,
  
  -- Matchup compatibility
  COALESCE(mc.pace_difference, 0.0) AS pace_difference,
  COALESCE(mc.pace_compatibility_score, 0.5) AS pace_compatibility_score,
  COALESCE(mc.home_off_vs_away_def_compatibility, 0.5) AS home_off_vs_away_def_compatibility,
  COALESCE(mc.away_off_vs_home_def_compatibility, 0.5) AS away_off_vs_home_def_compatibility,
  COALESCE(mc.style_matchup_advantage, 0.0) AS style_matchup_advantage,
  COALESCE(mc.net_rtg_difference, 0.0) AS net_rtg_difference,
  COALESCE(mc.net_rtg_compatibility_score, 0.5) AS net_rtg_compatibility_score,
  
  -- Pace context performance
  COALESCE(pcp.home_fast_pace_win_pct, 0.5) AS home_fast_pace_win_pct,
  COALESCE(pcp.home_fast_pace_game_count, 0) AS home_fast_pace_game_count,
  COALESCE(pcp.home_fast_pace_avg_point_diff, 0.0) AS home_fast_pace_avg_point_diff,
  COALESCE(pcp.home_slow_pace_win_pct, 0.5) AS home_slow_pace_win_pct,
  COALESCE(pcp.home_slow_pace_game_count, 0) AS home_slow_pace_game_count,
  COALESCE(pcp.home_slow_pace_avg_point_diff, 0.0) AS home_slow_pace_avg_point_diff,
  COALESCE(pcp.away_fast_pace_win_pct, 0.5) AS away_fast_pace_win_pct,
  COALESCE(pcp.away_fast_pace_game_count, 0) AS away_fast_pace_game_count,
  COALESCE(pcp.away_fast_pace_avg_point_diff, 0.0) AS away_fast_pace_avg_point_diff,
  COALESCE(pcp.away_slow_pace_win_pct, 0.5) AS away_slow_pace_win_pct,
  COALESCE(pcp.away_slow_pace_game_count, 0) AS away_slow_pace_game_count,
  COALESCE(pcp.away_slow_pace_avg_point_diff, 0.0) AS away_slow_pace_avg_point_diff,
  COALESCE(pcp.home_fast_pace_win_pct, 0.5) - COALESCE(pcp.away_fast_pace_win_pct, 0.5) AS fast_pace_win_pct_diff,
  COALESCE(pcp.home_fast_pace_avg_point_diff, 0.0) - COALESCE(pcp.away_fast_pace_avg_point_diff, 0.0) AS fast_pace_avg_point_diff_diff,
  COALESCE(pcp.home_slow_pace_win_pct, 0.5) - COALESCE(pcp.away_slow_pace_win_pct, 0.5) AS slow_pace_win_pct_diff,
  COALESCE(pcp.home_slow_pace_avg_point_diff, 0.0) - COALESCE(pcp.away_slow_pace_avg_point_diff, 0.0) AS slow_pace_avg_point_diff_diff

FROM games_chunk g
LEFT JOIN form_divergence fd_home ON fd_home.game_id = g.game_id AND fd_home.team_id = g.home_team_id
LEFT JOIN form_divergence fd_away ON fd_away.game_id = g.game_id AND fd_away.team_id = g.away_team_id
LEFT JOIN performance_trends pt_home ON pt_home.game_id = g.game_id AND pt_home.team_id = g.home_team_id
LEFT JOIN performance_trends pt_away ON pt_away.game_id = g.game_id AND pt_away.team_id = g.away_team_id
LEFT JOIN recent_momentum rm_home ON rm_home.game_id = g.game_id AND rm_home.team_id = g.home_team_id
LEFT JOIN recent_momentum rm_away ON rm_away.game_id = g.game_id AND rm_away.team_id = g.away_team_id
LEFT JOIN recent_form_trend rft ON rft.game_id = g.game_id
LEFT JOIN momentum_acceleration ma ON ma.game_id = g.game_id
LEFT JOIN performance_acceleration pa_home ON pa_home.game_id = g.game_id AND pa_home.team_id = g.home_team_id
LEFT JOIN performance_acceleration pa_away ON pa_away.game_id = g.game_id AND pa_away.team_id = g.away_team_id
LEFT JOIN performance_consistency pc ON pc.game_id = g.game_id
LEFT JOIN matchup_style_performance msp ON msp.game_id = g.game_id
LEFT JOIN similar_momentum_performance smp ON smp.game_id = g.game_id
LEFT JOIN similar_rest_performance srp ON srp.game_id = g.game_id
LEFT JOIN rest_advantage_scenarios ras ON ras.game_id = g.game_id
LEFT JOIN rest_combination_performance rcp ON rcp.game_id = g.game_id
LEFT JOIN matchup_compatibility mc ON mc.game_id = g.game_id
LEFT JOIN pace_context_performance pcp ON pcp.game_id = g.game_id;
