MODEL (
  name intermediate.int_team_rivalry_ind,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate rivalry indicators based on historical matchup frequency and intensity
WITH completed_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    g.winner_team_id,
    g.home_score,
    g.away_score,
    -- Calculate point differential (absolute value for intensity)
    ABS(g.home_score - g.away_score) AS point_margin
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Get all historical matchups before each game
historical_matchups AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.home_team_id,
    curr.away_team_id,
    prev.game_date AS prev_game_date,
    prev.point_margin,
    -- Determine if matchup was close (within 5 points) - indicates rivalry intensity
    CASE WHEN prev.point_margin <= 5 THEN 1 ELSE 0 END AS is_close_game,
    -- Determine if matchup was very close (within 3 points) - high intensity
    CASE WHEN prev.point_margin <= 3 THEN 1 ELSE 0 END AS is_very_close_game
  FROM raw_dev.games curr
  LEFT JOIN completed_games prev ON (
    (prev.home_team_id = curr.home_team_id AND prev.away_team_id = curr.away_team_id)
    OR (prev.home_team_id = curr.away_team_id AND prev.away_team_id = curr.home_team_id)
  )
  AND prev.game_date < curr.game_date::date
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
    AND curr.game_date < @end_ds  -- Only process games up to end of chunk
)

SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  -- Rivalry frequency: total number of matchups (more frequent = stronger rivalry)
  COALESCE(rivalry_all.total_matchups, 0) AS rivalry_total_matchups,
  -- Rivalry frequency (last 2 seasons): recent matchup frequency
  COALESCE(rivalry_recent.recent_matchups, 0) AS rivalry_recent_matchups,
  -- Average point margin in historical matchups (lower = closer games = stronger rivalry)
  COALESCE(rivalry_all.avg_point_margin, 0.0) AS rivalry_avg_point_margin,
  -- Rivalry intensity: percentage of close games (within 5 points)
  CASE 
    WHEN COALESCE(rivalry_all.total_matchups, 0) > 0 THEN
      COALESCE(rivalry_all.close_games, 0)::float / rivalry_all.total_matchups
    ELSE 0.0  -- Default to 0 if no history
  END AS rivalry_close_game_pct,
  -- High intensity: percentage of very close games (within 3 points)
  CASE 
    WHEN COALESCE(rivalry_all.total_matchups, 0) > 0 THEN
      COALESCE(rivalry_all.very_close_games, 0)::float / rivalry_all.total_matchups
    ELSE 0.0  -- Default to 0 if no history
  END AS rivalry_very_close_game_pct,
  -- Recent rivalry intensity: percentage of close games in last 5 matchups
  CASE 
    WHEN COALESCE(rivalry_recent_5.recent_5_matchups, 0) > 0 THEN
      COALESCE(rivalry_recent_5.recent_5_close_games, 0)::float / rivalry_recent_5.recent_5_matchups
    ELSE 0.0  -- Default to 0 if insufficient history
  END AS rivalry_recent_close_game_pct,
  -- Recent average margin (last 5 matchups) - lower = more intense recent rivalry
  COALESCE(rivalry_recent_5.recent_5_avg_margin, 0.0) AS rivalry_recent_avg_margin
FROM raw_dev.games g
LEFT JOIN (
  SELECT 
    game_id,
    COUNT(*) AS total_matchups,
    AVG(point_margin) AS avg_point_margin,
    SUM(is_close_game) AS close_games,
    SUM(is_very_close_game) AS very_close_games
  FROM historical_matchups
  GROUP BY game_id
) rivalry_all ON rivalry_all.game_id = g.game_id
LEFT JOIN (
  SELECT 
    game_id,
    COUNT(*) AS recent_matchups
  FROM (
    SELECT 
      hm.game_id,
      ROW_NUMBER() OVER (PARTITION BY hm.game_id ORDER BY hm.prev_game_date DESC) as rn
    FROM historical_matchups hm
  ) ranked
  WHERE rn <= 10  -- Last 10 matchups (approximately 2 seasons)
  GROUP BY game_id
) rivalry_recent ON rivalry_recent.game_id = g.game_id
LEFT JOIN (
  SELECT 
    game_id,
    COUNT(*) AS recent_5_matchups,
    SUM(is_close_game) AS recent_5_close_games,
    AVG(point_margin) AS recent_5_avg_margin
  FROM (
    SELECT 
      hm.game_id,
      hm.is_close_game,
      hm.point_margin,
      ROW_NUMBER() OVER (PARTITION BY hm.game_id ORDER BY hm.prev_game_date DESC) as rn
    FROM historical_matchups hm
  ) ranked
  WHERE rn <= 5
  GROUP BY game_id
) rivalry_recent_5 ON rivalry_recent_5.game_id = g.game_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds;
