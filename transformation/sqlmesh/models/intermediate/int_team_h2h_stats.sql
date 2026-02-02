MODEL (
  name intermediate.int_team_h2h_stats,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

WITH completed_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    g.winner_team_id,
    g.home_score,
    g.away_score,
    -- Calculate point differential from home team's perspective
    g.home_score - g.away_score AS home_point_diff
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Get all historical matchups before each game
historical_matchups AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.home_team_id,
    curr.away_team_id,
    prev.game_date AS prev_game_date,
    prev.winner_team_id,
    -- Determine if current home team won in previous matchup
    -- Need to account for whether prev game had same home/away or swapped
    CASE 
      WHEN prev.home_team_id = curr.home_team_id AND prev.away_team_id = curr.away_team_id THEN
        CASE WHEN prev.winner_team_id = curr.home_team_id THEN 1 ELSE 0 END
      WHEN prev.home_team_id = curr.away_team_id AND prev.away_team_id = curr.home_team_id THEN
        CASE WHEN prev.winner_team_id = curr.away_team_id THEN 1 ELSE 0 END
      ELSE 0
    END AS home_win,
    CASE 
      WHEN prev.home_team_id = curr.home_team_id AND prev.away_team_id = curr.away_team_id THEN
        CASE WHEN prev.winner_team_id = curr.away_team_id THEN 1 ELSE 0 END
      WHEN prev.home_team_id = curr.away_team_id AND prev.away_team_id = curr.home_team_id THEN
        CASE WHEN prev.winner_team_id = curr.home_team_id THEN 1 ELSE 0 END
      ELSE 0
    END AS away_win,
    -- Point differential from current home team's perspective
    CASE 
      WHEN prev.home_team_id = curr.home_team_id AND prev.away_team_id = curr.away_team_id THEN
        prev.home_score - prev.away_score
      WHEN prev.home_team_id = curr.away_team_id AND prev.away_team_id = curr.home_team_id THEN
        prev.away_score - prev.home_score
      ELSE 0
    END AS home_point_diff
  FROM raw_dev.games curr
  LEFT JOIN completed_games prev ON (
    (prev.home_team_id = curr.home_team_id AND prev.away_team_id = curr.away_team_id)
    OR (prev.home_team_id = curr.away_team_id AND prev.away_team_id = curr.home_team_id)
  )
  AND prev.game_date < curr.game_date::date
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
    AND curr.game_date < @end_ds  -- Only process games up to end of chunk
)

SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  -- Head-to-head stats (all time before this game)
  COALESCE(h2h_all.home_h2h_wins, 0) AS home_h2h_wins,
  COALESCE(h2h_all.away_h2h_wins, 0) AS away_h2h_wins,
  COALESCE(h2h_all.h2h_total_games, 0) AS h2h_total_games,
  -- Head-to-head win percentage (home team's historical win % vs this opponent)
  CASE 
    WHEN COALESCE(h2h_all.h2h_total_games, 0) > 0 THEN
      COALESCE(h2h_all.home_h2h_wins, 0)::float / h2h_all.h2h_total_games
    ELSE 0.5  -- Default to 50% if no history
  END AS home_h2h_win_pct,
  -- Recent trend: home team wins in last 5 meetings (before this game)
  COALESCE(h2h_recent.home_h2h_recent_wins, 0) AS home_h2h_recent_wins,
  -- Recent H2H win percentage (last 3 matchups)
  CASE 
    WHEN COALESCE(h2h_recent_3.h2h_recent_3_games, 0) > 0 THEN
      COALESCE(h2h_recent_3.home_h2h_recent_3_wins, 0)::float / h2h_recent_3.h2h_recent_3_games
    ELSE 0.5  -- Default to 50% if insufficient history
  END AS home_h2h_win_pct_3,
  -- Recent H2H win percentage (last 5 matchups)
  CASE 
    WHEN COALESCE(h2h_recent_5.h2h_recent_5_games, 0) > 0 THEN
      COALESCE(h2h_recent_5.home_h2h_recent_5_wins, 0)::float / h2h_recent_5.h2h_recent_5_games
    ELSE 0.5  -- Default to 50% if insufficient history
  END AS home_h2h_win_pct_5,
  -- Average point differential in last 3 matchups (from home team's perspective)
  COALESCE(h2h_recent_3.avg_point_diff_3, 0.0) AS home_h2h_avg_point_diff_3,
  -- Average point differential in last 5 matchups (from home team's perspective)
  COALESCE(h2h_recent_5.avg_point_diff_5, 0.0) AS home_h2h_avg_point_diff_5,
  -- H2H momentum: trend in recent games (positive = home team winning more recently)
  -- Calculated as (wins in last 3) - (wins in games 4-6) to capture recent trend
  COALESCE(h2h_momentum.momentum_score, 0.0) AS home_h2h_momentum
FROM raw_dev.games g
LEFT JOIN (
  SELECT 
    game_id,
    SUM(home_win) AS home_h2h_wins,
    SUM(away_win) AS away_h2h_wins,
    COUNT(*) AS h2h_total_games
  FROM historical_matchups
  GROUP BY game_id
) h2h_all ON h2h_all.game_id = g.game_id
LEFT JOIN (
  SELECT 
    game_id,
    SUM(home_win) AS home_h2h_recent_wins
  FROM (
    SELECT 
      hm.game_id,
      hm.home_win,
      ROW_NUMBER() OVER (PARTITION BY hm.game_id ORDER BY hm.prev_game_date DESC) as rn
    FROM historical_matchups hm
  ) ranked
  WHERE rn <= 5
  GROUP BY game_id
) h2h_recent ON h2h_recent.game_id = g.game_id
LEFT JOIN (
  SELECT 
    game_id,
    SUM(home_win) AS home_h2h_recent_3_wins,
    COUNT(*) AS h2h_recent_3_games,
    AVG(home_point_diff) AS avg_point_diff_3
  FROM (
    SELECT 
      hm.game_id,
      hm.home_win,
      hm.home_point_diff,
      ROW_NUMBER() OVER (PARTITION BY hm.game_id ORDER BY hm.prev_game_date DESC) as rn
    FROM historical_matchups hm
  ) ranked
  WHERE rn <= 3
  GROUP BY game_id
) h2h_recent_3 ON h2h_recent_3.game_id = g.game_id
LEFT JOIN (
  SELECT 
    game_id,
    SUM(home_win) AS home_h2h_recent_5_wins,
    COUNT(*) AS h2h_recent_5_games,
    AVG(home_point_diff) AS avg_point_diff_5
  FROM (
    SELECT 
      hm.game_id,
      hm.home_win,
      hm.home_point_diff,
      ROW_NUMBER() OVER (PARTITION BY hm.game_id ORDER BY hm.prev_game_date DESC) as rn
    FROM historical_matchups hm
  ) ranked
  WHERE rn <= 5
  GROUP BY game_id
) h2h_recent_5 ON h2h_recent_5.game_id = g.game_id
LEFT JOIN (
  SELECT 
    game_id,
    -- Momentum: (wins in last 3) - (wins in games 4-6) to capture recent trend
    -- Positive = home team winning more recently, negative = away team winning more recently
    COALESCE(SUM(CASE WHEN rn <= 3 THEN home_win ELSE -home_win END), 0.0) AS momentum_score
  FROM (
    SELECT 
      hm.game_id,
      hm.home_win,
      ROW_NUMBER() OVER (PARTITION BY hm.game_id ORDER BY hm.prev_game_date DESC) as rn
    FROM historical_matchups hm
  ) ranked
  WHERE rn <= 6
  GROUP BY game_id
) h2h_momentum ON h2h_momentum.game_id = g.game_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds;
