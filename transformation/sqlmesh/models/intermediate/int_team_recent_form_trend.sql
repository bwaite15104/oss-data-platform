MODEL (
  name intermediate.int_team_recent_form_trend,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate recent form trends to capture whether teams are improving or declining
-- This is different from rolling averages - it captures the direction of change
-- Teams on upward trends (improving) may be more likely to win than teams on downward trends
-- Approach: Compare most recent 3 games to most recent 5 games
-- If recent 3 > recent 5, team is improving (positive trend)
-- If recent 3 < recent 5, team is declining (negative trend)

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

-- Get home team's recent games (last 5 before current game)
home_recent_games AS (
  SELECT 
    curr.game_id,
    prev.game_date,
    CASE 
      WHEN prev.home_team_id = curr.home_team_id THEN 
        CASE WHEN prev.winner_team_id = curr.home_team_id THEN 1.0 ELSE 0.0 END
      WHEN prev.away_team_id = curr.home_team_id THEN 
        CASE WHEN prev.winner_team_id = curr.home_team_id THEN 1.0 ELSE 0.0 END
      ELSE NULL
    END as win_indicator,
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

-- Get away team's recent games (last 5 before current game)
away_recent_games AS (
  SELECT 
    curr.game_id,
    prev.game_date,
    CASE 
      WHEN prev.home_team_id = curr.away_team_id THEN 
        CASE WHEN prev.winner_team_id = curr.away_team_id THEN 1.0 ELSE 0.0 END
      WHEN prev.away_team_id = curr.away_team_id THEN 
        CASE WHEN prev.winner_team_id = curr.away_team_id THEN 1.0 ELSE 0.0 END
      ELSE NULL
    END as win_indicator,
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

-- Calculate home team trends (recent 3 vs recent 5)
home_trends AS (
  SELECT 
    game_id,
    AVG(CASE WHEN game_rank <= 3 THEN win_indicator END) as home_recent_3_win_pct,
    AVG(CASE WHEN game_rank <= 5 THEN win_indicator END) as home_recent_5_win_pct,
    AVG(CASE WHEN game_rank <= 3 THEN point_diff END) as home_recent_3_avg_point_diff,
    AVG(CASE WHEN game_rank <= 5 THEN point_diff END) as home_recent_5_avg_point_diff
  FROM home_recent_games
  WHERE win_indicator IS NOT NULL
  GROUP BY game_id
),

-- Calculate away team trends (recent 3 vs recent 5)
away_trends AS (
  SELECT 
    game_id,
    AVG(CASE WHEN game_rank <= 3 THEN win_indicator END) as away_recent_3_win_pct,
    AVG(CASE WHEN game_rank <= 5 THEN win_indicator END) as away_recent_5_win_pct,
    AVG(CASE WHEN game_rank <= 3 THEN point_diff END) as away_recent_3_avg_point_diff,
    AVG(CASE WHEN game_rank <= 5 THEN point_diff END) as away_recent_5_avg_point_diff
  FROM away_recent_games
  WHERE win_indicator IS NOT NULL
  GROUP BY game_id
)

SELECT 
  g.game_id,
  g.game_date,
  g.home_team_id,
  g.away_team_id,
  -- Home team form trends (recent 3 vs recent 5)
  -- Positive values indicate improving form (recent 3 > recent 5)
  COALESCE(ht.home_recent_3_win_pct, 0.5) - COALESCE(ht.home_recent_5_win_pct, 0.5) as home_form_trend_win_pct,
  COALESCE(ht.home_recent_3_avg_point_diff, 0.0) - COALESCE(ht.home_recent_5_avg_point_diff, 0.0) as home_form_trend_point_diff,
  -- Away team form trends
  COALESCE(at.away_recent_3_win_pct, 0.5) - COALESCE(at.away_recent_5_win_pct, 0.5) as away_form_trend_win_pct,
  COALESCE(at.away_recent_3_avg_point_diff, 0.0) - COALESCE(at.away_recent_5_avg_point_diff, 0.0) as away_form_trend_point_diff,
  -- Differential (home - away)
  (COALESCE(ht.home_recent_3_win_pct, 0.5) - COALESCE(ht.home_recent_5_win_pct, 0.5)) - 
  (COALESCE(at.away_recent_3_win_pct, 0.5) - COALESCE(at.away_recent_5_win_pct, 0.5)) as form_trend_win_pct_diff,
  (COALESCE(ht.home_recent_3_avg_point_diff, 0.0) - COALESCE(ht.home_recent_5_avg_point_diff, 0.0)) - 
  (COALESCE(at.away_recent_3_avg_point_diff, 0.0) - COALESCE(at.away_recent_5_avg_point_diff, 0.0)) as form_trend_point_diff_diff
FROM completed_games g
LEFT JOIN home_trends ht ON ht.game_id = g.game_id
LEFT JOIN away_trends at ON at.game_id = g.game_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds
