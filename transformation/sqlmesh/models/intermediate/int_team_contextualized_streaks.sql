MODEL (
  name intermediate.int_team_ctx_streaks,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate contextualized streaks: streak length weighted by opponent quality and home/away context
-- This captures whether teams are on meaningful streaks (against good teams) vs weak streaks (against bad teams)
-- Uses int_team_roll_lkp for opponent_quality (no correlated subqueries).
WITH team_games AS (
  -- Create one row per team per game with opponent quality from pre-computed lookup
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id AS team_id,
    g.away_team_id AS opponent_team_id,
    CASE WHEN g.winner_team_id = g.home_team_id THEN 'W' ELSE 'L' END AS result,
    TRUE AS is_home,
    COALESCE(away_lookup.rolling_10_win_pct, 0.5) AS opponent_quality
  FROM raw_dev.games g
  LEFT JOIN intermediate.int_team_roll_lkp away_lookup
    ON away_lookup.game_id = g.game_id AND away_lookup.team_id = g.away_team_id
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context

  UNION ALL

  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.away_team_id AS team_id,
    g.home_team_id AS opponent_team_id,
    CASE WHEN g.winner_team_id = g.away_team_id THEN 'W' ELSE 'L' END AS result,
    FALSE AS is_home,
    COALESCE(home_lookup.rolling_10_win_pct, 0.5) AS opponent_quality
  FROM raw_dev.games g
  LEFT JOIN intermediate.int_team_roll_lkp home_lookup
    ON home_lookup.game_id = g.game_id AND home_lookup.team_id = g.home_team_id
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Calculate weighted game value: opponent_quality * context_multiplier
weighted_games AS (
  SELECT 
    team_id,
    game_id,
    game_date,
    result,
    is_home,
    opponent_quality,
    -- Context multiplier: home games slightly more valuable for wins, away losses slightly more costly
    CASE 
      WHEN result = 'W' AND is_home THEN 1.0
      WHEN result = 'W' AND NOT is_home THEN 0.9  -- Away wins slightly less valuable
      WHEN result = 'L' AND is_home THEN 1.0
      WHEN result = 'L' AND NOT is_home THEN 1.1  -- Away losses slightly more costly
      ELSE 1.0
    END AS context_multiplier,
    -- Weighted game value: opponent_quality * context_multiplier
    opponent_quality * CASE 
      WHEN result = 'W' AND is_home THEN 1.0
      WHEN result = 'W' AND NOT is_home THEN 0.9
      WHEN result = 'L' AND is_home THEN 1.0
      WHEN result = 'L' AND NOT is_home THEN 1.1
      ELSE 1.0
    END AS weighted_game_value
  FROM team_games
),

-- Compute previous result per row (avoids nesting window functions)
with_prev_result AS (
  SELECT 
    team_id,
    game_id,
    game_date,
    result,
    weighted_game_value,
    opponent_quality,
    LAG(result) OVER (PARTITION BY team_id ORDER BY game_date, game_id) AS prev_result
  FROM weighted_games
),
-- Identify streak groups (similar to int_team_streaks logic)
streak_groups AS (
  SELECT 
    team_id,
    game_id,
    game_date,
    result,
    weighted_game_value,
    opponent_quality,
    SUM(CASE 
      WHEN prev_result IS NULL OR prev_result != result THEN 1 
      ELSE 0 
    END) OVER (
      PARTITION BY team_id 
      ORDER BY game_date, game_id
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS streak_group
  FROM with_prev_result
),

-- Calculate contextualized streak scores within each streak group
contextualized_streaks AS (
  SELECT 
    team_id,
    game_id,
    game_date,
    result,
    -- Weighted win streak: sum of weighted_game_value for consecutive wins
    CASE 
      WHEN result = 'W' THEN
        SUM(weighted_game_value) OVER (
          PARTITION BY team_id, streak_group
          ORDER BY game_date, game_id
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )
      ELSE 0.0
    END AS weighted_win_streak,
    -- Weighted loss streak: sum of weighted_game_value for consecutive losses
    CASE 
      WHEN result = 'L' THEN
        SUM(weighted_game_value) OVER (
          PARTITION BY team_id, streak_group
          ORDER BY game_date, game_id
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )
      ELSE 0.0
    END AS weighted_loss_streak,
    -- Average opponent quality in current win streak
    CASE 
      WHEN result = 'W' THEN
        AVG(opponent_quality) OVER (
          PARTITION BY team_id, streak_group
          ORDER BY game_date, game_id
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )
      ELSE 0.0
    END AS win_streak_avg_opponent_quality,
    -- Average opponent quality in current loss streak
    CASE 
      WHEN result = 'L' THEN
        AVG(opponent_quality) OVER (
          PARTITION BY team_id, streak_group
          ORDER BY game_date, game_id
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )
      ELSE 0.0
    END AS loss_streak_avg_opponent_quality
  FROM streak_groups
),

-- One row per (team_id, game_id) with previous game's contextualized streak (replaces LATERAL joins)
contextualized_streaks_prev AS (
  SELECT
    team_id,
    game_id,
    LAG(weighted_win_streak) OVER (PARTITION BY team_id ORDER BY game_date, game_id) AS prev_weighted_win_streak,
    LAG(weighted_loss_streak) OVER (PARTITION BY team_id ORDER BY game_date, game_id) AS prev_weighted_loss_streak,
    LAG(win_streak_avg_opponent_quality) OVER (PARTITION BY team_id ORDER BY game_date, game_id) AS prev_win_streak_avg_opponent_quality,
    LAG(loss_streak_avg_opponent_quality) OVER (PARTITION BY team_id ORDER BY game_date, game_id) AS prev_loss_streak_avg_opponent_quality
  FROM contextualized_streaks
)

-- Join games to previous-game contextualized streak for home and away (no LATERAL)
SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  COALESCE(cs_home.prev_weighted_win_streak, 0.0) AS home_weighted_win_streak,
  COALESCE(cs_home.prev_weighted_loss_streak, 0.0) AS home_weighted_loss_streak,
  COALESCE(cs_home.prev_win_streak_avg_opponent_quality, 0.5) AS home_win_streak_avg_opponent_quality,
  COALESCE(cs_home.prev_loss_streak_avg_opponent_quality, 0.5) AS home_loss_streak_avg_opponent_quality,
  COALESCE(cs_away.prev_weighted_win_streak, 0.0) AS away_weighted_win_streak,
  COALESCE(cs_away.prev_weighted_loss_streak, 0.0) AS away_weighted_loss_streak,
  COALESCE(cs_away.prev_win_streak_avg_opponent_quality, 0.5) AS away_win_streak_avg_opponent_quality,
  COALESCE(cs_away.prev_loss_streak_avg_opponent_quality, 0.5) AS away_loss_streak_avg_opponent_quality
FROM raw_dev.games g
LEFT JOIN contextualized_streaks_prev cs_home
  ON cs_home.game_id = g.game_id AND cs_home.team_id = g.home_team_id
LEFT JOIN contextualized_streaks_prev cs_away
  ON cs_away.game_id = g.game_id AND cs_away.team_id = g.away_team_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds
  AND g.home_score IS NOT NULL
  AND g.away_score IS NOT NULL;
