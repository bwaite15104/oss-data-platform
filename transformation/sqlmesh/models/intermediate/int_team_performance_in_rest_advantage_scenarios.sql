MODEL (
  name intermediate.int_team_perf_rest_adv,
  description 'Team performance in rest advantage scenarios. Short name for Postgres 63-char limit.',
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate team performance in different rest advantage scenarios
-- This captures how teams perform when they have rest advantage vs rest disadvantage
-- Scenarios:
-- - Well-rested (3+ days) vs tired (back-to-back)
-- - Well-rested (3+ days) vs moderately rested (2 days)
-- - Moderately rested (2 days) vs tired (back-to-back)
-- - Rest advantage (any advantage) vs rest disadvantage

WITH completed_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    g.winner_team_id,
    g.home_score,
    g.away_score,
    CASE WHEN g.winner_team_id = g.home_team_id THEN 1 ELSE 0 END AS home_win,
    (g.home_score - g.away_score) AS home_point_diff
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Get rest context for each game
game_rest_context AS (
  SELECT 
    g.game_id,
    g.game_date,
    g.home_team_id,
    g.away_team_id,
    rd.home_rest_days,
    rd.away_rest_days,
    rd.home_back_to_back,
    rd.away_back_to_back,
    rd.rest_advantage,
    -- Categorize rest levels for scenario matching
    CASE 
      WHEN rd.home_rest_days >= 3 THEN 'well_rested'
      WHEN rd.home_rest_days = 2 THEN 'moderate_rest'
      WHEN rd.home_back_to_back THEN 'tired'
      ELSE 'low_rest'
    END AS home_rest_level,
    CASE 
      WHEN rd.away_rest_days >= 3 THEN 'well_rested'
      WHEN rd.away_rest_days = 2 THEN 'moderate_rest'
      WHEN rd.away_back_to_back THEN 'tired'
      ELSE 'low_rest'
    END AS away_rest_level,
    -- Rest advantage scenarios
    CASE 
      WHEN rd.home_rest_days >= 3 AND rd.away_back_to_back THEN 'well_rested_vs_tired'
      WHEN rd.home_rest_days >= 3 AND rd.away_rest_days = 2 THEN 'well_rested_vs_moderate'
      WHEN rd.home_rest_days = 2 AND rd.away_back_to_back THEN 'moderate_vs_tired'
      WHEN rd.rest_advantage > 0 THEN 'has_rest_advantage'
      WHEN rd.rest_advantage < 0 THEN 'has_rest_disadvantage'
      ELSE 'equal_rest'
    END AS rest_scenario
  FROM completed_games g
  LEFT JOIN intermediate.int_team_rest_days rd ON rd.game_id = g.game_id
),

-- For each team, calculate performance in different rest advantage scenarios
-- Home team performance in rest advantage scenarios
home_rest_performance AS (
  SELECT 
    g.game_id,
    g.game_date,
    g.home_team_id AS team_id,
    -- Well-rested vs tired scenario
    AVG(CASE WHEN grc.rest_scenario = 'well_rested_vs_tired' THEN g.home_win ELSE NULL END) 
      OVER (
        PARTITION BY g.home_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS home_win_pct_well_rested_vs_tired,
    AVG(CASE WHEN grc.rest_scenario = 'well_rested_vs_tired' THEN g.home_point_diff ELSE NULL END) 
      OVER (
        PARTITION BY g.home_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS home_avg_point_diff_well_rested_vs_tired,
    COUNT(CASE WHEN grc.rest_scenario = 'well_rested_vs_tired' THEN 1 END) 
      OVER (
        PARTITION BY g.home_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS home_well_rested_vs_tired_count,
    -- Well-rested vs moderate scenario
    AVG(CASE WHEN grc.rest_scenario = 'well_rested_vs_moderate' THEN g.home_win ELSE NULL END) 
      OVER (
        PARTITION BY g.home_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS home_win_pct_well_rested_vs_moderate,
    AVG(CASE WHEN grc.rest_scenario = 'well_rested_vs_moderate' THEN g.home_point_diff ELSE NULL END) 
      OVER (
        PARTITION BY g.home_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS home_avg_point_diff_well_rested_vs_moderate,
    COUNT(CASE WHEN grc.rest_scenario = 'well_rested_vs_moderate' THEN 1 END) 
      OVER (
        PARTITION BY g.home_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS home_well_rested_vs_moderate_count,
    -- Moderate vs tired scenario
    AVG(CASE WHEN grc.rest_scenario = 'moderate_vs_tired' THEN g.home_win ELSE NULL END) 
      OVER (
        PARTITION BY g.home_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS home_win_pct_moderate_vs_tired,
    AVG(CASE WHEN grc.rest_scenario = 'moderate_vs_tired' THEN g.home_point_diff ELSE NULL END) 
      OVER (
        PARTITION BY g.home_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS home_avg_point_diff_moderate_vs_tired,
    COUNT(CASE WHEN grc.rest_scenario = 'moderate_vs_tired' THEN 1 END) 
      OVER (
        PARTITION BY g.home_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS home_moderate_vs_tired_count,
    -- Has rest advantage (any advantage)
    AVG(CASE WHEN grc.rest_scenario = 'has_rest_advantage' THEN g.home_win ELSE NULL END) 
      OVER (
        PARTITION BY g.home_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS home_win_pct_with_rest_advantage,
    AVG(CASE WHEN grc.rest_scenario = 'has_rest_advantage' THEN g.home_point_diff ELSE NULL END) 
      OVER (
        PARTITION BY g.home_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS home_avg_point_diff_with_rest_advantage,
    COUNT(CASE WHEN grc.rest_scenario = 'has_rest_advantage' THEN 1 END) 
      OVER (
        PARTITION BY g.home_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS home_with_rest_advantage_count
  FROM completed_games g
  LEFT JOIN game_rest_context grc ON grc.game_id = g.game_id
),

-- Away team performance in rest advantage scenarios
away_rest_performance AS (
  SELECT 
    g.game_id,
    g.game_date,
    g.away_team_id AS team_id,
    -- Well-rested vs tired scenario (from away team's perspective: away is well-rested, home is tired)
    AVG(CASE WHEN grc.rest_scenario = 'well_rested_vs_tired' AND grc.rest_advantage < 0 THEN 1 - g.home_win ELSE NULL END) 
      OVER (
        PARTITION BY g.away_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS away_win_pct_well_rested_vs_tired,
    AVG(CASE WHEN grc.rest_scenario = 'well_rested_vs_tired' AND grc.rest_advantage < 0 THEN -g.home_point_diff ELSE NULL END) 
      OVER (
        PARTITION BY g.away_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS away_avg_point_diff_well_rested_vs_tired,
    COUNT(CASE WHEN grc.rest_scenario = 'well_rested_vs_tired' AND grc.rest_advantage < 0 THEN 1 END) 
      OVER (
        PARTITION BY g.away_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS away_well_rested_vs_tired_count,
    -- Well-rested vs moderate scenario
    AVG(CASE WHEN grc.rest_scenario = 'well_rested_vs_moderate' AND grc.rest_advantage < 0 THEN 1 - g.home_win ELSE NULL END) 
      OVER (
        PARTITION BY g.away_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS away_win_pct_well_rested_vs_moderate,
    AVG(CASE WHEN grc.rest_scenario = 'well_rested_vs_moderate' AND grc.rest_advantage < 0 THEN -g.home_point_diff ELSE NULL END) 
      OVER (
        PARTITION BY g.away_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS away_avg_point_diff_well_rested_vs_moderate,
    COUNT(CASE WHEN grc.rest_scenario = 'well_rested_vs_moderate' AND grc.rest_advantage < 0 THEN 1 END) 
      OVER (
        PARTITION BY g.away_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS away_well_rested_vs_moderate_count,
    -- Moderate vs tired scenario
    AVG(CASE WHEN grc.rest_scenario = 'moderate_vs_tired' AND grc.rest_advantage < 0 THEN 1 - g.home_win ELSE NULL END) 
      OVER (
        PARTITION BY g.away_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS away_win_pct_moderate_vs_tired,
    AVG(CASE WHEN grc.rest_scenario = 'moderate_vs_tired' AND grc.rest_advantage < 0 THEN -g.home_point_diff ELSE NULL END) 
      OVER (
        PARTITION BY g.away_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS away_avg_point_diff_moderate_vs_tired,
    COUNT(CASE WHEN grc.rest_scenario = 'moderate_vs_tired' AND grc.rest_advantage < 0 THEN 1 END) 
      OVER (
        PARTITION BY g.away_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS away_moderate_vs_tired_count,
    -- Has rest advantage (any advantage)
    AVG(CASE WHEN grc.rest_scenario = 'has_rest_disadvantage' THEN 1 - g.home_win ELSE NULL END) 
      OVER (
        PARTITION BY g.away_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS away_win_pct_with_rest_advantage,
    AVG(CASE WHEN grc.rest_scenario = 'has_rest_disadvantage' THEN -g.home_point_diff ELSE NULL END) 
      OVER (
        PARTITION BY g.away_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS away_avg_point_diff_with_rest_advantage,
    COUNT(CASE WHEN grc.rest_scenario = 'has_rest_disadvantage' THEN 1 END) 
      OVER (
        PARTITION BY g.away_team_id 
        ORDER BY g.game_date 
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
      ) AS away_with_rest_advantage_count
  FROM completed_games g
  LEFT JOIN game_rest_context grc ON grc.game_id = g.game_id
)

-- Join back to games
SELECT 
  g.game_id,
  g.game_date,
  g.home_team_id,
  g.away_team_id,
  -- Home team rest advantage performance
  COALESCE(hrp.home_win_pct_well_rested_vs_tired, 0.5) AS home_win_pct_well_rested_vs_tired,
  COALESCE(hrp.home_avg_point_diff_well_rested_vs_tired, 0.0) AS home_avg_point_diff_well_rested_vs_tired,
  COALESCE(hrp.home_well_rested_vs_tired_count, 0) AS home_well_rested_vs_tired_count,
  COALESCE(hrp.home_win_pct_well_rested_vs_moderate, 0.5) AS home_win_pct_well_rested_vs_moderate,
  COALESCE(hrp.home_avg_point_diff_well_rested_vs_moderate, 0.0) AS home_avg_point_diff_well_rested_vs_moderate,
  COALESCE(hrp.home_well_rested_vs_moderate_count, 0) AS home_well_rested_vs_moderate_count,
  COALESCE(hrp.home_win_pct_moderate_vs_tired, 0.5) AS home_win_pct_moderate_vs_tired,
  COALESCE(hrp.home_avg_point_diff_moderate_vs_tired, 0.0) AS home_avg_point_diff_moderate_vs_tired,
  COALESCE(hrp.home_moderate_vs_tired_count, 0) AS home_moderate_vs_tired_count,
  COALESCE(hrp.home_win_pct_with_rest_advantage, 0.5) AS home_win_pct_with_rest_advantage,
  COALESCE(hrp.home_avg_point_diff_with_rest_advantage, 0.0) AS home_avg_point_diff_with_rest_advantage,
  COALESCE(hrp.home_with_rest_advantage_count, 0) AS home_with_rest_advantage_count,
  -- Away team rest advantage performance
  COALESCE(arp.away_win_pct_well_rested_vs_tired, 0.5) AS away_win_pct_well_rested_vs_tired,
  COALESCE(arp.away_avg_point_diff_well_rested_vs_tired, 0.0) AS away_avg_point_diff_well_rested_vs_tired,
  COALESCE(arp.away_well_rested_vs_tired_count, 0) AS away_well_rested_vs_tired_count,
  COALESCE(arp.away_win_pct_well_rested_vs_moderate, 0.5) AS away_win_pct_well_rested_vs_moderate,
  COALESCE(arp.away_avg_point_diff_well_rested_vs_moderate, 0.0) AS away_avg_point_diff_well_rested_vs_moderate,
  COALESCE(arp.away_well_rested_vs_moderate_count, 0) AS away_well_rested_vs_moderate_count,
  COALESCE(arp.away_win_pct_moderate_vs_tired, 0.5) AS away_win_pct_moderate_vs_tired,
  COALESCE(arp.away_avg_point_diff_moderate_vs_tired, 0.0) AS away_avg_point_diff_moderate_vs_tired,
  COALESCE(arp.away_moderate_vs_tired_count, 0) AS away_moderate_vs_tired_count,
  COALESCE(arp.away_win_pct_with_rest_advantage, 0.5) AS away_win_pct_with_rest_advantage,
  COALESCE(arp.away_avg_point_diff_with_rest_advantage, 0.0) AS away_avg_point_diff_with_rest_advantage,
  COALESCE(arp.away_with_rest_advantage_count, 0) AS away_with_rest_advantage_count,
  -- Current game rest scenario indicator
  CASE 
    WHEN grc.rest_scenario = 'well_rested_vs_tired' THEN 1 ELSE 0 
  END AS is_well_rested_vs_tired,
  CASE 
    WHEN grc.rest_scenario = 'well_rested_vs_moderate' THEN 1 ELSE 0 
  END AS is_well_rested_vs_moderate,
  CASE 
    WHEN grc.rest_scenario = 'moderate_vs_tired' THEN 1 ELSE 0 
  END AS is_moderate_vs_tired,
  CASE 
    WHEN grc.rest_scenario = 'has_rest_advantage' OR grc.rest_scenario = 'has_rest_disadvantage' THEN 1 ELSE 0 
  END AS is_rest_advantage_scenario,
  -- Differential features
  COALESCE(hrp.home_win_pct_well_rested_vs_tired, 0.5) - COALESCE(arp.away_win_pct_well_rested_vs_tired, 0.5) AS win_pct_well_rested_vs_tired_diff,
  COALESCE(hrp.home_win_pct_well_rested_vs_moderate, 0.5) - COALESCE(arp.away_win_pct_well_rested_vs_moderate, 0.5) AS win_pct_well_rested_vs_moderate_diff,
  COALESCE(hrp.home_win_pct_moderate_vs_tired, 0.5) - COALESCE(arp.away_win_pct_moderate_vs_tired, 0.5) AS win_pct_moderate_vs_tired_diff,
  COALESCE(hrp.home_win_pct_with_rest_advantage, 0.5) - COALESCE(arp.away_win_pct_with_rest_advantage, 0.5) AS win_pct_with_rest_advantage_diff
FROM completed_games g
LEFT JOIN game_rest_context grc ON grc.game_id = g.game_id
LEFT JOIN home_rest_performance hrp ON hrp.game_id = g.game_id
LEFT JOIN away_rest_performance arp ON arp.game_id = g.game_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds;
