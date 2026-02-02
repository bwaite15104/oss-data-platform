MODEL (
  name intermediate.int_team_perf_consistency,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate team performance consistency features
-- Consistency captures how reliably teams perform - teams with lower variance are more predictable
-- This is different from just rolling averages - it measures stability of performance

WITH completed_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    g.home_score,
    g.away_score,
    g.winner_team_id
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
    home_score AS points,
    away_score AS opponent_points,
    CASE WHEN winner_team_id = home_team_id THEN 1 ELSE 0 END AS is_win,
    home_score - away_score AS point_diff
  FROM completed_games
  
  UNION ALL
  
  SELECT 
    game_id,
    game_date,
    away_team_id AS team_id,
    away_score AS points,
    home_score AS opponent_points,
    CASE WHEN winner_team_id = away_team_id THEN 1 ELSE 0 END AS is_win,
    away_score - home_score AS point_diff
  FROM completed_games
),

-- Calculate consistency metrics using rolling windows
team_consistency AS (
  SELECT 
    team_id,
    game_id,
    game_date,
    -- Win consistency: standard deviation of win/loss outcomes (lower = more consistent)
    -- For binary outcomes, this measures how "streaky" a team is
    STDDEV(is_win::float) OVER (
      PARTITION BY team_id 
      ORDER BY game_date, game_id 
      ROWS BETWEEN 9 PRECEDING AND 1 PRECEDING
    ) AS win_consistency_10,
    
    -- Point differential consistency: standard deviation of point differentials (lower = more consistent)
    STDDEV(point_diff) OVER (
      PARTITION BY team_id 
      ORDER BY game_date, game_id 
      ROWS BETWEEN 9 PRECEDING AND 1 PRECEDING
    ) AS point_diff_consistency_10,
    
    -- Points scored consistency: standard deviation of points scored (lower = more consistent)
    STDDEV(points) OVER (
      PARTITION BY team_id 
      ORDER BY game_date, game_id 
      ROWS BETWEEN 9 PRECEDING AND 1 PRECEDING
    ) AS points_consistency_10,
    
    -- Points allowed consistency: standard deviation of points allowed (lower = more consistent)
    STDDEV(opponent_points) OVER (
      PARTITION BY team_id 
      ORDER BY game_date, game_id 
      ROWS BETWEEN 9 PRECEDING AND 1 PRECEDING
    ) AS opp_points_consistency_10,
    
    -- Coefficient of variation for points (stddev / mean) - normalized consistency
    CASE 
      WHEN AVG(points) OVER (
        PARTITION BY team_id 
        ORDER BY game_date, game_id 
        ROWS BETWEEN 9 PRECEDING AND 1 PRECEDING
      ) > 0 THEN
        STDDEV(points) OVER (
          PARTITION BY team_id 
          ORDER BY game_date, game_id 
          ROWS BETWEEN 9 PRECEDING AND 1 PRECEDING
        ) / AVG(points) OVER (
          PARTITION BY team_id 
          ORDER BY game_date, game_id 
          ROWS BETWEEN 9 PRECEDING AND 1 PRECEDING
        )
      ELSE 0.0
    END AS points_cv_10,
    
    -- Coefficient of variation for opponent points
    CASE 
      WHEN AVG(opponent_points) OVER (
        PARTITION BY team_id 
        ORDER BY game_date, game_id 
        ROWS BETWEEN 9 PRECEDING AND 1 PRECEDING
      ) > 0 THEN
        STDDEV(opponent_points) OVER (
          PARTITION BY team_id 
          ORDER BY game_date, game_id 
          ROWS BETWEEN 9 PRECEDING AND 1 PRECEDING
        ) / AVG(opponent_points) OVER (
          PARTITION BY team_id 
          ORDER BY game_date, game_id 
          ROWS BETWEEN 9 PRECEDING AND 1 PRECEDING
        )
      ELSE 0.0
    END AS opp_points_cv_10,
    
    -- 5-game consistency (shorter window for recent consistency)
    STDDEV(is_win::float) OVER (
      PARTITION BY team_id 
      ORDER BY game_date, game_id 
      ROWS BETWEEN 4 PRECEDING AND 1 PRECEDING
    ) AS win_consistency_5,
    
    STDDEV(point_diff) OVER (
      PARTITION BY team_id 
      ORDER BY game_date, game_id 
      ROWS BETWEEN 4 PRECEDING AND 1 PRECEDING
    ) AS point_diff_consistency_5,
    
    STDDEV(points) OVER (
      PARTITION BY team_id 
      ORDER BY game_date, game_id 
      ROWS BETWEEN 4 PRECEDING AND 1 PRECEDING
    ) AS points_consistency_5,
    
    STDDEV(opponent_points) OVER (
      PARTITION BY team_id 
      ORDER BY game_date, game_id 
      ROWS BETWEEN 4 PRECEDING AND 1 PRECEDING
    ) AS opp_points_consistency_5,
    
    -- Coefficient of variation for 5-game windows
    CASE 
      WHEN AVG(points) OVER (
        PARTITION BY team_id 
        ORDER BY game_date, game_id 
        ROWS BETWEEN 4 PRECEDING AND 1 PRECEDING
      ) > 0 THEN
        STDDEV(points) OVER (
          PARTITION BY team_id 
          ORDER BY game_date, game_id 
          ROWS BETWEEN 4 PRECEDING AND 1 PRECEDING
        ) / AVG(points) OVER (
          PARTITION BY team_id 
          ORDER BY game_date, game_id 
          ROWS BETWEEN 4 PRECEDING AND 1 PRECEDING
        )
      ELSE 0.0
    END AS points_cv_5,
    
    CASE 
      WHEN AVG(opponent_points) OVER (
        PARTITION BY team_id 
        ORDER BY game_date, game_id 
        ROWS BETWEEN 4 PRECEDING AND 1 PRECEDING
      ) > 0 THEN
        STDDEV(opponent_points) OVER (
          PARTITION BY team_id 
          ORDER BY game_date, game_id 
          ROWS BETWEEN 4 PRECEDING AND 1 PRECEDING
        ) / AVG(opponent_points) OVER (
          PARTITION BY team_id 
          ORDER BY game_date, game_id 
          ROWS BETWEEN 4 PRECEDING AND 1 PRECEDING
        )
      ELSE 0.0
    END AS opp_points_cv_5
    
  FROM team_games
)

