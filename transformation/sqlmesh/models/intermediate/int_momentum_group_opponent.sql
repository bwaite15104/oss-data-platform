MODEL (
  name intermediate.int_momentum_group_opponent,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date,
    batch_size 90  -- 3 months per batch for efficient backfill
  ),
  start '1946-11-01',
  grains [game_id],
  cron '@daily',
  description 'Feature Group 3: Opponent quality and tier performance features'
);

-- Opponent quality features: schedule strength, tier performance, quality-adjusted stats
-- This group aggregates 8 upstream models into a single materialized table

WITH games_chunk AS NOT MATERIALIZED (
  SELECT game_id, game_date, home_team_id, away_team_id
  FROM raw_dev.games 
  WHERE game_date >= @start_ds AND game_date < @end_ds
),

opponent_quality AS NOT MATERIALIZED (
  SELECT o.* FROM intermediate.int_team_opponent_quality o 
  INNER JOIN games_chunk gc ON gc.game_id = o.game_id
),

opponent_tier_performance AS NOT MATERIALIZED (
  SELECT o.* FROM intermediate.int_team_opp_tier_perf o 
  INNER JOIN games_chunk gc ON gc.game_id = o.game_id
),

opponent_tier_by_home_away AS NOT MATERIALIZED (
  SELECT o.* FROM intermediate.int_team_opp_tier_perf_home_away o 
  INNER JOIN games_chunk gc ON gc.game_id = o.game_id
),

opponent_quality_by_rest AS NOT MATERIALIZED (
  SELECT o.* FROM intermediate.int_team_perf_vs_opp_qual_rest o 
  INNER JOIN games_chunk gc ON gc.game_id = o.game_id
),

similar_quality_performance AS NOT MATERIALIZED (
  SELECT s.* FROM intermediate.int_team_perf_vs_similar_qual s 
  INNER JOIN games_chunk gc ON gc.game_id = s.game_id
),

quality_adjusted_performance AS NOT MATERIALIZED (
  SELECT q.* FROM intermediate.int_team_quality_adj_perf q 
  INNER JOIN games_chunk gc ON gc.game_id = q.game_id
),

opponent_similarity_performance AS NOT MATERIALIZED (
  SELECT o.* FROM intermediate.int_team_opp_similarity_perf o 
  INNER JOIN games_chunk gc ON gc.game_id = o.game_id
),

recent_performance_weighted AS NOT MATERIALIZED (
  SELECT r.* FROM intermediate.int_team_recent_perf_wt_opp_qual r 
  INNER JOIN games_chunk gc ON gc.game_id = r.game_id
)

SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  
  -- Opponent quality (schedule strength)
  COALESCE(oq.home_recent_opp_avg_win_pct, 0.5) AS home_recent_opp_avg_win_pct,
  COALESCE(oq.away_recent_opp_avg_win_pct, 0.5) AS away_recent_opp_avg_win_pct,
  COALESCE(oq.home_performance_vs_quality, 0.5) AS home_performance_vs_quality,
  COALESCE(oq.away_performance_vs_quality, 0.5) AS away_performance_vs_quality,
  COALESCE(oq.home_sos_5_rolling, 0.5) AS home_sos_5_rolling,
  COALESCE(oq.away_sos_5_rolling, 0.5) AS away_sos_5_rolling,
  COALESCE(oq.home_sos_10_rolling, 0.5) AS home_sos_10_rolling,
  COALESCE(oq.away_sos_10_rolling, 0.5) AS away_sos_10_rolling,
  COALESCE(oq.home_sos_5_weighted, 0.5) AS home_sos_5_weighted,
  COALESCE(oq.away_sos_5_weighted, 0.5) AS away_sos_5_weighted,
  
  -- Opponent tier performance (vs top/middle/bottom teams)
  COALESCE(otp.home_win_pct_vs_opponent_tier, 0.5) AS home_win_pct_vs_opponent_tier,
  COALESCE(otp.home_avg_point_diff_vs_opponent_tier, 0.0) AS home_avg_point_diff_vs_opponent_tier,
  COALESCE(otp.home_game_count_vs_opponent_tier, 0) AS home_game_count_vs_opponent_tier,
  COALESCE(otp.away_win_pct_vs_opponent_tier, 0.5) AS away_win_pct_vs_opponent_tier,
  COALESCE(otp.away_avg_point_diff_vs_opponent_tier, 0.0) AS away_avg_point_diff_vs_opponent_tier,
  COALESCE(otp.away_game_count_vs_opponent_tier, 0) AS away_game_count_vs_opponent_tier,
  COALESCE(otp.win_pct_vs_opponent_tier_diff, 0.0) AS win_pct_vs_opponent_tier_diff,
  COALESCE(otp.avg_point_diff_vs_opponent_tier_diff, 0.0) AS avg_point_diff_vs_opponent_tier_diff,
  
  -- Opponent tier by home/away context
  COALESCE(otbha.home_win_pct_vs_opponent_tier_at_home, 0.5) AS home_win_pct_vs_opponent_tier_at_home,
  COALESCE(otbha.home_avg_point_diff_vs_opponent_tier_at_home, 0.0) AS home_avg_point_diff_vs_opponent_tier_at_home,
  COALESCE(otbha.home_game_count_vs_opponent_tier_at_home, 0) AS home_game_count_vs_opponent_tier_at_home,
  COALESCE(otbha.home_win_pct_vs_opponent_tier_on_road, 0.5) AS home_win_pct_vs_opponent_tier_on_road,
  COALESCE(otbha.home_avg_point_diff_vs_opponent_tier_on_road, 0.0) AS home_avg_point_diff_vs_opponent_tier_on_road,
  COALESCE(otbha.home_game_count_vs_opponent_tier_on_road, 0) AS home_game_count_vs_opponent_tier_on_road,
  COALESCE(otbha.away_win_pct_vs_opponent_tier_at_home, 0.5) AS away_win_pct_vs_opponent_tier_at_home,
  COALESCE(otbha.away_avg_point_diff_vs_opponent_tier_at_home, 0.0) AS away_avg_point_diff_vs_opponent_tier_at_home,
  COALESCE(otbha.away_game_count_vs_opponent_tier_at_home, 0) AS away_game_count_vs_opponent_tier_at_home,
  COALESCE(otbha.away_win_pct_vs_opponent_tier_on_road, 0.5) AS away_win_pct_vs_opponent_tier_on_road,
  COALESCE(otbha.away_avg_point_diff_vs_opponent_tier_on_road, 0.0) AS away_avg_point_diff_vs_opponent_tier_on_road,
  COALESCE(otbha.away_game_count_vs_opponent_tier_on_road, 0) AS away_game_count_vs_opponent_tier_on_road,
  COALESCE(otbha.win_pct_vs_opponent_tier_home_away_diff, 0.0) AS win_pct_vs_opponent_tier_home_away_diff,
  COALESCE(otbha.avg_point_diff_vs_opponent_tier_home_away_diff, 0.0) AS avg_point_diff_vs_opponent_tier_home_away_diff,
  
  -- Opponent quality by rest
  COALESCE(oqbr.home_win_pct_vs_strong_by_rest, 0.5) AS home_win_pct_vs_strong_by_rest,
  COALESCE(oqbr.home_games_vs_strong_by_rest, 0) AS home_games_vs_strong_by_rest,
  COALESCE(oqbr.home_win_pct_vs_weak_by_rest, 0.5) AS home_win_pct_vs_weak_by_rest,
  COALESCE(oqbr.home_games_vs_weak_by_rest, 0) AS home_games_vs_weak_by_rest,
  COALESCE(oqbr.away_win_pct_vs_strong_by_rest, 0.5) AS away_win_pct_vs_strong_by_rest,
  COALESCE(oqbr.away_games_vs_strong_by_rest, 0) AS away_games_vs_strong_by_rest,
  COALESCE(oqbr.away_win_pct_vs_weak_by_rest, 0.5) AS away_win_pct_vs_weak_by_rest,
  COALESCE(oqbr.away_games_vs_weak_by_rest, 0) AS away_games_vs_weak_by_rest,
  COALESCE(oqbr.home_win_pct_vs_strong_by_rest, 0.5) - COALESCE(oqbr.away_win_pct_vs_strong_by_rest, 0.5) AS win_pct_vs_strong_by_rest_diff,
  COALESCE(oqbr.home_win_pct_vs_weak_by_rest, 0.5) - COALESCE(oqbr.away_win_pct_vs_weak_by_rest, 0.5) AS win_pct_vs_weak_by_rest_diff,
  
  -- Similar quality performance
  COALESCE(sqp.home_win_pct_vs_similar_quality, 0.5) AS home_win_pct_vs_similar_quality,
  COALESCE(sqp.home_avg_point_diff_vs_similar_quality, 0.0) AS home_avg_point_diff_vs_similar_quality,
  COALESCE(sqp.home_similar_quality_game_count, 0) AS home_similar_quality_game_count,
  COALESCE(sqp.away_win_pct_vs_similar_quality, 0.5) AS away_win_pct_vs_similar_quality,
  COALESCE(sqp.away_avg_point_diff_vs_similar_quality, 0.0) AS away_avg_point_diff_vs_similar_quality,
  COALESCE(sqp.away_similar_quality_game_count, 0) AS away_similar_quality_game_count,
  COALESCE(sqp.is_current_game_similar_quality, 0) AS is_current_game_similar_quality,
  COALESCE(sqp.home_win_pct_vs_similar_quality, 0.5) - COALESCE(sqp.away_win_pct_vs_similar_quality, 0.5) AS win_pct_vs_similar_quality_diff,
  COALESCE(sqp.home_avg_point_diff_vs_similar_quality, 0.0) - COALESCE(sqp.away_avg_point_diff_vs_similar_quality, 0.0) AS avg_point_diff_vs_similar_quality_diff,
  
  -- Quality-adjusted performance
  COALESCE(qap.home_avg_opponent_strength_in_wins_5, 0.5) AS home_avg_opponent_strength_in_wins_5,
  COALESCE(qap.home_avg_opponent_strength_in_losses_5, 0.5) AS home_avg_opponent_strength_in_losses_5,
  COALESCE(qap.home_avg_opponent_strength_5, 0.5) AS home_avg_opponent_strength_5,
  COALESCE(qap.home_quality_adjusted_win_pct_5, 0.5) AS home_quality_adjusted_win_pct_5,
  COALESCE(qap.home_avg_point_diff_in_wins_5, 0.0) AS home_avg_point_diff_in_wins_5,
  COALESCE(qap.home_avg_point_diff_in_losses_5, 0.0) AS home_avg_point_diff_in_losses_5,
  COALESCE(qap.home_wins_count_5, 0) AS home_wins_count_5,
  COALESCE(qap.home_losses_count_5, 0) AS home_losses_count_5,
  COALESCE(qap.home_total_games_5, 0) AS home_total_games_5,
  COALESCE(qap.home_avg_opponent_strength_in_wins_10, 0.5) AS home_avg_opponent_strength_in_wins_10,
  COALESCE(qap.home_avg_opponent_strength_in_losses_10, 0.5) AS home_avg_opponent_strength_in_losses_10,
  COALESCE(qap.home_avg_opponent_strength_10, 0.5) AS home_avg_opponent_strength_10,
  COALESCE(qap.home_quality_adjusted_win_pct_10, 0.5) AS home_quality_adjusted_win_pct_10,
  COALESCE(qap.home_avg_point_diff_in_wins_10, 0.0) AS home_avg_point_diff_in_wins_10,
  COALESCE(qap.home_avg_point_diff_in_losses_10, 0.0) AS home_avg_point_diff_in_losses_10,
  COALESCE(qap.home_wins_count_10, 0) AS home_wins_count_10,
  COALESCE(qap.home_losses_count_10, 0) AS home_losses_count_10,
  COALESCE(qap.home_total_games_10, 0) AS home_total_games_10,
  COALESCE(qap.away_avg_opponent_strength_in_wins_5, 0.5) AS away_avg_opponent_strength_in_wins_5,
  COALESCE(qap.away_avg_opponent_strength_in_losses_5, 0.5) AS away_avg_opponent_strength_in_losses_5,
  COALESCE(qap.away_avg_opponent_strength_5, 0.5) AS away_avg_opponent_strength_5,
  COALESCE(qap.away_quality_adjusted_win_pct_5, 0.5) AS away_quality_adjusted_win_pct_5,
  COALESCE(qap.away_avg_point_diff_in_wins_5, 0.0) AS away_avg_point_diff_in_wins_5,
  COALESCE(qap.away_avg_point_diff_in_losses_5, 0.0) AS away_avg_point_diff_in_losses_5,
  COALESCE(qap.away_wins_count_5, 0) AS away_wins_count_5,
  COALESCE(qap.away_losses_count_5, 0) AS away_losses_count_5,
  COALESCE(qap.away_total_games_5, 0) AS away_total_games_5,
  COALESCE(qap.away_avg_opponent_strength_in_wins_10, 0.5) AS away_avg_opponent_strength_in_wins_10,
  COALESCE(qap.away_avg_opponent_strength_in_losses_10, 0.5) AS away_avg_opponent_strength_in_losses_10,
  COALESCE(qap.away_avg_opponent_strength_10, 0.5) AS away_avg_opponent_strength_10,
  COALESCE(qap.away_quality_adjusted_win_pct_10, 0.5) AS away_quality_adjusted_win_pct_10,
  COALESCE(qap.away_avg_point_diff_in_wins_10, 0.0) AS away_avg_point_diff_in_wins_10,
  COALESCE(qap.away_avg_point_diff_in_losses_10, 0.0) AS away_avg_point_diff_in_losses_10,
  COALESCE(qap.away_wins_count_10, 0) AS away_wins_count_10,
  COALESCE(qap.away_losses_count_10, 0) AS away_losses_count_10,
  COALESCE(qap.away_total_games_10, 0) AS away_total_games_10,
  COALESCE(qap.quality_adjusted_win_pct_diff_5, 0.0) AS quality_adjusted_win_pct_diff_5,
  COALESCE(qap.quality_adjusted_win_pct_diff_10, 0.0) AS quality_adjusted_win_pct_diff_10,
  COALESCE(qap.avg_opponent_strength_diff_5, 0.0) AS avg_opponent_strength_diff_5,
  COALESCE(qap.avg_opponent_strength_diff_10, 0.0) AS avg_opponent_strength_diff_10,
  
  -- Opponent similarity performance
  COALESCE(osp.home_win_pct_vs_similar_opponents, 0.5) AS home_win_pct_vs_similar_opponents,
  COALESCE(osp.home_similar_opponent_game_count, 0) AS home_similar_opponent_game_count,
  COALESCE(osp.home_avg_point_diff_vs_similar_opponents, 0.0) AS home_avg_point_diff_vs_similar_opponents,
  COALESCE(osp.away_win_pct_vs_similar_opponents, 0.5) AS away_win_pct_vs_similar_opponents,
  COALESCE(osp.away_similar_opponent_game_count, 0) AS away_similar_opponent_game_count,
  COALESCE(osp.away_avg_point_diff_vs_similar_opponents, 0.0) AS away_avg_point_diff_vs_similar_opponents,
  COALESCE(osp.current_game_similarity_score, 0.5) AS current_game_similarity_score,
  COALESCE(osp.home_win_pct_vs_similar_opponents, 0.5) - COALESCE(osp.away_win_pct_vs_similar_opponents, 0.5) AS win_pct_vs_similar_opponents_diff,
  COALESCE(osp.home_avg_point_diff_vs_similar_opponents, 0.0) - COALESCE(osp.away_avg_point_diff_vs_similar_opponents, 0.0) AS avg_point_diff_vs_similar_opponents_diff,
  
  -- Recent performance weighted by opponent quality
  COALESCE(rpw.home_weighted_win_pct_5, 0.5) AS home_weighted_win_pct_5,
  COALESCE(rpw.home_weighted_win_pct_10, 0.5) AS home_weighted_win_pct_10,
  COALESCE(rpw.home_weighted_point_diff_5, 0.0) AS home_weighted_point_diff_5,
  COALESCE(rpw.home_weighted_point_diff_10, 0.0) AS home_weighted_point_diff_10,
  COALESCE(rpw.home_weighted_net_rtg_5, 0.0) AS home_weighted_net_rtg_5,
  COALESCE(rpw.home_weighted_net_rtg_10, 0.0) AS home_weighted_net_rtg_10,
  COALESCE(rpw.away_weighted_win_pct_5, 0.5) AS away_weighted_win_pct_5,
  COALESCE(rpw.away_weighted_win_pct_10, 0.5) AS away_weighted_win_pct_10,
  COALESCE(rpw.away_weighted_point_diff_5, 0.0) AS away_weighted_point_diff_5,
  COALESCE(rpw.away_weighted_point_diff_10, 0.0) AS away_weighted_point_diff_10,
  COALESCE(rpw.away_weighted_net_rtg_5, 0.0) AS away_weighted_net_rtg_5,
  COALESCE(rpw.away_weighted_net_rtg_10, 0.0) AS away_weighted_net_rtg_10,
  COALESCE(rpw.weighted_win_pct_diff_5, 0.0) AS weighted_win_pct_diff_5,
  COALESCE(rpw.weighted_win_pct_diff_10, 0.0) AS weighted_win_pct_diff_10,
  COALESCE(rpw.weighted_point_diff_diff_5, 0.0) AS weighted_point_diff_diff_5,
  COALESCE(rpw.weighted_point_diff_diff_10, 0.0) AS weighted_point_diff_diff_10,
  COALESCE(rpw.weighted_net_rtg_diff_5, 0.0) AS weighted_net_rtg_diff_5,
  COALESCE(rpw.weighted_net_rtg_diff_10, 0.0) AS weighted_net_rtg_diff_10

FROM games_chunk g
LEFT JOIN opponent_quality oq ON oq.game_id = g.game_id
LEFT JOIN opponent_tier_performance otp ON otp.game_id = g.game_id
LEFT JOIN opponent_tier_by_home_away otbha ON otbha.game_id = g.game_id
LEFT JOIN opponent_quality_by_rest oqbr ON oqbr.game_id = g.game_id
LEFT JOIN similar_quality_performance sqp ON sqp.game_id = g.game_id
LEFT JOIN quality_adjusted_performance qap ON qap.game_id = g.game_id
LEFT JOIN opponent_similarity_performance osp ON osp.game_id = g.game_id
LEFT JOIN recent_performance_weighted rpw ON rpw.game_id = g.game_id;
