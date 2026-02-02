MODEL (
  name intermediate.int_team_cum_fatigue,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- NBA BAA start; updated from 2010-10-01 to support full history backfill
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate cumulative fatigue features based on game density over recent periods
-- This captures how many games teams have played in recent windows (e.g., 5 games in 7 days)
-- which is different from just rest days - it captures cumulative fatigue over longer periods

WITH completed_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
),

team_games AS (
  -- Create one row per team per game
  SELECT 
    game_id,
    game_date,
    home_team_id AS team_id
  FROM completed_games
  
  UNION ALL
  
  SELECT 
    game_id,
    game_date,
    away_team_id AS team_id
  FROM completed_games
),

-- Calculate rest days for each game (days since previous game)
team_games_with_rest AS (
  SELECT 
    team_id,
    game_id,
    game_date,
    -- Rest days since previous game (NULL for first game)
    CASE 
      WHEN LAG(game_date) OVER (
        PARTITION BY team_id 
        ORDER BY game_date, game_id
      ) IS NOT NULL THEN
        (game_date - LAG(game_date) OVER (
          PARTITION BY team_id 
          ORDER BY game_date, game_id
        ))::integer
      ELSE NULL
    END AS rest_days
  FROM team_games
),

-- For each team game, calculate games played in recent windows
team_fatigue AS (
  SELECT 
    team_id,
    game_id,
    game_date,
    -- Games played in last 7 days (including current game)
    -- RANGE windows require single ORDER BY column (game_date only)
    COUNT(*) OVER (
      PARTITION BY team_id 
      ORDER BY game_date 
      RANGE BETWEEN INTERVAL '7 days' PRECEDING AND CURRENT ROW
    ) AS games_in_last_7_days,
    -- Games played in last 10 days
    COUNT(*) OVER (
      PARTITION BY team_id 
      ORDER BY game_date 
      RANGE BETWEEN INTERVAL '10 days' PRECEDING AND CURRENT ROW
    ) AS games_in_last_10_days,
    -- Games played in last 14 days
    COUNT(*) OVER (
      PARTITION BY team_id 
      ORDER BY game_date 
      RANGE BETWEEN INTERVAL '14 days' PRECEDING AND CURRENT ROW
    ) AS games_in_last_14_days,
    -- Average rest days in last 7 days (captures rest quality/distribution)
    -- Use ROWS window to average rest_days over recent games
    AVG(rest_days) OVER (
      PARTITION BY team_id 
      ORDER BY game_date, game_id 
      ROWS BETWEEN 3 PRECEDING AND CURRENT ROW
    ) AS avg_rest_days_last_7_days,
    -- Average rest days in last 10 days
    AVG(rest_days) OVER (
      PARTITION BY team_id 
      ORDER BY game_date, game_id 
      ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
    ) AS avg_rest_days_last_10_days,
    -- Game density score: games per day in last 7 days (higher = more fatigued)
    -- Normalize by dividing by 7 to get games per day
    COUNT(*) OVER (
      PARTITION BY team_id 
      ORDER BY game_date 
      RANGE BETWEEN INTERVAL '7 days' PRECEDING AND CURRENT ROW
    )::float / 7.0 AS game_density_7_days,
    -- Game density score: games per day in last 10 days
    COUNT(*) OVER (
      PARTITION BY team_id 
      ORDER BY game_date 
      RANGE BETWEEN INTERVAL '10 days' PRECEDING AND CURRENT ROW
    )::float / 10.0 AS game_density_10_days
  FROM team_games_with_rest
)

-- Join back to games to get home and away team fatigue features
-- Filter to only process games in the current incremental chunk
SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  -- Home team cumulative fatigue
  COALESCE(home_fatigue.games_in_last_7_days, 0) AS home_games_in_last_7_days,
  COALESCE(home_fatigue.games_in_last_10_days, 0) AS home_games_in_last_10_days,
  COALESCE(home_fatigue.games_in_last_14_days, 0) AS home_games_in_last_14_days,
  COALESCE(home_fatigue.avg_rest_days_last_7_days, 0.0) AS home_avg_rest_days_last_7_days,
  COALESCE(home_fatigue.avg_rest_days_last_10_days, 0.0) AS home_avg_rest_days_last_10_days,
  COALESCE(home_fatigue.game_density_7_days, 0.0) AS home_game_density_7_days,
  COALESCE(home_fatigue.game_density_10_days, 0.0) AS home_game_density_10_days,
  -- Away team cumulative fatigue
  COALESCE(away_fatigue.games_in_last_7_days, 0) AS away_games_in_last_7_days,
  COALESCE(away_fatigue.games_in_last_10_days, 0) AS away_games_in_last_10_days,
  COALESCE(away_fatigue.games_in_last_14_days, 0) AS away_games_in_last_14_days,
  COALESCE(away_fatigue.avg_rest_days_last_7_days, 0.0) AS away_avg_rest_days_last_7_days,
  COALESCE(away_fatigue.avg_rest_days_last_10_days, 0.0) AS away_avg_rest_days_last_10_days,
  COALESCE(away_fatigue.game_density_7_days, 0.0) AS away_game_density_7_days,
  COALESCE(away_fatigue.game_density_10_days, 0.0) AS away_game_density_10_days,
  -- Differential features (home - away)
  COALESCE(home_fatigue.games_in_last_7_days, 0) - COALESCE(away_fatigue.games_in_last_7_days, 0) AS games_in_last_7_days_diff,
  COALESCE(home_fatigue.games_in_last_10_days, 0) - COALESCE(away_fatigue.games_in_last_10_days, 0) AS games_in_last_10_days_diff,
  COALESCE(home_fatigue.games_in_last_14_days, 0) - COALESCE(away_fatigue.games_in_last_14_days, 0) AS games_in_last_14_days_diff,
  COALESCE(home_fatigue.avg_rest_days_last_7_days, 0.0) - COALESCE(away_fatigue.avg_rest_days_last_7_days, 0.0) AS avg_rest_days_last_7_days_diff,
  COALESCE(home_fatigue.avg_rest_days_last_10_days, 0.0) - COALESCE(away_fatigue.avg_rest_days_last_10_days, 0.0) AS avg_rest_days_last_10_days_diff,
  COALESCE(home_fatigue.game_density_7_days, 0.0) - COALESCE(away_fatigue.game_density_7_days, 0.0) AS game_density_7_days_diff,
  COALESCE(home_fatigue.game_density_10_days, 0.0) - COALESCE(away_fatigue.game_density_10_days, 0.0) AS game_density_10_days_diff
FROM raw_dev.games g
LEFT JOIN team_fatigue home_fatigue ON 
  home_fatigue.team_id = g.home_team_id 
  AND home_fatigue.game_id = g.game_id
LEFT JOIN team_fatigue away_fatigue ON 
  away_fatigue.team_id = g.away_team_id 
  AND away_fatigue.game_id = g.game_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds;
