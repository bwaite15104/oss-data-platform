MODEL (
    name intermediate.int_team_perf_after_wl,
    kind FULL,
    description 'Team performance after wins and losses - captures how teams respond to recent wins/losses. Teams that bounce back well after losses or maintain momentum after wins may have different characteristics.'
);

-- Calculate team performance after wins and losses
-- This captures psychological/mental aspects: how teams respond to recent results
-- Teams that bounce back well after losses or maintain momentum after wins may be more predictable

WITH team_game_results AS (
    SELECT 
        tb.team_id,
        tb.game_id,
        tb.game_date,
        g.winner_team_id = tb.team_id as is_win,
        CASE WHEN g.winner_team_id = tb.team_id THEN 1 ELSE 0 END as win_flag,
        g.home_score - g.away_score as point_margin,
        CASE WHEN tb.is_home THEN g.home_score - g.away_score ELSE g.away_score - g.home_score END as team_point_margin
    FROM staging.stg_team_boxscores tb
    JOIN staging.stg_games g ON tb.game_id = g.game_id
    WHERE g.is_completed
),

-- Identify previous game result for each game
game_with_prev_result AS (
    SELECT 
        t.*,
        LAG(t.is_win) OVER (
            PARTITION BY t.team_id 
            ORDER BY t.game_date, t.game_id
        ) as prev_game_is_win,
        LAG(t.win_flag) OVER (
            PARTITION BY t.team_id 
            ORDER BY t.game_date, t.game_id
        ) as prev_game_win_flag,
        LAG(t.team_point_margin) OVER (
            PARTITION BY t.team_id 
            ORDER BY t.game_date, t.game_id
        ) as prev_game_point_margin
    FROM team_game_results t
),

-- Calculate home team performance after wins/losses
home_after_wins_losses AS (
    SELECT 
        g.game_id,
        g.game_date,
        g.home_team_id as team_id,
        -- Performance after wins (last game was a win)
        AVG(CASE WHEN gwr.prev_game_is_win = true THEN gwr.win_flag ELSE NULL END) as home_win_pct_after_win,
        AVG(CASE WHEN gwr.prev_game_is_win = true THEN gwr.team_point_margin ELSE NULL END) as home_avg_point_diff_after_win,
        COUNT(CASE WHEN gwr.prev_game_is_win = true THEN 1 END) as home_game_count_after_win,
        -- Performance after losses (last game was a loss)
        AVG(CASE WHEN gwr.prev_game_is_win = false THEN gwr.win_flag ELSE NULL END) as home_win_pct_after_loss,
        AVG(CASE WHEN gwr.prev_game_is_win = false THEN gwr.team_point_margin ELSE NULL END) as home_avg_point_diff_after_loss,
        COUNT(CASE WHEN gwr.prev_game_is_win = false THEN 1 END) as home_game_count_after_loss,
        -- Performance after big wins (last game margin > 10)
        AVG(CASE WHEN gwr.prev_game_point_margin > 10 THEN gwr.win_flag ELSE NULL END) as home_win_pct_after_big_win,
        AVG(CASE WHEN gwr.prev_game_point_margin > 10 THEN gwr.team_point_margin ELSE NULL END) as home_avg_point_diff_after_big_win,
        COUNT(CASE WHEN gwr.prev_game_point_margin > 10 THEN 1 END) as home_game_count_after_big_win,
        -- Performance after big losses (last game margin < -10)
        AVG(CASE WHEN gwr.prev_game_point_margin < -10 THEN gwr.win_flag ELSE NULL END) as home_win_pct_after_big_loss,
        AVG(CASE WHEN gwr.prev_game_point_margin < -10 THEN gwr.team_point_margin ELSE NULL END) as home_avg_point_diff_after_big_loss,
        COUNT(CASE WHEN gwr.prev_game_point_margin < -10 THEN 1 END) as home_game_count_after_big_loss,
        -- Current game context: coming off a win or loss (cast bool to int for MAX)
        MAX(CASE WHEN gwr.game_id = g.game_id THEN (CASE WHEN gwr.prev_game_is_win THEN 1 ELSE 0 END) ELSE NULL END) as is_coming_off_win,
        MAX(CASE WHEN gwr.game_id = g.game_id THEN gwr.prev_game_point_margin ELSE NULL END) as prev_game_margin
    FROM staging.stg_games g
    JOIN game_with_prev_result gwr ON gwr.team_id = g.home_team_id 
        AND gwr.game_date < g.game_date
    WHERE g.is_completed
    GROUP BY g.game_id, g.game_date, g.home_team_id
),

