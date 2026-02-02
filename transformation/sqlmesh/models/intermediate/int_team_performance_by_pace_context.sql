MODEL (
  name intermediate.int_team_perf_by_pace_ctx,
  description 'Team performance by pace context. Short name for Postgres 63-char limit.',
  kind INCREMENTAL_BY_TIME_RANGE (
        time_column game_date
    ),
    start '1946-11-01',  -- Updated for full history backfill,
    grains [
        game_id
    ],
    cron '@daily',
    description 'Team performance by game pace context - how teams perform in fast-paced vs slow-paced games (iteration 54)'
);

-- Calculate team performance in different pace contexts
-- Fast-paced games: pace > league median for that season
-- Slow-paced games: pace <= league median for that season
-- Game pace: possessions per team (stg_team_boxscores has no pace column; derive from box score)
WITH completed_games AS (
    SELECT 
        g.game_id,
        g.game_date::date AS game_date,
        g.home_team_id,
        g.away_team_id,
        g.winner_team_id,
        COALESCE(
          (tb_home.fg_attempted + 0.44 * tb_home.ft_attempted + tb_home.turnovers
           + tb_away.fg_attempted + 0.44 * tb_away.ft_attempted + tb_away.turnovers) / 2.0,
          0
        ) AS game_pace,
        tb_home.points AS home_points,
        tb_away.points AS away_points,
        tb_home.points - tb_away.points AS home_point_diff
    FROM raw_dev.games g
    JOIN staging.stg_team_boxscores tb_home ON 
        tb_home.game_id = g.game_id 
        AND tb_home.team_id = g.home_team_id
        AND tb_home.is_home = true
    JOIN staging.stg_team_boxscores tb_away ON 
        tb_away.game_id = g.game_id 
        AND tb_away.team_id = g.away_team_id
        AND tb_away.is_home = false
    WHERE g.home_score IS NOT NULL
        AND g.away_score IS NOT NULL
        AND g.winner_team_id IS NOT NULL
        AND g.game_date < @end_ds  -- Include all historical games before end of chunk for context
),

-- Calculate league median pace by season
league_pace_median AS (
    SELECT 
        DATE_PART('year', game_date::date) AS season_year,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY game_pace) AS median_pace
    FROM completed_games
    GROUP BY DATE_PART('year', game_date::date)
),

-- Calculate home team performance in fast-paced games (pace > median)
home_fast_pace_performance AS (
    SELECT 
        curr.game_id,
        curr.game_date::date AS game_date,
        curr.home_team_id AS team_id,
        -- Win percentage in fast-paced games this season
        AVG(CASE 
            WHEN prev_g.game_pace > lpm.median_pace 
                AND prev_g.winner_team_id = prev_g.home_team_id THEN 1.0
            WHEN prev_g.game_pace > lpm.median_pace 
                AND prev_g.winner_team_id != prev_g.home_team_id THEN 0.0
            ELSE NULL
        END) AS home_fast_pace_win_pct,
        -- Game count in fast-paced games
        COUNT(CASE WHEN prev_g.game_pace > lpm.median_pace THEN 1 END) AS home_fast_pace_game_count,
        -- Average point differential in fast-paced games
        AVG(CASE 
            WHEN prev_g.game_pace > lpm.median_pace THEN prev_g.home_point_diff
            ELSE NULL
        END) AS home_fast_pace_avg_point_diff
    FROM raw_dev.games curr
    LEFT JOIN completed_games prev_g ON 
        (prev_g.home_team_id = curr.home_team_id OR prev_g.away_team_id = curr.home_team_id)
        AND prev_g.game_date < curr.game_date::date
        AND DATE_PART('year', (prev_g.game_date)::date) = DATE_PART('year', (curr.game_date)::date)
    LEFT JOIN league_pace_median lpm ON 
        DATE_PART('year', (prev_g.game_date)::date) = lpm.season_year
    WHERE curr.home_score IS NOT NULL
        AND curr.away_score IS NOT NULL
        AND curr.game_date < @end_ds  -- Only process games up to end of chunk
    GROUP BY curr.game_id, curr.game_date, curr.home_team_id
),

