MODEL (
  name intermediate.int_team_favored_underdog_perf,
  description 'Favored/underdog performance. Short name for Postgres 63-char limit.',
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate team performance when favored vs underdog
-- This captures how teams perform when expected to win vs when expected to lose
-- Uses rolling win percentage as proxy for being "favored" (team with higher win_pct is favored)
-- Teams that consistently beat weaker opponents or pull upsets may have different characteristics

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
    AND g.winner_team_id IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Get rolling win percentages from existing rolling stats table (from previous game)
-- We need the win_pct BEFORE this game to determine if team was favored
team_rolling_win_pct AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    -- Get home team's rolling win_pct from their most recent previous game
    (SELECT rolling_10_win_pct 
     FROM intermediate.int_team_rolling_stats 
     WHERE team_id = g.home_team_id 
       AND game_date < g.game_date::date 
     ORDER BY game_date DESC 
     LIMIT 1) AS home_rolling_win_pct,
    -- Get away team's rolling win_pct from their most recent previous game
    (SELECT rolling_10_win_pct 
     FROM intermediate.int_team_rolling_stats 
     WHERE team_id = g.away_team_id 
       AND game_date < g.game_date::date 
     ORDER BY game_date DESC 
     LIMIT 1) AS away_rolling_win_pct
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Calculate home team's performance when favored vs underdog
home_favored_underdog_performance AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.home_team_id AS team_id,
    -- Win percentage when favored (home team had higher rolling win_pct than opponent)
    AVG(CASE 
      WHEN prev_trwp.home_rolling_win_pct > prev_trwp.away_rolling_win_pct 
        AND prev_g.winner_team_id = prev_g.home_team_id THEN 1.0
      WHEN prev_trwp.home_rolling_win_pct > prev_trwp.away_rolling_win_pct 
        AND prev_g.winner_team_id != prev_g.home_team_id THEN 0.0
      ELSE NULL
    END) AS home_win_pct_when_favored,
    -- Game count when favored
    COUNT(CASE WHEN prev_trwp.home_rolling_win_pct > prev_trwp.away_rolling_win_pct THEN 1 END) AS home_favored_game_count,
    -- Win percentage when underdog (home team had lower rolling win_pct than opponent)
    AVG(CASE 
      WHEN prev_trwp.home_rolling_win_pct < prev_trwp.away_rolling_win_pct 
        AND prev_g.winner_team_id = prev_g.home_team_id THEN 1.0
      WHEN prev_trwp.home_rolling_win_pct < prev_trwp.away_rolling_win_pct 
        AND prev_g.winner_team_id != prev_g.home_team_id THEN 0.0
      ELSE NULL
    END) AS home_win_pct_when_underdog,
    -- Game count when underdog
    COUNT(CASE WHEN prev_trwp.home_rolling_win_pct < prev_trwp.away_rolling_win_pct THEN 1 END) AS home_underdog_game_count,
    -- Average point differential when favored
    AVG(CASE 
      WHEN prev_trwp.home_rolling_win_pct > prev_trwp.away_rolling_win_pct 
        THEN prev_g.home_score - prev_g.away_score
      ELSE NULL
    END) AS home_avg_point_diff_when_favored,
    -- Average point differential when underdog
    AVG(CASE 
      WHEN prev_trwp.home_rolling_win_pct < prev_trwp.away_rolling_win_pct 
        THEN prev_g.home_score - prev_g.away_score
      ELSE NULL
    END) AS home_avg_point_diff_when_underdog,
    -- Upset rate: win rate when significantly underdog (win_pct diff > 0.2)
    AVG(CASE 
      WHEN prev_trwp.away_rolling_win_pct - prev_trwp.home_rolling_win_pct > 0.2
        AND prev_g.winner_team_id = prev_g.home_team_id THEN 1.0
      WHEN prev_trwp.away_rolling_win_pct - prev_trwp.home_rolling_win_pct > 0.2
        AND prev_g.winner_team_id != prev_g.home_team_id THEN 0.0
      ELSE NULL
    END) AS home_upset_rate,
    -- Upset game count
    COUNT(CASE WHEN prev_trwp.away_rolling_win_pct - prev_trwp.home_rolling_win_pct > 0.2 THEN 1 END) AS home_upset_game_count
  FROM raw_dev.games curr
  LEFT JOIN completed_games prev_g ON 
    (prev_g.home_team_id = curr.home_team_id OR prev_g.away_team_id = curr.home_team_id)
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= curr.game_date::date - INTERVAL '90 days'  -- Last 90 days for recent signal
  LEFT JOIN team_rolling_win_pct prev_trwp ON prev_trwp.game_id = prev_g.game_id
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
    AND curr.game_date < @end_ds  -- Only process games up to end of chunk
  GROUP BY curr.game_id, curr.game_date, curr.home_team_id
),

