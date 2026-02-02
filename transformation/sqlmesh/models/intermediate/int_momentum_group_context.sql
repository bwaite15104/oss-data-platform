MODEL (
  name intermediate.int_momentum_group_context,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date,
    batch_size 90  -- 3 months per batch for efficient backfill
  ),
  start '1946-11-01',
  grains [game_id],
  cron '@daily',
  description 'Feature Group 4: Game context features (home/away, clutch, season timing, playoffs)'
);

-- Game context features: home/away splits, clutch performance, season timing, playoffs
-- This group aggregates 14 upstream models into a single materialized table

WITH games_chunk AS NOT MATERIALIZED (
  SELECT game_id, game_date, home_team_id, away_team_id
  FROM raw_dev.games 
  WHERE game_date >= @start_ds AND game_date < @end_ds
),

home_road_performance AS NOT MATERIALIZED (
  SELECT h.* FROM intermediate.int_team_home_road_perf h 
  INNER JOIN games_chunk gc ON gc.game_id = h.game_id
),

home_away_splits AS NOT MATERIALIZED (
  SELECT h.* FROM intermediate.int_team_home_away_opp_qual h 
  INNER JOIN games_chunk gc ON gc.game_id = h.game_id
),

home_away_rest_performance AS NOT MATERIALIZED (
  SELECT h.* FROM intermediate.int_team_home_away_rest_perf h 
  INNER JOIN games_chunk gc ON gc.game_id = h.game_id
),

clutch_performance AS NOT MATERIALIZED (
  SELECT c.* FROM intermediate.int_team_clutch_perf c 
  INNER JOIN games_chunk gc ON gc.game_id = c.game_id
),

blowout_performance AS NOT MATERIALIZED (
  SELECT b.* FROM intermediate.int_team_blowout_perf b 
  INNER JOIN games_chunk gc ON gc.game_id = b.game_id
),

comeback_closeout_performance AS NOT MATERIALIZED (
  SELECT c.* FROM intermediate.int_team_comeback_closeout_perf c 
  INNER JOIN games_chunk gc ON gc.game_id = c.game_id
),

overtime_performance AS NOT MATERIALIZED (
  SELECT o.* FROM intermediate.int_team_ot_perf o 
  INNER JOIN games_chunk gc ON gc.game_id = o.game_id
),

fourth_quarter_performance AS NOT MATERIALIZED (
  SELECT f.* FROM intermediate.int_team_4q_perf f 
  INNER JOIN games_chunk gc ON gc.game_id = f.game_id
),

game_outcome_quality AS NOT MATERIALIZED (
  SELECT g.* FROM intermediate.int_team_outcome_qual g 
  INNER JOIN games_chunk gc ON gc.game_id = g.game_id
),

upset_resistance AS NOT MATERIALIZED (
  SELECT u.* FROM intermediate.int_team_upset_resistance u 
  INNER JOIN games_chunk gc ON gc.game_id = u.game_id
),

favored_underdog_performance AS NOT MATERIALIZED (
  SELECT f.* FROM intermediate.int_team_favored_underdog_perf f 
  INNER JOIN games_chunk gc ON gc.game_id = f.game_id
),

recent_road_performance AS NOT MATERIALIZED (
  SELECT r.* FROM intermediate.int_team_road_perf r 
  INNER JOIN games_chunk gc ON gc.game_id = r.game_id
),

season_timing_performance AS NOT MATERIALIZED (
  SELECT s.* FROM intermediate.int_team_timing_perf s 
  INNER JOIN games_chunk gc ON gc.game_id = s.game_id
),

playoff_race_context AS NOT MATERIALIZED (
  SELECT p.* FROM intermediate.int_team_playoff_ctx p 
  WHERE p.game_date >= @start_ds AND p.game_date < @end_ds
)

SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  
  -- Home/road performance
  COALESCE(hrp.home_home_win_pct, 0.5) AS home_home_win_pct,
  COALESCE(hrp.away_road_win_pct, 0.5) AS away_road_win_pct,
  COALESCE(hrp.home_advantage, 0) AS home_advantage,
  
  -- Home/away splits by opponent quality
  COALESCE(has.home_win_pct_vs_strong, 0.5) AS home_win_pct_vs_strong,
  COALESCE(has.home_win_pct_vs_weak, 0.5) AS home_win_pct_vs_weak,
  COALESCE(has.away_win_pct_vs_strong, 0.5) AS away_win_pct_vs_strong,
  COALESCE(has.away_win_pct_vs_weak, 0.5) AS away_win_pct_vs_weak,
  
  -- Home/away by rest bucket
  COALESCE(harp.home_win_pct_by_rest_bucket, 0.5) AS home_win_pct_by_rest_bucket,
  COALESCE(harp.away_win_pct_by_rest_bucket, 0.5) AS away_win_pct_by_rest_bucket,
  COALESCE(harp.rest_bucket_win_pct_diff, 0.0) AS rest_bucket_win_pct_diff,
  
  -- Clutch performance (close games <= 5 points)
  COALESCE(cp.home_clutch_win_pct, 0.5) AS home_clutch_win_pct,
  COALESCE(cp.home_clutch_game_count, 0) AS home_clutch_game_count,
  COALESCE(cp.home_clutch_avg_point_diff, 0.0) AS home_clutch_avg_point_diff,
  COALESCE(cp.away_clutch_win_pct, 0.5) AS away_clutch_win_pct,
  COALESCE(cp.away_clutch_game_count, 0) AS away_clutch_game_count,
  COALESCE(cp.away_clutch_avg_point_diff, 0.0) AS away_clutch_avg_point_diff,
  
  -- Blowout performance (games decided by > 15 points)
  COALESCE(bp.home_blowout_win_pct, 0.5) AS home_blowout_win_pct,
  COALESCE(bp.home_blowout_game_count, 0) AS home_blowout_game_count,
  COALESCE(bp.home_blowout_avg_point_diff, 0.0) AS home_blowout_avg_point_diff,
  COALESCE(bp.home_blowout_frequency, 0.0) AS home_blowout_frequency,
  COALESCE(bp.away_blowout_win_pct, 0.5) AS away_blowout_win_pct,
  COALESCE(bp.away_blowout_game_count, 0) AS away_blowout_game_count,
  COALESCE(bp.away_blowout_avg_point_diff, 0.0) AS away_blowout_avg_point_diff,
  COALESCE(bp.away_blowout_frequency, 0.0) AS away_blowout_frequency,
  COALESCE(bp.home_blowout_win_pct, 0.5) - COALESCE(bp.away_blowout_win_pct, 0.5) AS blowout_win_pct_diff,
  COALESCE(bp.home_blowout_avg_point_diff, 0.0) - COALESCE(bp.away_blowout_avg_point_diff, 0.0) AS blowout_avg_point_diff_diff,
  COALESCE(bp.home_blowout_frequency, 0.0) - COALESCE(bp.away_blowout_frequency, 0.0) AS blowout_frequency_diff,
  
  -- Comeback/closeout performance
  COALESCE(ccp.home_comeback_win_pct, 0.5) AS home_comeback_win_pct,
  COALESCE(ccp.home_comeback_game_count, 0) AS home_comeback_game_count,
  COALESCE(ccp.home_comeback_avg_point_diff, 0.0) AS home_comeback_avg_point_diff,
  COALESCE(ccp.home_closeout_win_pct, 0.5) AS home_closeout_win_pct,
  COALESCE(ccp.home_closeout_game_count, 0) AS home_closeout_game_count,
  COALESCE(ccp.home_closeout_avg_point_diff, 0.0) AS home_closeout_avg_point_diff,
  COALESCE(ccp.away_comeback_win_pct, 0.5) AS away_comeback_win_pct,
  COALESCE(ccp.away_comeback_game_count, 0) AS away_comeback_game_count,
  COALESCE(ccp.away_comeback_avg_point_diff, 0.0) AS away_comeback_avg_point_diff,
  COALESCE(ccp.away_closeout_win_pct, 0.5) AS away_closeout_win_pct,
  COALESCE(ccp.away_closeout_game_count, 0) AS away_closeout_game_count,
  COALESCE(ccp.away_closeout_avg_point_diff, 0.0) AS away_closeout_avg_point_diff,
  COALESCE(ccp.home_comeback_win_pct, 0.5) - COALESCE(ccp.away_comeback_win_pct, 0.5) AS comeback_win_pct_diff,
  COALESCE(ccp.home_closeout_win_pct, 0.5) - COALESCE(ccp.away_closeout_win_pct, 0.5) AS closeout_win_pct_diff,
  COALESCE(ccp.home_comeback_avg_point_diff, 0.0) - COALESCE(ccp.away_comeback_avg_point_diff, 0.0) AS comeback_avg_point_diff_diff,
  COALESCE(ccp.home_closeout_avg_point_diff, 0.0) - COALESCE(ccp.away_closeout_avg_point_diff, 0.0) AS closeout_avg_point_diff_diff,
  
  -- Overtime performance
  COALESCE(ot.home_overtime_win_pct, 0.5) AS home_overtime_win_pct,
  COALESCE(ot.home_overtime_game_count, 0) AS home_overtime_game_count,
  COALESCE(ot.home_overtime_avg_point_diff, 0.0) AS home_overtime_avg_point_diff,
  COALESCE(ot.away_overtime_win_pct, 0.5) AS away_overtime_win_pct,
  COALESCE(ot.away_overtime_game_count, 0) AS away_overtime_game_count,
  COALESCE(ot.away_overtime_avg_point_diff, 0.0) AS away_overtime_avg_point_diff,
  
  -- Fourth quarter performance
  COALESCE(q4_home.rolling_5_q4_ppg, 0.0) AS home_rolling_5_q4_ppg,
  COALESCE(q4_home.rolling_5_q4_net_rtg, 0.0) AS home_rolling_5_q4_net_rtg,
  COALESCE(q4_home.rolling_5_q4_win_pct, 0.5) AS home_rolling_5_q4_win_pct,
  COALESCE(q4_home.rolling_10_q4_ppg, 0.0) AS home_rolling_10_q4_ppg,
  COALESCE(q4_home.rolling_10_q4_net_rtg, 0.0) AS home_rolling_10_q4_net_rtg,
  COALESCE(q4_home.rolling_10_q4_win_pct, 0.5) AS home_rolling_10_q4_win_pct,
  COALESCE(q4_home.season_q4_win_pct_when_won_q4, 0.5) AS home_season_q4_win_pct_when_won_q4,
  COALESCE(q4_home.q4_wins_count_season, 0) AS home_q4_wins_count_season,
  COALESCE(q4_away.rolling_5_q4_ppg, 0.0) AS away_rolling_5_q4_ppg,
  COALESCE(q4_away.rolling_5_q4_net_rtg, 0.0) AS away_rolling_5_q4_net_rtg,
  COALESCE(q4_away.rolling_5_q4_win_pct, 0.5) AS away_rolling_5_q4_win_pct,
  COALESCE(q4_away.rolling_10_q4_ppg, 0.0) AS away_rolling_10_q4_ppg,
  COALESCE(q4_away.rolling_10_q4_net_rtg, 0.0) AS away_rolling_10_q4_net_rtg,
  COALESCE(q4_away.rolling_10_q4_win_pct, 0.5) AS away_rolling_10_q4_win_pct,
  COALESCE(q4_away.season_q4_win_pct_when_won_q4, 0.5) AS away_season_q4_win_pct_when_won_q4,
  COALESCE(q4_away.q4_wins_count_season, 0) AS away_q4_wins_count_season,
  
  -- Game outcome quality (how teams win/lose)
  COALESCE(goq.home_avg_margin_in_wins_5, 0.0) AS home_avg_margin_in_wins_5,
  COALESCE(goq.home_avg_margin_in_losses_5, 0.0) AS home_avg_margin_in_losses_5,
  COALESCE(goq.home_close_game_win_pct_5, 0.5) AS home_close_game_win_pct_5,
  COALESCE(goq.home_blowout_game_win_pct_5, 0.5) AS home_blowout_game_win_pct_5,
  COALESCE(goq.home_avg_margin_in_wins_10, 0.0) AS home_avg_margin_in_wins_10,
  COALESCE(goq.home_avg_margin_in_losses_10, 0.0) AS home_avg_margin_in_losses_10,
  COALESCE(goq.home_close_game_win_pct_10, 0.5) AS home_close_game_win_pct_10,
  COALESCE(goq.home_blowout_game_win_pct_10, 0.5) AS home_blowout_game_win_pct_10,
  COALESCE(goq.away_avg_margin_in_wins_5, 0.0) AS away_avg_margin_in_wins_5,
  COALESCE(goq.away_avg_margin_in_losses_5, 0.0) AS away_avg_margin_in_losses_5,
  COALESCE(goq.away_close_game_win_pct_5, 0.5) AS away_close_game_win_pct_5,
  COALESCE(goq.away_blowout_game_win_pct_5, 0.5) AS away_blowout_game_win_pct_5,
  COALESCE(goq.away_avg_margin_in_wins_10, 0.0) AS away_avg_margin_in_wins_10,
  COALESCE(goq.away_avg_margin_in_losses_10, 0.0) AS away_avg_margin_in_losses_10,
  COALESCE(goq.away_close_game_win_pct_10, 0.5) AS away_close_game_win_pct_10,
  COALESCE(goq.away_blowout_game_win_pct_10, 0.5) AS away_blowout_game_win_pct_10,
  
  -- Upset resistance
  COALESCE(ur.home_favorite_win_pct, 0.5) AS home_favorite_win_pct,
  COALESCE(ur.home_favorite_game_count, 0) AS home_favorite_game_count,
  COALESCE(ur.home_underdog_win_pct, 0.3) AS home_underdog_win_pct,
  COALESCE(ur.home_underdog_game_count, 0) AS home_underdog_game_count_upset,
  COALESCE(ur.home_upset_resistance_score, 0.0) AS home_upset_resistance_score,
  COALESCE(ur.away_favorite_win_pct, 0.5) AS away_favorite_win_pct,
  COALESCE(ur.away_favorite_game_count, 0) AS away_favorite_game_count,
  COALESCE(ur.away_underdog_win_pct, 0.3) AS away_underdog_win_pct,
  COALESCE(ur.away_underdog_game_count, 0) AS away_underdog_game_count_upset,
  COALESCE(ur.away_upset_resistance_score, 0.0) AS away_upset_resistance_score,
  
  -- Favored/underdog performance
  COALESCE(fup.home_win_pct_when_favored, 0.5) AS home_win_pct_when_favored,
  COALESCE(fup.home_favored_game_count, 0) AS home_favored_game_count,
  COALESCE(fup.home_win_pct_when_underdog, 0.5) AS home_win_pct_when_underdog,
  COALESCE(fup.home_underdog_game_count, 0) AS home_underdog_game_count,
  COALESCE(fup.home_avg_point_diff_when_favored, 0.0) AS home_avg_point_diff_when_favored,
  COALESCE(fup.home_avg_point_diff_when_underdog, 0.0) AS home_avg_point_diff_when_underdog,
  COALESCE(fup.home_upset_rate, 0.0) AS home_upset_rate,
  COALESCE(fup.home_upset_game_count, 0) AS home_upset_game_count,
  COALESCE(fup.away_win_pct_when_favored, 0.5) AS away_win_pct_when_favored,
  COALESCE(fup.away_favored_game_count, 0) AS away_favored_game_count,
  COALESCE(fup.away_win_pct_when_underdog, 0.5) AS away_win_pct_when_underdog,
  COALESCE(fup.away_underdog_game_count, 0) AS away_underdog_game_count,
  COALESCE(fup.away_avg_point_diff_when_favored, 0.0) AS away_avg_point_diff_when_favored,
  COALESCE(fup.away_avg_point_diff_when_underdog, 0.0) AS away_avg_point_diff_when_underdog,
  COALESCE(fup.away_upset_rate, 0.0) AS away_upset_rate,
  COALESCE(fup.away_upset_game_count, 0) AS away_upset_game_count,
  COALESCE(fup.home_win_pct_when_favored, 0.5) - COALESCE(fup.away_win_pct_when_favored, 0.5) AS win_pct_when_favored_diff,
  COALESCE(fup.home_win_pct_when_underdog, 0.5) - COALESCE(fup.away_win_pct_when_underdog, 0.5) AS win_pct_when_underdog_diff,
  COALESCE(fup.home_avg_point_diff_when_favored, 0.0) - COALESCE(fup.away_avg_point_diff_when_favored, 0.0) AS avg_point_diff_when_favored_diff,
  COALESCE(fup.home_avg_point_diff_when_underdog, 0.0) - COALESCE(fup.away_avg_point_diff_when_underdog, 0.0) AS avg_point_diff_when_underdog_diff,
  COALESCE(fup.home_upset_rate, 0.0) - COALESCE(fup.away_upset_rate, 0.0) AS upset_rate_diff,
  
  -- Recent road performance
  COALESCE(rrp.away_recent_road_win_pct_5, 0.5) AS away_recent_road_win_pct_5,
  COALESCE(rrp.away_recent_road_game_count_5, 0) AS away_recent_road_game_count_5,
  COALESCE(rrp.away_recent_road_avg_point_diff_5, 0.0) AS away_recent_road_avg_point_diff_5,
  COALESCE(rrp.away_recent_road_win_pct_10, 0.5) AS away_recent_road_win_pct_10,
  COALESCE(rrp.away_recent_road_game_count_10, 0) AS away_recent_road_game_count_10,
  COALESCE(rrp.away_recent_road_avg_point_diff_10, 0.0) AS away_recent_road_avg_point_diff_10,
  
  -- Season timing performance
  COALESCE(stp.home_early_season_win_pct, 0.5) AS home_early_season_win_pct,
  COALESCE(stp.home_early_season_game_count, 0) AS home_early_season_game_count,
  COALESCE(stp.home_early_season_avg_point_diff, 0.0) AS home_early_season_avg_point_diff,
  COALESCE(stp.home_mid_season_win_pct, 0.5) AS home_mid_season_win_pct,
  COALESCE(stp.home_mid_season_game_count, 0) AS home_mid_season_game_count,
  COALESCE(stp.home_mid_season_avg_point_diff, 0.0) AS home_mid_season_avg_point_diff,
  COALESCE(stp.home_late_season_win_pct, 0.5) AS home_late_season_win_pct,
  COALESCE(stp.home_late_season_game_count, 0) AS home_late_season_game_count,
  COALESCE(stp.home_late_season_avg_point_diff, 0.0) AS home_late_season_avg_point_diff,
  COALESCE(stp.home_playoff_push_win_pct, 0.5) AS home_playoff_push_win_pct,
  COALESCE(stp.home_playoff_push_game_count, 0) AS home_playoff_push_game_count,
  COALESCE(stp.home_playoff_push_avg_point_diff, 0.0) AS home_playoff_push_avg_point_diff,
  COALESCE(stp.is_home_early_season, 0) AS is_home_early_season,
  COALESCE(stp.is_home_mid_season, 0) AS is_home_mid_season,
  COALESCE(stp.is_home_late_season, 0) AS is_home_late_season,
  COALESCE(stp.is_home_playoff_push, 0) AS is_home_playoff_push,
  COALESCE(stp.away_early_season_win_pct, 0.5) AS away_early_season_win_pct,
  COALESCE(stp.away_early_season_game_count, 0) AS away_early_season_game_count,
  COALESCE(stp.away_early_season_avg_point_diff, 0.0) AS away_early_season_avg_point_diff,
  COALESCE(stp.away_mid_season_win_pct, 0.5) AS away_mid_season_win_pct,
  COALESCE(stp.away_mid_season_game_count, 0) AS away_mid_season_game_count,
  COALESCE(stp.away_mid_season_avg_point_diff, 0.0) AS away_mid_season_avg_point_diff,
  COALESCE(stp.away_late_season_win_pct, 0.5) AS away_late_season_win_pct,
  COALESCE(stp.away_late_season_game_count, 0) AS away_late_season_game_count,
  COALESCE(stp.away_late_season_avg_point_diff, 0.0) AS away_late_season_avg_point_diff,
  COALESCE(stp.away_playoff_push_win_pct, 0.5) AS away_playoff_push_win_pct,
  COALESCE(stp.away_playoff_push_game_count, 0) AS away_playoff_push_game_count,
  COALESCE(stp.away_playoff_push_avg_point_diff, 0.0) AS away_playoff_push_avg_point_diff,
  COALESCE(stp.is_away_early_season, 0) AS is_away_early_season,
  COALESCE(stp.is_away_mid_season, 0) AS is_away_mid_season,
  COALESCE(stp.is_away_late_season, 0) AS is_away_late_season,
  COALESCE(stp.is_away_playoff_push, 0) AS is_away_playoff_push,
  COALESCE(stp.early_season_win_pct_diff, 0.0) AS early_season_win_pct_diff,
  COALESCE(stp.early_season_avg_point_diff_diff, 0.0) AS early_season_avg_point_diff_diff,
  COALESCE(stp.mid_season_win_pct_diff, 0.0) AS mid_season_win_pct_diff,
  COALESCE(stp.mid_season_avg_point_diff_diff, 0.0) AS mid_season_avg_point_diff_diff,
  COALESCE(stp.late_season_win_pct_diff, 0.0) AS late_season_win_pct_diff,
  COALESCE(stp.late_season_avg_point_diff_diff, 0.0) AS late_season_avg_point_diff_diff,
  COALESCE(stp.playoff_push_win_pct_diff, 0.0) AS playoff_push_win_pct_diff,
  COALESCE(stp.playoff_push_avg_point_diff_diff, 0.0) AS playoff_push_avg_point_diff_diff,
  
  -- Playoff race context
  COALESCE(prc_home.overall_rank, 30) AS home_playoff_rank,
  COALESCE(prc_home.is_in_playoff_position, 0) AS home_is_in_playoff_position,
  COALESCE(prc_home.games_back_from_cutoff, 0) AS home_games_back_from_cutoff,
  COALESCE(prc_home.win_pct_diff_from_cutoff, 0.0) AS home_win_pct_diff_from_cutoff,
  COALESCE(prc_home.is_close_to_cutoff, 0) AS home_is_close_to_cutoff,
  COALESCE(prc_home.is_fighting_for_playoff_spot, 0) AS home_is_fighting_for_playoff_spot,
  COALESCE(prc_away.overall_rank, 30) AS away_playoff_rank,
  COALESCE(prc_away.is_in_playoff_position, 0) AS away_is_in_playoff_position,
  COALESCE(prc_away.games_back_from_cutoff, 0) AS away_games_back_from_cutoff,
  COALESCE(prc_away.win_pct_diff_from_cutoff, 0.0) AS away_win_pct_diff_from_cutoff,
  COALESCE(prc_away.is_close_to_cutoff, 0) AS away_is_close_to_cutoff,
  COALESCE(prc_away.is_fighting_for_playoff_spot, 0) AS away_is_fighting_for_playoff_spot

