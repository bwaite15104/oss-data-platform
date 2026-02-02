MODEL (
  name intermediate.int_team_home_away_rest_perf,
  description 'Home/away win pct by rest day buckets. Short name for Postgres 63-char limit.',
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate historical win percentages for each team at home/away with different rest day buckets
-- Rest day buckets: 0-1 (back-to-back), 2, 3+ (well-rested)
WITH completed_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    g.winner_team_id
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Get rest days for each team in each game
team_rest_context AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    rd.home_rest_days,
    rd.away_rest_days,
    -- Rest day buckets
    CASE 
      WHEN rd.home_rest_days <= 1 THEN 'btb'
      WHEN rd.home_rest_days = 2 THEN '2days'
      WHEN rd.home_rest_days >= 3 THEN '3plus'
      ELSE 'unknown'
    END AS home_rest_bucket,
    CASE 
      WHEN rd.away_rest_days <= 1 THEN 'btb'
      WHEN rd.away_rest_days = 2 THEN '2days'
      WHEN rd.away_rest_days >= 3 THEN '3plus'
      ELSE 'unknown'
    END AS away_rest_bucket
  FROM raw_dev.games g
  LEFT JOIN intermediate.int_team_rest_days rd ON rd.game_id = g.game_id
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Calculate home team's win % at home with same rest bucket (before this game)
home_rest_performance AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.home_team_id AS team_id,
    curr.home_rest_bucket,
    AVG(CASE 
      WHEN prev_g.winner_team_id = curr.home_team_id THEN 1.0
      ELSE 0.0
    END) AS home_win_pct_by_rest_bucket
  FROM team_rest_context curr
  LEFT JOIN completed_games prev_g ON 
    prev_g.home_team_id = curr.home_team_id
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= DATE_TRUNC('year', curr.game_date::date)
  LEFT JOIN team_rest_context prev_ctx ON 
    prev_ctx.game_id = prev_g.game_id
    AND prev_ctx.home_rest_bucket = curr.home_rest_bucket
  WHERE curr.home_rest_bucket IS NOT NULL
    AND curr.home_rest_bucket != 'unknown'
  GROUP BY curr.game_id, curr.game_date, curr.home_team_id, curr.home_rest_bucket
),

-- Calculate away team's win % on road with same rest bucket (before this game)
away_rest_performance AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.away_team_id AS team_id,
    curr.away_rest_bucket,
    AVG(CASE 
      WHEN prev_g.winner_team_id = curr.away_team_id THEN 1.0
      ELSE 0.0
    END) AS away_win_pct_by_rest_bucket
  FROM team_rest_context curr
  LEFT JOIN completed_games prev_g ON 
    prev_g.away_team_id = curr.away_team_id
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= DATE_TRUNC('year', curr.game_date::date)
  LEFT JOIN team_rest_context prev_ctx ON 
    prev_ctx.game_id = prev_g.game_id
    AND prev_ctx.away_rest_bucket = curr.away_rest_bucket
  WHERE curr.away_rest_bucket IS NOT NULL
    AND curr.away_rest_bucket != 'unknown'
  GROUP BY curr.game_id, curr.game_date, curr.away_team_id, curr.away_rest_bucket
)

-- Join to games and provide features
SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  -- Home team's win % at home with current rest bucket (before this game)
  COALESCE(hrp.home_win_pct_by_rest_bucket, 0.5) AS home_win_pct_by_rest_bucket,
  -- Away team's win % on road with current rest bucket (before this game)
  COALESCE(arp.away_win_pct_by_rest_bucket, 0.5) AS away_win_pct_by_rest_bucket,
  -- Differential: home rest performance - away rest performance
  COALESCE(hrp.home_win_pct_by_rest_bucket, 0.5) - COALESCE(arp.away_win_pct_by_rest_bucket, 0.5) AS rest_bucket_win_pct_diff
FROM raw_dev.games g
LEFT JOIN home_rest_performance hrp ON 
  hrp.game_id = g.game_id
  AND hrp.team_id = g.home_team_id
LEFT JOIN away_rest_performance arp ON 
  arp.game_id = g.game_id
  AND arp.team_id = g.away_team_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds;