-- Calculate away team's performance when favored vs underdog
away_favored_underdog_performance AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.away_team_id AS team_id,
    -- Win percentage when favored (away team had higher rolling win_pct than opponent)
    AVG(CASE 
      WHEN prev_trwp.away_rolling_win_pct > prev_trwp.home_rolling_win_pct 
        AND prev_g.winner_team_id = prev_g.away_team_id THEN 1.0
      WHEN prev_trwp.away_rolling_win_pct > prev_trwp.home_rolling_win_pct 
        AND prev_g.winner_team_id != prev_g.away_team_id THEN 0.0
      ELSE NULL
    END) AS away_win_pct_when_favored,
    -- Game count when favored
    COUNT(CASE WHEN prev_trwp.away_rolling_win_pct > prev_trwp.home_rolling_win_pct THEN 1 END) AS away_favored_game_count,
    -- Win percentage when underdog
    AVG(CASE 
      WHEN prev_trwp.away_rolling_win_pct < prev_trwp.home_rolling_win_pct 
        AND prev_g.winner_team_id = prev_g.away_team_id THEN 1.0
      WHEN prev_trwp.away_rolling_win_pct < prev_trwp.home_rolling_win_pct 
        AND prev_g.winner_team_id != prev_g.away_team_id THEN 0.0
      ELSE NULL
    END) AS away_win_pct_when_underdog,
    -- Game count when underdog
    COUNT(CASE WHEN prev_trwp.away_rolling_win_pct < prev_trwp.home_rolling_win_pct THEN 1 END) AS away_underdog_game_count,
    -- Average point differential when favored
    AVG(CASE 
      WHEN prev_trwp.away_rolling_win_pct > prev_trwp.home_rolling_win_pct 
        THEN prev_g.away_score - prev_g.home_score
      ELSE NULL
    END) AS away_avg_point_diff_when_favored,
    -- Average point differential when underdog
    AVG(CASE 
      WHEN prev_trwp.away_rolling_win_pct < prev_trwp.home_rolling_win_pct 
        THEN prev_g.away_score - prev_g.home_score
      ELSE NULL
    END) AS away_avg_point_diff_when_underdog,
    -- Upset rate: win rate when significantly underdog
    AVG(CASE 
      WHEN prev_trwp.home_rolling_win_pct - prev_trwp.away_rolling_win_pct > 0.2
        AND prev_g.winner_team_id = prev_g.away_team_id THEN 1.0
      WHEN prev_trwp.home_rolling_win_pct - prev_trwp.away_rolling_win_pct > 0.2
        AND prev_g.winner_team_id != prev_g.away_team_id THEN 0.0
      ELSE NULL
    END) AS away_upset_rate,
    -- Upset game count
    COUNT(CASE WHEN prev_trwp.home_rolling_win_pct - prev_trwp.away_rolling_win_pct > 0.2 THEN 1 END) AS away_upset_game_count
  FROM raw_dev.games curr
  LEFT JOIN completed_games prev_g ON 
    (prev_g.home_team_id = curr.away_team_id OR prev_g.away_team_id = curr.away_team_id)
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= curr.game_date::date - INTERVAL '90 days'  -- Last 90 days for recent signal
  LEFT JOIN team_rolling_win_pct prev_trwp ON prev_trwp.game_id = prev_g.game_id
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
  GROUP BY curr.game_id, curr.game_date, curr.away_team_id
)

SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  -- Home team favored/underdog performance
  COALESCE(hfup.home_win_pct_when_favored, 0.5) AS home_win_pct_when_favored,
  COALESCE(hfup.home_favored_game_count, 0) AS home_favored_game_count,
  COALESCE(hfup.home_win_pct_when_underdog, 0.5) AS home_win_pct_when_underdog,
  COALESCE(hfup.home_underdog_game_count, 0) AS home_underdog_game_count,
  COALESCE(hfup.home_avg_point_diff_when_favored, 0.0) AS home_avg_point_diff_when_favored,
  COALESCE(hfup.home_avg_point_diff_when_underdog, 0.0) AS home_avg_point_diff_when_underdog,
  COALESCE(hfup.home_upset_rate, 0.0) AS home_upset_rate,
  COALESCE(hfup.home_upset_game_count, 0) AS home_upset_game_count,
  -- Away team favored/underdog performance
  COALESCE(afup.away_win_pct_when_favored, 0.5) AS away_win_pct_when_favored,
  COALESCE(afup.away_favored_game_count, 0) AS away_favored_game_count,
  COALESCE(afup.away_win_pct_when_underdog, 0.5) AS away_win_pct_when_underdog,
  COALESCE(afup.away_underdog_game_count, 0) AS away_underdog_game_count,
  COALESCE(afup.away_avg_point_diff_when_favored, 0.0) AS away_avg_point_diff_when_favored,
  COALESCE(afup.away_avg_point_diff_when_underdog, 0.0) AS away_avg_point_diff_when_underdog,
  COALESCE(afup.away_upset_rate, 0.0) AS away_upset_rate,
  COALESCE(afup.away_upset_game_count, 0) AS away_upset_game_count
FROM raw_dev.games g
LEFT JOIN home_favored_underdog_performance hfup ON 
  hfup.game_id = g.game_id
  AND hfup.team_id = g.home_team_id
LEFT JOIN away_favored_underdog_performance afup ON 
  afup.game_id = g.game_id
  AND afup.team_id = g.away_team_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds;
