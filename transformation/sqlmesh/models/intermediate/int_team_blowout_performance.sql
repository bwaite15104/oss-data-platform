MODEL (
  name intermediate.int_team_blowout_perf,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate blowout performance (games decided by > 15 points)
-- Blowout performance captures how teams perform in lopsided games (different from clutch performance)
-- Teams that can build big leads or prevent blowouts may have different characteristics than teams that excel in close games
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

-- Calculate home team's blowout performance (games where they were involved and margin > 15)
home_blowout_performance AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.home_team_id AS team_id,
    -- Blowout win percentage (games decided by > 15 points)
    AVG(CASE 
      WHEN prev_g.point_margin > 15 AND prev_g.winner_team_id = prev_g.home_team_id THEN 1.0
      WHEN prev_g.point_margin > 15 AND prev_g.winner_team_id != prev_g.home_team_id THEN 0.0
      ELSE NULL
    END) AS home_blowout_win_pct,
    -- Blowout game count (for sample size)
    COUNT(CASE WHEN prev_g.point_margin > 15 THEN 1 END) AS home_blowout_game_count,
    -- Average point differential in blowout games (from home team's perspective)
    AVG(CASE 
      WHEN prev_g.point_margin > 15 THEN prev_g.home_score - prev_g.away_score
      ELSE NULL
    END) AS home_blowout_avg_point_diff,
    -- Blowout frequency (percentage of games that are blowouts)
    AVG(CASE WHEN prev_g.point_margin > 15 THEN 1.0 ELSE 0.0 END) AS home_blowout_frequency
  FROM raw_dev.games curr
  LEFT JOIN completed_games prev_g ON 
    (prev_g.home_team_id = curr.home_team_id OR prev_g.away_team_id = curr.home_team_id)
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= curr.game_date::date - INTERVAL '90 days'  -- Last 90 days for more recent signal
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
    AND curr.game_date < @end_ds  -- Only process games up to end of chunk
  GROUP BY curr.game_id, curr.game_date, curr.home_team_id
),

-- Calculate away team's blowout performance
away_blowout_performance AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.away_team_id AS team_id,
    -- Blowout win percentage (games decided by > 15 points)
    AVG(CASE 
      WHEN prev_g.point_margin > 15 AND prev_g.winner_team_id = prev_g.away_team_id THEN 1.0
      WHEN prev_g.point_margin > 15 AND prev_g.winner_team_id = prev_g.home_team_id THEN 0.0
      ELSE NULL
    END) AS away_blowout_win_pct,
    -- Blowout game count (for sample size)
    COUNT(CASE WHEN prev_g.point_margin > 15 THEN 1 END) AS away_blowout_game_count,
    -- Average point differential in blowout games (from away team's perspective)
    AVG(CASE 
      WHEN prev_g.point_margin > 15 THEN prev_g.away_score - prev_g.home_score
      ELSE NULL
    END) AS away_blowout_avg_point_diff,
    -- Blowout frequency (percentage of games that are blowouts)
    AVG(CASE WHEN prev_g.point_margin > 15 THEN 1.0 ELSE 0.0 END) AS away_blowout_frequency
  FROM raw_dev.games curr
  LEFT JOIN completed_games prev_g ON 
    (prev_g.home_team_id = curr.away_team_id OR prev_g.away_team_id = curr.away_team_id)
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= curr.game_date::date - INTERVAL '90 days'  -- Last 90 days for more recent signal
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
  -- Home team blowout performance
  COALESCE(hbp.home_blowout_win_pct, 0.5) AS home_blowout_win_pct,
  COALESCE(hbp.home_blowout_game_count, 0) AS home_blowout_game_count,
  COALESCE(hbp.home_blowout_avg_point_diff, 0.0) AS home_blowout_avg_point_diff,
  COALESCE(hbp.home_blowout_frequency, 0.0) AS home_blowout_frequency,
  -- Away team blowout performance
  COALESCE(abp.away_blowout_win_pct, 0.5) AS away_blowout_win_pct,
  COALESCE(abp.away_blowout_game_count, 0) AS away_blowout_game_count,
  COALESCE(abp.away_blowout_avg_point_diff, 0.0) AS away_blowout_avg_point_diff,
  COALESCE(abp.away_blowout_frequency, 0.0) AS away_blowout_frequency
FROM raw_dev.games g
LEFT JOIN home_blowout_performance hbp ON 
  hbp.game_id = g.game_id
  AND hbp.team_id = g.home_team_id
LEFT JOIN away_blowout_performance abp ON 
  abp.game_id = g.game_id
  AND abp.team_id = g.away_team_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds;
