MODEL (
  name intermediate.int_team_perf_vs_rest_ctx,
  description 'Team performance vs similar rest context. Short name for Postgres 63-char limit.',
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate team performance in games with similar rest context
-- Similar rest context means both teams have similar rest levels:
-- - Both well-rested (3+ days rest)
-- - Both moderately rested (2 days rest)
-- - Both on back-to-back (0-1 days rest)
-- - Both on back-to-back with travel

WITH completed_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    g.winner_team_id,
    g.home_score,
    g.away_score,
    CASE WHEN g.winner_team_id = g.home_team_id THEN 1 ELSE 0 END AS home_win,
    (g.home_score - g.away_score) AS home_point_diff
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Get rest context for each game
game_rest_context AS (
  SELECT 
    g.game_id,
    g.game_date,
    g.home_team_id,
    g.away_team_id,
    rd.home_rest_days,
    rd.away_rest_days,
    rd.home_back_to_back,
    rd.away_back_to_back,
    rd.home_back_to_back_with_travel,
    rd.away_back_to_back_with_travel,
    -- Categorize rest context for similarity matching
    CASE 
      WHEN rd.home_back_to_back_with_travel THEN 'btb_travel'
      WHEN rd.home_back_to_back THEN 'btb'
      WHEN rd.home_rest_days >= 3 THEN 'well_rested'
      WHEN rd.home_rest_days = 2 THEN 'moderate_rest'
      ELSE 'low_rest'
    END AS home_rest_category,
    CASE 
      WHEN rd.away_back_to_back_with_travel THEN 'btb_travel'
      WHEN rd.away_back_to_back THEN 'btb'
      WHEN rd.away_rest_days >= 3 THEN 'well_rested'
      WHEN rd.away_rest_days = 2 THEN 'moderate_rest'
      ELSE 'low_rest'
    END AS away_rest_category,
    -- Similar rest context: both teams have same rest category
    CASE 
      WHEN rd.home_back_to_back_with_travel AND rd.away_back_to_back_with_travel THEN TRUE
      WHEN rd.home_back_to_back AND rd.away_back_to_back AND 
           NOT rd.home_back_to_back_with_travel AND NOT rd.away_back_to_back_with_travel THEN TRUE
      WHEN rd.home_rest_days >= 3 AND rd.away_rest_days >= 3 THEN TRUE
      WHEN rd.home_rest_days = 2 AND rd.away_rest_days = 2 THEN TRUE
      WHEN rd.home_rest_days <= 1 AND rd.away_rest_days <= 1 AND
           NOT rd.home_back_to_back AND NOT rd.away_back_to_back THEN TRUE
      ELSE FALSE
    END AS is_similar_rest_context
  FROM completed_games g
  LEFT JOIN intermediate.int_team_rest_days rd ON rd.game_id = g.game_id
),

-- For each team, calculate performance in games with similar rest context
-- (where both teams had similar rest levels)
team_performance_vs_similar_rest AS (
  SELECT 
    grc.game_id,
    grc.game_date,
    grc.home_team_id,
    grc.away_team_id,
    -- Home team performance vs similar rest context
    -- Look at home team's historical games where both teams had similar rest
    (
      SELECT COUNT(*)
      FROM game_rest_context hist_grc
      JOIN completed_games hist_g ON hist_g.game_id = hist_grc.game_id
      WHERE hist_grc.home_team_id = grc.home_team_id
        AND hist_grc.game_date < grc.game_date
        AND hist_grc.is_similar_rest_context = TRUE
        AND hist_g.home_win = 1
    ) AS home_wins_vs_similar_rest,
    (
      SELECT COUNT(*)
      FROM game_rest_context hist_grc
      JOIN completed_games hist_g ON hist_g.game_id = hist_grc.game_id
      WHERE hist_grc.home_team_id = grc.home_team_id
        AND hist_grc.game_date < grc.game_date
        AND hist_grc.is_similar_rest_context = TRUE
    ) AS home_games_vs_similar_rest,
    (
      SELECT AVG(hist_g.home_point_diff)
      FROM game_rest_context hist_grc
      JOIN completed_games hist_g ON hist_g.game_id = hist_grc.game_id
      WHERE hist_grc.home_team_id = grc.home_team_id
        AND hist_grc.game_date < grc.game_date
        AND hist_grc.is_similar_rest_context = TRUE
    ) AS home_avg_point_diff_vs_similar_rest,
    -- Away team performance vs similar rest context
    -- Look at away team's historical games where both teams had similar rest
    (
      SELECT COUNT(*)
      FROM game_rest_context hist_grc
      JOIN completed_games hist_g ON hist_g.game_id = hist_grc.game_id
      WHERE hist_grc.away_team_id = grc.away_team_id
        AND hist_grc.game_date < grc.game_date
        AND hist_grc.is_similar_rest_context = TRUE
        AND hist_g.home_win = 0
    ) AS away_wins_vs_similar_rest,
    (
      SELECT COUNT(*)
      FROM game_rest_context hist_grc
      JOIN completed_games hist_g ON hist_g.game_id = hist_grc.game_id
      WHERE hist_grc.away_team_id = grc.away_team_id
        AND hist_grc.game_date < grc.game_date
        AND hist_grc.is_similar_rest_context = TRUE
    ) AS away_games_vs_similar_rest,
    (
      SELECT AVG(-hist_g.home_point_diff)  -- Negative because away team perspective
      FROM game_rest_context hist_grc
      JOIN completed_games hist_g ON hist_g.game_id = hist_grc.game_id
      WHERE hist_grc.away_team_id = grc.away_team_id
        AND hist_grc.game_date < grc.game_date
        AND hist_grc.is_similar_rest_context = TRUE
    ) AS away_avg_point_diff_vs_similar_rest,
    -- Current game similar rest context indicator
    grc.is_similar_rest_context
  FROM game_rest_context grc
)

SELECT 
  game_id,
  game_date,
  home_team_id,
  away_team_id,
  -- Home team metrics
  CASE 
    WHEN home_games_vs_similar_rest >= 5 THEN 
      home_wins_vs_similar_rest::float / NULLIF(home_games_vs_similar_rest, 0)
    ELSE NULL
  END AS home_win_pct_vs_similar_rest,
  COALESCE(home_avg_point_diff_vs_similar_rest, 0.0) AS home_avg_point_diff_vs_similar_rest,
  COALESCE(home_games_vs_similar_rest, 0) AS home_similar_rest_game_count,
  -- Away team metrics
  CASE 
    WHEN away_games_vs_similar_rest >= 5 THEN 
      away_wins_vs_similar_rest::float / NULLIF(away_games_vs_similar_rest, 0)
    ELSE NULL
  END AS away_win_pct_vs_similar_rest,
  COALESCE(away_avg_point_diff_vs_similar_rest, 0.0) AS away_avg_point_diff_vs_similar_rest,
  COALESCE(away_games_vs_similar_rest, 0) AS away_similar_rest_game_count,
  -- Current game indicator
  COALESCE(is_similar_rest_context, FALSE) AS is_current_game_similar_rest_context,
  -- Differential features
  CASE 
    WHEN home_games_vs_similar_rest >= 5 AND away_games_vs_similar_rest >= 5 THEN 
      (home_wins_vs_similar_rest::float / NULLIF(home_games_vs_similar_rest, 0)) -
      (away_wins_vs_similar_rest::float / NULLIF(away_games_vs_similar_rest, 0))
    ELSE NULL
  END AS win_pct_vs_similar_rest_diff,
  COALESCE(home_avg_point_diff_vs_similar_rest, 0.0) - 
  COALESCE(away_avg_point_diff_vs_similar_rest, 0.0) AS avg_point_diff_vs_similar_rest_diff
FROM team_performance_vs_similar_rest
WHERE game_date >= @start_ds
  AND game_date < @end_ds;
