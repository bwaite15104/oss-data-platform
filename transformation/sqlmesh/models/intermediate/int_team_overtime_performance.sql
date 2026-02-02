MODEL (
  name intermediate.int_team_ot_perf,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate overtime performance (games that went to overtime)
-- Use staging.stg_games for is_overtime and is_completed (raw_dev.games does not have these columns)
WITH completed_overtime_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    g.home_score,
    g.away_score,
    g.winner_team_id,
    g.is_overtime
  FROM staging.stg_games g
  WHERE g.is_completed
    AND g.is_overtime
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Calculate home team's overtime performance (games where they were involved and went to overtime)
home_overtime_performance AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.home_team_id AS team_id,
    -- Overtime win percentage (games that went to overtime)
    AVG(CASE 
      WHEN prev_g.is_overtime AND prev_g.winner_team_id = prev_g.home_team_id THEN 1.0
      WHEN prev_g.is_overtime AND prev_g.winner_team_id != prev_g.home_team_id THEN 0.0
      ELSE NULL
    END) AS home_overtime_win_pct,
    -- Overtime game count (for sample size)
    COUNT(CASE WHEN prev_g.is_overtime THEN 1 END) AS home_overtime_game_count,
    -- Average point differential in overtime games (from home team's perspective)
    AVG(CASE 
      WHEN prev_g.is_overtime THEN prev_g.home_score - prev_g.away_score
      ELSE NULL
    END) AS home_overtime_avg_point_diff
  FROM raw_dev.games curr
  LEFT JOIN completed_overtime_games prev_g ON 
    (prev_g.home_team_id = curr.home_team_id OR prev_g.away_team_id = curr.home_team_id)
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= DATE_TRUNC('year', curr.game_date::date)
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
    AND curr.game_date < @end_ds  -- Only process games up to end of chunk
  GROUP BY curr.game_id, curr.game_date, curr.home_team_id
),

-- Calculate away team's overtime performance
away_overtime_performance AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.away_team_id AS team_id,
    -- Overtime win percentage (games that went to overtime)
    AVG(CASE 
      WHEN prev_g.is_overtime AND prev_g.winner_team_id = prev_g.away_team_id THEN 1.0
      WHEN prev_g.is_overtime AND prev_g.winner_team_id != prev_g.home_team_id THEN 0.0
      ELSE NULL
    END) AS away_overtime_win_pct,
    -- Overtime game count (for sample size)
    COUNT(CASE WHEN prev_g.is_overtime THEN 1 END) AS away_overtime_game_count,
    -- Average point differential in overtime games (from away team's perspective)
    AVG(CASE 
      WHEN prev_g.is_overtime THEN prev_g.away_score - prev_g.home_score
      ELSE NULL
    END) AS away_overtime_avg_point_diff
  FROM raw_dev.games curr
  LEFT JOIN completed_overtime_games prev_g ON 
    (prev_g.home_team_id = curr.away_team_id OR prev_g.away_team_id = curr.away_team_id)
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= DATE_TRUNC('year', curr.game_date::date)
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
  GROUP BY curr.game_id, curr.game_date, curr.away_team_id
)

SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  -- Home team overtime performance
  COALESCE(hop.home_overtime_win_pct, 0.5) AS home_overtime_win_pct,
  COALESCE(hop.home_overtime_game_count, 0) AS home_overtime_game_count,
  COALESCE(hop.home_overtime_avg_point_diff, 0.0) AS home_overtime_avg_point_diff,
  -- Away team overtime performance
  COALESCE(aop.away_overtime_win_pct, 0.5) AS away_overtime_win_pct,
  COALESCE(aop.away_overtime_game_count, 0) AS away_overtime_game_count,
  COALESCE(aop.away_overtime_avg_point_diff, 0.0) AS away_overtime_avg_point_diff
FROM raw_dev.games g
LEFT JOIN home_overtime_performance hop ON 
  hop.game_id = g.game_id
  AND hop.team_id = g.home_team_id
LEFT JOIN away_overtime_performance aop ON 
  aop.game_id = g.game_id
  AND aop.team_id = g.away_team_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds
  AND g.home_score IS NOT NULL
  AND g.away_score IS NOT NULL;
