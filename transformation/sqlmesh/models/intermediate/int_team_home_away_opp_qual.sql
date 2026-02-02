MODEL (
  name intermediate.int_team_home_away_opp_qual,
  description 'Home/away win pct split by opponent quality (strong vs weak). Short name for Postgres 63-char limit.',
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate home/away win percentages split by opponent quality (strong vs weak)
-- Strong opponent = rolling 10-game win_pct > 0.5
-- Weak opponent = rolling 10-game win_pct <= 0.5
-- Uses int_team_roll_lkp for opponent quality (no LATERAL/correlated subqueries).
WITH completed_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    g.winner_team_id
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Home team's performance at home vs strong opponents (before this game)
home_vs_strong AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.home_team_id AS team_id,
    AVG(CASE 
      WHEN prev_g.winner_team_id = curr.home_team_id THEN 1.0
      ELSE 0.0
    END) AS home_win_pct_vs_strong,
    COUNT(*) AS home_games_vs_strong
  FROM raw_dev.games curr
  LEFT JOIN completed_games prev_g ON 
    prev_g.home_team_id = curr.home_team_id
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= DATE_TRUNC('year', curr.game_date::date)
  LEFT JOIN intermediate.int_team_roll_lkp prev_opp_quality
    ON prev_opp_quality.game_id = prev_g.game_id
    AND prev_opp_quality.team_id = prev_g.away_team_id
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
    AND prev_opp_quality.rolling_10_win_pct > 0.5  -- Strong opponent (away team was strong)
    AND curr.game_date >= @start_ds
    AND curr.game_date < @end_ds
  GROUP BY curr.game_id, curr.game_date, curr.home_team_id
),

-- Home team's performance at home vs weak opponents (before this game)
home_vs_weak AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.home_team_id AS team_id,
    AVG(CASE 
      WHEN prev_g.winner_team_id = curr.home_team_id THEN 1.0
      ELSE 0.0
    END) AS home_win_pct_vs_weak,
    COUNT(*) AS home_games_vs_weak
  FROM raw_dev.games curr
  LEFT JOIN completed_games prev_g ON 
    prev_g.home_team_id = curr.home_team_id
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= DATE_TRUNC('year', curr.game_date::date)
  LEFT JOIN intermediate.int_team_roll_lkp prev_opp_quality
    ON prev_opp_quality.game_id = prev_g.game_id
    AND prev_opp_quality.team_id = prev_g.away_team_id
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
    AND (prev_opp_quality.rolling_10_win_pct <= 0.5 OR prev_opp_quality.rolling_10_win_pct IS NULL)  -- Weak opponent
    AND curr.game_date >= @start_ds
    AND curr.game_date < @end_ds
  GROUP BY curr.game_id, curr.game_date, curr.home_team_id
),

-- Away team's performance on road vs strong opponents (before this game)
away_vs_strong AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.away_team_id AS team_id,
    AVG(CASE 
      WHEN prev_g.winner_team_id = curr.away_team_id THEN 1.0
      ELSE 0.0
    END) AS away_win_pct_vs_strong,
    COUNT(*) AS away_games_vs_strong
  FROM raw_dev.games curr
  LEFT JOIN completed_games prev_g ON 
    prev_g.away_team_id = curr.away_team_id
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= DATE_TRUNC('year', curr.game_date::date)
  LEFT JOIN intermediate.int_team_roll_lkp prev_opp_quality
    ON prev_opp_quality.game_id = prev_g.game_id
    AND prev_opp_quality.team_id = prev_g.home_team_id
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
    AND prev_opp_quality.rolling_10_win_pct > 0.5  -- Strong opponent (home team was strong)
    AND curr.game_date >= @start_ds
    AND curr.game_date < @end_ds
  GROUP BY curr.game_id, curr.game_date, curr.away_team_id
),

-- Away team's performance on road vs weak opponents (before this game)
away_vs_weak AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.away_team_id AS team_id,
    AVG(CASE 
      WHEN prev_g.winner_team_id = curr.away_team_id THEN 1.0
      ELSE 0.0
    END) AS away_win_pct_vs_weak,
    COUNT(*) AS away_games_vs_weak
  FROM raw_dev.games curr
  LEFT JOIN completed_games prev_g ON 
    prev_g.away_team_id = curr.away_team_id
    AND prev_g.game_date < curr.game_date::date
    AND prev_g.game_date >= DATE_TRUNC('year', curr.game_date::date)
  LEFT JOIN intermediate.int_team_roll_lkp prev_opp_quality
    ON prev_opp_quality.game_id = prev_g.game_id
    AND prev_opp_quality.team_id = prev_g.home_team_id
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
    AND (prev_opp_quality.rolling_10_win_pct <= 0.5 OR prev_opp_quality.rolling_10_win_pct IS NULL)  -- Weak opponent
    AND curr.game_date >= @start_ds
    AND curr.game_date < @end_ds
  GROUP BY curr.game_id, curr.game_date, curr.away_team_id
)

SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  -- Home team's win % at home vs strong opponents (default to 0.5 if insufficient data)
  COALESCE(home_strong.home_win_pct_vs_strong, 0.5) AS home_win_pct_vs_strong,
  -- Home team's win % at home vs weak opponents
  COALESCE(home_weak.home_win_pct_vs_weak, 0.5) AS home_win_pct_vs_weak,
  -- Away team's win % on road vs strong opponents
  COALESCE(away_strong.away_win_pct_vs_strong, 0.5) AS away_win_pct_vs_strong,
  -- Away team's win % on road vs weak opponents
  COALESCE(away_weak.away_win_pct_vs_weak, 0.5) AS away_win_pct_vs_weak
FROM raw_dev.games g
LEFT JOIN home_vs_strong home_strong ON 
  home_strong.game_id = g.game_id
  AND home_strong.team_id = g.home_team_id
LEFT JOIN home_vs_weak home_weak ON 
  home_weak.game_id = g.game_id
  AND home_weak.team_id = g.home_team_id
LEFT JOIN away_vs_strong away_strong ON 
  away_strong.game_id = g.game_id
  AND away_strong.team_id = g.away_team_id
LEFT JOIN away_vs_weak away_weak ON 
  away_weak.game_id = g.game_id
  AND away_weak.team_id = g.away_team_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds;