-- Calculate away team performance after wins/losses
away_after_wins_losses AS (
    SELECT 
        g.game_id,
        g.game_date,
        g.away_team_id as team_id,
        -- Performance after wins (last game was a win)
        AVG(CASE WHEN gwr.prev_game_is_win = true THEN gwr.win_flag ELSE NULL END) as away_win_pct_after_win,
        AVG(CASE WHEN gwr.prev_game_is_win = true THEN gwr.team_point_margin ELSE NULL END) as away_avg_point_diff_after_win,
        COUNT(CASE WHEN gwr.prev_game_is_win = true THEN 1 END) as away_game_count_after_win,
        -- Performance after losses (last game was a loss)
        AVG(CASE WHEN gwr.prev_game_is_win = false THEN gwr.win_flag ELSE NULL END) as away_win_pct_after_loss,
        AVG(CASE WHEN gwr.prev_game_is_win = false THEN gwr.team_point_margin ELSE NULL END) as away_avg_point_diff_after_loss,
        COUNT(CASE WHEN gwr.prev_game_is_win = false THEN 1 END) as away_game_count_after_loss,
        -- Performance after big wins (last game margin > 10)
        AVG(CASE WHEN gwr.prev_game_point_margin > 10 THEN gwr.win_flag ELSE NULL END) as away_win_pct_after_big_win,
        AVG(CASE WHEN gwr.prev_game_point_margin > 10 THEN gwr.team_point_margin ELSE NULL END) as away_avg_point_diff_after_big_win,
        COUNT(CASE WHEN gwr.prev_game_point_margin > 10 THEN 1 END) as away_game_count_after_big_win,
        -- Performance after big losses (last game margin < -10)
        AVG(CASE WHEN gwr.prev_game_point_margin < -10 THEN gwr.win_flag ELSE NULL END) as away_win_pct_after_big_loss,
        AVG(CASE WHEN gwr.prev_game_point_margin < -10 THEN gwr.team_point_margin ELSE NULL END) as away_avg_point_diff_after_big_loss,
        COUNT(CASE WHEN gwr.prev_game_point_margin < -10 THEN 1 END) as away_game_count_after_big_loss,
        -- Current game context: coming off a win or loss (cast bool to int for MAX)
        MAX(CASE WHEN gwr.game_id = g.game_id THEN (CASE WHEN gwr.prev_game_is_win THEN 1 ELSE 0 END) ELSE NULL END) as is_coming_off_win,
        MAX(CASE WHEN gwr.game_id = g.game_id THEN gwr.prev_game_point_margin ELSE NULL END) as prev_game_margin
    FROM staging.stg_games g
    JOIN game_with_prev_result gwr ON gwr.team_id = g.away_team_id 
        AND gwr.game_date < g.game_date
    WHERE g.is_completed
    GROUP BY g.game_id, g.game_date, g.away_team_id
)

SELECT 
    g.game_id,
    -- Home team performance after wins/losses
    COALESCE(h.home_win_pct_after_win, 0.5) as home_win_pct_after_win,
    COALESCE(h.home_avg_point_diff_after_win, 0.0) as home_avg_point_diff_after_win,
    COALESCE(h.home_game_count_after_win, 0) as home_game_count_after_win,
    COALESCE(h.home_win_pct_after_loss, 0.5) as home_win_pct_after_loss,
    COALESCE(h.home_avg_point_diff_after_loss, 0.0) as home_avg_point_diff_after_loss,
    COALESCE(h.home_game_count_after_loss, 0) as home_game_count_after_loss,
    COALESCE(h.home_win_pct_after_big_win, 0.5) as home_win_pct_after_big_win,
    COALESCE(h.home_avg_point_diff_after_big_win, 0.0) as home_avg_point_diff_after_big_win,
    COALESCE(h.home_game_count_after_big_win, 0) as home_game_count_after_big_win,
    COALESCE(h.home_win_pct_after_big_loss, 0.5) as home_win_pct_after_big_loss,
    COALESCE(h.home_avg_point_diff_after_big_loss, 0.0) as home_avg_point_diff_after_big_loss,
    COALESCE(h.home_game_count_after_big_loss, 0) as home_game_count_after_big_loss,
    -- Away team performance after wins/losses
    COALESCE(a.away_win_pct_after_win, 0.5) as away_win_pct_after_win,
    COALESCE(a.away_avg_point_diff_after_win, 0.0) as away_avg_point_diff_after_win,
    COALESCE(a.away_game_count_after_win, 0) as away_game_count_after_win,
    COALESCE(a.away_win_pct_after_loss, 0.5) as away_win_pct_after_loss,
    COALESCE(a.away_avg_point_diff_after_loss, 0.0) as away_avg_point_diff_after_loss,
    COALESCE(a.away_game_count_after_loss, 0) as away_game_count_after_loss,
    COALESCE(a.away_win_pct_after_big_win, 0.5) as away_win_pct_after_big_win,
    COALESCE(a.away_avg_point_diff_after_big_win, 0.0) as away_avg_point_diff_after_big_win,
    COALESCE(a.away_game_count_after_big_win, 0) as away_game_count_after_big_win,
    COALESCE(a.away_win_pct_after_big_loss, 0.5) as away_win_pct_after_big_loss,
    COALESCE(a.away_avg_point_diff_after_big_loss, 0.0) as away_avg_point_diff_after_big_loss,
    COALESCE(a.away_game_count_after_big_loss, 0) as away_game_count_after_big_loss,
    -- Current game context indicators (is_coming_off_win is 0/1 from CTE)
    COALESCE(h.is_coming_off_win, 0) as home_is_coming_off_win,
    COALESCE(a.is_coming_off_win, 0) as away_is_coming_off_win,
    COALESCE(h.prev_game_margin, 0.0) as home_prev_game_margin,
    COALESCE(a.prev_game_margin, 0.0) as away_prev_game_margin
FROM staging.stg_games g
LEFT JOIN home_after_wins_losses h ON h.game_id = g.game_id
LEFT JOIN away_after_wins_losses a ON a.game_id = g.game_id
WHERE g.is_completed
