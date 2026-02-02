MODEL (
  name intermediate.int_team_streaks,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

WITH completed_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    g.winner_team_id,
    -- Determine result for home team
    CASE 
      WHEN g.winner_team_id = g.home_team_id THEN 'W'
      WHEN g.home_score IS NOT NULL AND g.away_score IS NOT NULL THEN 'L'
      ELSE NULL
    END AS home_result,
    -- Determine result for away team
    CASE 
      WHEN g.winner_team_id = g.away_team_id THEN 'W'
      WHEN g.home_score IS NOT NULL AND g.away_score IS NOT NULL THEN 'L'
      ELSE NULL
    END AS away_result
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

team_games AS (
  -- Create one row per team per game
  SELECT 
    game_id,
    game_date,
    home_team_id AS team_id,
    home_result AS result
  FROM completed_games
  WHERE home_result IS NOT NULL
  
  UNION ALL
  
  SELECT 
    game_id,
    game_date,
    away_team_id AS team_id,
    away_result AS result
  FROM completed_games
  WHERE away_result IS NOT NULL
),

-- Add previous result to identify where streaks change
team_games_with_prev AS (
  SELECT 
    team_id,
    game_id,
    game_date,
    result,
    LAG(result) OVER (
      PARTITION BY team_id 
      ORDER BY game_date, game_id
    ) AS prev_result
  FROM team_games
),

-- Create groups where consecutive results are the same
team_result_groups AS (
  SELECT 
    team_id,
    game_id,
    game_date,
    result,
    -- Create groups where consecutive results are the same
    SUM(CASE 
      WHEN prev_result IS NULL OR prev_result != result THEN 1
      ELSE 0
    END) OVER (
      PARTITION BY team_id 
      ORDER BY game_date, game_id
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS result_group
  FROM team_games_with_prev
),

-- Calculate streaks within each group
team_streaks AS (
  SELECT 
    team_id,
    game_id,
    game_date,
    result,
    -- Count consecutive wins in the current group
    CASE 
      WHEN result = 'W' THEN
        COUNT(*) FILTER (WHERE result = 'W') OVER (
          PARTITION BY team_id, result_group
          ORDER BY game_date, game_id
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )
      ELSE 0
    END AS win_streak,
    -- Count consecutive losses in the current group
    CASE 
      WHEN result = 'L' THEN
        COUNT(*) FILTER (WHERE result = 'L') OVER (
          PARTITION BY team_id, result_group
          ORDER BY game_date, game_id
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )
      ELSE 0
    END AS loss_streak
  FROM team_result_groups
),

-- One row per (team_id, game_id) with previous game's streak (replaces LATERAL joins)
streak_prev AS (
  SELECT
    team_id,
    game_id,
    LAG(win_streak) OVER (PARTITION BY team_id ORDER BY game_date, game_id) AS prev_win_streak,
    LAG(loss_streak) OVER (PARTITION BY team_id ORDER BY game_date, game_id) AS prev_loss_streak
  FROM team_streaks
)

-- Join games to previous-game streak for home and away (no LATERAL)
SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  COALESCE(home_prev.prev_win_streak, 0) AS home_win_streak,
  COALESCE(home_prev.prev_loss_streak, 0) AS home_loss_streak,
  COALESCE(away_prev.prev_win_streak, 0) AS away_win_streak,
  COALESCE(away_prev.prev_loss_streak, 0) AS away_loss_streak,
  0 AS home_momentum_score,
  0 AS away_momentum_score
FROM raw_dev.games g
LEFT JOIN streak_prev home_prev
  ON home_prev.game_id = g.game_id AND home_prev.team_id = g.home_team_id
LEFT JOIN streak_prev away_prev
  ON away_prev.game_id = g.game_id AND away_prev.team_id = g.away_team_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds;
