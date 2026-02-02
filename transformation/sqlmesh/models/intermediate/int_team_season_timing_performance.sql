MODEL (
  name intermediate.int_team_timing_perf,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate team performance by season phase (early season, mid season, late season, playoff push)
WITH completed_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.season,
    g.home_team_id,
    g.away_team_id,
    g.home_score,
    g.away_score,
    g.winner_team_id,
    -- Calculate game number in season for each team
    ROW_NUMBER() OVER (PARTITION BY g.season, g.home_team_id ORDER BY g.game_date) AS home_game_num,
    ROW_NUMBER() OVER (PARTITION BY g.season, g.away_team_id ORDER BY g.game_date) AS away_game_num
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.winner_team_id IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Calculate home team's performance by season phase
home_season_timing AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.home_team_id AS team_id,
    -- Early season performance (first 20 games of season)
    AVG(CASE 
      WHEN prev_g.home_game_num <= 20 AND prev_g.winner_team_id = prev_g.home_team_id THEN 1.0
      WHEN prev_g.home_game_num <= 20 AND prev_g.winner_team_id != prev_g.home_team_id THEN 0.0
      ELSE NULL
    END) AS home_early_season_win_pct,
    COUNT(CASE WHEN prev_g.home_game_num <= 20 THEN 1 END) AS home_early_season_game_count,
    AVG(CASE 
      WHEN prev_g.home_game_num <= 20 THEN prev_g.home_score - prev_g.away_score
      ELSE NULL
    END) AS home_early_season_avg_point_diff,
    -- Mid season performance (games 21-60)
    AVG(CASE 
      WHEN prev_g.home_game_num > 20 AND prev_g.home_game_num <= 60 AND prev_g.winner_team_id = prev_g.home_team_id THEN 1.0
      WHEN prev_g.home_game_num > 20 AND prev_g.home_game_num <= 60 AND prev_g.winner_team_id != prev_g.home_team_id THEN 0.0
      ELSE NULL
    END) AS home_mid_season_win_pct,
    COUNT(CASE WHEN prev_g.home_game_num > 20 AND prev_g.home_game_num <= 60 THEN 1 END) AS home_mid_season_game_count,
    AVG(CASE 
      WHEN prev_g.home_game_num > 20 AND prev_g.home_game_num <= 60 THEN prev_g.home_score - prev_g.away_score
      ELSE NULL
    END) AS home_mid_season_avg_point_diff,
    -- Late season performance (games 61-82)
    AVG(CASE 
      WHEN prev_g.home_game_num > 60 AND prev_g.home_game_num <= 82 AND prev_g.winner_team_id = prev_g.home_team_id THEN 1.0
      WHEN prev_g.home_game_num > 60 AND prev_g.home_game_num <= 82 AND prev_g.winner_team_id != prev_g.home_team_id THEN 0.0
      ELSE NULL
    END) AS home_late_season_win_pct,
    COUNT(CASE WHEN prev_g.home_game_num > 60 AND prev_g.home_game_num <= 82 THEN 1 END) AS home_late_season_game_count,
    AVG(CASE 
      WHEN prev_g.home_game_num > 60 AND prev_g.home_game_num <= 82 THEN prev_g.home_score - prev_g.away_score
      ELSE NULL
    END) AS home_late_season_avg_point_diff,
    -- Playoff push performance (last 10 games of regular season)
    AVG(CASE 
      WHEN prev_g.home_game_num > 72 AND prev_g.home_game_num <= 82 AND prev_g.winner_team_id = prev_g.home_team_id THEN 1.0
      WHEN prev_g.home_game_num > 72 AND prev_g.home_game_num <= 82 AND prev_g.winner_team_id != prev_g.home_team_id THEN 0.0
      ELSE NULL
    END) AS home_playoff_push_win_pct,
    COUNT(CASE WHEN prev_g.home_game_num > 72 AND prev_g.home_game_num <= 82 THEN 1 END) AS home_playoff_push_game_count,
    AVG(CASE 
      WHEN prev_g.home_game_num > 72 AND prev_g.home_game_num <= 82 THEN prev_g.home_score - prev_g.away_score
      ELSE NULL
    END) AS home_playoff_push_avg_point_diff,
    -- Current game season phase indicators
    CASE WHEN curr.home_game_num <= 20 THEN 1 ELSE 0 END AS is_home_early_season,
    CASE WHEN curr.home_game_num > 20 AND curr.home_game_num <= 60 THEN 1 ELSE 0 END AS is_home_mid_season,
    CASE WHEN curr.home_game_num > 60 AND curr.home_game_num <= 82 THEN 1 ELSE 0 END AS is_home_late_season,
    CASE WHEN curr.home_game_num > 72 AND curr.home_game_num <= 82 THEN 1 ELSE 0 END AS is_home_playoff_push
  FROM completed_games curr
  LEFT JOIN completed_games prev_g ON 
    prev_g.home_team_id = curr.home_team_id
    AND prev_g.season = curr.season
    AND prev_g.game_date < curr.game_date
  WHERE curr.game_date >= @start_ds
    AND curr.game_date < @end_ds
  GROUP BY curr.game_id, curr.game_date, curr.home_team_id, curr.home_game_num
),

