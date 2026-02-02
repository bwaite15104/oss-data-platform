MODEL (
  name intermediate.int_away_upset_tend,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date,
    batch_size 1  -- One day per batch to keep each run short
  ),
  start '1946-11-01',  -- NBA BAA start for full history backfill
  grains [
    game_id
  ],
  cron '@daily',
);

-- OPTIMIZED VERSION: Uses window functions instead of self-join (O(n log n) vs O(n^2))
-- Calculate away team's historical tendency to win as underdogs
-- An "upset" is when the away team wins despite having a lower win percentage than the home team

-- Step 1: Get base game data with win percentages (only for chunk + enough context)
WITH games_chunk AS NOT MATERIALIZED (
  SELECT game_id FROM staging.stg_games 
  WHERE game_date >= @start_ds AND game_date < @end_ds AND is_completed
),

-- Get all completed games with underdog indicators (we need full history for cumulative sums)
all_games_with_indicators AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    g.winner_team_id,
    -- Get season win percentages for determining underdog status
    COALESCE(hs.win_pct, 0.5) AS home_season_win_pct,
    COALESCE(aws.win_pct, 0.5) AS away_season_win_pct,
    -- Get rolling win percentages for recent form
    COALESCE(rs_home.rolling_10_win_pct, 0.5) AS home_rolling_10_win_pct,
    COALESCE(rs_away.rolling_10_win_pct, 0.5) AS away_rolling_10_win_pct,
    -- Season-based: Is away team the underdog? (lower season win_pct)
    CASE WHEN COALESCE(aws.win_pct, 0.5) < COALESCE(hs.win_pct, 0.5) THEN 1 ELSE 0 END AS is_underdog_season,
    -- Season-based: Did away team win when underdog?
    CASE WHEN COALESCE(aws.win_pct, 0.5) < COALESCE(hs.win_pct, 0.5) 
              AND g.winner_team_id = g.away_team_id THEN 1 ELSE 0 END AS is_underdog_win_season,
    -- Rolling-based: Is away team the underdog? (lower rolling win_pct)
    CASE WHEN COALESCE(rs_away.rolling_10_win_pct, 0.5) < COALESCE(rs_home.rolling_10_win_pct, 0.5) THEN 1 ELSE 0 END AS is_underdog_rolling,
    -- Rolling-based: Did away team win when underdog?
    CASE WHEN COALESCE(rs_away.rolling_10_win_pct, 0.5) < COALESCE(rs_home.rolling_10_win_pct, 0.5) 
              AND g.winner_team_id = g.away_team_id THEN 1 ELSE 0 END AS is_underdog_win_rolling
  FROM staging.stg_games g
  LEFT JOIN intermediate.int_team_season_stats hs ON hs.team_id = g.home_team_id
  LEFT JOIN intermediate.int_team_season_stats aws ON aws.team_id = g.away_team_id
  LEFT JOIN intermediate.int_team_rolling_stats rs_home 
    ON rs_home.team_id = g.home_team_id 
    AND rs_home.game_date = g.game_date::date
  LEFT JOIN intermediate.int_team_rolling_stats rs_away 
    ON rs_away.team_id = g.away_team_id 
    AND rs_away.game_date = g.game_date::date
  WHERE g.is_completed
    AND g.game_date < @end_ds  -- All games up to end of chunk for cumulative calculation
),

-- Step 2: Calculate cumulative upset stats using window functions (much faster than self-join)
cumulative_stats AS (
  SELECT 
    game_id,
    game_date,
    away_team_id,
    -- Cumulative underdog games BEFORE this game (exclude current row)
    COALESCE(SUM(is_underdog_season) OVER (
      PARTITION BY away_team_id 
      ORDER BY game_date, game_id 
      ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ), 0) AS away_underdog_games,
    -- Cumulative underdog wins BEFORE this game
    COALESCE(SUM(is_underdog_win_season) OVER (
      PARTITION BY away_team_id 
      ORDER BY game_date, game_id 
      ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ), 0) AS away_underdog_wins,
    -- Rolling-based cumulative stats
    COALESCE(SUM(is_underdog_rolling) OVER (
      PARTITION BY away_team_id 
      ORDER BY game_date, game_id 
      ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ), 0) AS away_underdog_games_rolling,
    COALESCE(SUM(is_underdog_win_rolling) OVER (
      PARTITION BY away_team_id 
      ORDER BY game_date, game_id 
      ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
    ), 0) AS away_underdog_wins_rolling
  FROM all_games_with_indicators
)

-- Step 3: Calculate upset tendency rates (only for games in the chunk)
SELECT 
  cs.game_id,
  cs.game_date,
  cs.away_team_id,
  -- Season-based upset tendency (historical win rate when underdog by season stats)
  CASE 
    WHEN cs.away_underdog_games >= 5 THEN
      cs.away_underdog_wins::float / NULLIF(cs.away_underdog_games, 0)
    ELSE 0.3  -- Default to 30% (league average for away wins) if insufficient history
  END AS away_upset_tendency_season,
  -- Rolling-based upset tendency (historical win rate when underdog by recent form)
  CASE 
    WHEN cs.away_underdog_games_rolling >= 5 THEN
      cs.away_underdog_wins_rolling::float / NULLIF(cs.away_underdog_games_rolling, 0)
    ELSE 0.3  -- Default to 30% if insufficient history
  END AS away_upset_tendency_rolling,
  -- Sample size indicators (for model to weight these features)
  LEAST(cs.away_underdog_games, 50)::int AS away_upset_sample_size_season,
  LEAST(cs.away_underdog_games_rolling, 50)::int AS away_upset_sample_size_rolling
FROM cumulative_stats cs
INNER JOIN games_chunk gc ON gc.game_id = cs.game_id
WHERE cs.game_date >= @start_ds
  AND cs.game_date < @end_ds;
