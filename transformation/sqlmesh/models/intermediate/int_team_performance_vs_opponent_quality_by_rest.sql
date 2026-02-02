MODEL (
  name intermediate.int_team_perf_vs_opp_qual_rest,
  description 'Performance vs opponent quality by rest. Short name for Postgres 63-char limit.',
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate team performance vs opponent quality (strong vs weak) by rest days
-- This combines opponent quality context with rest context to capture how teams perform
-- vs strong/weak opponents when they have different rest levels
-- Strong opponent = rolling 10-game win_pct > 0.5
-- Weak opponent = rolling 10-game win_pct <= 0.5
-- Rest buckets: back-to-back (0-1 days), 2 days, 3+ days (well-rested)
-- Uses int_team_roll_lkp for opponent quality (no LATERAL/correlated subqueries).
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
team_rest AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    COALESCE(rd.home_rest_days, 0) AS home_rest_days,
    COALESCE(rd.away_rest_days, 0) AS away_rest_days,
    CASE 
      WHEN COALESCE(rd.home_rest_days, 0) <= 1 THEN 'back_to_back'
      WHEN COALESCE(rd.home_rest_days, 0) = 2 THEN '2_days'
      ELSE 'well_rested'
    END AS home_rest_bucket,
    CASE 
      WHEN COALESCE(rd.away_rest_days, 0) <= 1 THEN 'back_to_back'
      WHEN COALESCE(rd.away_rest_days, 0) = 2 THEN '2_days'
      ELSE 'well_rested'
    END AS away_rest_bucket
  FROM raw_dev.games g
  LEFT JOIN intermediate.int_team_rest_days rd ON rd.game_id = g.game_id
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Home team's performance vs strong opponents by rest bucket (before this game)
home_vs_strong_by_rest AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.home_team_id AS team_id,
    curr_tr.home_rest_bucket,
    AVG(CASE 
      WHEN prev_g.winner_team_id = curr.home_team_id THEN 1.0
      ELSE 0.0
    END) AS home_win_pct_vs_strong_by_rest,
    COUNT(*) AS home_games_vs_strong_by_rest
  FROM raw_dev.games curr
  INNER JOIN team_rest curr_tr ON curr_tr.game_id = curr.game_id
  LEFT JOIN completed_games prev_g ON 
    prev_g.home_team_id = curr.home_team_id
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= DATE_TRUNC('year', curr.game_date::date)
  LEFT JOIN team_rest prev_tr ON prev_tr.game_id = prev_g.game_id
  LEFT JOIN intermediate.int_team_roll_lkp prev_opp_quality
    ON prev_opp_quality.game_id = prev_g.game_id
    AND prev_opp_quality.team_id = prev_g.away_team_id
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
    AND prev_opp_quality.rolling_10_win_pct > 0.5  -- Strong opponent
    AND prev_tr.home_rest_bucket = curr_tr.home_rest_bucket  -- Same rest bucket
    AND curr.game_date >= @start_ds
    AND curr.game_date < @end_ds
  GROUP BY curr.game_id, curr.game_date, curr.home_team_id, curr_tr.home_rest_bucket
),

-- Home team's performance vs weak opponents by rest bucket (before this game)
home_vs_weak_by_rest AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.home_team_id AS team_id,
    curr_tr.home_rest_bucket,
    AVG(CASE 
      WHEN prev_g.winner_team_id = curr.home_team_id THEN 1.0
      ELSE 0.0
    END) AS home_win_pct_vs_weak_by_rest,
    COUNT(*) AS home_games_vs_weak_by_rest
  FROM raw_dev.games curr
  INNER JOIN team_rest curr_tr ON curr_tr.game_id = curr.game_id
  LEFT JOIN completed_games prev_g ON 
    prev_g.home_team_id = curr.home_team_id
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= DATE_TRUNC('year', curr.game_date::date)
  LEFT JOIN team_rest prev_tr ON prev_tr.game_id = prev_g.game_id
  LEFT JOIN intermediate.int_team_roll_lkp prev_opp_quality
    ON prev_opp_quality.game_id = prev_g.game_id
    AND prev_opp_quality.team_id = prev_g.away_team_id
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
    AND (prev_opp_quality.rolling_10_win_pct <= 0.5 OR prev_opp_quality.rolling_10_win_pct IS NULL)  -- Weak opponent
    AND prev_tr.home_rest_bucket = curr_tr.home_rest_bucket  -- Same rest bucket
    AND curr.game_date >= @start_ds
    AND curr.game_date < @end_ds
  GROUP BY curr.game_id, curr.game_date, curr.home_team_id, curr_tr.home_rest_bucket
),