-- Calculate away team's performance by season phase
away_season_timing AS (
  SELECT 
    curr.game_id,
    curr.game_date::date AS game_date,
    curr.away_team_id AS team_id,
    -- Early season performance (first 20 games of season)
    AVG(CASE 
      WHEN prev_g.away_game_num <= 20 AND prev_g.winner_team_id = prev_g.away_team_id THEN 1.0
      WHEN prev_g.away_game_num <= 20 AND prev_g.winner_team_id != prev_g.away_team_id THEN 0.0
      ELSE NULL
    END) AS away_early_season_win_pct,
    COUNT(CASE WHEN prev_g.away_game_num <= 20 THEN 1 END) AS away_early_season_game_count,
    AVG(CASE 
      WHEN prev_g.away_game_num <= 20 THEN prev_g.away_score - prev_g.home_score
      ELSE NULL
    END) AS away_early_season_avg_point_diff,
    -- Mid season performance (games 21-60)
    AVG(CASE 
      WHEN prev_g.away_game_num > 20 AND prev_g.away_game_num <= 60 AND prev_g.winner_team_id = prev_g.away_team_id THEN 1.0
      WHEN prev_g.away_game_num > 20 AND prev_g.away_game_num <= 60 AND prev_g.winner_team_id != prev_g.away_team_id THEN 0.0
      ELSE NULL
    END) AS away_mid_season_win_pct,
    COUNT(CASE WHEN prev_g.away_game_num > 20 AND prev_g.away_game_num <= 60 THEN 1 END) AS away_mid_season_game_count,
    AVG(CASE 
      WHEN prev_g.away_game_num > 20 AND prev_g.away_game_num <= 60 THEN prev_g.away_score - prev_g.home_score
      ELSE NULL
    END) AS away_mid_season_avg_point_diff,
    -- Late season performance (games 61-82)
    AVG(CASE 
      WHEN prev_g.away_game_num > 60 AND prev_g.away_game_num <= 82 AND prev_g.winner_team_id = prev_g.away_team_id THEN 1.0
      WHEN prev_g.away_game_num > 60 AND prev_g.away_game_num <= 82 AND prev_g.winner_team_id != prev_g.away_team_id THEN 0.0
      ELSE NULL
    END) AS away_late_season_win_pct,
    COUNT(CASE WHEN prev_g.away_game_num > 60 AND prev_g.away_game_num <= 82 THEN 1 END) AS away_late_season_game_count,
    AVG(CASE 
      WHEN prev_g.away_game_num > 60 AND prev_g.away_game_num <= 82 THEN prev_g.away_score - prev_g.home_score
      ELSE NULL
    END) AS away_late_season_avg_point_diff,
    -- Playoff push performance (last 10 games of regular season)
    AVG(CASE 
      WHEN prev_g.away_game_num > 72 AND prev_g.away_game_num <= 82 AND prev_g.winner_team_id = prev_g.away_team_id THEN 1.0
      WHEN prev_g.away_game_num > 72 AND prev_g.away_game_num <= 82 AND prev_g.winner_team_id != prev_g.away_team_id THEN 0.0
      ELSE NULL
    END) AS away_playoff_push_win_pct,
    COUNT(CASE WHEN prev_g.away_game_num > 72 AND prev_g.away_game_num <= 82 THEN 1 END) AS away_playoff_push_game_count,
    AVG(CASE 
      WHEN prev_g.away_game_num > 72 AND prev_g.away_game_num <= 82 THEN prev_g.away_score - prev_g.home_score
      ELSE NULL
    END) AS away_playoff_push_avg_point_diff,
    -- Current game season phase indicators
    CASE WHEN curr.away_game_num <= 20 THEN 1 ELSE 0 END AS is_away_early_season,
    CASE WHEN curr.away_game_num > 20 AND curr.away_game_num <= 60 THEN 1 ELSE 0 END AS is_away_mid_season,
    CASE WHEN curr.away_game_num > 60 AND curr.away_game_num <= 82 THEN 1 ELSE 0 END AS is_away_late_season,
    CASE WHEN curr.away_game_num > 72 AND curr.away_game_num <= 82 THEN 1 ELSE 0 END AS is_away_playoff_push
  FROM completed_games curr
  LEFT JOIN completed_games prev_g ON 
    prev_g.away_team_id = curr.away_team_id
    AND prev_g.season = curr.season
    AND prev_g.game_date < curr.game_date
  WHERE curr.game_date >= @start_ds
    AND curr.game_date < @end_ds
  GROUP BY curr.game_id, curr.game_date, curr.away_team_id, curr.away_game_num
)

