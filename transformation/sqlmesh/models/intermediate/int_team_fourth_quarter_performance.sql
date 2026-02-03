MODEL (
    name intermediate.int_team_4q_perf,
    kind INCREMENTAL_BY_TIME_RANGE (
        time_column game_date,
        batch_size 30
    ),
    start '1946-11-01',
    grains [game_id],
    cron '@daily',
    description 'Team fourth quarter performance metrics - captures ability to close games, conditioning, and late-game execution. Calculates rolling averages of fourth quarter scoring, net rating, and win percentage when winning the fourth quarter.'
);

-- Calculate fourth quarter performance metrics for each team
-- q4_points from raw_dev.team_boxscores (not in staging view to avoid snapshot churn)
WITH team_q4_stats AS (
    SELECT 
        tb.game_id,
        tb.game_date,
        tb.team_id,
        tb.is_home,
        COALESCE(rtb.q4_points, 0) as q4_points,
        -- Get opponent's Q4 points for net rating calculation
        opp_raw.q4_points as opp_q4_points,
        -- Calculate Q4 net rating (Q4 points scored - Q4 points allowed)
        COALESCE(rtb.q4_points, 0) - COALESCE(opp_raw.q4_points, 0) as q4_net_rating,
        -- Determine if team won the fourth quarter
        CASE 
            WHEN COALESCE(rtb.q4_points, 0) > COALESCE(opp_raw.q4_points, 0) THEN 1 
            ELSE 0 
        END as won_q4,
        -- Determine if team won the game
        CASE 
            WHEN g.winner_team_id = tb.team_id THEN 1 
            ELSE 0 
        END as won_game
    FROM staging.stg_team_boxscores tb
    INNER JOIN staging.stg_games g ON g.game_id = tb.game_id
    LEFT JOIN raw_dev.team_boxscores rtb ON rtb.game_id = tb.game_id AND rtb.team_id = tb.team_id
    LEFT JOIN raw_dev.team_boxscores opp_raw ON opp_raw.game_id = tb.game_id AND opp_raw.team_id != tb.team_id
    WHERE rtb.q4_points IS NOT NULL
        AND g.winner_team_id IS NOT NULL
        AND tb.game_date < @end_ds
),

-- Calculate rolling fourth quarter performance metrics
rolling_q4_performance AS (
    SELECT 
        team_id,
        game_date,
        game_id,
        is_home,
        q4_points,
        q4_net_rating,
        won_q4,
        won_game,
        -- Rolling 5-game averages
        AVG(q4_points) OVER (
            PARTITION BY team_id 
            ORDER BY game_date 
            ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
        ) as rolling_5_q4_ppg,
        AVG(q4_net_rating) OVER (
            PARTITION BY team_id 
            ORDER BY game_date 
            ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
        ) as rolling_5_q4_net_rtg,
        AVG(CASE WHEN won_q4 = 1 THEN 1.0 ELSE 0.0 END) OVER (
            PARTITION BY team_id 
            ORDER BY game_date 
            ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
        ) as rolling_5_q4_win_pct,
        -- Rolling 10-game averages
        AVG(q4_points) OVER (
            PARTITION BY team_id 
            ORDER BY game_date 
            ROWS BETWEEN 9 PRECEDING AND CURRENT ROW
        ) as rolling_10_q4_ppg,
        AVG(q4_net_rating) OVER (
            PARTITION BY team_id 
            ORDER BY game_date 
            ROWS BETWEEN 9 PRECEDING AND CURRENT ROW
        ) as rolling_10_q4_net_rtg,
        AVG(CASE WHEN won_q4 = 1 THEN 1.0 ELSE 0.0 END) OVER (
            PARTITION BY team_id 
            ORDER BY game_date 
            ROWS BETWEEN 9 PRECEDING AND CURRENT ROW
        ) as rolling_10_q4_win_pct,
        -- Win percentage when winning the fourth quarter (season-to-date)
        AVG(CASE WHEN won_q4 = 1 AND won_game = 1 THEN 1.0 ELSE 0.0 END) OVER (
            PARTITION BY team_id 
            ORDER BY game_date 
            ROWS UNBOUNDED PRECEDING
        ) / NULLIF(
            AVG(CASE WHEN won_q4 = 1 THEN 1.0 ELSE 0.0 END) OVER (
                PARTITION BY team_id 
                ORDER BY game_date 
                ROWS UNBOUNDED PRECEDING
            ),
            0
        ) as season_q4_win_pct_when_won_q4,
        -- Count of games where team won Q4 (for sample size)
        COUNT(*) FILTER (WHERE won_q4 = 1) OVER (
            PARTITION BY team_id 
            ORDER BY game_date 
            ROWS UNBOUNDED PRECEDING
        ) as q4_wins_count_season
    FROM team_q4_stats
)

SELECT 
    team_id,
    game_date,
    game_id,
    is_home,
    q4_points,
    q4_net_rating,
    won_q4,
    rolling_5_q4_ppg,
    rolling_5_q4_net_rtg,
    rolling_5_q4_win_pct,
    rolling_10_q4_ppg,
    rolling_10_q4_net_rtg,
    rolling_10_q4_win_pct,
    COALESCE(season_q4_win_pct_when_won_q4, 0.5) as season_q4_win_pct_when_won_q4,  -- Default to 0.5 (league average) if no data
    q4_wins_count_season
FROM rolling_q4_performance
WHERE game_date >= @start_ds
  AND game_date < @end_ds
ORDER BY team_id, game_date
