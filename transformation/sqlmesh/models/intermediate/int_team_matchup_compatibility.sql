MODEL (
  name intermediate.int_team_matchup_compat,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
  description 'Team matchup compatibility features that measure how well two teams styles match up in the current game. Captures pace compatibility, offensive/defensive style compatibility, and style matchup advantages.'
);

-- Calculate matchup compatibility between home and away teams
-- This captures how well the two teams' styles match up in the current game
WITH game_rolling_stats AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    -- Home team rolling stats (most recent before this game)
    COALESCE((
      SELECT rs.rolling_10_pace
      FROM intermediate.int_team_rolling_stats rs
      WHERE rs.team_id = g.home_team_id
        AND rs.game_date < g.game_date::date
      ORDER BY rs.game_date DESC
      LIMIT 1
    ), 100.0) AS home_pace,
    COALESCE((
      SELECT rs.rolling_10_off_rtg
      FROM intermediate.int_team_rolling_stats rs
      WHERE rs.team_id = g.home_team_id
        AND rs.game_date < g.game_date::date
      ORDER BY rs.game_date DESC
      LIMIT 1
    ), 100.0) AS home_off_rtg,
    COALESCE((
      SELECT rs.rolling_10_def_rtg
      FROM intermediate.int_team_rolling_stats rs
      WHERE rs.team_id = g.home_team_id
        AND rs.game_date < g.game_date::date
      ORDER BY rs.game_date DESC
      LIMIT 1
    ), 100.0) AS home_def_rtg,
    -- Away team rolling stats (most recent before this game)
    COALESCE((
      SELECT rs.rolling_10_pace
      FROM intermediate.int_team_rolling_stats rs
      WHERE rs.team_id = g.away_team_id
        AND rs.game_date < g.game_date::date
      ORDER BY rs.game_date DESC
      LIMIT 1
    ), 100.0) AS away_pace,
    COALESCE((
      SELECT rs.rolling_10_off_rtg
      FROM intermediate.int_team_rolling_stats rs
      WHERE rs.team_id = g.away_team_id
        AND rs.game_date < g.game_date::date
      ORDER BY rs.game_date DESC
      LIMIT 1
    ), 100.0) AS away_off_rtg,
    COALESCE((
      SELECT rs.rolling_10_def_rtg
      FROM intermediate.int_team_rolling_stats rs
      WHERE rs.team_id = g.away_team_id
        AND rs.game_date < g.game_date::date
      ORDER BY rs.game_date DESC
      LIMIT 1
    ), 100.0) AS away_def_rtg
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Calculate league medians for style classification
league_medians AS (
  SELECT 
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pace) AS median_pace,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY off_rtg) AS median_off_rtg,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY def_rtg) AS median_def_rtg
  FROM (
    SELECT DISTINCT
      rs.rolling_10_pace AS pace,
      rs.rolling_10_off_rtg AS off_rtg,
      rs.rolling_10_def_rtg AS def_rtg
    FROM intermediate.int_team_rolling_stats rs
    WHERE rs.rolling_10_pace IS NOT NULL
      AND rs.rolling_10_off_rtg IS NOT NULL
      AND rs.rolling_10_def_rtg IS NOT NULL
  ) stats
)

SELECT 
  grs.game_id,
  grs.game_date,
  grs.home_team_id,
  grs.away_team_id,
  -- Pace compatibility: how similar are the teams' paces (lower = more compatible)
  ABS(grs.home_pace - grs.away_pace) AS pace_difference,
  -- Pace compatibility score: 1.0 = same pace, 0.0 = very different (normalized)
  CASE 
    WHEN ABS(grs.home_pace - grs.away_pace) <= 2.0 THEN 1.0
    WHEN ABS(grs.home_pace - grs.away_pace) <= 5.0 THEN 0.75
    WHEN ABS(grs.home_pace - grs.away_pace) <= 10.0 THEN 0.5
    ELSE 0.25
  END AS pace_compatibility_score,
  -- Offensive/defensive style compatibility
  -- High-scoring offense vs defensive team (favorable matchup for offense)
  CASE 
    WHEN grs.home_off_rtg > lm.median_off_rtg AND grs.away_def_rtg < lm.median_def_rtg THEN 1.0
    WHEN grs.home_off_rtg > lm.median_off_rtg AND grs.away_def_rtg > lm.median_def_rtg THEN 0.5
    WHEN grs.home_off_rtg < lm.median_off_rtg AND grs.away_def_rtg < lm.median_def_rtg THEN 0.5
    ELSE 0.0
  END AS home_off_vs_away_def_compatibility,
  -- Away team offense vs home team defense
  CASE 
    WHEN grs.away_off_rtg > lm.median_off_rtg AND grs.home_def_rtg < lm.median_def_rtg THEN 1.0
    WHEN grs.away_off_rtg > lm.median_off_rtg AND grs.home_def_rtg > lm.median_def_rtg THEN 0.5
    WHEN grs.away_off_rtg < lm.median_off_rtg AND grs.home_def_rtg < lm.median_def_rtg THEN 0.5
    ELSE 0.0
  END AS away_off_vs_home_def_compatibility,
  -- Style matchup advantage (which team's style is better suited)
  -- Home team advantage: positive = home team has style advantage
  (CASE 
    WHEN grs.home_off_rtg > lm.median_off_rtg AND grs.away_def_rtg < lm.median_def_rtg THEN 1.0
    WHEN grs.home_off_rtg > lm.median_off_rtg AND grs.away_def_rtg > lm.median_def_rtg THEN 0.5
    WHEN grs.home_off_rtg < lm.median_off_rtg AND grs.away_def_rtg < lm.median_def_rtg THEN 0.5
    ELSE 0.0
  END) - (CASE 
    WHEN grs.away_off_rtg > lm.median_off_rtg AND grs.home_def_rtg < lm.median_def_rtg THEN 1.0
    WHEN grs.away_off_rtg > lm.median_off_rtg AND grs.home_def_rtg > lm.median_def_rtg THEN 0.5
    WHEN grs.away_off_rtg < lm.median_off_rtg AND grs.home_def_rtg < lm.median_def_rtg THEN 0.5
    ELSE 0.0
  END) AS style_matchup_advantage,
  -- Net rating compatibility (how similar are teams' net ratings)
  ABS((grs.home_off_rtg - grs.home_def_rtg) - (grs.away_off_rtg - grs.away_def_rtg)) AS net_rtg_difference,
  -- Net rating compatibility score
  CASE 
    WHEN ABS((grs.home_off_rtg - grs.home_def_rtg) - (grs.away_off_rtg - grs.away_def_rtg)) <= 2.0 THEN 1.0
    WHEN ABS((grs.home_off_rtg - grs.home_def_rtg) - (grs.away_off_rtg - grs.away_def_rtg)) <= 5.0 THEN 0.75
    WHEN ABS((grs.home_off_rtg - grs.home_def_rtg) - (grs.away_off_rtg - grs.away_def_rtg)) <= 10.0 THEN 0.5
    ELSE 0.25
  END AS net_rtg_compatibility_score
FROM game_rolling_stats grs
CROSS JOIN league_medians lm
WHERE grs.game_date >= @start_ds
  AND grs.game_date < @end_ds;
