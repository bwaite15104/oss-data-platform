MODEL (
    name marts.mart_team_standings,
    kind VIEW,
    description 'Current team standings with streaks and recent form'
);

-- Calculate current standings with recent form indicators
WITH team_games AS (
    SELECT 
        tb.team_id,
        t.team_name,
        t.team_abbreviation,
        t.conference,
        t.division,
        g.game_date,
        g.game_id,
        g.winner_team_id = tb.team_id as is_win,
        tb.is_home,
        ROW_NUMBER() OVER (PARTITION BY tb.team_id ORDER BY g.game_date DESC, g.game_id DESC) as recency_rank
    FROM staging.stg_team_boxscores tb
    JOIN staging.stg_games g ON tb.game_id = g.game_id
    JOIN staging.stg_teams t ON tb.team_id = t.team_id
    WHERE g.is_completed
),

-- Calculate current streak
streak_calc AS (
    SELECT 
        team_id,
        is_win as first_result,
        COUNT(*) as streak_length
    FROM (
        SELECT 
            team_id,
            is_win,
            recency_rank,
            recency_rank - ROW_NUMBER() OVER (PARTITION BY team_id, is_win ORDER BY recency_rank) as grp
        FROM team_games
        WHERE recency_rank <= 20  -- Look at last 20 games for streak
    ) streak_groups
    WHERE grp = 0  -- Only consecutive from most recent
    GROUP BY team_id, is_win
),

-- Recent form
recent_form AS (
    SELECT 
        team_id,
        SUM(CASE WHEN is_win THEN 1 ELSE 0 END) as last_10_wins,
        SUM(CASE WHEN NOT is_win THEN 1 ELSE 0 END) as last_10_losses
    FROM team_games
    WHERE recency_rank <= 10
    GROUP BY team_id
)

SELECT 
    s.team_id,
    tg.team_name,
    tg.team_abbreviation,
    tg.conference,
    tg.division,
    
    -- Overall record
    s.wins,
    s.losses,
    s.win_pct,
    
    -- Rankings (within conference)
    RANK() OVER (PARTITION BY tg.conference ORDER BY s.win_pct DESC, s.point_diff DESC) as conf_rank,
    RANK() OVER (ORDER BY s.win_pct DESC, s.point_diff DESC) as league_rank,
    
    -- Home/Away records
    s.home_wins,
    s.home_losses,
    s.away_wins,
    s.away_losses,
    
    -- Scoring
    s.ppg,
    s.opp_ppg,
    s.point_diff,
    
    -- Shooting
    s.fg_pct,
    s.fg3_pct,
    s.ft_pct,
    
    -- Recent form
    rf.last_10_wins,
    rf.last_10_losses,
    rf.last_10_wins || '-' || rf.last_10_losses as last_10,
    
    -- Current streak
    CASE 
        WHEN sc.first_result THEN 'W' || sc.streak_length
        ELSE 'L' || sc.streak_length
    END as streak,
    sc.first_result as on_winning_streak,
    sc.streak_length,
    
    -- Games back (from conference leader)
    ROUND((FIRST_VALUE(s.wins) OVER (PARTITION BY tg.conference ORDER BY s.win_pct DESC) - s.wins + 
           (s.losses - FIRST_VALUE(s.losses) OVER (PARTITION BY tg.conference ORDER BY s.win_pct DESC)))::DECIMAL / 2, 1) as games_back
    
FROM intermediate.int_team_season_stats s
JOIN (SELECT DISTINCT team_id, team_name, team_abbreviation, conference, division FROM team_games) tg 
    ON s.team_id = tg.team_id
LEFT JOIN streak_calc sc ON s.team_id = sc.team_id
LEFT JOIN recent_form rf ON s.team_id = rf.team_id
ORDER BY conference, conf_rank
