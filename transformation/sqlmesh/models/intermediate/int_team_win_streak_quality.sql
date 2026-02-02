MODEL (
  name intermediate.int_team_streak_qual,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate win streak quality: win streak length weighted by average opponent quality during the streak
-- A 5-game win streak against strong opponents (avg win_pct > 0.6) is more meaningful than against weak opponents
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
  -- Create one row per team per game with opponent info
  SELECT 
    game_id,
    game_date,
    home_team_id AS team_id,
    away_team_id AS opponent_team_id,
    home_result AS result
  FROM completed_games
  WHERE home_result IS NOT NULL
  
  UNION ALL
  
  SELECT 
    game_id,
    game_date,
    away_team_id AS team_id,
    home_team_id AS opponent_team_id,
    away_result AS result
  FROM completed_games
  WHERE away_result IS NOT NULL
),

-- Get opponent quality (rolling 10-game win_pct) from pre-computed lookup (no LATERAL)
team_games_with_opponent_quality AS (
  SELECT 
    tg.game_id,
    tg.game_date,
    tg.team_id,
    tg.opponent_team_id,
    tg.result,
    COALESCE(opp_lookup.rolling_10_win_pct, 0.5) AS opponent_quality
  FROM team_games tg
  LEFT JOIN intermediate.int_team_roll_lkp opp_lookup
    ON opp_lookup.game_id = tg.game_id AND opp_lookup.team_id = tg.opponent_team_id
),

-- Add previous result to identify where streaks change
team_games_with_prev AS (
  SELECT 
    team_id,
    game_id,
    game_date,
    opponent_team_id,
    result,
    opponent_quality,
    LAG(result) OVER (
      PARTITION BY team_id 
      ORDER BY game_date, game_id
    ) AS prev_result
  FROM team_games_with_opponent_quality
),

-- Create groups where consecutive results are the same
team_result_groups AS (
  SELECT 
    team_id,
    game_id,
    game_date,
    opponent_team_id,
    result,
    opponent_quality,
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

-- Calculate win streak quality: streak length weighted by average opponent quality
team_win_streak_quality AS (
  SELECT 
    team_id,
    game_id,
    game_date,
    result,
    -- Current win streak length
    CASE 
      WHEN result = 'W' THEN
        COUNT(*) FILTER (WHERE result = 'W') OVER (
          PARTITION BY team_id, result_group
          ORDER BY game_date, game_id
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )
      ELSE 0
    END AS win_streak_length,
    -- Average opponent quality during current win streak (if in win streak)
    CASE 
      WHEN result = 'W' THEN
        AVG(opponent_quality) FILTER (WHERE result = 'W') OVER (
          PARTITION BY team_id, result_group
          ORDER BY game_date, game_id
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )
      ELSE NULL
    END AS avg_opponent_quality_in_streak
  FROM team_result_groups
),

-- One row per (team_id, game_id) with previous game's streak (replaces LATERAL joins)
win_streak_quality_prev AS (
  SELECT
    team_id,
    game_id,
    game_date,
    LAG(win_streak_length) OVER (PARTITION BY team_id ORDER BY game_date, game_id) AS prev_win_streak_length,
    LAG(avg_opponent_quality_in_streak) OVER (PARTITION BY team_id ORDER BY game_date, game_id) AS prev_avg_opponent_quality_in_streak
  FROM team_win_streak_quality
),

-- Join games to previous-game streak for home and away (no LATERAL)
game_win_streak_quality AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    COALESCE(
      CASE 
        WHEN home_prev.prev_win_streak_length > 0 AND home_prev.prev_avg_opponent_quality_in_streak IS NOT NULL THEN
          home_prev.prev_win_streak_length * home_prev.prev_avg_opponent_quality_in_streak
        ELSE 0.0
      END,
      0.0
    ) AS home_win_streak_quality,
    COALESCE(home_prev.prev_win_streak_length, 0) AS home_win_streak_length,
    COALESCE(
      CASE 
        WHEN away_prev.prev_win_streak_length > 0 AND away_prev.prev_avg_opponent_quality_in_streak IS NOT NULL THEN
          away_prev.prev_win_streak_length * away_prev.prev_avg_opponent_quality_in_streak
        ELSE 0.0
      END,
      0.0
    ) AS away_win_streak_quality,
    COALESCE(away_prev.prev_win_streak_length, 0) AS away_win_streak_length
  FROM raw_dev.games g
  LEFT JOIN win_streak_quality_prev home_prev
    ON home_prev.team_id = g.home_team_id AND home_prev.game_id = g.game_id
  LEFT JOIN win_streak_quality_prev away_prev
    ON away_prev.team_id = g.away_team_id AND away_prev.game_id = g.game_id
  WHERE g.game_date >= @start_ds
    AND g.game_date < @end_ds
)

SELECT 
  game_id,
  game_date,
  home_win_streak_quality,
  away_win_streak_quality,
  -- Differential: home streak quality - away streak quality
  home_win_streak_quality - away_win_streak_quality AS win_streak_quality_diff,
  -- Streak length differential (for reference)
  home_win_streak_length - away_win_streak_length AS win_streak_length_diff
FROM game_win_streak_quality
WHERE game_date >= @start_ds
  AND game_date < @end_ds;
