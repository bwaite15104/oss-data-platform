MODEL (
  name intermediate.int_team_opp_specific_perf,
  description 'Team performance vs specific opponents. Short name for Postgres 63-char limit.',
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate team performance vs specific opponents in different contexts
-- This captures matchup-specific patterns beyond general H2H stats
-- Example: Team A might always struggle vs Team B at home, but win on the road

WITH games AS (
  SELECT 
    game_id,
    game_date,
    home_team_id,
    away_team_id,
    home_score,
    away_score,
    winner_team_id,
    is_completed
  FROM staging.stg_games
  WHERE is_completed
    AND game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Get all historical matchups before each game, with context
historical_matchups AS (
  SELECT 
    g.game_id,
    g.game_date,
    g.home_team_id,
    g.away_team_id,
    prev.game_id as prev_game_id,
    prev.game_date as prev_game_date,
    prev.home_team_id as prev_home_team_id,
    prev.away_team_id as prev_away_team_id,
    prev.winner_team_id as prev_winner_team_id,
    prev.home_score as prev_home_score,
    prev.away_score as prev_away_score,
    -- Determine if current home team was home in previous matchup
    CASE WHEN prev.home_team_id = g.home_team_id AND prev.away_team_id = g.away_team_id THEN true
         WHEN prev.home_team_id = g.away_team_id AND prev.away_team_id = g.home_team_id THEN false
         ELSE NULL END as current_home_was_home_in_prev,
    -- Determine if current home team won in previous matchup
    CASE WHEN prev.winner_team_id = g.home_team_id THEN true ELSE false END as current_home_won_prev,
    -- Point differential from current home team's perspective
    CASE WHEN prev.home_team_id = g.home_team_id AND prev.away_team_id = g.away_team_id THEN prev.home_score - prev.away_score
         WHEN prev.home_team_id = g.away_team_id AND prev.away_team_id = g.home_team_id THEN prev.away_score - prev.home_score
         ELSE NULL END as point_diff_from_current_home_perspective,
    ROW_NUMBER() OVER (
      PARTITION BY g.game_id 
      ORDER BY prev.game_date DESC
    ) as rn
  FROM games g
  JOIN games prev ON (
    -- Same teams, different game, before current game
    ((prev.home_team_id = g.home_team_id AND prev.away_team_id = g.away_team_id) OR
     (prev.home_team_id = g.away_team_id AND prev.away_team_id = g.home_team_id))
    AND prev.game_date < g.game_date
  )
  WHERE g.is_completed
    AND g.game_date < @end_ds  -- Only process games up to end of chunk
),

-- Home team performance vs this specific opponent (last 5 matchups, home context)
home_vs_opponent_home AS (
  SELECT 
    game_id,
    COUNT(*) as home_vs_opponent_home_games,
    SUM(CASE WHEN current_home_won_prev THEN 1 ELSE 0 END) as home_vs_opponent_home_wins,
    AVG(CASE WHEN current_home_won_prev THEN 1.0 ELSE 0.0 END) as home_vs_opponent_home_win_pct,
    AVG(point_diff_from_current_home_perspective) as home_vs_opponent_home_avg_point_diff
  FROM historical_matchups
  WHERE current_home_was_home_in_prev = true
    AND rn <= 5
  GROUP BY game_id
),

-- Home team performance vs this specific opponent (last 5 matchups, away context)
home_vs_opponent_away AS (
  SELECT 
    game_id,
    COUNT(*) as home_vs_opponent_away_games,
    SUM(CASE WHEN current_home_won_prev THEN 1 ELSE 0 END) as home_vs_opponent_away_wins,
    AVG(CASE WHEN current_home_won_prev THEN 1.0 ELSE 0.0 END) as home_vs_opponent_away_win_pct,
    AVG(point_diff_from_current_home_perspective) as home_vs_opponent_away_avg_point_diff
  FROM historical_matchups
  WHERE current_home_was_home_in_prev = false
    AND rn <= 5
  GROUP BY game_id
),

-- Away team performance vs this specific opponent (last 5 matchups, home context)
-- Note: From away team's perspective, "home context" means they were home in previous matchup
away_vs_opponent_home AS (
  SELECT 
    game_id,
    COUNT(*) as away_vs_opponent_home_games,
    SUM(CASE WHEN NOT current_home_won_prev THEN 1 ELSE 0 END) as away_vs_opponent_home_wins,
    AVG(CASE WHEN NOT current_home_won_prev THEN 1.0 ELSE 0.0 END) as away_vs_opponent_home_win_pct,
    AVG(-point_diff_from_current_home_perspective) as away_vs_opponent_home_avg_point_diff
  FROM historical_matchups
  WHERE current_home_was_home_in_prev = true
    AND rn <= 5
  GROUP BY game_id
),

-- Away team performance vs this specific opponent (last 5 matchups, away context)
away_vs_opponent_away AS (
  SELECT 
    game_id,
    COUNT(*) as away_vs_opponent_away_games,
    SUM(CASE WHEN NOT current_home_won_prev THEN 1 ELSE 0 END) as away_vs_opponent_away_wins,
    AVG(CASE WHEN NOT current_home_won_prev THEN 1.0 ELSE 0.0 END) as away_vs_opponent_away_win_pct,
    AVG(-point_diff_from_current_home_perspective) as away_vs_opponent_away_avg_point_diff
  FROM historical_matchups
  WHERE current_home_was_home_in_prev = false
    AND rn <= 5
  GROUP BY game_id
)

SELECT 
  g.game_id,
  g.game_date,
  g.home_team_id,
  g.away_team_id,
  
  -- Home team performance vs this specific opponent (home context)
  COALESCE(hvh.home_vs_opponent_home_win_pct, 0.5) as home_vs_opponent_home_win_pct,
  COALESCE(hvh.home_vs_opponent_home_games, 0) as home_vs_opponent_home_games,
  COALESCE(hvh.home_vs_opponent_home_avg_point_diff, 0.0) as home_vs_opponent_home_avg_point_diff,
  
  -- Home team performance vs this specific opponent (away context)
  COALESCE(hva.home_vs_opponent_away_win_pct, 0.5) as home_vs_opponent_away_win_pct,
  COALESCE(hva.home_vs_opponent_away_games, 0) as home_vs_opponent_away_games,
  COALESCE(hva.home_vs_opponent_away_avg_point_diff, 0.0) as home_vs_opponent_away_avg_point_diff,
  
  -- Away team performance vs this specific opponent (home context)
  COALESCE(avh.away_vs_opponent_home_win_pct, 0.5) as away_vs_opponent_home_win_pct,
  COALESCE(avh.away_vs_opponent_home_games, 0) as away_vs_opponent_home_games,
  COALESCE(avh.away_vs_opponent_home_avg_point_diff, 0.0) as away_vs_opponent_home_avg_point_diff,
  
  -- Away team performance vs this specific opponent (away context)
  COALESCE(ava.away_vs_opponent_away_win_pct, 0.5) as away_vs_opponent_away_win_pct,
  COALESCE(ava.away_vs_opponent_away_games, 0) as away_vs_opponent_away_games,
  COALESCE(ava.away_vs_opponent_away_avg_point_diff, 0.0) as away_vs_opponent_away_avg_point_diff,
  
  -- Differential features (home - away)
  COALESCE(hvh.home_vs_opponent_home_win_pct, 0.5) - COALESCE(ava.away_vs_opponent_away_win_pct, 0.5) as opponent_specific_win_pct_diff,
  COALESCE(hvh.home_vs_opponent_home_avg_point_diff, 0.0) - COALESCE(ava.away_vs_opponent_away_avg_point_diff, 0.0) as opponent_specific_avg_point_diff_diff

FROM games g
LEFT JOIN home_vs_opponent_home hvh ON hvh.game_id = g.game_id
LEFT JOIN home_vs_opponent_away hva ON hva.game_id = g.game_id
LEFT JOIN away_vs_opponent_home avh ON avh.game_id = g.game_id
LEFT JOIN away_vs_opponent_away ava ON ava.game_id = g.game_id
WHERE g.is_completed
  AND g.game_date >= @start_ds
  AND g.game_date < @end_ds