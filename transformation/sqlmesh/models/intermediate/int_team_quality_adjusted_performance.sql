MODEL (
  name intermediate.int_team_quality_adj_perf,
  description 'Quality-adjusted team performance. Short name for Postgres 63-char limit.',
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
  description 'Quality-adjusted recent performance features that capture the strength of recent wins and losses, not just win percentage. Helps distinguish teams that beat strong opponents from teams that only beat weak opponents.'
);

-- Calculate quality-adjusted performance metrics
-- This captures not just win percentage, but the quality of opponents in recent games
WITH completed_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    g.winner_team_id,
    g.home_score,
    g.away_score,
    g.home_score - g.away_score AS home_point_diff
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Get rolling win_pct for each team at each game date to determine opponent strength
team_rolling_win_pct AS (
  SELECT 
    rs.team_id,
    rs.game_date::date AS game_date,
    rs.rolling_10_win_pct
  FROM intermediate.int_team_rolling_stats rs
  WHERE rs.rolling_10_win_pct IS NOT NULL
),

-- For each team's recent games, calculate opponent strength and outcomes
team_recent_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id AS team_id,
    g.away_team_id AS opponent_id,
    CASE WHEN g.winner_team_id = g.home_team_id THEN 1 ELSE 0 END AS team_won,
    g.home_score - g.away_score AS team_point_diff,
    away_win_pct.rolling_10_win_pct AS opponent_strength
  FROM completed_games g
  LEFT JOIN team_rolling_win_pct away_win_pct ON 
    away_win_pct.team_id = g.away_team_id
    AND away_win_pct.game_date = g.game_date::date
  
  UNION ALL
  
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.away_team_id AS team_id,
    g.home_team_id AS opponent_id,
    CASE WHEN g.winner_team_id = g.away_team_id THEN 1 ELSE 0 END AS team_won,
    g.away_score - g.home_score AS team_point_diff,
    home_win_pct.rolling_10_win_pct AS opponent_strength
  FROM completed_games g
  LEFT JOIN team_rolling_win_pct home_win_pct ON 
    home_win_pct.team_id = g.home_team_id
    AND home_win_pct.game_date = g.game_date::date
),

-- Rank recent games for each team to get last N games
ranked_recent_games AS (
  SELECT 
    trg.*,
    curr.game_id AS current_game_id,
    curr.game_date::date AS current_game_date,
    curr.home_team_id,
    curr.away_team_id,
    ROW_NUMBER() OVER (
      PARTITION BY curr.game_id, trg.team_id 
      ORDER BY trg.game_date DESC
    ) AS game_rank
  FROM completed_games curr
  INNER JOIN team_recent_games trg ON 
    trg.team_id IN (curr.home_team_id, curr.away_team_id)
    AND trg.game_date < curr.game_date::date
    AND trg.opponent_strength IS NOT NULL
    AND trg.game_date >= curr.game_date::date - INTERVAL '30 days'
    AND curr.game_date < @end_ds  -- Only process games up to end of chunk
),

-- Calculate home team's quality-adjusted performance (last 5 games before this game)
home_quality_adjusted AS (
  SELECT 
    current_game_id AS game_id,
    current_game_date AS game_date,
    team_id AS home_team_id,
    -- Average opponent strength in recent wins (quality of wins)
    AVG(CASE 
      WHEN team_won = 1 THEN opponent_strength
      ELSE NULL
    END) AS home_avg_opponent_strength_in_wins_5,
    -- Average opponent strength in recent losses (quality of losses)
    AVG(CASE 
      WHEN team_won = 0 THEN opponent_strength
      ELSE NULL
    END) AS home_avg_opponent_strength_in_losses_5,
    -- Average opponent strength overall (all recent games)
    AVG(opponent_strength) AS home_avg_opponent_strength_5,
    -- Quality-adjusted win percentage: weight wins by opponent strength
    CASE 
      WHEN SUM(opponent_strength) > 0 THEN
        SUM(CASE WHEN team_won = 1 THEN opponent_strength ELSE 0.0 END) / SUM(opponent_strength)
      ELSE 0.5
    END AS home_quality_adjusted_win_pct_5,
    -- Average point differential in wins
    AVG(CASE 
      WHEN team_won = 1 THEN team_point_diff
      ELSE NULL
    END) AS home_avg_point_diff_in_wins_5,
    -- Average point differential in losses
    AVG(CASE 
      WHEN team_won = 0 THEN team_point_diff
      ELSE NULL
    END) AS home_avg_point_diff_in_losses_5,
    -- Game counts
    COUNT(CASE WHEN team_won = 1 THEN 1 END) AS home_wins_count_5,
    COUNT(CASE WHEN team_won = 0 THEN 1 END) AS home_losses_count_5,
    COUNT(*) AS home_total_games_5
  FROM ranked_recent_games
  WHERE game_rank <= 5
    AND team_id = home_team_id
  GROUP BY current_game_id, current_game_date, team_id, home_team_id
),

