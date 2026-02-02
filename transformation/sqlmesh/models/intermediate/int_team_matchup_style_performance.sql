MODEL (
  name intermediate.int_team_matchup_perf,
  description 'Team performance by opponent matchup style. Shortened for Postgres 63-char limit.',
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column game_date
  ),
  start '1946-11-01',  -- Updated for full history backfill,
  grains [
    game_id
  ],
  cron '@daily',
);

-- Calculate team performance against different opponent styles
-- Refactored: use int_team_roll_lkp for opponent stats; replace final LATERALs with JOIN + GROUP BY.

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
    AND g.winner_team_id IS NOT NULL
    AND (g.game_date::date) >= DATE_TRUNC('year', (@start_ds::date)::timestamp)::date
    AND (g.game_date::date) < (@end_ds::date)
),

-- Team-game rows (each game from both perspectives) for chunk + same-season context
team_games AS (
  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id AS team_id,
    g.away_team_id AS opponent_team_id,
    CASE WHEN g.winner_team_id = g.home_team_id THEN 1 ELSE 0 END AS is_win
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.winner_team_id IS NOT NULL
    AND (g.game_date::date) >= DATE_TRUNC('year', (@start_ds::date)::timestamp)::date
    AND (g.game_date::date) < (@end_ds::date)

  UNION ALL

  SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.away_team_id AS team_id,
    g.home_team_id AS opponent_team_id,
    CASE WHEN g.winner_team_id = g.away_team_id THEN 1 ELSE 0 END AS is_win
  FROM raw_dev.games g
  WHERE g.home_score IS NOT NULL
    AND g.away_score IS NOT NULL
    AND g.winner_team_id IS NOT NULL
    AND (g.game_date::date) >= DATE_TRUNC('year', (@start_ds::date)::timestamp)::date
    AND (g.game_date::date) < (@end_ds::date)
),

-- Opponent's rolling stats at time of game via lookup (one LATERAL to games for game_id, then JOIN lookup)
opponent_styles AS (
  SELECT 
    tg.game_id,
    tg.game_date,
    tg.team_id,
    tg.opponent_team_id,
    tg.is_win,
    COALESCE(rs_opp.rolling_10_pace, 95.0) AS opponent_pace,
    COALESCE(rs_opp.rolling_10_off_rtg, 110.0) AS opponent_off_rtg,
    COALESCE(rs_opp.rolling_10_def_rtg, 110.0) AS opponent_def_rtg
  FROM team_games tg
  LEFT JOIN LATERAL (
    SELECT game_id
    FROM raw_dev.games g2
    WHERE (g2.home_team_id = tg.opponent_team_id OR g2.away_team_id = tg.opponent_team_id)
      AND (g2.game_date::date) < tg.game_date
      AND g2.home_score IS NOT NULL
    ORDER BY (g2.game_date::date) DESC
    LIMIT 1
  ) opp_game ON TRUE
  LEFT JOIN intermediate.int_team_roll_lkp rs_opp
    ON rs_opp.game_id = opp_game.game_id
   AND rs_opp.team_id = tg.opponent_team_id
),

league_averages AS (
  SELECT 
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY rolling_10_pace) AS median_pace,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY rolling_10_off_rtg) AS median_off_rtg,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY rolling_10_def_rtg) AS median_def_rtg
  FROM intermediate.int_team_rolling_stats rs
  WHERE rs.game_date >= CURRENT_DATE - INTERVAL '30 days'
    AND rolling_10_pace IS NOT NULL
    AND rolling_10_off_rtg IS NOT NULL
    AND rolling_10_def_rtg IS NOT NULL
),