FROM games_chunk g
LEFT JOIN home_road_performance hrp ON hrp.game_id = g.game_id
LEFT JOIN home_away_splits has ON has.game_id = g.game_id
LEFT JOIN home_away_rest_performance harp ON harp.game_id = g.game_id
LEFT JOIN clutch_performance cp ON cp.game_id = g.game_id
LEFT JOIN blowout_performance bp ON bp.game_id = g.game_id
LEFT JOIN comeback_closeout_performance ccp ON ccp.game_id = g.game_id
LEFT JOIN overtime_performance ot ON ot.game_id = g.game_id
LEFT JOIN fourth_quarter_performance q4_home ON q4_home.game_id = g.game_id AND q4_home.team_id = g.home_team_id
LEFT JOIN fourth_quarter_performance q4_away ON q4_away.game_id = g.game_id AND q4_away.team_id = g.away_team_id
LEFT JOIN game_outcome_quality goq ON goq.game_id = g.game_id
LEFT JOIN upset_resistance ur ON ur.game_id = g.game_id
LEFT JOIN favored_underdog_performance fup ON fup.game_id = g.game_id
LEFT JOIN recent_road_performance rrp ON rrp.game_id = g.game_id
LEFT JOIN season_timing_performance stp ON stp.game_id = g.game_id
LEFT JOIN playoff_race_context prc_home ON prc_home.game_date = g.game_date AND prc_home.team_id = g.home_team_id
LEFT JOIN playoff_race_context prc_away ON prc_away.game_date = g.game_date AND prc_away.team_id = g.away_team_id;
