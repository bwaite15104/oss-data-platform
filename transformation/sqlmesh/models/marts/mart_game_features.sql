MODEL (
    name marts.mart_game_features,
    kind VIEW,
    description 'ML-ready game features for prediction models'
);

-- Build feature set for each game combining home and away team stats
WITH game_base AS (
    SELECT 
        g.game_id,
        g.game_date,
        g.home_team_id,
        g.away_team_id,
        g.home_score,
        g.away_score,
        g.winner_team_id,
        g.is_completed,
        g.is_overtime,
        
        -- Target variable: 1 if home team wins, 0 otherwise
        CASE WHEN g.winner_team_id = g.home_team_id THEN 1 ELSE 0 END as home_win
        
    FROM staging.stg_games g
    WHERE g.is_completed
),

-- Get most recent rolling stats for each team BEFORE this game
home_stats AS (
    SELECT DISTINCT ON (g.game_id)
        g.game_id,
        r.rolling_5_ppg as home_rolling_5_ppg,
        r.rolling_5_opp_ppg as home_rolling_5_opp_ppg,
        r.rolling_5_win_pct as home_rolling_5_win_pct,
        r.rolling_5_apg as home_rolling_5_apg,
        r.rolling_5_rpg as home_rolling_5_rpg,
        r.rolling_5_fg_pct as home_rolling_5_fg_pct,
        r.rolling_5_fg3_pct as home_rolling_5_fg3_pct,
        r.wins_last_5 as home_wins_last_5,
        r.rolling_10_ppg as home_rolling_10_ppg,
        r.rolling_10_opp_ppg as home_rolling_10_opp_ppg,
        r.rolling_10_win_pct as home_rolling_10_win_pct,
        r.wins_last_10 as home_wins_last_10
    FROM game_base g
    JOIN intermediate.int_team_rolling_stats r 
        ON r.team_id = g.home_team_id 
        AND r.game_date < g.game_date
    ORDER BY g.game_id, r.game_date DESC
),

away_stats AS (
    SELECT DISTINCT ON (g.game_id)
        g.game_id,
        r.rolling_5_ppg as away_rolling_5_ppg,
        r.rolling_5_opp_ppg as away_rolling_5_opp_ppg,
        r.rolling_5_win_pct as away_rolling_5_win_pct,
        r.rolling_5_apg as away_rolling_5_apg,
        r.rolling_5_rpg as away_rolling_5_rpg,
        r.rolling_5_fg_pct as away_rolling_5_fg_pct,
        r.rolling_5_fg3_pct as away_rolling_5_fg3_pct,
        r.wins_last_5 as away_wins_last_5,
        r.rolling_10_ppg as away_rolling_10_ppg,
        r.rolling_10_opp_ppg as away_rolling_10_opp_ppg,
        r.rolling_10_win_pct as away_rolling_10_win_pct,
        r.wins_last_10 as away_wins_last_10
    FROM game_base g
    JOIN intermediate.int_team_rolling_stats r 
        ON r.team_id = g.away_team_id 
        AND r.game_date < g.game_date
    ORDER BY g.game_id, r.game_date DESC
),

-- Get season stats for context
home_season AS (
    SELECT 
        team_id,
        games_played as home_season_games,
        win_pct as home_season_win_pct,
        ppg as home_season_ppg,
        opp_ppg as home_season_opp_ppg,
        point_diff as home_season_point_diff,
        home_wins,
        home_losses,
        fg_pct as home_season_fg_pct,
        fg3_pct as home_season_fg3_pct
    FROM intermediate.int_team_season_stats
),

away_season AS (
    SELECT 
        team_id,
        games_played as away_season_games,
        win_pct as away_season_win_pct,
        ppg as away_season_ppg,
        opp_ppg as away_season_opp_ppg,
        point_diff as away_season_point_diff,
        away_wins,
        away_losses,
        fg_pct as away_season_fg_pct,
        fg3_pct as away_season_fg3_pct
    FROM intermediate.int_team_season_stats
)

SELECT 
    -- Identifiers
    g.game_id,
    g.game_date,
    g.home_team_id,
    g.away_team_id,
    
    -- Target variables
    g.home_win,
    g.home_score,
    g.away_score,
    g.home_score - g.away_score as point_spread,
    g.home_score + g.away_score as total_points,
    g.is_overtime,
    
    -- Home team rolling features
    h.home_rolling_5_ppg,
    h.home_rolling_5_opp_ppg,
    h.home_rolling_5_win_pct,
    h.home_rolling_5_apg,
    h.home_rolling_5_rpg,
    h.home_rolling_5_fg_pct,
    h.home_rolling_5_fg3_pct,
    h.home_wins_last_5,
    h.home_rolling_10_ppg,
    h.home_rolling_10_win_pct,
    
    -- Away team rolling features
    a.away_rolling_5_ppg,
    a.away_rolling_5_opp_ppg,
    a.away_rolling_5_win_pct,
    a.away_rolling_5_apg,
    a.away_rolling_5_rpg,
    a.away_rolling_5_fg_pct,
    a.away_rolling_5_fg3_pct,
    a.away_wins_last_5,
    a.away_rolling_10_ppg,
    a.away_rolling_10_win_pct,
    
    -- Differential features (home - away)
    h.home_rolling_5_ppg - a.away_rolling_5_ppg as ppg_diff_5,
    h.home_rolling_5_win_pct - a.away_rolling_5_win_pct as win_pct_diff_5,
    h.home_rolling_5_fg_pct - a.away_rolling_5_fg_pct as fg_pct_diff_5,
    h.home_rolling_10_ppg - a.away_rolling_10_ppg as ppg_diff_10,
    h.home_rolling_10_win_pct - a.away_rolling_10_win_pct as win_pct_diff_10,
    
    -- Season context
    hs.home_season_win_pct,
    hs.home_season_point_diff,
    hs.home_wins as home_wins_at_home,
    hs.home_losses as home_losses_at_home,
    aws.away_season_win_pct,
    aws.away_season_point_diff,
    aws.away_wins as away_wins_on_road,
    aws.away_losses as away_losses_on_road,
    
    -- Season differential
    hs.home_season_win_pct - aws.away_season_win_pct as season_win_pct_diff,
    hs.home_season_point_diff - aws.away_season_point_diff as season_point_diff_diff
    
FROM game_base g
LEFT JOIN home_stats h ON g.game_id = h.game_id
LEFT JOIN away_stats a ON g.game_id = a.game_id
LEFT JOIN home_season hs ON g.home_team_id = hs.team_id
LEFT JOIN away_season aws ON g.away_team_id = aws.team_id
WHERE h.home_rolling_5_ppg IS NOT NULL 
  AND a.away_rolling_5_ppg IS NOT NULL  -- Ensure both teams have rolling stats
