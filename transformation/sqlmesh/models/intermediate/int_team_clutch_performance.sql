MODEL (
  name intermediate.int_team_clutch_perf,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate clutch performance (games decided by 5 points or less)
WITH completed_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    g.home_score,
    g.away_score,
    g.winner_team_id,
    ABS(g.home_score - g.away_score) AS point_margin
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.winner_team_id IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Calculate home team's clutch performance (games where they were involved and margin <= 5)
home_clutch_performance AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.home_team_id AS team_id,
    -- Clutch win percentage (games decided by <= 5 points)
    AVG(CASE 
      WHEN prev_g.point_margin <= 5 AND prev_g.winner_team_id = prev_g.home_team_id THEN 1.0
      WHEN prev_g.point_margin <= 5 AND prev_g.winner_team_id != prev_g.home_team_id THEN 0.0
      ELSE NULL
    END) AS home_clutch_win_pct,
    -- Clutch game count (for sample size)
    COUNT(CASE WHEN prev_g.point_margin <= 5 THEN 1 END) AS home_clutch_game_count,
    -- Average point differential in clutch games (from home team's perspective)
    AVG(CASE 
      WHEN prev_g.point_margin <= 5 THEN prev_g.home_score - prev_g.away_score
      ELSE NULL
    END) AS home_clutch_avg_point_diff
  FROM raw_dev.games curr
  LEFT JOIN completed_games prev_g ON 
    (prev_g.home_team_id = curr.home_team_id OR prev_g.away_team_id = curr.home_team_id)
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= DATE_TRUNC('year', curr.game_date::date)
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
    AND curr.game_date < @end_ds  -- Only process games up to end of chunk
  GROUP BY curr.game_id, curr.game_date, curr.home_team_id
),

-- Calculate away team's clutch performance
away_clutch_performance AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.away_team_id AS team_id,
    -- Clutch win percentage (games decided by <= 5 points)
    AVG(CASE 
      WHEN prev_g.point_margin <= 5 AND prev_g.winner_team_id = prev_g.away_team_id THEN 1.0
      WHEN prev_g.point_margin <= 5 AND prev_g.winner_team_id = prev_g.home_team_id THEN 0.0
      ELSE NULL
    END) AS away_clutch_win_pct,
    -- Clutch game count (for sample size)
    COUNT(CASE WHEN prev_g.point_margin <= 5 THEN 1 END) AS away_clutch_game_count,
    -- Average point differential in clutch games (from away team's perspective)
    AVG(CASE 
      WHEN prev_g.point_margin <= 5 THEN prev_g.away_score - prev_g.home_score
      ELSE NULL
    END) AS away_clutch_avg_point_diff
  FROM raw_dev.games curr
  LEFT JOIN completed_games prev_g ON 
    (prev_g.home_team_id = curr.away_team_id OR prev_g.away_team_id = curr.away_team_id)
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= DATE_TRUNC('year', curr.game_date::date)
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
    AND curr.game_date < @end_ds  -- Only process games up to end of chunk
  GROUP BY curr.game_id, curr.game_date, curr.away_team_id
)

SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  -- Home team clutch performance
  COALESCE(hcp.home_clutch_win_pct, 0.5) AS home_clutch_win_pct,
  COALESCE(hcp.home_clutch_game_count, 0) AS home_clutch_game_count,
  COALESCE(hcp.home_clutch_avg_point_diff, 0.0) AS home_clutch_avg_point_diff,
  -- Away team clutch performance
  COALESCE(acp.away_clutch_win_pct, 0.5) AS away_clutch_win_pct,
  COALESCE(acp.away_clutch_game_count, 0) AS away_clutch_game_count,
  COALESCE(acp.away_clutch_avg_point_diff, 0.0) AS away_clutch_avg_point_diff
FROM raw_dev.games g
LEFT JOIN home_clutch_performance hcp ON 
  hcp.game_id = g.game_id
  AND hcp.team_id = g.home_team_id
LEFT JOIN away_clutch_performance acp ON 
  acp.game_id = g.game_id
  AND acp.team_id = g.away_team_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds;