-- Rank recent games for 10-game window
ranked_recent_games_10 AS (
  SELECT 
    trg.*,
    curr.game_id AS current_game_id,
    curr.game_date::date AS current_game_date,
    curr.home_team_id,
    curr.away_team_id,
    ROW_NUMBER() OVER (
      PARTITION BY curr.game_id, trg.team_id 
      ORDER BY trg.game_date DESC
    ) AS game_rank
  FROM completed_games curr
  INNER JOIN team_recent_games trg ON 
    trg.team_id IN (curr.home_team_id, curr.away_team_id)
    AND trg.game_date < curr.game_date::date
    AND trg.opponent_strength IS NOT NULL
    AND trg.game_date >= curr.game_date::date - INTERVAL '60 days'
    AND curr.game_date < @end_ds  -- Only process games up to end of chunk
),

-- Calculate home team's quality-adjusted performance (last 10 games before this game)
home_quality_adjusted_10 AS (
  SELECT 
    current_game_id AS game_id,
    current_game_date AS game_date,
    team_id AS home_team_id,
    -- Average opponent strength in recent wins
    AVG(CASE 
      WHEN team_won = 1 THEN opponent_strength
      ELSE NULL
    END) AS home_avg_opponent_strength_in_wins_10,
    -- Average opponent strength in recent losses
    AVG(CASE 
      WHEN team_won = 0 THEN opponent_strength
      ELSE NULL
    END) AS home_avg_opponent_strength_in_losses_10,
    -- Average opponent strength overall
    AVG(opponent_strength) AS home_avg_opponent_strength_10,
    -- Quality-adjusted win percentage
    CASE 
      WHEN SUM(opponent_strength) > 0 THEN
        SUM(CASE WHEN team_won = 1 THEN opponent_strength ELSE 0.0 END) / SUM(opponent_strength)
      ELSE 0.5
    END AS home_quality_adjusted_win_pct_10,
    -- Average point differentials
    AVG(CASE 
      WHEN team_won = 1 THEN team_point_diff
      ELSE NULL
    END) AS home_avg_point_diff_in_wins_10,
    AVG(CASE 
      WHEN team_won = 0 THEN team_point_diff
      ELSE NULL
    END) AS home_avg_point_diff_in_losses_10,
    -- Game counts
    COUNT(CASE WHEN team_won = 1 THEN 1 END) AS home_wins_count_10,
    COUNT(CASE WHEN team_won = 0 THEN 1 END) AS home_losses_count_10,
    COUNT(*) AS home_total_games_10
  FROM ranked_recent_games_10
  WHERE game_rank <= 10
    AND team_id = home_team_id
  GROUP BY current_game_id, current_game_date, team_id, home_team_id
),