-- Home team's last 20 games (at home) with opponent style; aggregate win % by style
home_prev AS (
  SELECT 
    curr.game_id,
    curr.home_team_id,
    prev_g.game_id AS prev_game_id,
    ROW_NUMBER() OVER (PARTITION BY curr.game_id ORDER BY prev_g.game_date DESC) AS rn
  FROM raw_dev.games curr
  JOIN completed_games prev_g
    ON prev_g.home_team_id = curr.home_team_id
   AND prev_g.game_date < (curr.game_date::date)
   AND prev_g.game_date >= (DATE_TRUNC('year', (curr.game_date::date)::timestamp)::date)
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
    AND (curr.game_date::date) >= (@start_ds::date)
    AND (curr.game_date::date) < (@end_ds::date)
),
home_style_agg AS (
  SELECT 
    hp.game_id,
    AVG(CASE WHEN os.opponent_pace > COALESCE(la.median_pace, 95.0) THEN os.is_win::float END) AS fast_paced,
    AVG(CASE WHEN os.opponent_pace < COALESCE(la.median_pace, 95.0) THEN os.is_win::float END) AS slow_paced,
    AVG(CASE WHEN os.opponent_off_rtg > COALESCE(la.median_off_rtg, 110.0) THEN os.is_win::float END) AS high_scoring,
    AVG(CASE WHEN os.opponent_def_rtg < COALESCE(la.median_def_rtg, 110.0) THEN os.is_win::float END) AS defensive
  FROM home_prev hp
  JOIN opponent_styles os ON os.game_id = hp.prev_game_id AND os.team_id = hp.home_team_id
  CROSS JOIN league_averages la
  WHERE hp.rn <= 20
  GROUP BY hp.game_id
),

-- Away team's last 20 games (on road) with opponent style
away_prev AS (
  SELECT 
    curr.game_id,
    curr.away_team_id,
    prev_g.game_id AS prev_game_id,
    ROW_NUMBER() OVER (PARTITION BY curr.game_id ORDER BY prev_g.game_date DESC) AS rn
  FROM raw_dev.games curr
  JOIN completed_games prev_g
    ON prev_g.away_team_id = curr.away_team_id
   AND prev_g.game_date < (curr.game_date::date)
   AND prev_g.game_date >= (DATE_TRUNC('year', (curr.game_date::date)::timestamp)::date)
  WHERE curr.home_score IS NOT NULL
    AND curr.away_score IS NOT NULL
    AND (curr.game_date::date) >= (@start_ds::date)
    AND (curr.game_date::date) < (@end_ds::date)
),
away_style_agg AS (
  SELECT 
    ap.game_id,
    AVG(CASE WHEN os.opponent_pace > COALESCE(la.median_pace, 95.0) THEN os.is_win::float END) AS fast_paced,
    AVG(CASE WHEN os.opponent_pace < COALESCE(la.median_pace, 95.0) THEN os.is_win::float END) AS slow_paced,
    AVG(CASE WHEN os.opponent_off_rtg > COALESCE(la.median_off_rtg, 110.0) THEN os.is_win::float END) AS high_scoring,
    AVG(CASE WHEN os.opponent_def_rtg < COALESCE(la.median_def_rtg, 110.0) THEN os.is_win::float END) AS defensive
  FROM away_prev ap
  JOIN opponent_styles os ON os.game_id = ap.prev_game_id AND os.team_id = ap.away_team_id
  CROSS JOIN league_averages la
  WHERE ap.rn <= 20
  GROUP BY ap.game_id
)

SELECT 
  g.game_id,
  g.game_date::date AS game_date,
  g.home_team_id,
  g.away_team_id,
  COALESCE(home.fast_paced, 0.5) AS home_win_pct_vs_fast_paced,
  COALESCE(home.slow_paced, 0.5) AS home_win_pct_vs_slow_paced,
  COALESCE(home.high_scoring, 0.5) AS home_win_pct_vs_high_scoring,
  COALESCE(home.defensive, 0.5) AS home_win_pct_vs_defensive,
  COALESCE(away.fast_paced, 0.5) AS away_win_pct_vs_fast_paced,
  COALESCE(away.slow_paced, 0.5) AS away_win_pct_vs_slow_paced,
  COALESCE(away.high_scoring, 0.5) AS away_win_pct_vs_high_scoring,
  COALESCE(away.defensive, 0.5) AS away_win_pct_vs_defensive
FROM raw_dev.games g
LEFT JOIN home_style_agg home ON home.game_id = g.game_id
LEFT JOIN away_style_agg away ON away.game_id = g.game_id
WHERE g.home_score IS NOT NULL
  AND g.away_score IS NOT NULL
  AND (g.game_date::date) >= (@start_ds::date)
  AND (g.game_date::date) < (@end_ds::date);
