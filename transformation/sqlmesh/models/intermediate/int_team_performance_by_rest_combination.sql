MODEL (
  name intermediate.int_team_perf_by_rest_combo,
  description 'Team performance by rest day combination. Short name for Postgres 63-char limit.',
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate team performance in specific rest day combination scenarios
-- This captures how teams perform in games with specific home/away rest day combinations
-- Focuses on most common and impactful combinations to avoid feature explosion
-- Combinations: (home_rest_days, away_rest_days) pairs

WITH completed_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    g.winner_team_id,
    g.home_score,
    g.away_score,
    CASE WHEN g.winner_team_id = g.home_team_id THEN 1 ELSE 0 END AS home_win,
    (g.home_score - g.away_score) AS home_point_diff
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Get rest days for each game
game_rest_context AS (
  SELECT 
    g.game_id,
    g.game_date,
    g.home_team_id,
    g.away_team_id,
    COALESCE(rd.home_rest_days, 0) AS home_rest_days,
    COALESCE(rd.away_rest_days, 0) AS away_rest_days,
    -- Create rest combination key (home_rest, away_rest)
    -- Focus on most common combinations: (0,1), (1,0), (1,1), (2,1), (1,2), (2,2), (3,1), (1,3), (3,2), (2,3), (3,3)
    -- Use buckets to reduce feature space: 0-1, 2, 3+
    CASE 
      WHEN COALESCE(rd.home_rest_days, 0) <= 1 THEN 1
      WHEN COALESCE(rd.home_rest_days, 0) = 2 THEN 2
      WHEN COALESCE(rd.home_rest_days, 0) >= 3 THEN 3
      ELSE 0
    END AS home_rest_bucket,
    CASE 
      WHEN COALESCE(rd.away_rest_days, 0) <= 1 THEN 1
      WHEN COALESCE(rd.away_rest_days, 0) = 2 THEN 2
      WHEN COALESCE(rd.away_rest_days, 0) >= 3 THEN 3
      ELSE 0
    END AS away_rest_bucket,
    -- Create combination key
    CONCAT(
      CASE 
        WHEN COALESCE(rd.home_rest_days, 0) <= 1 THEN '1'
        WHEN COALESCE(rd.home_rest_days, 0) = 2 THEN '2'
        WHEN COALESCE(rd.home_rest_days, 0) >= 3 THEN '3'
        ELSE '0'
      END,
      '_',
      CASE 
        WHEN COALESCE(rd.away_rest_days, 0) <= 1 THEN '1'
        WHEN COALESCE(rd.away_rest_days, 0) = 2 THEN '2'
        WHEN COALESCE(rd.away_rest_days, 0) >= 3 THEN '3'
        ELSE '0'
      END
    ) AS rest_combination_key
  FROM completed_games g
  LEFT JOIN intermediate.int_team_rest_days rd ON rd.game_id = g.game_id
),

-- Calculate home team's performance in each rest combination scenario (before this game)
home_rest_combination_performance AS (
  SELECT 
    curr.game_id,
    curr.game_date,
    curr.home_team_id AS team_id,
    curr.rest_combination_key,
    -- Win percentage in this rest combination
    AVG(CASE 
      WHEN prev_g.winner_team_id = curr.home_team_id THEN 1.0
      ELSE 0.0
    END) AS home_win_pct_by_rest_combination,
    -- Average point differential in this rest combination
    AVG(CASE 
      WHEN prev_g.home_team_id = curr.home_team_id THEN prev_g.home_point_diff
      WHEN prev_g.away_team_id = curr.home_team_id THEN -prev_g.home_point_diff
      ELSE NULL
    END) AS home_avg_point_diff_by_rest_combination,
    -- Sample size
    COUNT(*) AS home_rest_combination_count
  FROM game_rest_context curr
  LEFT JOIN completed_games prev_g ON 
    (prev_g.home_team_id = curr.home_team_id OR prev_g.away_team_id = curr.home_team_id)
    AND prev_g.game_date < curr.game_date
    AND prev_g.game_date >= DATE_TRUNC('year', curr.game_date)
  LEFT JOIN game_rest_context prev_ctx ON 
    prev_ctx.game_id = prev_g.game_id
    AND prev_ctx.rest_combination_key = curr.rest_combination_key
  WHERE curr.rest_combination_key IS NOT NULL
    AND curr.rest_combination_key != '0_0'
    AND curr.game_date < @end_ds  -- Only process games up to end of chunk
  GROUP BY curr.game_id, curr.game_date, curr.home_team_id, curr.rest_combination_key
),

-- Calculate away team's performance in each rest combination scenario (before this game)
away_rest_combination_performance AS (
  SELECT 
    curr.game_id,
    curr.game_date,
    curr.away_team_id AS team_id,
    curr.rest_combination_key,
    -- Win percentage in this rest combination (from away team's perspective)
    AVG(CASE 
      WHEN prev_g.winner_team_id = curr.away_team_id THEN 1.0
      ELSE 0.0
    END) AS away_win_pct_by_rest_combination,
    -- Average point differential in this rest combination (from away team's perspective)
    AVG(CASE 
      WHEN prev_g.away_team_id = curr.away_team_id THEN -prev_g.home_point_diff
      WHEN prev_g.home_team_id = curr.away_team_id THEN prev_g.home_point_diff
      ELSE NULL
    END) AS away_avg_point_diff_by_rest_combination,
    -- Sample size
    COUNT(*) AS away_rest_combination_count
  FROM game_rest_context curr
  LEFT JOIN completed_games prev_g ON 
    (prev_g.home_team_id = curr.away_team_id OR prev_g.away_team_id = curr.away_team_id)
    AND prev_g.game_date < curr.game_date
    AND prev_g.game_date >= DATE_TRUNC('year', curr.game_date)
  LEFT JOIN game_rest_context prev_ctx ON 
    prev_ctx.game_id = prev_g.game_id
    AND prev_ctx.rest_combination_key = curr.rest_combination_key
  WHERE curr.rest_combination_key IS NOT NULL
    AND curr.rest_combination_key != '0_0'
  GROUP BY curr.game_id, curr.game_date, curr.away_team_id, curr.rest_combination_key
)

-- Join to games and provide features
SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  grc.rest_combination_key,
  -- Home team's performance in this rest combination
  COALESCE(hrp.home_win_pct_by_rest_combination, 0.5) AS home_win_pct_by_rest_combination,
  COALESCE(hrp.home_avg_point_diff_by_rest_combination, 0.0) AS home_avg_point_diff_by_rest_combination,
  COALESCE(hrp.home_rest_combination_count, 0) AS home_rest_combination_count,
  -- Away team's performance in this rest combination
  COALESCE(arp.away_win_pct_by_rest_combination, 0.5) AS away_win_pct_by_rest_combination,
  COALESCE(arp.away_avg_point_diff_by_rest_combination, 0.0) AS away_avg_point_diff_by_rest_combination,
  COALESCE(arp.away_rest_combination_count, 0) AS away_rest_combination_count,
  -- Differential features
  COALESCE(hrp.home_win_pct_by_rest_combination, 0.5) - COALESCE(arp.away_win_pct_by_rest_combination, 0.5) AS rest_combination_win_pct_diff,
  COALESCE(hrp.home_avg_point_diff_by_rest_combination, 0.0) - COALESCE(arp.away_avg_point_diff_by_rest_combination, 0.0) AS rest_combination_point_diff_diff
FROM raw_dev.games g
LEFT JOIN game_rest_context grc ON grc.game_id = g.game_id
LEFT JOIN home_rest_combination_performance hrp ON 
  hrp.game_id = g.game_id
  AND hrp.team_id = g.home_team_id
  AND hrp.rest_combination_key = grc.rest_combination_key
LEFT JOIN away_rest_combination_performance arp ON 
  arp.game_id = g.game_id
  AND arp.team_id = g.away_team_id
  AND arp.rest_combination_key = grc.rest_combination_key
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds;
