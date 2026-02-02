MODEL (
    name intermediate.int_team_outcome_qual,
    kind INCREMENTAL_BY_TIME_RANGE (
        time_column game_date,
        batch_size 30
    ),
    start '1946-11-01',  -- Updated for full history backfill,
    grains [
        game_id
    ],
    cron '@daily',
);

-- Calculate game outcome quality features
-- Captures how teams win/lose (close vs blowouts) in recent games
-- Refactored: 16 correlated subqueries replaced with team_games + window aggregation (opt iteration 11)
WITH completed_games AS (
    SELECT 
        g.game_id,
        g.game_date::date AS game_date,
        g.home_team_id,
        g.away_team_id,
        g.home_score,
        g.away_score,
        g.winner_team_id,
        ABS(g.home_score - g.away_score) AS point_margin
    FROM raw_dev.games g
    WHERE g.home_score IS NOT NULL
        AND g.away_score IS NOT NULL
        AND g.winner_team_id IS NOT NULL
        AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- One row per team per game (home and away perspective)
team_games AS (
    SELECT 
        g.game_id,
        g.game_date,
        g.home_team_id AS team_id,
        g.point_margin,
        g.winner_team_id,
        CASE WHEN g.winner_team_id = g.home_team_id THEN 1 ELSE 0 END AS is_win,
        CASE WHEN g.point_margin <= 5 THEN 1 ELSE 0 END AS is_close,
        CASE WHEN g.point_margin > 15 THEN 1 ELSE 0 END AS is_blowout
    FROM completed_games g
    UNION ALL
    SELECT 
        g.game_id,
        g.game_date,
        g.away_team_id AS team_id,
        g.point_margin,
        g.winner_team_id,
        CASE WHEN g.winner_team_id = g.away_team_id THEN 1 ELSE 0 END AS is_win,
        CASE WHEN g.point_margin <= 5 THEN 1 ELSE 0 END AS is_close,
        CASE WHEN g.point_margin > 15 THEN 1 ELSE 0 END AS is_blowout
    FROM completed_games g
),

-- Window aggregation: previous 5 and 10 games per team (no correlated subqueries)
outcome_quality_windowed AS (
    SELECT 
        tg.game_id,
        tg.game_date,
        tg.team_id,
        -- Last 5 games
        AVG(tg.point_margin) FILTER (WHERE tg.winner_team_id = tg.team_id)
            OVER w5 AS avg_margin_in_wins_5,
        AVG(tg.point_margin) FILTER (WHERE tg.winner_team_id IS NOT NULL AND tg.winner_team_id != tg.team_id)
            OVER w5 AS avg_margin_in_losses_5,
        AVG(tg.is_win::numeric) FILTER (WHERE tg.is_close = 1)
            OVER w5 AS close_game_win_pct_5,
        AVG(tg.is_win::numeric) FILTER (WHERE tg.is_blowout = 1)
            OVER w5 AS blowout_game_win_pct_5,
        -- Last 10 games
        AVG(tg.point_margin) FILTER (WHERE tg.winner_team_id = tg.team_id)
            OVER w10 AS avg_margin_in_wins_10,
        AVG(tg.point_margin) FILTER (WHERE tg.winner_team_id IS NOT NULL AND tg.winner_team_id != tg.team_id)
            OVER w10 AS avg_margin_in_losses_10,
        AVG(tg.is_win::numeric) FILTER (WHERE tg.is_close = 1)
            OVER w10 AS close_game_win_pct_10,
        AVG(tg.is_win::numeric) FILTER (WHERE tg.is_blowout = 1)
            OVER w10 AS blowout_game_win_pct_10
    FROM team_games tg
    WINDOW 
        w5  AS (PARTITION BY tg.team_id ORDER BY tg.game_date ASC, tg.game_id ASC ROWS BETWEEN 5 PRECEDING AND 1 PRECEDING),
        w10 AS (PARTITION BY tg.team_id ORDER BY tg.game_date ASC, tg.game_id ASC ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING)
),

-- One row per (game_id, team_id) with outcome quality metrics (before this game)
outcome_quality_per_team AS (
    SELECT 
        game_id,
        game_date,
        team_id,
        avg_margin_in_wins_5,
        avg_margin_in_losses_5,
        close_game_win_pct_5,
        blowout_game_win_pct_5,
        avg_margin_in_wins_10,
        avg_margin_in_losses_10,
        close_game_win_pct_10,
        blowout_game_win_pct_10
    FROM outcome_quality_windowed
)

SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    -- Home team outcome quality features
    COALESCE(hoq.avg_margin_in_wins_5, 0.0) AS home_avg_margin_in_wins_5,
    COALESCE(hoq.avg_margin_in_losses_5, 0.0) AS home_avg_margin_in_losses_5,
    COALESCE(hoq.close_game_win_pct_5, 0.5) AS home_close_game_win_pct_5,
    COALESCE(hoq.blowout_game_win_pct_5, 0.5) AS home_blowout_game_win_pct_5,
    COALESCE(hoq.avg_margin_in_wins_10, 0.0) AS home_avg_margin_in_wins_10,
    COALESCE(hoq.avg_margin_in_losses_10, 0.0) AS home_avg_margin_in_losses_10,
    COALESCE(hoq.close_game_win_pct_10, 0.5) AS home_close_game_win_pct_10,
    COALESCE(hoq.blowout_game_win_pct_10, 0.5) AS home_blowout_game_win_pct_10,
    -- Away team outcome quality features
    COALESCE(aoq.avg_margin_in_wins_5, 0.0) AS away_avg_margin_in_wins_5,
    COALESCE(aoq.avg_margin_in_losses_5, 0.0) AS away_avg_margin_in_losses_5,
    COALESCE(aoq.close_game_win_pct_5, 0.5) AS away_close_game_win_pct_5,
    COALESCE(aoq.blowout_game_win_pct_5, 0.5) AS away_blowout_game_win_pct_5,
    COALESCE(aoq.avg_margin_in_wins_10, 0.0) AS away_avg_margin_in_wins_10,
    COALESCE(aoq.avg_margin_in_losses_10, 0.0) AS away_avg_margin_in_losses_10,
    COALESCE(aoq.close_game_win_pct_10, 0.5) AS away_close_game_win_pct_10,
    COALESCE(aoq.blowout_game_win_pct_10, 0.5) AS away_blowout_game_win_pct_10
FROM raw_dev.games g
LEFT JOIN outcome_quality_per_team hoq ON hoq.game_id = g.game_id AND hoq.team_id = g.home_team_id
LEFT JOIN outcome_quality_per_team aoq ON aoq.game_id = g.game_id AND aoq.team_id = g.away_team_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds
  AND g.home_score IS NOT NULL
  AND g.away_score IS NOT NULL;