-- Calculate home team performance in slow-paced games (pace <= median)
home_slow_pace_performance AS (
    SELECT 
        curr.game_id,
        curr.game_date::date AS game_date,
        curr.home_team_id AS team_id,
        -- Win percentage in slow-paced games this season
        AVG(CASE 
            WHEN prev_g.game_pace <= lpm.median_pace 
                AND prev_g.winner_team_id = prev_g.home_team_id THEN 1.0
            WHEN prev_g.game_pace <= lpm.median_pace 
                AND prev_g.winner_team_id != prev_g.home_team_id THEN 0.0
            ELSE NULL
        END) AS home_slow_pace_win_pct,
        -- Game count in slow-paced games
        COUNT(CASE WHEN prev_g.game_pace <= lpm.median_pace THEN 1 END) AS home_slow_pace_game_count,
        -- Average point differential in slow-paced games
        AVG(CASE 
            WHEN prev_g.game_pace <= lpm.median_pace THEN prev_g.home_point_diff
            ELSE NULL
        END) AS home_slow_pace_avg_point_diff
    FROM raw_dev.games curr
    LEFT JOIN completed_games prev_g ON 
        (prev_g.home_team_id = curr.home_team_id OR prev_g.away_team_id = curr.home_team_id)
        AND prev_g.game_date < curr.game_date::date
        AND DATE_PART('year', (prev_g.game_date)::date) = DATE_PART('year', (curr.game_date)::date)
    LEFT JOIN league_pace_median lpm ON 
        DATE_PART('year', (prev_g.game_date)::date) = lpm.season_year
    WHERE curr.home_score IS NOT NULL
        AND curr.away_score IS NOT NULL
        AND curr.game_date < @end_ds  -- Only process games up to end of chunk
    GROUP BY curr.game_id, curr.game_date, curr.home_team_id
),