-- Calculate away team's quality-adjusted performance (last 5 games)
away_quality_adjusted AS (
  SELECT 
    current_game_id AS game_id,
    current_game_date AS game_date,
    team_id AS away_team_id,
    -- Average opponent strength in recent wins
    AVG(CASE 
      WHEN team_won = 1 THEN opponent_strength
      ELSE NULL
    END) AS away_avg_opponent_strength_in_wins_5,
    -- Average opponent strength in recent losses
    AVG(CASE 
      WHEN team_won = 0 THEN opponent_strength
      ELSE NULL
    END) AS away_avg_opponent_strength_in_losses_5,
    -- Average opponent strength overall
    AVG(opponent_strength) AS away_avg_opponent_strength_5,
    -- Quality-adjusted win percentage
    CASE 
      WHEN SUM(opponent_strength) > 0 THEN
        SUM(CASE WHEN team_won = 1 THEN opponent_strength ELSE 0.0 END) / SUM(opponent_strength)
      ELSE 0.5
    END AS away_quality_adjusted_win_pct_5,
    -- Average point differentials
    AVG(CASE 
      WHEN team_won = 1 THEN team_point_diff
      ELSE NULL
    END) AS away_avg_point_diff_in_wins_5,
    AVG(CASE 
      WHEN team_won = 0 THEN team_point_diff
      ELSE NULL
    END) AS away_avg_point_diff_in_losses_5,
    -- Game counts
    COUNT(CASE WHEN team_won = 1 THEN 1 END) AS away_wins_count_5,
    COUNT(CASE WHEN team_won = 0 THEN 1 END) AS away_losses_count_5,
    COUNT(*) AS away_total_games_5
  FROM ranked_recent_games
  WHERE game_rank <= 5
    AND team_id = away_team_id
  GROUP BY current_game_id, current_game_date, team_id, away_team_id
),

-- Calculate away team's quality-adjusted performance (last 10 games)
away_quality_adjusted_10 AS (
  SELECT 
    current_game_id AS game_id,
    current_game_date AS game_date,
    team_id AS away_team_id,
    -- Average opponent strength in recent wins
    AVG(CASE 
      WHEN team_won = 1 THEN opponent_strength
      ELSE NULL
    END) AS away_avg_opponent_strength_in_wins_10,
    -- Average opponent strength in recent losses
    AVG(CASE 
      WHEN team_won = 0 THEN opponent_strength
      ELSE NULL
    END) AS away_avg_opponent_strength_in_losses_10,
    -- Average opponent strength overall
    AVG(opponent_strength) AS away_avg_opponent_strength_10,
    -- Quality-adjusted win percentage
    CASE 
      WHEN SUM(opponent_strength) > 0 THEN
        SUM(CASE WHEN team_won = 1 THEN opponent_strength ELSE 0.0 END) / SUM(opponent_strength)
      ELSE 0.5
    END AS away_quality_adjusted_win_pct_10,
    -- Average point differentials
    AVG(CASE 
      WHEN team_won = 1 THEN team_point_diff
      ELSE NULL
    END) AS away_avg_point_diff_in_wins_10,
    AVG(CASE 
      WHEN team_won = 0 THEN team_point_diff
      ELSE NULL
    END) AS away_avg_point_diff_in_losses_10,
    -- Game counts
    COUNT(CASE WHEN team_won = 1 THEN 1 END) AS away_wins_count_10,
    COUNT(CASE WHEN team_won = 0 THEN 1 END) AS away_losses_count_10,
    COUNT(*) AS away_total_games_10
  FROM ranked_recent_games_10
  WHERE game_rank <= 10
    AND team_id = away_team_id
  GROUP BY current_game_id, current_game_date, team_id, away_team_id
)