-- Join back to games to get home and away team consistency features
SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  -- Home team consistency features
  COALESCE(home_consistency.win_consistency_10, 0.0) AS home_win_consistency_10,
  COALESCE(home_consistency.point_diff_consistency_10, 0.0) AS home_point_diff_consistency_10,
  COALESCE(home_consistency.points_consistency_10, 0.0) AS home_points_consistency_10,
  COALESCE(home_consistency.opp_points_consistency_10, 0.0) AS home_opp_points_consistency_10,
  COALESCE(home_consistency.points_cv_10, 0.0) AS home_points_cv_10,
  COALESCE(home_consistency.opp_points_cv_10, 0.0) AS home_opp_points_cv_10,
  COALESCE(home_consistency.win_consistency_5, 0.0) AS home_win_consistency_5,
  COALESCE(home_consistency.point_diff_consistency_5, 0.0) AS home_point_diff_consistency_5,
  COALESCE(home_consistency.points_consistency_5, 0.0) AS home_points_consistency_5,
  COALESCE(home_consistency.opp_points_consistency_5, 0.0) AS home_opp_points_consistency_5,
  COALESCE(home_consistency.points_cv_5, 0.0) AS home_points_cv_5,
  COALESCE(home_consistency.opp_points_cv_5, 0.0) AS home_opp_points_cv_5,
  -- Away team consistency features
  COALESCE(away_consistency.win_consistency_10, 0.0) AS away_win_consistency_10,
  COALESCE(away_consistency.point_diff_consistency_10, 0.0) AS away_point_diff_consistency_10,
  COALESCE(away_consistency.points_consistency_10, 0.0) AS away_points_consistency_10,
  COALESCE(away_consistency.opp_points_consistency_10, 0.0) AS away_opp_points_consistency_10,
  COALESCE(away_consistency.points_cv_10, 0.0) AS away_points_cv_10,
  COALESCE(away_consistency.opp_points_cv_10, 0.0) AS away_opp_points_cv_10,
  COALESCE(away_consistency.win_consistency_5, 0.0) AS away_win_consistency_5,
  COALESCE(away_consistency.point_diff_consistency_5, 0.0) AS away_point_diff_consistency_5,
  COALESCE(away_consistency.points_consistency_5, 0.0) AS away_points_consistency_5,
  COALESCE(away_consistency.opp_points_consistency_5, 0.0) AS away_opp_points_consistency_5,
  COALESCE(away_consistency.points_cv_5, 0.0) AS away_points_cv_5,
  COALESCE(away_consistency.opp_points_cv_5, 0.0) AS away_opp_points_cv_5,
  -- Differential features (home - away) - more consistent team may have advantage
  COALESCE(home_consistency.win_consistency_10, 0.0) - COALESCE(away_consistency.win_consistency_10, 0.0) AS win_consistency_10_diff,
  COALESCE(home_consistency.point_diff_consistency_10, 0.0) - COALESCE(away_consistency.point_diff_consistency_10, 0.0) AS point_diff_consistency_10_diff,
  COALESCE(home_consistency.points_consistency_10, 0.0) - COALESCE(away_consistency.points_consistency_10, 0.0) AS points_consistency_10_diff,
  COALESCE(home_consistency.opp_points_consistency_10, 0.0) - COALESCE(away_consistency.opp_points_consistency_10, 0.0) AS opp_points_consistency_10_diff,
  COALESCE(home_consistency.points_cv_10, 0.0) - COALESCE(away_consistency.points_cv_10, 0.0) AS points_cv_10_diff,
  COALESCE(home_consistency.opp_points_cv_10, 0.0) - COALESCE(away_consistency.opp_points_cv_10, 0.0) AS opp_points_cv_10_diff,
  COALESCE(home_consistency.win_consistency_5, 0.0) - COALESCE(away_consistency.win_consistency_5, 0.0) AS win_consistency_5_diff,
  COALESCE(home_consistency.point_diff_consistency_5, 0.0) - COALESCE(away_consistency.point_diff_consistency_5, 0.0) AS point_diff_consistency_5_diff,
  COALESCE(home_consistency.points_consistency_5, 0.0) - COALESCE(away_consistency.points_consistency_5, 0.0) AS points_consistency_5_diff,
  COALESCE(home_consistency.opp_points_consistency_5, 0.0) - COALESCE(away_consistency.opp_points_consistency_5, 0.0) AS opp_points_consistency_5_diff,
  COALESCE(home_consistency.points_cv_5, 0.0) - COALESCE(away_consistency.points_cv_5, 0.0) AS points_cv_5_diff,
  COALESCE(home_consistency.opp_points_cv_5, 0.0) - COALESCE(away_consistency.opp_points_cv_5, 0.0) AS opp_points_cv_5_diff
FROM raw_dev.games g
LEFT JOIN team_consistency home_consistency ON 
  home_consistency.team_id = g.home_team_id 
  AND home_consistency.game_id = g.game_id
LEFT JOIN team_consistency away_consistency ON 
  away_consistency.team_id = g.away_team_id 
  AND away_consistency.game_id = g.game_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds;
