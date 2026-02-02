MODEL (
  name intermediate.int_team_opp_tier_perf,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
  description 'Team performance against opponent quality tiers (top 10, middle 10, bottom 10) based on rolling win percentage. Captures how teams perform against different opponent quality levels.'
);

-- Calculate team performance against opponent quality tiers
-- Tiers: top 10 (best teams), middle 10, bottom 10 (worst teams) based on rolling 10-game win_pct
WITH completed_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    g.winner_team_id,
    g.home_score,
    g.away_score
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Get rolling win_pct for each team at each game date to determine opponent tier
team_rolling_win_pct AS (
  SELECT 
    rs.team_id,
    rs.game_date::date AS game_date,
    rs.rolling_10_win_pct
  FROM intermediate.int_team_rolling_stats rs
  WHERE rs.rolling_10_win_pct IS NOT NULL
),

-- Determine opponent tier for each game based on opponent's rolling win_pct
-- Tiers: top 10 (>= 0.6), middle 10 (0.4-0.6), bottom 10 (< 0.4)
-- Using percentiles would be better, but using fixed thresholds for simplicity
opponent_tiers AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    -- Home team's opponent (away team) tier
    CASE 
      WHEN away_win_pct.rolling_10_win_pct >= 0.6 THEN 'top'
      WHEN away_win_pct.rolling_10_win_pct >= 0.4 THEN 'middle'
      WHEN away_win_pct.rolling_10_win_pct < 0.4 THEN 'bottom'
      ELSE 'unknown'
    END AS home_opponent_tier,
    -- Away team's opponent (home team) tier
    CASE 
      WHEN home_win_pct.rolling_10_win_pct >= 0.6 THEN 'top'
      WHEN home_win_pct.rolling_10_win_pct >= 0.4 THEN 'middle'
      WHEN home_win_pct.rolling_10_win_pct < 0.4 THEN 'bottom'
      ELSE 'unknown'
    END AS away_opponent_tier
  FROM raw_dev.games g
  LEFT JOIN team_rolling_win_pct home_win_pct ON 
    home_win_pct.team_id = g.home_team_id
    AND home_win_pct.game_date = g.game_date::date
  LEFT JOIN team_rolling_win_pct away_win_pct ON 
    away_win_pct.team_id = g.away_team_id
    AND away_win_pct.game_date = g.game_date::date
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
),

-- Calculate home team's performance vs each opponent tier (at home, before this game)
home_vs_tier_performance AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.home_team_id AS team_id,
    curr.home_opponent_tier,
    -- Win percentage vs this tier
    AVG(CASE 
      WHEN prev_g.winner_team_id = curr.home_team_id THEN 1.0
      ELSE 0.0
    END) AS home_win_pct_vs_tier,
    -- Average point differential vs this tier
    AVG(prev_g.home_score - prev_g.away_score) AS home_avg_point_diff_vs_tier,
    -- Game count vs this tier
    COUNT(*) AS home_game_count_vs_tier
  FROM opponent_tiers curr
  LEFT JOIN completed_games prev_g ON 
    prev_g.home_team_id = curr.home_team_id
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= curr.game_date::date - INTERVAL '90 days'  -- Last 90 days
  LEFT JOIN opponent_tiers prev_tier ON 
    prev_tier.game_id = prev_g.game_id
    AND prev_tier.home_opponent_tier = curr.home_opponent_tier
  WHERE curr.home_opponent_tier IS NOT NULL
    AND curr.home_opponent_tier != 'unknown'
    AND curr.game_date < @end_ds  -- Only process games up to end of chunk
  GROUP BY curr.game_id, curr.game_date, curr.home_team_id, curr.home_opponent_tier
),

-- Calculate away team's performance vs each opponent tier (on road, before this game)
away_vs_tier_performance AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.away_team_id AS team_id,
    curr.away_opponent_tier,
    -- Win percentage vs this tier
    AVG(CASE 
      WHEN prev_g.winner_team_id = curr.away_team_id THEN 1.0
      ELSE 0.0
    END) AS away_win_pct_vs_tier,
    -- Average point differential vs this tier (from away team's perspective)
    AVG(prev_g.away_score - prev_g.home_score) AS away_avg_point_diff_vs_tier,
    -- Game count vs this tier
    COUNT(*) AS away_game_count_vs_tier
  FROM opponent_tiers curr
  LEFT JOIN completed_games prev_g ON 
    prev_g.away_team_id = curr.away_team_id
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= curr.game_date::date - INTERVAL '90 days'  -- Last 90 days
  LEFT JOIN opponent_tiers prev_tier ON 
    prev_tier.game_id = prev_g.game_id
    AND prev_tier.away_opponent_tier = curr.away_opponent_tier
  WHERE curr.away_opponent_tier IS NOT NULL
    AND curr.away_opponent_tier != 'unknown'
    AND curr.game_date < @end_ds  -- Only process games up to end of chunk
  GROUP BY curr.game_id, curr.game_date, curr.away_team_id, curr.away_opponent_tier
)

-- Join to games and provide features
SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  -- Home team's performance vs current opponent tier
  COALESCE(htp.home_win_pct_vs_tier, 0.5) AS home_win_pct_vs_opponent_tier,
  COALESCE(htp.home_avg_point_diff_vs_tier, 0.0) AS home_avg_point_diff_vs_opponent_tier,
  COALESCE(htp.home_game_count_vs_tier, 0) AS home_game_count_vs_opponent_tier,
  -- Away team's performance vs current opponent tier
  COALESCE(atp.away_win_pct_vs_tier, 0.5) AS away_win_pct_vs_opponent_tier,
  COALESCE(atp.away_avg_point_diff_vs_tier, 0.0) AS away_avg_point_diff_vs_opponent_tier,
  COALESCE(atp.away_game_count_vs_tier, 0) AS away_game_count_vs_opponent_tier,
  -- Differential features
  COALESCE(htp.home_win_pct_vs_tier, 0.5) - COALESCE(atp.away_win_pct_vs_tier, 0.5) AS win_pct_vs_opponent_tier_diff,
  COALESCE(htp.home_avg_point_diff_vs_tier, 0.0) - COALESCE(atp.away_avg_point_diff_vs_tier, 0.0) AS avg_point_diff_vs_opponent_tier_diff
FROM raw_dev.games g
LEFT JOIN opponent_tiers ot ON ot.game_id = g.game_id
LEFT JOIN home_vs_tier_performance htp ON 
  htp.game_id = g.game_id
  AND htp.team_id = g.home_team_id
LEFT JOIN away_vs_tier_performance atp ON 
  atp.game_id = g.game_id
  AND atp.team_id = g.away_team_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds;