-- Away team's performance vs strong opponents by rest bucket (before this game)
away_vs_strong_by_rest AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.away_team_id AS team_id,
    curr_tr.away_rest_bucket,
    AVG(CASE 
      WHEN prev_g.winner_team_id = curr.away_team_id THEN 1.0
      ELSE 0.0
    END) AS away_win_pct_vs_strong_by_rest,
    COUNT(*) AS away_games_vs_strong_by_rest
  FROM raw_dev.games curr
  INNER JOIN team_rest curr_tr ON curr_tr.game_id = curr.game_id
  LEFT JOIN completed_games prev_g ON 
    prev_g.away_team_id = curr.away_team_id
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= DATE_TRUNC('year', curr.game_date::date)
  LEFT JOIN team_rest prev_tr ON prev_tr.game_id = prev_g.game_id
  LEFT JOIN intermediate.int_team_roll_lkp prev_opp_quality
    ON prev_opp_quality.game_id = prev_g.game_id
    AND prev_opp_quality.team_id = prev_g.home_team_id
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
    AND prev_opp_quality.rolling_10_win_pct > 0.5  -- Strong opponent
    AND prev_tr.away_rest_bucket = curr_tr.away_rest_bucket  -- Same rest bucket
    AND curr.game_date >= @start_ds
    AND curr.game_date < @end_ds
  GROUP BY curr.game_id, curr.game_date, curr.away_team_id, curr_tr.away_rest_bucket
),

-- Away team's performance vs weak opponents by rest bucket (before this game)
away_vs_weak_by_rest AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.away_team_id AS team_id,
    curr_tr.away_rest_bucket,
    AVG(CASE 
      WHEN prev_g.winner_team_id = curr.away_team_id THEN 1.0
      ELSE 0.0
    END) AS away_win_pct_vs_weak_by_rest,
    COUNT(*) AS away_games_vs_weak_by_rest
  FROM raw_dev.games curr
  INNER JOIN team_rest curr_tr ON curr_tr.game_id = curr.game_id
  LEFT JOIN completed_games prev_g ON 
    prev_g.away_team_id = curr.away_team_id
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= DATE_TRUNC('year', curr.game_date::date)
  LEFT JOIN team_rest prev_tr ON prev_tr.game_id = prev_g.game_id
  LEFT JOIN intermediate.int_team_roll_lkp prev_opp_quality
    ON prev_opp_quality.game_id = prev_g.game_id
    AND prev_opp_quality.team_id = prev_g.home_team_id
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
    AND (prev_opp_quality.rolling_10_win_pct <= 0.5 OR prev_opp_quality.rolling_10_win_pct IS NULL)  -- Weak opponent
    AND prev_tr.away_rest_bucket = curr_tr.away_rest_bucket  -- Same rest bucket
    AND curr.game_date >= @start_ds
    AND curr.game_date < @end_ds
  GROUP BY curr.game_id, curr.game_date, curr.away_team_id, curr_tr.away_rest_bucket
)

SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  tr.home_rest_bucket,
  tr.away_rest_bucket,
  -- Home team's win % vs strong opponents by rest bucket (default to 0.5 if insufficient data)
  COALESCE(home_strong.home_win_pct_vs_strong_by_rest, 0.5) AS home_win_pct_vs_strong_by_rest,
  COALESCE(home_strong.home_games_vs_strong_by_rest, 0) AS home_games_vs_strong_by_rest,
  -- Home team's win % vs weak opponents by rest bucket
  COALESCE(home_weak.home_win_pct_vs_weak_by_rest, 0.5) AS home_win_pct_vs_weak_by_rest,
  COALESCE(home_weak.home_games_vs_weak_by_rest, 0) AS home_games_vs_weak_by_rest,
  -- Away team's win % vs strong opponents by rest bucket
  COALESCE(away_strong.away_win_pct_vs_strong_by_rest, 0.5) AS away_win_pct_vs_strong_by_rest,
  COALESCE(away_strong.away_games_vs_strong_by_rest, 0) AS away_games_vs_strong_by_rest,
  -- Away team's win % vs weak opponents by rest bucket
  COALESCE(away_weak.away_win_pct_vs_weak_by_rest, 0.5) AS away_win_pct_vs_weak_by_rest,
  COALESCE(away_weak.away_games_vs_weak_by_rest, 0) AS away_games_vs_weak_by_rest
FROM raw_dev.games g
INNER JOIN team_rest tr ON tr.game_id = g.game_id
LEFT JOIN home_vs_strong_by_rest home_strong ON 
  home_strong.game_id = g.game_id
  AND home_strong.team_id = g.home_team_id
  AND home_strong.home_rest_bucket = tr.home_rest_bucket
LEFT JOIN home_vs_weak_by_rest home_weak ON 
  home_weak.game_id = g.game_id
  AND home_weak.team_id = g.home_team_id
  AND home_weak.home_rest_bucket = tr.home_rest_bucket
LEFT JOIN away_vs_strong_by_rest away_strong ON 
  away_strong.game_id = g.game_id
  AND away_strong.team_id = g.away_team_id
  AND away_strong.away_rest_bucket = tr.away_rest_bucket
LEFT JOIN away_vs_weak_by_rest away_weak ON 
  away_weak.game_id = g.game_id
  AND away_weak.team_id = g.away_team_id
  AND away_weak.away_rest_bucket = tr.away_rest_bucket
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds;
