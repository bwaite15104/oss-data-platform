MODEL (
  name intermediate.int_team_weighted_momentum,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate momentum weighted by opponent quality
-- A win against a strong team contributes more to momentum than a win against a weak team
-- A loss against a strong team hurts momentum less than a loss against a weak team
-- Refactored: LATERAL joins replaced with int_team_roll_lkp; 4 correlated subqueries replaced with LAG + JOIN (opt iteration 12)

WITH completed_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    g.winner_team_id,
    g.home_score,
    g.away_score
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.winner_team_id IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Get opponent strength for each game using lookup JOIN (no LATERAL)
games_with_opponent_strength AS (
  SELECT 
    g.game_id,
    g.game_date,
    g.home_team_id,
    g.away_team_id,
    COALESCE(rs_away.rolling_10_win_pct, 0.5) AS away_opponent_strength,
    COALESCE(rs_home.rolling_10_win_pct, 0.5) AS home_opponent_strength
  FROM completed_games g
  LEFT JOIN intermediate.int_team_roll_lkp rs_away
    ON rs_away.game_id = g.game_id AND rs_away.team_id = g.away_team_id
  LEFT JOIN intermediate.int_team_roll_lkp rs_home
    ON rs_home.game_id = g.game_id AND rs_home.team_id = g.home_team_id
),

team_games AS (
  SELECT 
    g.game_id,
    g.game_date,
    g.home_team_id AS team_id,
    g.away_team_id AS opponent_team_id,
    CASE 
      WHEN g.winner_team_id = g.home_team_id THEN 1
      WHEN g.winner_team_id = g.away_team_id THEN -1
      ELSE 0
    END AS result,
    gos.away_opponent_strength AS opponent_strength
  FROM completed_games g
  JOIN games_with_opponent_strength gos ON gos.game_id = g.game_id

  UNION ALL

  SELECT 
    g.game_id,
    g.game_date,
    g.away_team_id AS team_id,
    g.home_team_id AS opponent_team_id,
    CASE 
      WHEN g.winner_team_id = g.away_team_id THEN 1
      WHEN g.winner_team_id = g.home_team_id THEN -1
      ELSE 0
    END AS result,
    gos.home_opponent_strength AS opponent_strength
  FROM completed_games g
  JOIN games_with_opponent_strength gos ON gos.game_id = g.game_id
),

team_momentum_windowed AS (
  SELECT 
    tg.*,
    CASE 
      WHEN tg.result = 1 THEN tg.result * tg.opponent_strength
      WHEN tg.result = -1 THEN tg.result * (1 - tg.opponent_strength)
      ELSE 0
    END AS momentum_contribution
  FROM team_games tg
),

team_momentum_aggregated AS (
  SELECT 
    tmw.team_id,
    tmw.game_id,
    tmw.game_date,
    SUM(tmw.momentum_contribution)
      OVER (
        PARTITION BY tmw.team_id 
        ORDER BY tmw.game_date ASC, tmw.game_id ASC
        ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
      ) AS momentum_5_including_current,
    SUM(tmw.momentum_contribution)
      OVER (
        PARTITION BY tmw.team_id 
        ORDER BY tmw.game_date ASC, tmw.game_id ASC
        ROWS BETWEEN 9 PRECEDING AND CURRENT ROW
      ) AS momentum_10_including_current
  FROM team_momentum_windowed tmw
),

-- Previous game's momentum per (team_id, game_id) via LAG (replaces 4 correlated subqueries)
prev_momentum AS (
  SELECT 
    team_id,
    game_id,
    game_date,
    LAG(momentum_5_including_current) OVER (PARTITION BY team_id ORDER BY game_date ASC, game_id ASC) AS prev_momentum_5,
    LAG(momentum_10_including_current) OVER (PARTITION BY team_id ORDER BY game_date ASC, game_id ASC) AS prev_momentum_10
  FROM team_momentum_aggregated
),

weighted_momentum AS (
  SELECT 
    g.game_id,
    g.game_date,
    g.home_team_id,
    g.away_team_id,
    COALESCE(home.prev_momentum_5, 0.0) AS home_weighted_momentum_5,
    COALESCE(home.prev_momentum_10, 0.0) AS home_weighted_momentum_10,
    COALESCE(away.prev_momentum_5, 0.0) AS away_weighted_momentum_5,
    COALESCE(away.prev_momentum_10, 0.0) AS away_weighted_momentum_10
  FROM raw_dev.games g
  LEFT JOIN prev_momentum home ON home.team_id = g.home_team_id AND home.game_id = g.game_id
  LEFT JOIN prev_momentum away ON away.team_id = g.away_team_id AND away.game_id = g.game_id
  WHERE g.game_date >= @start_ds
    AND g.game_date < @end_ds
)

SELECT 
  game_id,
  game_date,
  home_team_id,
  away_team_id,
  home_weighted_momentum_5,
  home_weighted_momentum_10,
  away_weighted_momentum_5,
  away_weighted_momentum_10,
  home_weighted_momentum_5 - away_weighted_momentum_5 AS weighted_momentum_diff_5,
  home_weighted_momentum_10 - away_weighted_momentum_10 AS weighted_momentum_diff_10
FROM weighted_momentum
WHERE game_date >= @start_ds
  AND game_date < @end_ds;
