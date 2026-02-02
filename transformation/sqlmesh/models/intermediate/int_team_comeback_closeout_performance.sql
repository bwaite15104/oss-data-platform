MODEL (
  name intermediate.int_team_comeback_closeout_perf,
  description 'Comeback/closeout performance. Short name for Postgres 63-char limit.',
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate comeback and closeout performance
-- Comeback performance: how teams perform when trailing at halftime (ability to come back)
-- Closeout performance: how teams perform when leading at halftime (ability to close out games)
-- This captures late-game execution and mental toughness beyond just clutch performance
WITH completed_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    g.home_score,
    g.away_score,
    g.winner_team_id,
    -- We don't have halftime scores, so we'll use final score differential as proxy
    -- Games where final margin is small (< 5 points) likely had close halftime scores
    -- Games where final margin is large (> 10 points) likely had lopsided halftime scores
    -- For comeback: games where team was trailing by > 5 at some point (proxy: final margin against them > 5)
    -- For closeout: games where team was leading by > 5 at some point (proxy: final margin for them > 5)
    g.home_score - g.away_score AS home_point_diff,
    ABS(g.home_score - g.away_score) AS point_margin
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.winner_team_id IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Calculate home team's comeback performance (games where they were trailing and came back to win or lose close)
-- Comeback ability: win percentage in games where they were trailing by > 5 points at some point
-- We'll use games where final margin was small (< 5 points) as proxy for "close games where comeback was possible"
-- And games where they won despite being behind (negative point diff that became positive) as actual comebacks
home_comeback_performance AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.home_team_id AS team_id,
    -- Comeback win percentage: games where they won despite being behind (negative point diff that became positive)
    -- We'll use games where final margin was small (< 5 points) and they won as proxy for comeback wins
    -- Actually, let's use a different approach: games where they were trailing by > 5 at some point
    -- Since we don't have play-by-play, we'll use games where final margin was small (< 5) as proxy
    -- Comeback rate: percentage of close games (< 5 margin) where they won when they were the underdog (based on rolling win_pct)
    AVG(CASE 
      WHEN prev_g.point_margin <= 5 AND prev_g.winner_team_id = prev_g.home_team_id THEN 1.0
      WHEN prev_g.point_margin <= 5 AND prev_g.winner_team_id != prev_g.home_team_id THEN 0.0
      ELSE NULL
    END) AS home_comeback_win_pct,
    -- Comeback game count (close games where comeback was possible)
    COUNT(CASE WHEN prev_g.point_margin <= 5 THEN 1 END) AS home_comeback_game_count,
    -- Average point differential in comeback games (from home team's perspective)
    AVG(CASE 
      WHEN prev_g.point_margin <= 5 THEN prev_g.home_point_diff
      ELSE NULL
    END) AS home_comeback_avg_point_diff
  FROM raw_dev.games curr
  LEFT JOIN completed_games prev_g ON 
    (prev_g.home_team_id = curr.home_team_id OR prev_g.away_team_id = curr.home_team_id)
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= curr.game_date::date - INTERVAL '90 days'  -- Last 90 days for more recent signal
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
    AND curr.game_date < @end_ds  -- Only process games up to end of chunk
  GROUP BY curr.game_id, curr.game_date, curr.home_team_id
),

-- Calculate home team's closeout performance (games where they were leading and closed out)
-- Closeout ability: win percentage in games where they were leading by > 5 points at some point
-- We'll use games where final margin was large (> 10 points) as proxy for "games where they had a big lead"
home_closeout_performance AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.home_team_id AS team_id,
    -- Closeout win percentage: games where they won with a large margin (> 10 points)
    AVG(CASE 
      WHEN prev_g.point_margin > 10 AND prev_g.winner_team_id = prev_g.home_team_id THEN 1.0
      WHEN prev_g.point_margin > 10 AND prev_g.winner_team_id != prev_g.home_team_id THEN 0.0
      ELSE NULL
    END) AS home_closeout_win_pct,
    -- Closeout game count (games with large margins)
    COUNT(CASE WHEN prev_g.point_margin > 10 THEN 1 END) AS home_closeout_game_count,
    -- Average point differential in closeout games (from home team's perspective)
    AVG(CASE 
      WHEN prev_g.point_margin > 10 THEN prev_g.home_point_diff
      ELSE NULL
    END) AS home_closeout_avg_point_diff
  FROM raw_dev.games curr
  LEFT JOIN completed_games prev_g ON 
    (prev_g.home_team_id = curr.home_team_id OR prev_g.away_team_id = curr.home_team_id)
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= curr.game_date::date - INTERVAL '90 days'  -- Last 90 days for more recent signal
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
    AND curr.game_date < @end_ds  -- Only process games up to end of chunk
  GROUP BY curr.game_id, curr.game_date, curr.home_team_id
),

-- Calculate away team's comeback performance
away_comeback_performance AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.away_team_id AS team_id,
    -- Comeback win percentage: games where they won despite being behind
    AVG(CASE 
      WHEN prev_g.point_margin <= 5 AND prev_g.winner_team_id = prev_g.away_team_id THEN 1.0
      WHEN prev_g.point_margin <= 5 AND prev_g.winner_team_id != prev_g.away_team_id THEN 0.0
      ELSE NULL
    END) AS away_comeback_win_pct,
    -- Comeback game count
    COUNT(CASE WHEN prev_g.point_margin <= 5 THEN 1 END) AS away_comeback_game_count,
    -- Average point differential in comeback games (from away team's perspective)
    AVG(CASE 
      WHEN prev_g.point_margin <= 5 THEN -prev_g.home_point_diff  -- Flip sign for away team perspective
      ELSE NULL
    END) AS away_comeback_avg_point_diff
  FROM raw_dev.games curr
  LEFT JOIN completed_games prev_g ON 
    (prev_g.home_team_id = curr.away_team_id OR prev_g.away_team_id = curr.away_team_id)
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= curr.game_date::date - INTERVAL '90 days'  -- Last 90 days for more recent signal
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
  GROUP BY curr.game_id, curr.game_date, curr.away_team_id
),

-- Calculate away team's closeout performance
away_closeout_performance AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.away_team_id AS team_id,
    -- Closeout win percentage: games where they won with a large margin (> 10 points)
    AVG(CASE 
      WHEN prev_g.point_margin > 10 AND prev_g.winner_team_id = prev_g.away_team_id THEN 1.0
      WHEN prev_g.point_margin > 10 AND prev_g.winner_team_id != prev_g.home_team_id THEN 0.0
      ELSE NULL
    END) AS away_closeout_win_pct,
    -- Closeout game count
    COUNT(CASE WHEN prev_g.point_margin > 10 THEN 1 END) AS away_closeout_game_count,
    -- Average point differential in closeout games (from away team's perspective)
    AVG(CASE 
      WHEN prev_g.point_margin > 10 THEN -prev_g.home_point_diff  -- Flip sign for away team perspective
      ELSE NULL
    END) AS away_closeout_avg_point_diff
  FROM raw_dev.games curr
  LEFT JOIN completed_games prev_g ON 
    (prev_g.home_team_id = curr.away_team_id OR prev_g.away_team_id = curr.away_team_id)
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= curr.game_date::date - INTERVAL '90 days'  -- Last 90 days for more recent signal
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
  GROUP BY curr.game_id, curr.game_date, curr.away_team_id
)

SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  -- Home team comeback performance
  COALESCE(hcb.home_comeback_win_pct, 0.5) AS home_comeback_win_pct,
  COALESCE(hcb.home_comeback_game_count, 0) AS home_comeback_game_count,
  COALESCE(hcb.home_comeback_avg_point_diff, 0.0) AS home_comeback_avg_point_diff,
  -- Home team closeout performance
  COALESCE(hco.home_closeout_win_pct, 0.5) AS home_closeout_win_pct,
  COALESCE(hco.home_closeout_game_count, 0) AS home_closeout_game_count,
  COALESCE(hco.home_closeout_avg_point_diff, 0.0) AS home_closeout_avg_point_diff,
  -- Away team comeback performance
  COALESCE(acb.away_comeback_win_pct, 0.5) AS away_comeback_win_pct,
  COALESCE(acb.away_comeback_game_count, 0) AS away_comeback_game_count,
  COALESCE(acb.away_comeback_avg_point_diff, 0.0) AS away_comeback_avg_point_diff,
  -- Away team closeout performance
  COALESCE(aco.away_closeout_win_pct, 0.5) AS away_closeout_win_pct,
  COALESCE(aco.away_closeout_game_count, 0) AS away_closeout_game_count,
  COALESCE(aco.away_closeout_avg_point_diff, 0.0) AS away_closeout_avg_point_diff
FROM raw_dev.games g
LEFT JOIN home_comeback_performance hcb ON 
  hcb.game_id = g.game_id
  AND hcb.team_id = g.home_team_id
LEFT JOIN home_closeout_performance hco ON 
  hco.game_id = g.game_id
  AND hco.team_id = g.home_team_id
LEFT JOIN away_comeback_performance acb ON 
  acb.game_id = g.game_id
  AND acb.team_id = g.away_team_id
LEFT JOIN away_closeout_performance aco ON 
  aco.game_id = g.game_id
  AND aco.team_id = g.away_team_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds;