-- Join to games and provide features
SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  -- Home team quality-adjusted features (5-game window)
  COALESCE(hq5.home_avg_opponent_strength_in_wins_5, 0.5) AS home_avg_opponent_strength_in_wins_5,
  COALESCE(hq5.home_avg_opponent_strength_in_losses_5, 0.5) AS home_avg_opponent_strength_in_losses_5,
  COALESCE(hq5.home_avg_opponent_strength_5, 0.5) AS home_avg_opponent_strength_5,
  COALESCE(hq5.home_quality_adjusted_win_pct_5, 0.5) AS home_quality_adjusted_win_pct_5,
  COALESCE(hq5.home_avg_point_diff_in_wins_5, 0.0) AS home_avg_point_diff_in_wins_5,
  COALESCE(hq5.home_avg_point_diff_in_losses_5, 0.0) AS home_avg_point_diff_in_losses_5,
  COALESCE(hq5.home_wins_count_5, 0) AS home_wins_count_5,
  COALESCE(hq5.home_losses_count_5, 0) AS home_losses_count_5,
  COALESCE(hq5.home_total_games_5, 0) AS home_total_games_5,
  -- Home team quality-adjusted features (10-game window)
  COALESCE(hq10.home_avg_opponent_strength_in_wins_10, 0.5) AS home_avg_opponent_strength_in_wins_10,
  COALESCE(hq10.home_avg_opponent_strength_in_losses_10, 0.5) AS home_avg_opponent_strength_in_losses_10,
  COALESCE(hq10.home_avg_opponent_strength_10, 0.5) AS home_avg_opponent_strength_10,
  COALESCE(hq10.home_quality_adjusted_win_pct_10, 0.5) AS home_quality_adjusted_win_pct_10,
  COALESCE(hq10.home_avg_point_diff_in_wins_10, 0.0) AS home_avg_point_diff_in_wins_10,
  COALESCE(hq10.home_avg_point_diff_in_losses_10, 0.0) AS home_avg_point_diff_in_losses_10,
  COALESCE(hq10.home_wins_count_10, 0) AS home_wins_count_10,
  COALESCE(hq10.home_losses_count_10, 0) AS home_losses_count_10,
  COALESCE(hq10.home_total_games_10, 0) AS home_total_games_10,
  -- Away team quality-adjusted features (5-game window)
  COALESCE(aq5.away_avg_opponent_strength_in_wins_5, 0.5) AS away_avg_opponent_strength_in_wins_5,
  COALESCE(aq5.away_avg_opponent_strength_in_losses_5, 0.5) AS away_avg_opponent_strength_in_losses_5,
  COALESCE(aq5.away_avg_opponent_strength_5, 0.5) AS away_avg_opponent_strength_5,
  COALESCE(aq5.away_quality_adjusted_win_pct_5, 0.5) AS away_quality_adjusted_win_pct_5,
  COALESCE(aq5.away_avg_point_diff_in_wins_5, 0.0) AS away_avg_point_diff_in_wins_5,
  COALESCE(aq5.away_avg_point_diff_in_losses_5, 0.0) AS away_avg_point_diff_in_losses_5,
  COALESCE(aq5.away_wins_count_5, 0) AS away_wins_count_5,
  COALESCE(aq5.away_losses_count_5, 0) AS away_losses_count_5,
  COALESCE(aq5.away_total_games_5, 0) AS away_total_games_5,
  -- Away team quality-adjusted features (10-game window)
  COALESCE(aq10.away_avg_opponent_strength_in_wins_10, 0.5) AS away_avg_opponent_strength_in_wins_10,
  COALESCE(aq10.away_avg_opponent_strength_in_losses_10, 0.5) AS away_avg_opponent_strength_in_losses_10,
  COALESCE(aq10.away_avg_opponent_strength_10, 0.5) AS away_avg_opponent_strength_10,
  COALESCE(aq10.away_quality_adjusted_win_pct_10, 0.5) AS away_quality_adjusted_win_pct_10,
  COALESCE(aq10.away_avg_point_diff_in_wins_10, 0.0) AS away_avg_point_diff_in_wins_10,
  COALESCE(aq10.away_avg_point_diff_in_losses_10, 0.0) AS away_avg_point_diff_in_losses_10,
  COALESCE(aq10.away_wins_count_10, 0) AS away_wins_count_10,
  COALESCE(aq10.away_losses_count_10, 0) AS away_losses_count_10,
  COALESCE(aq10.away_total_games_10, 0) AS away_total_games_10,
  -- Differential features (home - away)
  COALESCE(hq5.home_quality_adjusted_win_pct_5, 0.5) - COALESCE(aq5.away_quality_adjusted_win_pct_5, 0.5) AS quality_adjusted_win_pct_diff_5,
  COALESCE(hq10.home_quality_adjusted_win_pct_10, 0.5) - COALESCE(aq10.away_quality_adjusted_win_pct_10, 0.5) AS quality_adjusted_win_pct_diff_10,
  COALESCE(hq5.home_avg_opponent_strength_5, 0.5) - COALESCE(aq5.away_avg_opponent_strength_5, 0.5) AS avg_opponent_strength_diff_5,
  COALESCE(hq10.home_avg_opponent_strength_10, 0.5) - COALESCE(aq10.away_avg_opponent_strength_10, 0.5) AS avg_opponent_strength_diff_10
FROM raw_dev.games g
LEFT JOIN home_quality_adjusted hq5 ON hq5.game_id = g.game_id
LEFT JOIN home_quality_adjusted_10 hq10 ON hq10.game_id = g.game_id
LEFT JOIN away_quality_adjusted aq5 ON aq5.game_id = g.game_id
LEFT JOIN away_quality_adjusted_10 aq10 ON aq10.game_id = g.game_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds;
