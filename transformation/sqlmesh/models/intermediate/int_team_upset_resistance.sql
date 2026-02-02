MODEL (
  name intermediate.int_team_upset_resistance,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate upset resistance: teams that rarely lose when favored, frequently win when underdogs
WITH completed_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    g.home_score,
    g.away_score,
    g.winner_team_id,
    CASE WHEN g.winner_team_id = g.home_team_id THEN 1 ELSE 0 END AS home_win
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.winner_team_id IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Get rolling win_pct for each team at each game date (for determining favorite/underdog)
team_rolling_win_pct AS (
  SELECT 
    r.team_id,
    r.game_date::date AS game_date,
    r.rolling_10_win_pct
  FROM intermediate.int_team_rolling_stats r
  WHERE r.rolling_10_win_pct IS NOT NULL
),

-- Calculate home team's upset resistance metrics
home_upset_resistance AS (
  SELECT 
    curr.game_id,
    curr.game_date,
    curr.home_team_id AS team_id,
    -- Win percentage when home team is favorite (should be high - rarely lose when favored)
    AVG(CASE 
      WHEN home_r.rolling_10_win_pct > away_r.rolling_10_win_pct AND prev_g.home_team_id = curr.home_team_id THEN prev_g.home_win::float
      WHEN home_r.rolling_10_win_pct < away_r.rolling_10_win_pct AND prev_g.away_team_id = curr.home_team_id THEN (1 - prev_g.home_win)::float
      ELSE NULL
    END) AS home_favorite_win_pct,
    -- Sample size when favorite
    COUNT(CASE 
      WHEN (home_r.rolling_10_win_pct > away_r.rolling_10_win_pct AND prev_g.home_team_id = curr.home_team_id)
        OR (home_r.rolling_10_win_pct < away_r.rolling_10_win_pct AND prev_g.away_team_id = curr.home_team_id)
      THEN 1 END) AS home_favorite_game_count,
    -- Win percentage when home team is underdog (upset rate - should be moderate/high)
    AVG(CASE 
      WHEN home_r.rolling_10_win_pct < away_r.rolling_10_win_pct AND prev_g.home_team_id = curr.home_team_id THEN prev_g.home_win::float
      WHEN home_r.rolling_10_win_pct > away_r.rolling_10_win_pct AND prev_g.away_team_id = curr.home_team_id THEN (1 - prev_g.home_win)::float
      ELSE NULL
    END) AS home_underdog_win_pct,
    -- Sample size when underdog
    COUNT(CASE 
      WHEN (home_r.rolling_10_win_pct < away_r.rolling_10_win_pct AND prev_g.home_team_id = curr.home_team_id)
        OR (home_r.rolling_10_win_pct > away_r.rolling_10_win_pct AND prev_g.away_team_id = curr.home_team_id)
      THEN 1 END) AS home_underdog_game_count,
    -- Upset resistance score: (favorite_win_pct - 0.5) + (underdog_win_pct - 0.3)
    -- Higher = better (wins when favored, wins when underdog)
    (AVG(CASE 
      WHEN home_r.rolling_10_win_pct > away_r.rolling_10_win_pct AND prev_g.home_team_id = curr.home_team_id THEN prev_g.home_win::float
      WHEN home_r.rolling_10_win_pct < away_r.rolling_10_win_pct AND prev_g.away_team_id = curr.home_team_id THEN (1 - prev_g.home_win)::float
      ELSE NULL
    END) - 0.5) + 
    (AVG(CASE 
      WHEN home_r.rolling_10_win_pct < away_r.rolling_10_win_pct AND prev_g.home_team_id = curr.home_team_id THEN prev_g.home_win::float
      WHEN home_r.rolling_10_win_pct > away_r.rolling_10_win_pct AND prev_g.away_team_id = curr.home_team_id THEN (1 - prev_g.home_win)::float
      ELSE NULL
    END) - 0.3) AS home_upset_resistance_score
  FROM raw_dev.games curr
  LEFT JOIN completed_games prev_g ON 
    (prev_g.home_team_id = curr.home_team_id OR prev_g.away_team_id = curr.home_team_id)
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= DATE_TRUNC('year', curr.game_date::date)
  LEFT JOIN team_rolling_win_pct home_r ON 
    home_r.team_id = prev_g.home_team_id
    AND home_r.game_date = prev_g.game_date
  LEFT JOIN team_rolling_win_pct away_r ON 
    away_r.team_id = prev_g.away_team_id
    AND away_r.game_date = prev_g.game_date
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
    AND home_r.rolling_10_win_pct IS NOT NULL
    AND away_r.rolling_10_win_pct IS NOT NULL
    AND curr.game_date < @end_ds  -- Only process games up to end of chunk
  GROUP BY curr.game_id, curr.game_date, curr.home_team_id
),

-- Calculate away team's upset resistance metrics
away_upset_resistance AS (
  SELECT 
    curr.game_id,
    curr.game_date,
    curr.away_team_id AS team_id,
    -- Win percentage when away team is favorite
    AVG(CASE 
      WHEN home_r.rolling_10_win_pct < away_r.rolling_10_win_pct AND prev_g.home_team_id = curr.away_team_id THEN prev_g.home_win::float
      WHEN home_r.rolling_10_win_pct > away_r.rolling_10_win_pct AND prev_g.away_team_id = curr.away_team_id THEN (1 - prev_g.home_win)::float
      ELSE NULL
    END) AS away_favorite_win_pct,
    -- Sample size when favorite
    COUNT(CASE 
      WHEN (home_r.rolling_10_win_pct < away_r.rolling_10_win_pct AND prev_g.home_team_id = curr.away_team_id)
        OR (home_r.rolling_10_win_pct > away_r.rolling_10_win_pct AND prev_g.away_team_id = curr.away_team_id)
      THEN 1 END) AS away_favorite_game_count,
    -- Win percentage when away team is underdog
    AVG(CASE 
      WHEN home_r.rolling_10_win_pct > away_r.rolling_10_win_pct AND prev_g.home_team_id = curr.away_team_id THEN prev_g.home_win::float
      WHEN home_r.rolling_10_win_pct < away_r.rolling_10_win_pct AND prev_g.away_team_id = curr.away_team_id THEN (1 - prev_g.home_win)::float
      ELSE NULL
    END) AS away_underdog_win_pct,
    -- Sample size when underdog
    COUNT(CASE 
      WHEN (home_r.rolling_10_win_pct > away_r.rolling_10_win_pct AND prev_g.home_team_id = curr.away_team_id)
        OR (home_r.rolling_10_win_pct < away_r.rolling_10_win_pct AND prev_g.away_team_id = curr.away_team_id)
      THEN 1 END) AS away_underdog_game_count,
    -- Upset resistance score
    (AVG(CASE 
      WHEN home_r.rolling_10_win_pct < away_r.rolling_10_win_pct AND prev_g.home_team_id = curr.away_team_id THEN prev_g.home_win::float
      WHEN home_r.rolling_10_win_pct > away_r.rolling_10_win_pct AND prev_g.away_team_id = curr.away_team_id THEN (1 - prev_g.home_win)::float
      ELSE NULL
    END) - 0.5) + 
    (AVG(CASE 
      WHEN home_r.rolling_10_win_pct > away_r.rolling_10_win_pct AND prev_g.home_team_id = curr.away_team_id THEN prev_g.home_win::float
      WHEN home_r.rolling_10_win_pct < away_r.rolling_10_win_pct AND prev_g.away_team_id = curr.away_team_id THEN (1 - prev_g.home_win)::float
      ELSE NULL
    END) - 0.3) AS away_upset_resistance_score
  FROM raw_dev.games curr
  LEFT JOIN completed_games prev_g ON 
    (prev_g.home_team_id = curr.away_team_id OR prev_g.away_team_id = curr.away_team_id)
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= DATE_TRUNC('year', curr.game_date::date)
  LEFT JOIN team_rolling_win_pct home_r ON 
    home_r.team_id = prev_g.home_team_id
    AND home_r.game_date = prev_g.game_date
  LEFT JOIN team_rolling_win_pct away_r ON 
    away_r.team_id = prev_g.away_team_id
    AND away_r.game_date = prev_g.game_date
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
    AND home_r.rolling_10_win_pct IS NOT NULL
    AND away_r.rolling_10_win_pct IS NOT NULL
    AND curr.game_date < @end_ds  -- Only process games up to end of chunk
  GROUP BY curr.game_id, curr.game_date, curr.away_team_id
)

SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  -- Home team upset resistance
  COALESCE(hur.home_favorite_win_pct, 0.5) AS home_favorite_win_pct,
  COALESCE(hur.home_favorite_game_count, 0) AS home_favorite_game_count,
  COALESCE(hur.home_underdog_win_pct, 0.3) AS home_underdog_win_pct,
  COALESCE(hur.home_underdog_game_count, 0) AS home_underdog_game_count,
  COALESCE(hur.home_upset_resistance_score, 0.0) AS home_upset_resistance_score,
  -- Away team upset resistance
  COALESCE(aur.away_favorite_win_pct, 0.5) AS away_favorite_win_pct,
  COALESCE(aur.away_favorite_game_count, 0) AS away_favorite_game_count,
  COALESCE(aur.away_underdog_win_pct, 0.3) AS away_underdog_win_pct,
  COALESCE(aur.away_underdog_game_count, 0) AS away_underdog_game_count,
  COALESCE(aur.away_upset_resistance_score, 0.0) AS away_upset_resistance_score
FROM raw_dev.games g
LEFT JOIN home_upset_resistance hur ON 
  hur.game_id = g.game_id
  AND hur.team_id = g.home_team_id
LEFT JOIN away_upset_resistance aur ON 
  aur.game_id = g.game_id
  AND aur.team_id = g.away_team_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds;
