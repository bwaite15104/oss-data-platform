MODEL (
  name intermediate.int_team_road_perf,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
  description 'Team performance in recent road games (last 5/10 road games). Captures recent road form, which may differ from overall season road win percentage.'
);

-- Calculate recent road performance (last 5/10 road games)
-- This captures recent road form, which is more relevant than season-long road win percentage
WITH completed_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    g.winner_team_id,
    g.home_score,
    g.away_score
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Get away team's recent road games (last 5 and last 10)
recent_road_games AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.away_team_id AS team_id,
    prev_g.game_id AS prev_game_id,
    prev_g.game_date AS prev_game_date,
    prev_g.winner_team_id,
    prev_g.away_score - prev_g.home_score AS road_point_diff,
    ROW_NUMBER() OVER (
      PARTITION BY curr.game_id, curr.away_team_id 
      ORDER BY prev_g.game_date DESC
    ) AS road_game_rank
  FROM raw_dev.games curr
  LEFT JOIN completed_games prev_g ON 
    prev_g.away_team_id = curr.away_team_id
    AND prev_g.game_date < curr.game_date::date
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
    AND curr.game_date < @end_ds  -- Only process games up to end of chunk
)

SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  -- Away team's recent road win percentage (last 5 road games)
  CASE 
    WHEN COUNT(CASE WHEN rrg.road_game_rank <= 5 THEN 1 END) >= 3 THEN
      AVG(CASE 
        WHEN rrg.road_game_rank <= 5 AND rrg.winner_team_id = g.away_team_id THEN 1.0
        WHEN rrg.road_game_rank <= 5 AND rrg.winner_team_id != g.away_team_id THEN 0.0
        ELSE NULL
      END)
    ELSE 0.5  -- Default to 50% if insufficient road game history
  END AS away_recent_road_win_pct_5,
  -- Away team's recent road game count (last 5)
  COUNT(CASE WHEN rrg.road_game_rank <= 5 THEN 1 END) AS away_recent_road_game_count_5,
  -- Away team's average point differential in recent road games (last 5)
  COALESCE(AVG(CASE 
    WHEN rrg.road_game_rank <= 5 THEN rrg.road_point_diff
    ELSE NULL
  END), 0.0) AS away_recent_road_avg_point_diff_5,
  -- Away team's recent road win percentage (last 10 road games)
  CASE 
    WHEN COUNT(CASE WHEN rrg.road_game_rank <= 10 THEN 1 END) >= 5 THEN
      AVG(CASE 
        WHEN rrg.road_game_rank <= 10 AND rrg.winner_team_id = g.away_team_id THEN 1.0
        WHEN rrg.road_game_rank <= 10 AND rrg.winner_team_id != g.away_team_id THEN 0.0
        ELSE NULL
      END)
    ELSE 0.5  -- Default to 50% if insufficient road game history
  END AS away_recent_road_win_pct_10,
  -- Away team's recent road game count (last 10)
  COUNT(CASE WHEN rrg.road_game_rank <= 10 THEN 1 END) AS away_recent_road_game_count_10,
  -- Away team's average point differential in recent road games (last 10)
  COALESCE(AVG(CASE 
    WHEN rrg.road_game_rank <= 10 THEN rrg.road_point_diff
    ELSE NULL
  END), 0.0) AS away_recent_road_avg_point_diff_10
FROM raw_dev.games g
LEFT JOIN recent_road_games rrg ON 
  rrg.game_id = g.game_id
  AND rrg.team_id = g.away_team_id
WHERE g.home_score IS NOT NULL
  AND g.away_score IS NOT NULL
  AND g.game_date >= @start_ds
  AND g.game_date < @end_ds
GROUP BY g.game_id, g.game_date, g.home_team_id, g.away_team_id;