-- Combine home and away season timing features
SELECT 
  h.game_id,
  h.game_date,
  h.team_id AS home_team_id,
  a.team_id AS away_team_id,
  -- Home team season timing features
  COALESCE(h.home_early_season_win_pct, 0.5) AS home_early_season_win_pct,
  COALESCE(h.home_early_season_game_count, 0) AS home_early_season_game_count,
  COALESCE(h.home_early_season_avg_point_diff, 0.0) AS home_early_season_avg_point_diff,
  COALESCE(h.home_mid_season_win_pct, 0.5) AS home_mid_season_win_pct,
  COALESCE(h.home_mid_season_game_count, 0) AS home_mid_season_game_count,
  COALESCE(h.home_mid_season_avg_point_diff, 0.0) AS home_mid_season_avg_point_diff,
  COALESCE(h.home_late_season_win_pct, 0.5) AS home_late_season_win_pct,
  COALESCE(h.home_late_season_game_count, 0) AS home_late_season_game_count,
  COALESCE(h.home_late_season_avg_point_diff, 0.0) AS home_late_season_avg_point_diff,
  COALESCE(h.home_playoff_push_win_pct, 0.5) AS home_playoff_push_win_pct,
  COALESCE(h.home_playoff_push_game_count, 0) AS home_playoff_push_game_count,
  COALESCE(h.home_playoff_push_avg_point_diff, 0.0) AS home_playoff_push_avg_point_diff,
  h.is_home_early_season,
  h.is_home_mid_season,
  h.is_home_late_season,
  h.is_home_playoff_push,
  -- Away team season timing features
  COALESCE(a.away_early_season_win_pct, 0.5) AS away_early_season_win_pct,
  COALESCE(a.away_early_season_game_count, 0) AS away_early_season_game_count,
  COALESCE(a.away_early_season_avg_point_diff, 0.0) AS away_early_season_avg_point_diff,
  COALESCE(a.away_mid_season_win_pct, 0.5) AS away_mid_season_win_pct,
  COALESCE(a.away_mid_season_game_count, 0) AS away_mid_season_game_count,
  COALESCE(a.away_mid_season_avg_point_diff, 0.0) AS away_mid_season_avg_point_diff,
  COALESCE(a.away_late_season_win_pct, 0.5) AS away_late_season_win_pct,
  COALESCE(a.away_late_season_game_count, 0) AS away_late_season_game_count,
  COALESCE(a.away_late_season_avg_point_diff, 0.0) AS away_late_season_avg_point_diff,
  COALESCE(a.away_playoff_push_win_pct, 0.5) AS away_playoff_push_win_pct,
  COALESCE(a.away_playoff_push_game_count, 0) AS away_playoff_push_game_count,
  COALESCE(a.away_playoff_push_avg_point_diff, 0.0) AS away_playoff_push_avg_point_diff,
  a.is_away_early_season,
  a.is_away_mid_season,
  a.is_away_late_season,
  a.is_away_playoff_push,
  -- Differential features (home - away)
  COALESCE(h.home_early_season_win_pct, 0.5) - COALESCE(a.away_early_season_win_pct, 0.5) AS early_season_win_pct_diff,
  COALESCE(h.home_early_season_avg_point_diff, 0.0) - COALESCE(a.away_early_season_avg_point_diff, 0.0) AS early_season_avg_point_diff_diff,
  COALESCE(h.home_mid_season_win_pct, 0.5) - COALESCE(a.away_mid_season_win_pct, 0.5) AS mid_season_win_pct_diff,
  COALESCE(h.home_mid_season_avg_point_diff, 0.0) - COALESCE(a.away_mid_season_avg_point_diff, 0.0) AS mid_season_avg_point_diff_diff,
  COALESCE(h.home_late_season_win_pct, 0.5) - COALESCE(a.away_late_season_win_pct, 0.5) AS late_season_win_pct_diff,
  COALESCE(h.home_late_season_avg_point_diff, 0.0) - COALESCE(a.away_late_season_avg_point_diff, 0.0) AS late_season_avg_point_diff_diff,
  COALESCE(h.home_playoff_push_win_pct, 0.5) - COALESCE(a.away_playoff_push_win_pct, 0.5) AS playoff_push_win_pct_diff,
  COALESCE(h.home_playoff_push_avg_point_diff, 0.0) - COALESCE(a.away_playoff_push_avg_point_diff, 0.0) AS playoff_push_avg_point_diff_diff
FROM home_season_timing h
JOIN away_season_timing a ON h.game_id = a.game_id
WHERE h.game_date >= @start_ds
  AND h.game_date < @end_ds