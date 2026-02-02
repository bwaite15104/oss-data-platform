MODEL (
  name intermediate.int_team_mom_accel,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate momentum acceleration features to capture the rate of change in team momentum
-- This is different from just momentum - it captures whether momentum is increasing or decreasing
-- Teams with accelerating momentum (improving faster) may be more likely to win
-- Approach: Compare recent 3-game momentum vs recent 5-game momentum
-- If recent 3 > recent 5, momentum is accelerating (positive acceleration)
-- If recent 3 < recent 5, momentum is decelerating (negative acceleration)

WITH completed_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    g.home_score,
    g.away_score,
    g.winner_team_id,
    CASE WHEN g.winner_team_id = g.home_team_id THEN 1 ELSE 0 END as home_win
  FROM staging.stg_games g
  WHERE g.is_completed
    AND g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Get home team's recent games with point differentials
home_recent_games AS (
  SELECT 
    curr.game_id,
    prev.game_date,
    CASE 
      WHEN prev.home_team_id = curr.home_team_id THEN prev.home_score - prev.away_score
      WHEN prev.away_team_id = curr.home_team_id THEN prev.away_score - prev.home_score
      ELSE NULL
    END as point_diff,
    ROW_NUMBER() OVER (
      PARTITION BY curr.game_id
      ORDER BY prev.game_date DESC
    ) as game_rank
  FROM completed_games curr
  JOIN completed_games prev ON 
    (prev.home_team_id = curr.home_team_id OR prev.away_team_id = curr.home_team_id)
    AND prev.game_date < curr.game_date
    AND prev.winner_team_id IS NOT NULL
),

-- Get away team's recent games with point differentials
away_recent_games AS (
  SELECT 
    curr.game_id,
    prev.game_date,
    CASE 
      WHEN prev.home_team_id = curr.away_team_id THEN prev.home_score - prev.away_score
      WHEN prev.away_team_id = curr.away_team_id THEN prev.away_score - prev.home_score
      ELSE NULL
    END as point_diff,
    ROW_NUMBER() OVER (
      PARTITION BY curr.game_id
      ORDER BY prev.game_date DESC
    ) as game_rank
  FROM completed_games curr
  JOIN completed_games prev ON 
    (prev.home_team_id = curr.away_team_id OR prev.away_team_id = curr.away_team_id)
    AND prev.game_date < curr.game_date
    AND prev.winner_team_id IS NOT NULL
),

-- Calculate home team momentum acceleration (recent 3 vs recent 5)
home_momentum_accel AS (
  SELECT 
    game_id,
    AVG(CASE WHEN game_rank <= 3 THEN point_diff END) as home_recent_3_avg_point_diff,
    AVG(CASE WHEN game_rank <= 5 THEN point_diff END) as home_recent_5_avg_point_diff
  FROM home_recent_games
  WHERE point_diff IS NOT NULL
  GROUP BY game_id
),

-- Calculate away team momentum acceleration (recent 3 vs recent 5)
away_momentum_accel AS (
  SELECT 
    game_id,
    AVG(CASE WHEN game_rank <= 3 THEN point_diff END) as away_recent_3_avg_point_diff,
    AVG(CASE WHEN game_rank <= 5 THEN point_diff END) as away_recent_5_avg_point_diff
  FROM away_recent_games
  WHERE point_diff IS NOT NULL
  GROUP BY game_id
)

SELECT 
  g.game_id,
  g.game_date,
  g.home_team_id,
  g.away_team_id,
  -- Home team momentum acceleration (recent 3 vs recent 5)
  -- Positive values indicate accelerating momentum (improving faster)
  COALESCE(hma.home_recent_3_avg_point_diff, 0.0) - COALESCE(hma.home_recent_5_avg_point_diff, 0.0) as home_momentum_acceleration,
  -- Away team momentum acceleration
  COALESCE(ama.away_recent_3_avg_point_diff, 0.0) - COALESCE(ama.away_recent_5_avg_point_diff, 0.0) as away_momentum_acceleration,
  -- Differential (home - away)
  (COALESCE(hma.home_recent_3_avg_point_diff, 0.0) - COALESCE(hma.home_recent_5_avg_point_diff, 0.0)) - 
  (COALESCE(ama.away_recent_3_avg_point_diff, 0.0) - COALESCE(ama.away_recent_5_avg_point_diff, 0.0)) as momentum_acceleration_diff
FROM completed_games g
LEFT JOIN home_momentum_accel hma ON hma.game_id = g.game_id
LEFT JOIN away_momentum_accel ama ON ama.game_id = g.game_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds
