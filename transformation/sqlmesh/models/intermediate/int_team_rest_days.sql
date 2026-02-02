MODEL (
  name intermediate.int_team_rest_days,
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
    g.away_team_id
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

team_games AS (
  -- Create one row per team per game with location (home/away)
  SELECT 
    game_id,
    game_date,
    home_team_id AS team_id,
    TRUE AS is_home
  FROM completed_games
  
  UNION ALL
  
  SELECT 
    game_id,
    game_date,
    away_team_id AS team_id,
    FALSE AS is_home
  FROM completed_games
),

-- For each team, get their most recent game before each game with location
team_prev_games AS (
  SELECT 
    team_id,
    game_id,
    game_date,
    is_home,
    LAG(game_date) OVER (
      PARTITION BY team_id 
      ORDER BY game_date, game_id
    ) AS prev_game_date,
    LAG(is_home) OVER (
      PARTITION BY team_id 
      ORDER BY game_date, game_id
    ) AS prev_is_home
  FROM team_games
),

-- Calculate rest days, back-to-back flag, and back-to-back with travel
team_rest AS (
  SELECT 
    team_id,
    game_id,
    game_date,
    CASE 
      WHEN prev_game_date IS NOT NULL THEN 
        (game_date - prev_game_date)::integer
      ELSE NULL
    END AS rest_days,
    CASE 
      WHEN prev_game_date IS NOT NULL AND 
           (game_date - prev_game_date)::integer <= 1 
      THEN TRUE
      ELSE FALSE
    END AS is_back_to_back,
    -- Back-to-back with travel: occurs when:
    -- 1. It's a back-to-back (rest_days <= 1)
    -- 2. AND there was a location change (prev_is_home != is_home)
    -- OR both games were away (travel between away games)
    CASE 
      WHEN prev_game_date IS NOT NULL AND 
           (game_date - prev_game_date)::integer <= 1 AND
           (prev_is_home IS NOT NULL AND is_home IS NOT NULL) THEN
        CASE
          -- Location changed (home->away or away->home) = travel
          WHEN prev_is_home != is_home THEN TRUE
          -- Both away games = travel between away games
          WHEN NOT prev_is_home AND NOT is_home THEN TRUE
          -- Both home games = no travel
          WHEN prev_is_home AND is_home THEN FALSE
          ELSE FALSE
        END
      ELSE FALSE
    END AS is_back_to_back_with_travel
  FROM team_prev_games
),

-- Add travel fatigue counts using window functions
team_rest_enriched AS (
  SELECT 
    team_id,
    game_id,
    game_date,
    rest_days,
    is_back_to_back,
    is_back_to_back_with_travel,
    -- Count back-to-backs with travel in last 5 games
    SUM(CASE WHEN is_back_to_back_with_travel THEN 1 ELSE 0 END) 
      OVER (
        PARTITION BY team_id 
        ORDER BY game_date, game_id 
        ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
      ) AS btb_travel_count_5,
    -- Count back-to-backs with travel in last 10 games
    SUM(CASE WHEN is_back_to_back_with_travel THEN 1 ELSE 0 END) 
      OVER (
        PARTITION BY team_id 
        ORDER BY game_date, game_id 
        ROWS BETWEEN 9 PRECEDING AND CURRENT ROW
      ) AS btb_travel_count_10,
    -- Travel fatigue score (same as count_5)
    SUM(CASE WHEN is_back_to_back_with_travel THEN 1.0 ELSE 0.0 END) 
      OVER (
        PARTITION BY team_id 
        ORDER BY game_date, game_id 
        ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
      ) AS travel_fatigue_score_5
  FROM team_rest
)

-- Join back to games to get home and away team rest days
SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  -- Home team rest days
  COALESCE(home_rest.rest_days, 0) AS home_rest_days,
  COALESCE(home_rest.is_back_to_back, FALSE) AS home_back_to_back,
  COALESCE(home_rest.is_back_to_back_with_travel, FALSE) AS home_back_to_back_with_travel,
  -- Home team travel fatigue (Iteration 11)
  COALESCE(home_rest.btb_travel_count_5, 0) AS home_btb_travel_count_5,
  COALESCE(home_rest.btb_travel_count_10, 0) AS home_btb_travel_count_10,
  COALESCE(home_rest.travel_fatigue_score_5, 0.0) AS home_travel_fatigue_score_5,
  -- Away team rest days
  COALESCE(away_rest.rest_days, 0) AS away_rest_days,
  COALESCE(away_rest.is_back_to_back, FALSE) AS away_back_to_back,
  COALESCE(away_rest.is_back_to_back_with_travel, FALSE) AS away_back_to_back_with_travel,
  -- Away team travel fatigue (Iteration 11)
  COALESCE(away_rest.btb_travel_count_5, 0) AS away_btb_travel_count_5,
  COALESCE(away_rest.btb_travel_count_10, 0) AS away_btb_travel_count_10,
  COALESCE(away_rest.travel_fatigue_score_5, 0.0) AS away_travel_fatigue_score_5,
  -- Rest advantage (positive means home team has more rest)
  COALESCE(home_rest.rest_days, 0) - COALESCE(away_rest.rest_days, 0) AS rest_advantage,
  -- Back-to-back with travel advantage (positive means home team has travel disadvantage)
  -- Note: We invert because travel is a disadvantage, so positive = home has more disadvantage
  CASE 
    WHEN COALESCE(home_rest.is_back_to_back_with_travel, FALSE) AND 
         NOT COALESCE(away_rest.is_back_to_back_with_travel, FALSE) THEN 1
    WHEN NOT COALESCE(home_rest.is_back_to_back_with_travel, FALSE) AND 
         COALESCE(away_rest.is_back_to_back_with_travel, FALSE) THEN -1
    ELSE 0
  END AS back_to_back_travel_advantage,
  -- Travel fatigue differentials (Iteration 11)
  -- Positive = home team has more travel fatigue (favor away)
  COALESCE(home_rest.btb_travel_count_5, 0) - COALESCE(away_rest.btb_travel_count_5, 0) AS btb_travel_count_5_diff,
  COALESCE(home_rest.btb_travel_count_10, 0) - COALESCE(away_rest.btb_travel_count_10, 0) AS btb_travel_count_10_diff,
  COALESCE(home_rest.travel_fatigue_score_5, 0.0) - COALESCE(away_rest.travel_fatigue_score_5, 0.0) AS travel_fatigue_score_5_diff
FROM raw_dev.games g
LEFT JOIN team_rest_enriched home_rest ON 
  home_rest.team_id = g.home_team_id 
  AND home_rest.game_id = g.game_id
LEFT JOIN team_rest_enriched away_rest ON 
  away_rest.team_id = g.away_team_id 
  AND away_rest.game_id = g.game_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds;