-- Calculate away team performance in fast-paced games
away_fast_pace_performance AS (
    SELECT 
        curr.game_id,
        curr.game_date::date AS game_date,
        curr.away_team_id AS team_id,
        -- Win percentage in fast-paced games this season (from away team's perspective)
        AVG(CASE 
            WHEN prev_g.game_pace > lpm.median_pace 
                AND prev_g.winner_team_id = prev_g.away_team_id THEN 1.0
            WHEN prev_g.game_pace > lpm.median_pace 
                AND prev_g.winner_team_id != prev_g.home_team_id THEN 0.0
            ELSE NULL
        END) AS away_fast_pace_win_pct,
        -- Game count in fast-paced games
        COUNT(CASE WHEN prev_g.game_pace > lpm.median_pace THEN 1 END) AS away_fast_pace_game_count,
        -- Average point differential in fast-paced games (from away team's perspective)
        AVG(CASE 
            WHEN prev_g.game_pace > lpm.median_pace THEN -prev_g.home_point_diff
            ELSE NULL
        END) AS away_fast_pace_avg_point_diff
    FROM raw_dev.games curr
    LEFT JOIN completed_games prev_g ON 
        (prev_g.home_team_id = curr.away_team_id OR prev_g.away_team_id = curr.away_team_id)
        AND prev_g.game_date < curr.game_date::date
        AND DATE_PART('year', (prev_g.game_date)::date) = DATE_PART('year', (curr.game_date)::date)
    LEFT JOIN league_pace_median lpm ON 
        DATE_PART('year', (prev_g.game_date)::date) = lpm.season_year
    WHERE curr.home_score IS NOT NULL
        AND curr.away_score IS NOT NULL
    GROUP BY curr.game_id, curr.game_date, curr.away_team_id
),

-- Calculate away team performance in slow-paced games
away_slow_pace_performance AS (
    SELECT 
        curr.game_id,
        curr.game_date::date AS game_date,
        curr.away_team_id AS team_id,
        -- Win percentage in slow-paced games this season (from away team's perspective)
        AVG(CASE 
            WHEN prev_g.game_pace <= lpm.median_pace 
                AND prev_g.winner_team_id = prev_g.away_team_id THEN 1.0
            WHEN prev_g.game_pace <= lpm.median_pace 
                AND prev_g.winner_team_id != prev_g.home_team_id THEN 0.0
            ELSE NULL
        END) AS away_slow_pace_win_pct,
        -- Game count in slow-paced games
        COUNT(CASE WHEN prev_g.game_pace <= lpm.median_pace THEN 1 END) AS away_slow_pace_game_count,
        -- Average point differential in slow-paced games (from away team's perspective)
        AVG(CASE 
            WHEN prev_g.game_pace <= lpm.median_pace THEN -prev_g.home_point_diff
            ELSE NULL
        END) AS away_slow_pace_avg_point_diff
    FROM raw_dev.games curr
    LEFT JOIN completed_games prev_g ON 
        (prev_g.home_team_id = curr.away_team_id OR prev_g.away_team_id = curr.away_team_id)
        AND prev_g.game_date < curr.game_date::date
        AND DATE_PART('year', (prev_g.game_date)::date) = DATE_PART('year', (curr.game_date)::date)
    LEFT JOIN league_pace_median lpm ON 
        DATE_PART('year', (prev_g.game_date)::date) = lpm.season_year
    WHERE curr.home_score IS NOT NULL
        AND curr.away_score IS NOT NULL
    GROUP BY curr.game_id, curr.game_date, curr.away_team_id
)

SELECT 
    g.game_id,
    g.game_date::date AS game_date,
    g.home_team_id,
    g.away_team_id,
    -- Home team pace performance
    COALESCE(hfp.home_fast_pace_win_pct, 0.5) AS home_fast_pace_win_pct,
    COALESCE(hfp.home_fast_pace_game_count, 0) AS home_fast_pace_game_count,
    COALESCE(hfp.home_fast_pace_avg_point_diff, 0.0) AS home_fast_pace_avg_point_diff,
    COALESCE(hsp.home_slow_pace_win_pct, 0.5) AS home_slow_pace_win_pct,
    COALESCE(hsp.home_slow_pace_game_count, 0) AS home_slow_pace_game_count,
    COALESCE(hsp.home_slow_pace_avg_point_diff, 0.0) AS home_slow_pace_avg_point_diff,
    -- Away team pace performance
    COALESCE(afp.away_fast_pace_win_pct, 0.5) AS away_fast_pace_win_pct,
    COALESCE(afp.away_fast_pace_game_count, 0) AS away_fast_pace_game_count,
    COALESCE(afp.away_fast_pace_avg_point_diff, 0.0) AS away_fast_pace_avg_point_diff,
    COALESCE(asp.away_slow_pace_win_pct, 0.5) AS away_slow_pace_win_pct,
    COALESCE(asp.away_slow_pace_game_count, 0) AS away_slow_pace_game_count,
    COALESCE(asp.away_slow_pace_avg_point_diff, 0.0) AS away_slow_pace_avg_point_diff
FROM raw_dev.games g
LEFT JOIN home_fast_pace_performance hfp ON 
    hfp.game_id = g.game_id
    AND hfp.team_id = g.home_team_id
LEFT JOIN home_slow_pace_performance hsp ON 
    hsp.game_id = g.game_id
    AND hsp.team_id = g.home_team_id
LEFT JOIN away_fast_pace_performance afp ON 
    afp.game_id = g.game_id
    AND afp.team_id = g.away_team_id
LEFT JOIN away_slow_pace_performance asp ON 
    asp.game_id = g.game_id
    AND asp.team_id = g.away_team_id
WHERE g.game_date >= @start_ds
  AND g.game_date < @end_ds
