MODEL (
    name marts.mart_game_features,
    kind INCREMENTAL_BY_TIME_RANGE(
        time_column game_date,
        batch_size 1
    ),
    description 'ML-ready game features for prediction models. Incremental by game_date so new features only require backfilling the range you need.',
    start '1946-11-01',
    cron '@yearly'
);

-- Build feature set for each game in the current interval only
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
      AND g.game_date >= @start_ds
      AND g.game_date < @end_ds
),

-- Only read upstream rows for games in the current chunk (avoids full table scans per chunk).
-- NOT MATERIALIZED allows planner to inline and use index seeks instead of full scans (PostgreSQL 12+).
rolling_lookup_chunk AS NOT MATERIALIZED (
    SELECT r.* FROM intermediate.int_team_roll_lkp r
    INNER JOIN game_base gb ON gb.game_id = r.game_id
),
momentum_chunk AS NOT MATERIALIZED (
    SELECT m.* FROM intermediate.int_game_momentum_features m
    INNER JOIN game_base gb ON gb.game_id = m.game_id
),
injury_chunk AS NOT MATERIALIZED (
    SELECT i.* FROM intermediate.int_game_injury_features i
    INNER JOIN game_base gb ON gb.game_id = i.game_id
),
star_avail_chunk AS NOT MATERIALIZED (
    SELECT s.* FROM intermediate.int_star_player_avail s
    INNER JOIN game_base gb ON gb.game_id = s.game_id
),
star_features_chunk AS NOT MATERIALIZED (
    SELECT s.* FROM intermediate.int_team_star_feats s
    INNER JOIN game_base gb ON gb.game_id = s.game_id
),
upset_chunk AS NOT MATERIALIZED (
    SELECT u.* FROM intermediate.int_away_upset_tend u
    INNER JOIN game_base gb ON gb.game_id = u.game_id
),
season_stats_chunk AS NOT MATERIALIZED (
    SELECT s.* FROM intermediate.int_team_season_stats s
    WHERE s.team_id IN (SELECT home_team_id FROM game_base UNION SELECT away_team_id FROM game_base)
),

-- Get most recent rolling stats for each team BEFORE this game
-- Using pre-computed lookup table for better performance (replaces LATERAL joins)
home_stats AS (
    SELECT 
        g.game_id,
        r.rolling_5_ppg as home_rolling_5_ppg,
        r.rolling_5_opp_ppg as home_rolling_5_opp_ppg,
        r.rolling_5_win_pct as home_rolling_5_win_pct,
        r.rolling_5_apg as home_rolling_5_apg,
        r.rolling_5_rpg as home_rolling_5_rpg,
        r.rolling_5_fg_pct as home_rolling_5_fg_pct,
        r.rolling_5_fg3_pct as home_rolling_5_fg3_pct,
        r.rolling_5_efg_pct as home_rolling_5_efg_pct,
        r.rolling_5_ts_pct as home_rolling_5_ts_pct,
        r.rolling_5_off_rtg as home_rolling_5_off_rtg,
        r.rolling_5_def_rtg as home_rolling_5_def_rtg,
        r.rolling_5_net_rtg as home_rolling_5_net_rtg,
        r.rolling_5_pace as home_rolling_5_pace,
        r.rolling_5_possessions as home_rolling_5_possessions,
        r.rolling_5_tov_rate as home_rolling_5_tov_rate,
        r.rolling_5_assist_rate as home_rolling_5_assist_rate,
        r.rolling_5_steal_rate as home_rolling_5_steal_rate,
        r.rolling_5_block_rate as home_rolling_5_block_rate,
        r.wins_last_5 as home_wins_last_5,
        r.rolling_5_ppg_stddev as home_rolling_5_ppg_stddev,
        r.rolling_5_opp_ppg_stddev as home_rolling_5_opp_ppg_stddev,
        r.rolling_5_ppg_cv as home_rolling_5_ppg_cv,
        r.rolling_5_opp_ppg_cv as home_rolling_5_opp_ppg_cv,
        r.rolling_10_ppg as home_rolling_10_ppg,
        r.rolling_10_opp_ppg as home_rolling_10_opp_ppg,
        r.rolling_10_win_pct as home_rolling_10_win_pct,
        r.rolling_10_efg_pct as home_rolling_10_efg_pct,
        r.rolling_10_ts_pct as home_rolling_10_ts_pct,
        r.rolling_10_off_rtg as home_rolling_10_off_rtg,
        r.rolling_10_def_rtg as home_rolling_10_def_rtg,
        r.rolling_10_net_rtg as home_rolling_10_net_rtg,
        r.rolling_10_pace as home_rolling_10_pace,
        r.rolling_10_possessions as home_rolling_10_possessions,
        r.rolling_10_tov_rate as home_rolling_10_tov_rate,
        r.rolling_10_assist_rate as home_rolling_10_assist_rate,
        r.rolling_10_steal_rate as home_rolling_10_steal_rate,
        r.rolling_10_block_rate as home_rolling_10_block_rate,
        r.wins_last_10 as home_wins_last_10,
        r.rolling_10_ppg_stddev as home_rolling_10_ppg_stddev,
        r.rolling_10_opp_ppg_stddev as home_rolling_10_opp_ppg_stddev,
        r.rolling_10_ppg_cv as home_rolling_10_ppg_cv,
        r.rolling_10_opp_ppg_cv as home_rolling_10_opp_ppg_cv
    FROM game_base g
    LEFT JOIN rolling_lookup_chunk r
        ON r.game_id = g.game_id
        AND r.team_id = g.home_team_id
),

away_stats AS (
    SELECT 
        g.game_id,
        r.rolling_5_ppg as away_rolling_5_ppg,
        r.rolling_5_opp_ppg as away_rolling_5_opp_ppg,
        r.rolling_5_win_pct as away_rolling_5_win_pct,
        r.rolling_5_apg as away_rolling_5_apg,
        r.rolling_5_rpg as away_rolling_5_rpg,
        r.rolling_5_fg_pct as away_rolling_5_fg_pct,
        r.rolling_5_fg3_pct as away_rolling_5_fg3_pct,
        r.rolling_5_efg_pct as away_rolling_5_efg_pct,
        r.rolling_5_ts_pct as away_rolling_5_ts_pct,
        r.rolling_5_off_rtg as away_rolling_5_off_rtg,
        r.rolling_5_def_rtg as away_rolling_5_def_rtg,
        r.rolling_5_net_rtg as away_rolling_5_net_rtg,
        r.rolling_5_pace as away_rolling_5_pace,
        r.rolling_5_possessions as away_rolling_5_possessions,
        r.rolling_5_tov_rate as away_rolling_5_tov_rate,
        r.rolling_5_assist_rate as away_rolling_5_assist_rate,
        r.rolling_5_steal_rate as away_rolling_5_steal_rate,
        r.rolling_5_block_rate as away_rolling_5_block_rate,
        r.wins_last_5 as away_wins_last_5,
        r.rolling_5_ppg_stddev as away_rolling_5_ppg_stddev,
        r.rolling_5_opp_ppg_stddev as away_rolling_5_opp_ppg_stddev,
        r.rolling_5_ppg_cv as away_rolling_5_ppg_cv,
        r.rolling_5_opp_ppg_cv as away_rolling_5_opp_ppg_cv,
        r.rolling_10_ppg as away_rolling_10_ppg,
        r.rolling_10_opp_ppg as away_rolling_10_opp_ppg,
        r.rolling_10_win_pct as away_rolling_10_win_pct,
        r.rolling_10_efg_pct as away_rolling_10_efg_pct,
        r.rolling_10_ts_pct as away_rolling_10_ts_pct,
        r.rolling_10_off_rtg as away_rolling_10_off_rtg,
        r.rolling_10_def_rtg as away_rolling_10_def_rtg,
        r.rolling_10_net_rtg as away_rolling_10_net_rtg,
        r.rolling_10_pace as away_rolling_10_pace,
        r.rolling_10_possessions as away_rolling_10_possessions,
        r.rolling_10_tov_rate as away_rolling_10_tov_rate,
        r.rolling_10_assist_rate as away_rolling_10_assist_rate,
        r.rolling_10_steal_rate as away_rolling_10_steal_rate,
        r.rolling_10_block_rate as away_rolling_10_block_rate,
        r.wins_last_10 as away_wins_last_10,
        r.rolling_10_ppg_stddev as away_rolling_10_ppg_stddev,
        r.rolling_10_opp_ppg_stddev as away_rolling_10_opp_ppg_stddev,
        r.rolling_10_ppg_cv as away_rolling_10_ppg_cv,
        r.rolling_10_opp_ppg_cv as away_rolling_10_opp_ppg_cv
    FROM game_base g
    LEFT JOIN rolling_lookup_chunk r
        ON r.game_id = g.game_id
        AND r.team_id = g.away_team_id
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
    FROM season_stats_chunk
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
    FROM season_stats_chunk
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
        h.home_rolling_5_efg_pct,
        h.home_rolling_5_ts_pct,
        h.home_rolling_5_off_rtg,
        h.home_rolling_5_def_rtg,
        h.home_rolling_5_net_rtg,
    h.home_rolling_5_pace,
    h.home_rolling_5_possessions,
    h.home_rolling_5_tov_rate,
    h.home_rolling_5_assist_rate,
    h.home_rolling_5_steal_rate,
    h.home_rolling_5_block_rate,
    h.home_rolling_5_ppg_stddev,
        h.home_rolling_5_opp_ppg_stddev,
        h.home_rolling_5_ppg_cv,
        h.home_rolling_5_opp_ppg_cv,
        h.home_rolling_10_ppg,
    h.home_rolling_10_win_pct,
    h.home_rolling_10_efg_pct,
    h.home_rolling_10_ts_pct,
    h.home_rolling_10_off_rtg,
    h.home_rolling_10_def_rtg,
    h.home_rolling_10_net_rtg,
    h.home_rolling_10_pace,
    h.home_rolling_10_possessions,
    h.home_rolling_10_tov_rate,
    h.home_rolling_10_assist_rate,
    h.home_rolling_10_steal_rate,
    h.home_rolling_10_block_rate,
    h.home_rolling_10_ppg_stddev,
    h.home_rolling_10_opp_ppg_stddev,
    h.home_rolling_10_ppg_cv,
    h.home_rolling_10_opp_ppg_cv,
    
    -- Away team rolling features
    a.away_rolling_5_ppg,
    a.away_rolling_5_opp_ppg,
    a.away_rolling_5_win_pct,
    a.away_rolling_5_apg,
    a.away_rolling_5_rpg,
        a.away_rolling_5_fg_pct,
        a.away_rolling_5_fg3_pct,
        a.away_rolling_5_efg_pct,
        a.away_rolling_5_ts_pct,
        a.away_rolling_5_off_rtg,
        a.away_rolling_5_def_rtg,
        a.away_rolling_5_net_rtg,
        a.away_rolling_5_pace,
        a.away_rolling_5_possessions,
        a.away_rolling_5_tov_rate,
        a.away_rolling_5_assist_rate,
        a.away_rolling_5_steal_rate,
        a.away_rolling_5_block_rate,
        a.away_rolling_5_ppg_stddev,
        a.away_rolling_5_opp_ppg_stddev,
        a.away_rolling_5_ppg_cv,
        a.away_rolling_5_opp_ppg_cv,
        a.away_rolling_10_ppg,
    a.away_rolling_10_win_pct,
    a.away_rolling_10_efg_pct,
    a.away_rolling_10_ts_pct,
    a.away_rolling_10_off_rtg,
    a.away_rolling_10_def_rtg,
        a.away_rolling_10_net_rtg,
        a.away_rolling_10_pace,
        a.away_rolling_10_possessions,
        a.away_rolling_10_tov_rate,
        a.away_rolling_10_assist_rate,
        a.away_rolling_10_steal_rate,
        a.away_rolling_10_block_rate,
        a.away_rolling_10_ppg_stddev,
    a.away_rolling_10_opp_ppg_stddev,
    a.away_rolling_10_ppg_cv,
    a.away_rolling_10_opp_ppg_cv,
    
    -- Differential features (home - away)
    h.home_rolling_5_ppg - a.away_rolling_5_ppg as ppg_diff_5,
    h.home_rolling_5_win_pct - a.away_rolling_5_win_pct as win_pct_diff_5,
    h.home_rolling_5_fg_pct - a.away_rolling_5_fg_pct as fg_pct_diff_5,
    h.home_rolling_5_efg_pct - a.away_rolling_5_efg_pct as efg_pct_diff_5,
    h.home_rolling_5_ts_pct - a.away_rolling_5_ts_pct as ts_pct_diff_5,
    h.home_rolling_5_off_rtg - a.away_rolling_5_off_rtg as off_rtg_diff_5,
    h.home_rolling_5_def_rtg - a.away_rolling_5_def_rtg as def_rtg_diff_5,
    h.home_rolling_5_net_rtg - a.away_rolling_5_net_rtg as net_rtg_diff_5,
    h.home_rolling_5_pace - a.away_rolling_5_pace as pace_diff_5,
    h.home_rolling_5_tov_rate - a.away_rolling_5_tov_rate as tov_rate_diff_5,
    h.home_rolling_5_assist_rate - a.away_rolling_5_assist_rate as assist_rate_diff_5,
    h.home_rolling_5_steal_rate - a.away_rolling_5_steal_rate as steal_rate_diff_5,
    h.home_rolling_5_block_rate - a.away_rolling_5_block_rate as block_rate_diff_5,
    h.home_rolling_10_ppg - a.away_rolling_10_ppg as ppg_diff_10,
    h.home_rolling_10_win_pct - a.away_rolling_10_win_pct as win_pct_diff_10,
    h.home_rolling_10_efg_pct - a.away_rolling_10_efg_pct as efg_pct_diff_10,
    h.home_rolling_10_ts_pct - a.away_rolling_10_ts_pct as ts_pct_diff_10,
    h.home_rolling_10_off_rtg - a.away_rolling_10_off_rtg as off_rtg_diff_10,
    h.home_rolling_10_def_rtg - a.away_rolling_10_def_rtg as def_rtg_diff_10,
    h.home_rolling_10_net_rtg - a.away_rolling_10_net_rtg as net_rtg_diff_10,
    h.home_rolling_10_pace - a.away_rolling_10_pace as pace_diff_10,
    h.home_rolling_10_tov_rate - a.away_rolling_10_tov_rate as tov_rate_diff_10,
    h.home_rolling_10_assist_rate - a.away_rolling_10_assist_rate as assist_rate_diff_10,
    h.home_rolling_10_steal_rate - a.away_rolling_10_steal_rate as steal_rate_diff_10,
    h.home_rolling_10_block_rate - a.away_rolling_10_block_rate as block_rate_diff_10,
    -- Consistency differential features
    h.home_rolling_5_ppg_stddev - a.away_rolling_5_ppg_stddev as ppg_stddev_diff_5,
    h.home_rolling_5_opp_ppg_stddev - a.away_rolling_5_opp_ppg_stddev as opp_ppg_stddev_diff_5,
    h.home_rolling_5_ppg_cv - a.away_rolling_5_ppg_cv as ppg_cv_diff_5,
    h.home_rolling_5_opp_ppg_cv - a.away_rolling_5_opp_ppg_cv as opp_ppg_cv_diff_5,
    h.home_rolling_10_ppg_stddev - a.away_rolling_10_ppg_stddev as ppg_stddev_diff_10,
    h.home_rolling_10_opp_ppg_stddev - a.away_rolling_10_opp_ppg_stddev as opp_ppg_stddev_diff_10,
    h.home_rolling_10_ppg_cv - a.away_rolling_10_ppg_cv as ppg_cv_diff_10,
    h.home_rolling_10_opp_ppg_cv - a.away_rolling_10_opp_ppg_cv as opp_ppg_cv_diff_10,
    
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
    hs.home_season_point_diff - aws.away_season_point_diff as season_point_diff_diff,
    
    -- Home team star player return features
    COALESCE(hsf.star_players_returning, 0) as home_star_players_returning,
    COALESCE(hsf.key_players_returning, 0) as home_key_players_returning,
    COALESCE(hsf.extended_returns, 0) as home_extended_returns,
    COALESCE(hsf.total_return_impact, 0) as home_total_return_impact,
    hsf.max_days_since_return as home_max_days_since_return,
    hsf.avg_days_since_return as home_avg_days_since_return,
    COALESCE(hsf.has_star_return, 0) as home_has_star_return,
    COALESCE(hsf.has_extended_return, 0) as home_has_extended_return,
    
    -- Away team star player return features
    COALESCE(asf.star_players_returning, 0) as away_star_players_returning,
    COALESCE(asf.key_players_returning, 0) as away_key_players_returning,
    COALESCE(asf.extended_returns, 0) as away_extended_returns,
    COALESCE(asf.total_return_impact, 0) as away_total_return_impact,
    asf.max_days_since_return as away_max_days_since_return,
    asf.avg_days_since_return as away_avg_days_since_return,
    COALESCE(asf.has_star_return, 0) as away_has_star_return,
    COALESCE(asf.has_extended_return, 0) as away_has_extended_return,
    
    -- Differential: home return advantage
    COALESCE(hsf.has_star_return, 0) - COALESCE(asf.has_star_return, 0) as star_return_advantage,
    COALESCE(hsf.total_return_impact, 0) - COALESCE(asf.total_return_impact, 0) as return_impact_diff,
    
    -- Home team injury features
    COALESCE(inf.home_star_players_out, 0) as home_star_players_out,
    COALESCE(inf.home_key_players_out, 0) as home_key_players_out,
    COALESCE(inf.home_star_players_doubtful, 0) as home_star_players_doubtful,
    COALESCE(inf.home_key_players_doubtful, 0) as home_key_players_doubtful,
    COALESCE(inf.home_star_players_questionable, 0) as home_star_players_questionable,
    COALESCE(inf.home_injury_impact_score, 0) as home_injury_impact_score,
    COALESCE(inf.home_injured_players_count, 0) as home_injured_players_count,
    COALESCE(inf.home_has_key_injury, 0) as home_has_key_injury,
    
    -- Away team injury features
    COALESCE(inf.away_star_players_out, 0) as away_star_players_out,
    COALESCE(inf.away_key_players_out, 0) as away_key_players_out,
    COALESCE(inf.away_star_players_doubtful, 0) as away_star_players_doubtful,
    COALESCE(inf.away_key_players_doubtful, 0) as away_key_players_doubtful,
    COALESCE(inf.away_star_players_questionable, 0) as away_star_players_questionable,
    COALESCE(inf.away_injury_impact_score, 0) as away_injury_impact_score,
    COALESCE(inf.away_injured_players_count, 0) as away_injured_players_count,
    COALESCE(inf.away_has_key_injury, 0) as away_has_key_injury,
    
    -- Injury differential features  
    COALESCE(inf.injury_impact_diff, 0.0) as injury_impact_diff,
    -- injury_advantage_home: positive when away has more injuries (favor HOME)
    -- Inverts injury_impact_diff so model learns "higher = favor home"; clip extremes
    LEAST(3000.0, GREATEST(-3000.0, -COALESCE(inf.injury_impact_diff, 0.0))) as injury_advantage_home,
    COALESCE(inf.star_players_out_diff, 0) as star_players_out_diff,
    
    -- Feature interactions: Injury impact with other key features (v2 - added 2026-01-23)
    -- When injuries are high AND recent form is good, discount the form (injuries matter more)
    -- Negative value means away team has higher injury impact, positive means home team does
    -- Multiply by form diff: if away team is hot (negative form diff) but injured (negative injury diff), 
    -- the product is positive, indicating we should discount their hot form
    COALESCE(inf.injury_impact_diff, 0.0) * COALESCE(h.home_rolling_10_win_pct - a.away_rolling_10_win_pct, 0.0) as injury_impact_x_form_diff,
    -- Away team injury penalty: HIGH injury * form = should PENALIZE away team (negative contribution)
    -- FIXED: Use negative so high values favor HOME (opponent)
    -COALESCE(inf.away_injury_impact_score, 0.0) * COALESCE(a.away_rolling_10_win_pct, 0.0) as away_injury_x_form,
    -- Home team injury penalty: HIGH injury * form = should PENALIZE home team (negative contribution)
    -- FIXED: Use negative so high values favor AWAY (opponent)
    -COALESCE(inf.home_injury_impact_score, 0.0) * COALESCE(h.home_rolling_10_win_pct, 0.0) as home_injury_x_form,
    -- Injury impact ratio: normalize injury impact relative to team strength
    -- Higher ratio = injury impact is large relative to team's recent performance
    CASE 
        WHEN COALESCE(h.home_rolling_10_win_pct, 0.0) > 0 THEN 
            COALESCE(inf.home_injury_impact_score, 0.0) / NULLIF(h.home_rolling_10_win_pct * 100, 0)
        ELSE 0.0
    END as home_injury_impact_ratio,
    CASE 
        WHEN COALESCE(a.away_rolling_10_win_pct, 0.0) > 0 THEN 
            COALESCE(inf.away_injury_impact_score, 0.0) / NULLIF(a.away_rolling_10_win_pct * 100, 0)
        ELSE 0.0
    END as away_injury_impact_ratio,
    
    -- Explicit injury penalty features (v4): Continuous encoding, stronger impact
    -- Severe: LEAST(2, ratio/10) so ratio 20+ -> 2.0, 10->1.0; absolute: LEAST(2, score/500)
    LEAST(2.0, (CASE 
        WHEN COALESCE(h.home_rolling_10_win_pct, 0.0) > 0 THEN 
            COALESCE(inf.home_injury_impact_score, 0.0) / NULLIF(h.home_rolling_10_win_pct * 100, 0)
        ELSE 0.0
    END) / 10.0) as home_injury_penalty_severe,
    LEAST(2.0, (CASE 
        WHEN COALESCE(a.away_rolling_10_win_pct, 0.0) > 0 THEN 
            COALESCE(inf.away_injury_impact_score, 0.0) / NULLIF(a.away_rolling_10_win_pct * 100, 0)
        ELSE 0.0
    END) / 10.0) as away_injury_penalty_severe,
    LEAST(2.0, COALESCE(inf.home_injury_impact_score, 0.0) / 500.0) as home_injury_penalty_absolute,
    LEAST(2.0, COALESCE(inf.away_injury_impact_score, 0.0) / 500.0) as away_injury_penalty_absolute,
    
    -- NEW: Advanced feature interactions (Iteration 2 - 2026-01-25)
    -- Injury x Rest: Injuries matter more when teams are tired (back-to-back, low rest)
    -- Positive when home team has injury advantage AND rest advantage (favor home)
    COALESCE(inf.injury_impact_diff, 0.0) * COALESCE(mf.rest_advantage, 0.0) as injury_impact_x_rest_advantage,
    -- Win % x Home Advantage: Home advantage matters more for better teams
    -- Positive when home team is better AND has home advantage (favor home)
    COALESCE(h.home_rolling_10_win_pct - a.away_rolling_10_win_pct, 0.0) * COALESCE(mf.home_advantage, 0.0) as win_pct_diff_10_x_home_advantage,
    -- Net Rating x Rest: Better teams benefit more from rest
    -- Positive when home team has better net rating AND rest advantage (favor home)
    COALESCE(h.home_rolling_10_net_rtg - a.away_rolling_10_net_rtg, 0.0) * COALESCE(mf.rest_advantage, 0.0) as net_rtg_diff_10_x_rest_advantage,
    -- Net Rating x Home Advantage: Home advantage amplifies team quality differences
    -- Positive when home team has better net rating AND home advantage (favor home)
    COALESCE(h.home_rolling_10_net_rtg - a.away_rolling_10_net_rtg, 0.0) * COALESCE(mf.home_advantage, 0.0) as net_rtg_diff_10_x_home_advantage,
    -- Offensive Rating x Pace: Fast-paced teams with high offensive rating are more dangerous
    -- Positive when home team has better offensive rating AND higher pace (favor home)
    COALESCE(h.home_rolling_10_off_rtg - a.away_rolling_10_off_rtg, 0.0) * COALESCE(h.home_rolling_10_pace - a.away_rolling_10_pace, 0.0) as off_rtg_diff_10_x_pace_diff_10,
    -- Defensive Rating x Rest: Rest helps defense more (tired teams defend worse)
    -- Positive when home team has better defensive rating AND rest advantage (favor home)
    -- Note: Lower def_rtg is better, so we invert: (away_def_rtg - home_def_rtg) * rest_advantage
    (COALESCE(a.away_rolling_10_def_rtg, 0.0) - COALESCE(h.home_rolling_10_def_rtg, 0.0)) * COALESCE(mf.rest_advantage, 0.0) as def_rtg_advantage_x_rest_advantage,
    -- NEW: Rest Advantage x Team Quality interactions (Iteration 19 - 2026-01-25)
    -- Better teams benefit more from rest - rest advantage amplifies team quality differences
    -- Positive when home team is better AND has rest advantage (favor home)
    COALESCE(h.home_rolling_10_win_pct - a.away_rolling_10_win_pct, 0.0) * COALESCE(mf.rest_advantage, 0.0) as rest_advantage_x_win_pct_diff_10,
    -- Rest days × team quality: Rest matters more for better teams
    -- Positive when better team has more rest (favor home if home is better and has more rest)
    COALESCE(mf.home_rest_days, 0.0) * COALESCE(h.home_rolling_10_win_pct, 0.5) as home_rest_days_x_home_win_pct_10,
    COALESCE(mf.away_rest_days, 0.0) * COALESCE(a.away_rolling_10_win_pct, 0.5) as away_rest_days_x_away_win_pct_10,
    -- NEW: Rest Quality interactions (Iteration 23 - 2026-01-25)
    -- Rest matters more after tough schedule (high SOS) than after easy schedule (low SOS)
    -- Positive when team has more rest AND faced tougher opponents (rest is more valuable)
    COALESCE(mf.home_rest_days, 0.0) * COALESCE(mf.home_sos_10_rolling, 0.5) as home_rest_days_x_home_sos_10,
    COALESCE(mf.away_rest_days, 0.0) * COALESCE(mf.away_sos_10_rolling, 0.5) as away_rest_days_x_away_sos_10,
    -- Rest advantage × SOS difference: Rest advantage matters more when schedule strength differs
    -- Positive when home team has rest advantage AND faced tougher schedule (favor home)
    COALESCE(mf.rest_advantage, 0.0) * (COALESCE(mf.home_sos_10_rolling, 0.5) - COALESCE(mf.away_sos_10_rolling, 0.5)) as rest_advantage_x_sos_10_diff,
    -- Iteration 87: Form divergence × Rest advantage – hot teams (playing above season avg) with rest are especially dangerous
    -- Positive = home team has form advantage (hot) AND rest advantage (favor home)
    (COALESCE(mf.home_form_divergence_win_pct_10, 0.0) - COALESCE(mf.away_form_divergence_win_pct_10, 0.0)) * COALESCE(mf.rest_advantage, 0.0) as form_divergence_win_pct_diff_10_x_rest_advantage,
    
    -- NEW: Back-to-back with travel impact features (Iteration 25 - 2026-01-25)
    -- Back-to-backs with travel are significantly harder than back-to-backs without travel
    -- Travel occurs when: location changed (home->away or away->home) OR both games were away
    COALESCE(mf.home_back_to_back_with_travel, FALSE)::integer as home_back_to_back_with_travel,
    COALESCE(mf.away_back_to_back_with_travel, FALSE)::integer as away_back_to_back_with_travel,
    -- Back-to-back with travel advantage: Positive = home team has travel disadvantage (favor away)
    -- Negative = away team has travel disadvantage (favor home)
    COALESCE(mf.back_to_back_travel_advantage, 0) as back_to_back_travel_advantage,
    -- NEW: Cumulative travel fatigue features (Iteration 11 - 2026-01-27)
    -- Multiple back-to-backs with travel in recent games indicate cumulative fatigue
    -- Teams with more recent travel fatigue may perform worse
    COALESCE(mf.home_btb_travel_count_5, 0) as home_btb_travel_count_5,
    COALESCE(mf.home_btb_travel_count_10, 0) as home_btb_travel_count_10,
    COALESCE(mf.home_travel_fatigue_score_5, 0.0) as home_travel_fatigue_score_5,
    COALESCE(mf.away_btb_travel_count_5, 0) as away_btb_travel_count_5,
    COALESCE(mf.away_btb_travel_count_10, 0) as away_btb_travel_count_10,
    COALESCE(mf.away_travel_fatigue_score_5, 0.0) as away_travel_fatigue_score_5,
    -- Travel fatigue differentials: Positive = home team has more travel fatigue (favor away)
    COALESCE(mf.btb_travel_count_5_diff, 0) as btb_travel_count_5_diff,
    COALESCE(mf.btb_travel_count_10_diff, 0) as btb_travel_count_10_diff,
    COALESCE(mf.travel_fatigue_score_5_diff, 0.0) as travel_fatigue_score_5_diff,
    -- Interaction: Back-to-back with travel × team quality (better teams handle travel better)
    -- Positive when better team has travel disadvantage (penalize better team less)
    COALESCE(mf.home_back_to_back_with_travel, FALSE)::integer * COALESCE(h.home_rolling_10_win_pct, 0.5) as home_btb_travel_x_win_pct_10,
    COALESCE(mf.away_back_to_back_with_travel, FALSE)::integer * COALESCE(a.away_rolling_10_win_pct, 0.5) as away_btb_travel_x_win_pct_10,
    -- Interaction: Back-to-back with travel × rest days (travel matters more with less rest)
    -- If team has 0 rest days AND travel, it's much harder than 1 rest day with travel
    COALESCE(mf.home_back_to_back_with_travel, FALSE)::integer * (1.0 / NULLIF(COALESCE(mf.home_rest_days, 1), 0)) as home_btb_travel_x_rest_penalty,
    COALESCE(mf.away_back_to_back_with_travel, FALSE)::integer * (1.0 / NULLIF(COALESCE(mf.away_rest_days, 1), 0)) as away_btb_travel_x_rest_penalty,
    
    -- NEW: Recent Head-to-Head features (Iteration 7 - 2026-01-25)
    -- Recent H2H win percentages and point differentials for better matchup prediction
    COALESCE(mf.home_h2h_win_pct_3, 0.5) as home_h2h_win_pct_3,
    COALESCE(mf.home_h2h_win_pct_5, 0.5) as home_h2h_win_pct_5,
    COALESCE(mf.home_h2h_avg_point_diff_3, 0.0) as home_h2h_avg_point_diff_3,
    COALESCE(mf.home_h2h_avg_point_diff_5, 0.0) as home_h2h_avg_point_diff_5,
    COALESCE(mf.home_h2h_momentum, 0.0) as home_h2h_momentum,
    -- NEW: Momentum × H2H interactions (Iteration 8 - 2026-01-26)
    -- Momentum and H2H history together are more predictive than either alone
    -- Teams with strong momentum AND good H2H record are especially dangerous
    -- Positive = home team has momentum advantage AND H2H advantage (favor home)
    (COALESCE(mf.home_momentum_score, 0.0) - COALESCE(mf.away_momentum_score, 0.0)) * COALESCE(mf.home_h2h_win_pct_3, 0.5) as momentum_score_diff_x_h2h_win_pct_3,
    (COALESCE(mf.home_momentum_score, 0.0) - COALESCE(mf.away_momentum_score, 0.0)) * COALESCE(mf.home_h2h_win_pct_5, 0.5) as momentum_score_diff_x_h2h_win_pct_5,
    -- Home momentum × home H2H: Positive = home team has both momentum and H2H advantage
    COALESCE(mf.home_momentum_score, 0.0) * COALESCE(mf.home_h2h_win_pct_3, 0.5) as home_momentum_x_h2h_win_pct_3,
    COALESCE(mf.home_momentum_score, 0.0) * COALESCE(mf.home_h2h_win_pct_5, 0.5) as home_momentum_x_h2h_win_pct_5,
    -- Away momentum × away H2H: Positive = away team has both momentum and H2H advantage (favor away)
    -- Note: away H2H = 1 - home_h2h_win_pct (inverted)
    COALESCE(mf.away_momentum_score, 0.0) * (1.0 - COALESCE(mf.home_h2h_win_pct_3, 0.5)) as away_momentum_x_h2h_win_pct_3,
    COALESCE(mf.away_momentum_score, 0.0) * (1.0 - COALESCE(mf.home_h2h_win_pct_5, 0.5)) as away_momentum_x_h2h_win_pct_5,
    -- Weighted momentum × H2H: Weighted momentum (by opponent quality) × H2H history
    -- Positive = home team has weighted momentum advantage AND H2H advantage (favor home)
    COALESCE(mf.weighted_momentum_diff_5, 0.0) * COALESCE(mf.home_h2h_win_pct_5, 0.5) as weighted_momentum_diff_5_x_h2h_win_pct_5,
    COALESCE(mf.weighted_momentum_diff_10, 0.0) * COALESCE(mf.home_h2h_win_pct_5, 0.5) as weighted_momentum_diff_10_x_h2h_win_pct_5,
    
    -- NEW: Win streak quality (Iteration 26 - 2026-01-27)
    -- Win streak length weighted by average opponent quality during the streak
    -- A 5-game win streak against strong opponents (avg win_pct > 0.6) is more meaningful than against weak opponents
    COALESCE(mf.home_win_streak_quality, 0.0) as home_win_streak_quality,
    COALESCE(mf.away_win_streak_quality, 0.0) as away_win_streak_quality,
    COALESCE(mf.win_streak_quality_diff, 0.0) as win_streak_quality_diff,
    -- NEW: Win streak quality × opponent quality interactions (Iteration 27 - 2026-01-27)
    -- Win streak quality matters more when facing strong opponents - a quality win streak is more predictive against good teams
    COALESCE(mf.home_win_streak_quality, 0.0) * COALESCE(a.away_rolling_10_win_pct, 0.5) as home_win_streak_quality_x_away_opponent_quality,
    COALESCE(mf.away_win_streak_quality, 0.0) * COALESCE(h.home_rolling_10_win_pct, 0.5) as away_win_streak_quality_x_home_opponent_quality,
    COALESCE(mf.win_streak_quality_diff, 0.0) * (COALESCE(h.home_rolling_10_win_pct, 0.5) - COALESCE(a.away_rolling_10_win_pct, 0.5)) as win_streak_quality_diff_x_opponent_quality_diff,
    
    -- NEW: Away team upset tendency (Iteration 3 - 2026-01-25)
    -- Historical rate at which away team wins when they're the underdog
    -- Higher values indicate away team is more likely to pull off upsets
    COALESCE(ut.away_upset_tendency_season, 0.3) as away_upset_tendency_season,
    COALESCE(ut.away_upset_tendency_rolling, 0.3) as away_upset_tendency_rolling,
    COALESCE(ut.away_upset_sample_size_season, 0) as away_upset_sample_size_season,
    COALESCE(ut.away_upset_sample_size_rolling, 0) as away_upset_sample_size_rolling,
    
    -- NEW: Rolling Schedule Strength (SOS) features (Iteration 9 - 2026-01-25)
    -- Contextualize win percentages by opponent quality using rolling win_pct
    -- Higher SOS = team has faced stronger opponents recently
    COALESCE(mf.home_sos_5_rolling, 0.5) as home_sos_5_rolling,
    COALESCE(mf.away_sos_5_rolling, 0.5) as away_sos_5_rolling,
    COALESCE(mf.home_sos_10_rolling, 0.5) as home_sos_10_rolling,
    COALESCE(mf.away_sos_10_rolling, 0.5) as away_sos_10_rolling,
    COALESCE(mf.home_sos_5_weighted, 0.5) as home_sos_5_weighted,
    COALESCE(mf.away_sos_5_weighted, 0.5) as away_sos_5_weighted,
    -- SOS differentials: Positive = home team faced stronger opponents (favor home)
    COALESCE(mf.home_sos_5_rolling, 0.5) - COALESCE(mf.away_sos_5_rolling, 0.5) as sos_5_diff,
    COALESCE(mf.home_sos_10_rolling, 0.5) - COALESCE(mf.away_sos_10_rolling, 0.5) as sos_10_diff,
    COALESCE(mf.home_sos_5_weighted, 0.5) - COALESCE(mf.away_sos_5_weighted, 0.5) as sos_5_weighted_diff,
    -- SOS-adjusted win percentage: Win % adjusted for opponent strength
    -- If team has high win_pct but low SOS, they may be overrated
    -- If team has moderate win_pct but high SOS, they may be underrated
    COALESCE(h.home_rolling_10_win_pct, 0.5) - COALESCE(mf.home_sos_10_rolling, 0.5) as home_win_pct_vs_sos_10,
    COALESCE(a.away_rolling_10_win_pct, 0.5) - COALESCE(mf.away_sos_10_rolling, 0.5) as away_win_pct_vs_sos_10,
    (COALESCE(h.home_rolling_10_win_pct, 0.5) - COALESCE(mf.home_sos_10_rolling, 0.5)) - 
    (COALESCE(a.away_rolling_10_win_pct, 0.5) - COALESCE(mf.away_sos_10_rolling, 0.5)) as win_pct_vs_sos_10_diff,
    
    -- NEW: Clutch performance features (Iteration 12 - 2026-01-25)
    -- Team performance in close games (decided by <= 5 points)
    -- Higher clutch win_pct = team executes better under pressure
    COALESCE(mf.home_clutch_win_pct, 0.5) as home_clutch_win_pct,
    COALESCE(mf.home_clutch_game_count, 0) as home_clutch_game_count,
    COALESCE(mf.home_clutch_avg_point_diff, 0.0) as home_clutch_avg_point_diff,
    COALESCE(mf.away_clutch_win_pct, 0.5) as away_clutch_win_pct,
    COALESCE(mf.away_clutch_game_count, 0) as away_clutch_game_count,
    COALESCE(mf.away_clutch_avg_point_diff, 0.0) as away_clutch_avg_point_diff,
    -- Clutch differentials: Positive = home team performs better in close games (favor home)
    COALESCE(mf.home_clutch_win_pct, 0.5) - COALESCE(mf.away_clutch_win_pct, 0.5) as clutch_win_pct_diff,
    COALESCE(mf.home_clutch_avg_point_diff, 0.0) - COALESCE(mf.away_clutch_avg_point_diff, 0.0) as clutch_avg_point_diff_diff,
    
    -- NEW: Blowout performance features (Iteration 4 - 2026-01-25)
    -- Team performance in games decided by > 15 points (complementary to clutch performance)
    -- Teams that can build big leads or prevent blowouts may have different characteristics
    COALESCE(mf.home_blowout_win_pct, 0.5) as home_blowout_win_pct,
    COALESCE(mf.home_blowout_game_count, 0) as home_blowout_game_count,
    COALESCE(mf.home_blowout_avg_point_diff, 0.0) as home_blowout_avg_point_diff,
    COALESCE(mf.home_blowout_frequency, 0.0) as home_blowout_frequency,
    COALESCE(mf.away_blowout_win_pct, 0.5) as away_blowout_win_pct,
    COALESCE(mf.away_blowout_game_count, 0) as away_blowout_game_count,
    COALESCE(mf.away_blowout_avg_point_diff, 0.0) as away_blowout_avg_point_diff,
    COALESCE(mf.away_blowout_frequency, 0.0) as away_blowout_frequency,
    -- Blowout differentials: Positive = home team performs better in blowouts (favor home)
    COALESCE(mf.home_blowout_win_pct, 0.5) - COALESCE(mf.away_blowout_win_pct, 0.5) as blowout_win_pct_diff,
    COALESCE(mf.home_blowout_avg_point_diff, 0.0) - COALESCE(mf.away_blowout_avg_point_diff, 0.0) as blowout_avg_point_diff_diff,
    COALESCE(mf.home_blowout_frequency, 0.0) - COALESCE(mf.away_blowout_frequency, 0.0) as blowout_frequency_diff,
    -- Iteration 3 (2026-01-26): Comeback/closeout performance features
    -- Comeback performance: ability to win close games (comeback ability)
    -- Closeout performance: ability to win games with large margins (closeout ability)
    -- Captures late-game execution and mental toughness beyond just clutch performance
    COALESCE(mf.home_comeback_win_pct, 0.5) as home_comeback_win_pct,
    COALESCE(mf.home_comeback_game_count, 0) as home_comeback_game_count,
    COALESCE(mf.home_comeback_avg_point_diff, 0.0) as home_comeback_avg_point_diff,
    COALESCE(mf.home_closeout_win_pct, 0.5) as home_closeout_win_pct,
    COALESCE(mf.home_closeout_game_count, 0) as home_closeout_game_count,
    COALESCE(mf.home_closeout_avg_point_diff, 0.0) as home_closeout_avg_point_diff,
    COALESCE(mf.away_comeback_win_pct, 0.5) as away_comeback_win_pct,
    COALESCE(mf.away_comeback_game_count, 0) as away_comeback_game_count,
    COALESCE(mf.away_comeback_avg_point_diff, 0.0) as away_comeback_avg_point_diff,
    COALESCE(mf.away_closeout_win_pct, 0.5) as away_closeout_win_pct,
    COALESCE(mf.away_closeout_game_count, 0) as away_closeout_game_count,
    COALESCE(mf.away_closeout_avg_point_diff, 0.0) as away_closeout_avg_point_diff,
    -- Differential features (home - away): Positive = home team has better comeback/closeout ability (favor home)
    COALESCE(mf.home_comeback_win_pct, 0.5) - COALESCE(mf.away_comeback_win_pct, 0.5) as comeback_win_pct_diff,
    COALESCE(mf.home_closeout_win_pct, 0.5) - COALESCE(mf.away_closeout_win_pct, 0.5) as closeout_win_pct_diff,
    COALESCE(mf.home_comeback_avg_point_diff, 0.0) - COALESCE(mf.away_comeback_avg_point_diff, 0.0) as comeback_avg_point_diff_diff,
    COALESCE(mf.home_closeout_avg_point_diff, 0.0) - COALESCE(mf.away_closeout_avg_point_diff, 0.0) as closeout_avg_point_diff_diff,
    
    -- NEW: Favored/underdog performance features (Iteration 1 - 2026-01-26)
    -- Captures how teams perform when expected to win vs when expected to lose (using rolling win_pct as proxy)
    COALESCE(mf.home_win_pct_when_favored, 0.5) as home_win_pct_when_favored,
    COALESCE(mf.home_favored_game_count, 0) as home_favored_game_count,
    COALESCE(mf.home_win_pct_when_underdog, 0.5) as home_win_pct_when_underdog,
    COALESCE(mf.home_underdog_game_count, 0) as home_underdog_game_count,
    COALESCE(mf.home_avg_point_diff_when_favored, 0.0) as home_avg_point_diff_when_favored,
    COALESCE(mf.home_avg_point_diff_when_underdog, 0.0) as home_avg_point_diff_when_underdog,
    COALESCE(mf.home_upset_rate, 0.0) as home_upset_rate,
    COALESCE(mf.home_upset_game_count, 0) as home_upset_game_count,
    COALESCE(mf.away_win_pct_when_favored, 0.5) as away_win_pct_when_favored,
    COALESCE(mf.away_favored_game_count, 0) as away_favored_game_count,
    COALESCE(mf.away_win_pct_when_underdog, 0.5) as away_win_pct_when_underdog,
    COALESCE(mf.away_underdog_game_count, 0) as away_underdog_game_count,
    COALESCE(mf.away_avg_point_diff_when_favored, 0.0) as away_avg_point_diff_when_favored,
    COALESCE(mf.away_avg_point_diff_when_underdog, 0.0) as away_avg_point_diff_when_underdog,
    COALESCE(mf.away_upset_rate, 0.0) as away_upset_rate,
    COALESCE(mf.away_upset_game_count, 0) as away_upset_game_count,
    -- Favored/underdog differentials: Positive = home team performs better when favored/underdog
    COALESCE(mf.win_pct_when_favored_diff, 0.0) as win_pct_when_favored_diff,
    COALESCE(mf.win_pct_when_underdog_diff, 0.0) as win_pct_when_underdog_diff,
    COALESCE(mf.avg_point_diff_when_favored_diff, 0.0) as avg_point_diff_when_favored_diff,
    COALESCE(mf.avg_point_diff_when_underdog_diff, 0.0) as avg_point_diff_when_underdog_diff,
    COALESCE(mf.upset_rate_diff, 0.0) as upset_rate_diff,
    
    -- NEW: Recent road performance features (Iteration 2 - 2026-01-26)
    -- Away team's performance in last 5/10 road games - captures recent road form
    -- More relevant than overall season road win percentage for predicting current road performance
    COALESCE(mf.away_recent_road_win_pct_5, 0.5) as away_recent_road_win_pct_5,
    COALESCE(mf.away_recent_road_game_count_5, 0) as away_recent_road_game_count_5,
    COALESCE(mf.away_recent_road_avg_point_diff_5, 0.0) as away_recent_road_avg_point_diff_5,
    COALESCE(mf.away_recent_road_win_pct_10, 0.5) as away_recent_road_win_pct_10,
    COALESCE(mf.away_recent_road_game_count_10, 0) as away_recent_road_game_count_10,
    COALESCE(mf.away_recent_road_avg_point_diff_10, 0.0) as away_recent_road_avg_point_diff_10,
    
    -- NEW: Overtime performance features (Iteration 36 - 2026-01-25)
    -- Team performance in games that went to overtime (tests conditioning, mental toughness, late-game execution)
    -- Distinct from clutch performance: overtime specifically tests ability to win extended games
    COALESCE(mf.home_overtime_win_pct, 0.5) as home_overtime_win_pct,
    COALESCE(mf.home_overtime_game_count, 0) as home_overtime_game_count,
    COALESCE(mf.home_overtime_avg_point_diff, 0.0) as home_overtime_avg_point_diff,
    COALESCE(mf.away_overtime_win_pct, 0.5) as away_overtime_win_pct,
    COALESCE(mf.away_overtime_game_count, 0) as away_overtime_game_count,
    COALESCE(mf.away_overtime_avg_point_diff, 0.0) as away_overtime_avg_point_diff,
    -- Overtime differentials: Positive = home team performs better in overtime games (favor home)
    COALESCE(mf.home_overtime_win_pct, 0.5) - COALESCE(mf.away_overtime_win_pct, 0.5) as overtime_win_pct_diff,
    COALESCE(mf.home_overtime_avg_point_diff, 0.0) - COALESCE(mf.away_overtime_avg_point_diff, 0.0) as overtime_avg_point_diff_diff,
    
    -- NEW: Form divergence features (Iteration 37 - 2026-01-25)
    -- Recent performance vs season average - captures teams trending above/below their baseline
    -- Positive = playing above season average (trending up), Negative = playing below season average (trending down)
    -- Home team form divergence (5-game)
    COALESCE(mf.home_form_divergence_win_pct_5, 0.0) as home_form_divergence_win_pct_5,
    COALESCE(mf.home_form_divergence_ppg_5, 0.0) as home_form_divergence_ppg_5,
    COALESCE(mf.home_form_divergence_opp_ppg_5, 0.0) as home_form_divergence_opp_ppg_5,
    COALESCE(mf.home_form_divergence_net_rtg_5, 0.0) as home_form_divergence_net_rtg_5,
    -- Home team form divergence (10-game)
    COALESCE(mf.home_form_divergence_win_pct_10, 0.0) as home_form_divergence_win_pct_10,
    COALESCE(mf.home_form_divergence_ppg_10, 0.0) as home_form_divergence_ppg_10,
    COALESCE(mf.home_form_divergence_opp_ppg_10, 0.0) as home_form_divergence_opp_ppg_10,
    COALESCE(mf.home_form_divergence_net_rtg_10, 0.0) as home_form_divergence_net_rtg_10,
    -- Away team form divergence (5-game)
    COALESCE(mf.away_form_divergence_win_pct_5, 0.0) as away_form_divergence_win_pct_5,
    COALESCE(mf.away_form_divergence_ppg_5, 0.0) as away_form_divergence_ppg_5,
    COALESCE(mf.away_form_divergence_opp_ppg_5, 0.0) as away_form_divergence_opp_ppg_5,
    COALESCE(mf.away_form_divergence_net_rtg_5, 0.0) as away_form_divergence_net_rtg_5,
    -- Away team form divergence (10-game)
    COALESCE(mf.away_form_divergence_win_pct_10, 0.0) as away_form_divergence_win_pct_10,
    COALESCE(mf.away_form_divergence_ppg_10, 0.0) as away_form_divergence_ppg_10,
    COALESCE(mf.away_form_divergence_opp_ppg_10, 0.0) as away_form_divergence_opp_ppg_10,
    COALESCE(mf.away_form_divergence_net_rtg_10, 0.0) as away_form_divergence_net_rtg_10,
    -- Form divergence magnitude (absolute divergence - how much is form diverging?)
    COALESCE(mf.home_form_divergence_magnitude_win_pct_5, 0.0) as home_form_divergence_magnitude_win_pct_5,
    COALESCE(mf.home_form_divergence_magnitude_win_pct_10, 0.0) as home_form_divergence_magnitude_win_pct_10,
    COALESCE(mf.away_form_divergence_magnitude_win_pct_5, 0.0) as away_form_divergence_magnitude_win_pct_5,
    COALESCE(mf.away_form_divergence_magnitude_win_pct_10, 0.0) as away_form_divergence_magnitude_win_pct_10,
    -- Differential features (home - away): Positive = home team trending better than away team (favor home)
    COALESCE(mf.home_form_divergence_win_pct_5, 0.0) - COALESCE(mf.away_form_divergence_win_pct_5, 0.0) as form_divergence_win_pct_diff_5,
    COALESCE(mf.home_form_divergence_ppg_5, 0.0) - COALESCE(mf.away_form_divergence_ppg_5, 0.0) as form_divergence_ppg_diff_5,
    COALESCE(mf.home_form_divergence_net_rtg_5, 0.0) - COALESCE(mf.away_form_divergence_net_rtg_5, 0.0) as form_divergence_net_rtg_diff_5,
    COALESCE(mf.home_form_divergence_win_pct_10, 0.0) - COALESCE(mf.away_form_divergence_win_pct_10, 0.0) as form_divergence_win_pct_diff_10,
    COALESCE(mf.home_form_divergence_ppg_10, 0.0) - COALESCE(mf.away_form_divergence_ppg_10, 0.0) as form_divergence_ppg_diff_10,
    COALESCE(mf.home_form_divergence_net_rtg_10, 0.0) - COALESCE(mf.away_form_divergence_net_rtg_10, 0.0) as form_divergence_net_rtg_diff_10,
    -- Iteration 38: Upset resistance features (teams that rarely lose when favored, frequently win when underdogs)
    -- Home team upset resistance (underdog counts from upset_resistance to avoid duplicate with favored/underdog)
    COALESCE(mf.home_favorite_win_pct, 0.5) as home_favorite_win_pct,
    COALESCE(mf.home_favorite_game_count, 0) as home_favorite_game_count,
    COALESCE(mf.home_underdog_win_pct, 0.3) as home_underdog_win_pct,
    COALESCE(mf.home_underdog_game_count_upset, 0) as home_underdog_game_count_upset,
    COALESCE(mf.home_upset_resistance_score, 0.0) as home_upset_resistance_score,
    -- Away team upset resistance
    COALESCE(mf.away_favorite_win_pct, 0.5) as away_favorite_win_pct,
    COALESCE(mf.away_favorite_game_count, 0) as away_favorite_game_count,
    COALESCE(mf.away_underdog_win_pct, 0.3) as away_underdog_win_pct,
    COALESCE(mf.away_underdog_game_count_upset, 0) as away_underdog_game_count_upset,
    COALESCE(mf.away_upset_resistance_score, 0.0) as away_upset_resistance_score,
    -- Differential features (home - away): Positive = home team has better upset resistance (favor home)
    COALESCE(mf.home_favorite_win_pct, 0.5) - COALESCE(mf.away_favorite_win_pct, 0.5) as favorite_win_pct_diff,
    COALESCE(mf.home_underdog_win_pct, 0.3) - COALESCE(mf.away_underdog_win_pct, 0.3) as underdog_win_pct_diff,
    COALESCE(mf.home_upset_resistance_score, 0.0) - COALESCE(mf.away_upset_resistance_score, 0.0) as upset_resistance_score_diff,
    
    -- NEW: Momentum weighted by opponent quality (Iteration 21 - 2026-01-25)
    -- Wins/losses weighted by opponent strength (rolling 10-game win_pct)
    -- A win against a strong team contributes more to momentum than a win against a weak team
    -- A loss against a strong team hurts momentum less than a loss against a weak team
    COALESCE(mf.home_weighted_momentum_5, 0.0) as home_weighted_momentum_5,
    COALESCE(mf.home_weighted_momentum_10, 0.0) as home_weighted_momentum_10,
    COALESCE(mf.away_weighted_momentum_5, 0.0) as away_weighted_momentum_5,
    COALESCE(mf.away_weighted_momentum_10, 0.0) as away_weighted_momentum_10,
    -- Weighted momentum differentials: Positive = home team has better weighted momentum (favor home)
    COALESCE(mf.weighted_momentum_diff_5, 0.0) as weighted_momentum_diff_5,
    COALESCE(mf.weighted_momentum_diff_10, 0.0) as weighted_momentum_diff_10,
    -- Iteration 18: Recent performance weighted by opponent quality
    COALESCE(mf.home_weighted_win_pct_5, 0.5) as home_weighted_win_pct_5,
    COALESCE(mf.home_weighted_win_pct_10, 0.5) as home_weighted_win_pct_10,
    COALESCE(mf.home_weighted_point_diff_5, 0.0) as home_weighted_point_diff_5,
    COALESCE(mf.home_weighted_point_diff_10, 0.0) as home_weighted_point_diff_10,
    COALESCE(mf.home_weighted_net_rtg_5, 0.0) as home_weighted_net_rtg_5,
    COALESCE(mf.home_weighted_net_rtg_10, 0.0) as home_weighted_net_rtg_10,
    COALESCE(mf.away_weighted_win_pct_5, 0.5) as away_weighted_win_pct_5,
    COALESCE(mf.away_weighted_win_pct_10, 0.5) as away_weighted_win_pct_10,
    COALESCE(mf.away_weighted_point_diff_5, 0.0) as away_weighted_point_diff_5,
    COALESCE(mf.away_weighted_point_diff_10, 0.0) as away_weighted_point_diff_10,
    COALESCE(mf.away_weighted_net_rtg_5, 0.0) as away_weighted_net_rtg_5,
    COALESCE(mf.away_weighted_net_rtg_10, 0.0) as away_weighted_net_rtg_10,
    COALESCE(mf.weighted_win_pct_diff_5, 0.0) as weighted_win_pct_diff_5,
    COALESCE(mf.weighted_win_pct_diff_10, 0.0) as weighted_win_pct_diff_10,
    COALESCE(mf.weighted_point_diff_diff_5, 0.0) as weighted_point_diff_diff_5,
    COALESCE(mf.weighted_point_diff_diff_10, 0.0) as weighted_point_diff_diff_10,
    COALESCE(mf.weighted_net_rtg_diff_5, 0.0) as weighted_net_rtg_diff_5,
    COALESCE(mf.weighted_net_rtg_diff_10, 0.0) as weighted_net_rtg_diff_10,
    -- Iteration 22: Form divergence features (recent performance vs season average)
    -- Home team form divergence (5-game and 10-game)
    COALESCE(mf.home_win_pct_divergence_5, 0.0) as home_win_pct_divergence_5,
    COALESCE(mf.home_win_pct_divergence_10, 0.0) as home_win_pct_divergence_10,
    COALESCE(mf.home_net_rtg_divergence_5, 0.0) as home_net_rtg_divergence_5,
    COALESCE(mf.home_net_rtg_divergence_10, 0.0) as home_net_rtg_divergence_10,
    COALESCE(mf.home_ppg_divergence_5, 0.0) as home_ppg_divergence_5,
    COALESCE(mf.home_ppg_divergence_10, 0.0) as home_ppg_divergence_10,
    COALESCE(mf.home_opp_ppg_divergence_5, 0.0) as home_opp_ppg_divergence_5,
    COALESCE(mf.home_opp_ppg_divergence_10, 0.0) as home_opp_ppg_divergence_10,
    COALESCE(mf.home_off_rtg_divergence_5, 0.0) as home_off_rtg_divergence_5,
    COALESCE(mf.home_off_rtg_divergence_10, 0.0) as home_off_rtg_divergence_10,
    COALESCE(mf.home_def_rtg_divergence_5, 0.0) as home_def_rtg_divergence_5,
    COALESCE(mf.home_def_rtg_divergence_10, 0.0) as home_def_rtg_divergence_10,
    -- Away team form divergence (5-game and 10-game)
    COALESCE(mf.away_win_pct_divergence_5, 0.0) as away_win_pct_divergence_5,
    COALESCE(mf.away_win_pct_divergence_10, 0.0) as away_win_pct_divergence_10,
    COALESCE(mf.away_net_rtg_divergence_5, 0.0) as away_net_rtg_divergence_5,
    COALESCE(mf.away_net_rtg_divergence_10, 0.0) as away_net_rtg_divergence_10,
    COALESCE(mf.away_ppg_divergence_5, 0.0) as away_ppg_divergence_5,
    COALESCE(mf.away_ppg_divergence_10, 0.0) as away_ppg_divergence_10,
    COALESCE(mf.away_opp_ppg_divergence_5, 0.0) as away_opp_ppg_divergence_5,
    COALESCE(mf.away_opp_ppg_divergence_10, 0.0) as away_opp_ppg_divergence_10,
    COALESCE(mf.away_off_rtg_divergence_5, 0.0) as away_off_rtg_divergence_5,
    COALESCE(mf.away_off_rtg_divergence_10, 0.0) as away_off_rtg_divergence_10,
    COALESCE(mf.away_def_rtg_divergence_5, 0.0) as away_def_rtg_divergence_5,
    COALESCE(mf.away_def_rtg_divergence_10, 0.0) as away_def_rtg_divergence_10,
    -- Form divergence differentials (home - away)
    COALESCE(mf.home_win_pct_divergence_5, 0.0) - COALESCE(mf.away_win_pct_divergence_5, 0.0) as win_pct_divergence_diff_5,
    COALESCE(mf.home_win_pct_divergence_10, 0.0) - COALESCE(mf.away_win_pct_divergence_10, 0.0) as win_pct_divergence_diff_10,
    COALESCE(mf.home_net_rtg_divergence_5, 0.0) - COALESCE(mf.away_net_rtg_divergence_5, 0.0) as net_rtg_divergence_diff_5,
    COALESCE(mf.home_net_rtg_divergence_10, 0.0) - COALESCE(mf.away_net_rtg_divergence_10, 0.0) as net_rtg_divergence_diff_10,
    COALESCE(mf.home_ppg_divergence_5, 0.0) - COALESCE(mf.away_ppg_divergence_5, 0.0) as ppg_divergence_diff_5,
    COALESCE(mf.home_ppg_divergence_10, 0.0) - COALESCE(mf.away_ppg_divergence_10, 0.0) as ppg_divergence_diff_10,
    -- NEW: Home/away splits by opponent quality (Iteration 24 - 2026-01-25)
    -- How teams perform at home/away against strong vs weak opponents
    -- Strong opponent = rolling 10-game win_pct > 0.5, Weak = <= 0.5
    -- Captures context-specific performance: some teams excel at home vs strong teams, struggle on road vs weak teams
    COALESCE(mf.home_win_pct_vs_strong, 0.5) as home_win_pct_vs_strong,
    COALESCE(mf.home_win_pct_vs_weak, 0.5) as home_win_pct_vs_weak,
    COALESCE(mf.away_win_pct_vs_strong, 0.5) as away_win_pct_vs_strong,
    COALESCE(mf.away_win_pct_vs_weak, 0.5) as away_win_pct_vs_weak,
    -- Differential features: Positive = home team performs better in this context (favor home)
    COALESCE(mf.home_win_pct_vs_strong, 0.5) - COALESCE(mf.away_win_pct_vs_strong, 0.5) as win_pct_vs_strong_diff,
    COALESCE(mf.home_win_pct_vs_weak, 0.5) - COALESCE(mf.away_win_pct_vs_weak, 0.5) as win_pct_vs_weak_diff,
    -- NEW: Home/away performance by rest days (Iteration 28 - 2026-01-25)
    -- How teams perform at home/away with different rest day buckets (back-to-back, 2 days, 3+ days)
    -- Captures context-specific performance: some teams perform better at home with more rest, others don't show as much benefit
    -- Rest day buckets: 0-1 (back-to-back), 2 days, 3+ days (well-rested)
    COALESCE(mf.home_win_pct_by_rest_bucket, 0.5) as home_win_pct_by_rest_bucket,
    COALESCE(mf.away_win_pct_by_rest_bucket, 0.5) as away_win_pct_by_rest_bucket,
    -- Differential feature: Positive = home team performs better with current rest bucket (favor home)
    COALESCE(mf.rest_bucket_win_pct_diff, 0.0) as rest_bucket_win_pct_diff,
    -- Iteration 29: Performance trends (rate of change between 5-game and 10-game windows)
    -- Home team trends: Positive = improving, Negative = declining
    COALESCE(mf.home_win_pct_trend, 0.0) as home_win_pct_trend,
    COALESCE(mf.home_net_rtg_trend, 0.0) as home_net_rtg_trend,
    COALESCE(mf.home_ppg_trend, 0.0) as home_ppg_trend,
    COALESCE(mf.home_off_rtg_trend, 0.0) as home_off_rtg_trend,
    COALESCE(mf.home_efg_pct_trend, 0.0) as home_efg_pct_trend,
    COALESCE(mf.home_ts_pct_trend, 0.0) as home_ts_pct_trend,
    COALESCE(mf.home_opp_ppg_trend, 0.0) as home_opp_ppg_trend,
    COALESCE(mf.home_def_rtg_trend, 0.0) as home_def_rtg_trend,
    -- Away team trends: Positive = improving, Negative = declining
    COALESCE(mf.away_win_pct_trend, 0.0) as away_win_pct_trend,
    COALESCE(mf.away_net_rtg_trend, 0.0) as away_net_rtg_trend,
    COALESCE(mf.away_ppg_trend, 0.0) as away_ppg_trend,
    COALESCE(mf.away_off_rtg_trend, 0.0) as away_off_rtg_trend,
    COALESCE(mf.away_efg_pct_trend, 0.0) as away_efg_pct_trend,
    COALESCE(mf.away_ts_pct_trend, 0.0) as away_ts_pct_trend,
    COALESCE(mf.away_opp_ppg_trend, 0.0) as away_opp_ppg_trend,
    COALESCE(mf.away_def_rtg_trend, 0.0) as away_def_rtg_trend,
    -- Trend differentials: Positive = home team trending better than away team (favor home)
    COALESCE(mf.home_win_pct_trend, 0.0) - COALESCE(mf.away_win_pct_trend, 0.0) as win_pct_trend_diff,
    COALESCE(mf.home_net_rtg_trend, 0.0) - COALESCE(mf.away_net_rtg_trend, 0.0) as net_rtg_trend_diff,
    COALESCE(mf.home_ppg_trend, 0.0) - COALESCE(mf.away_ppg_trend, 0.0) as ppg_trend_diff,
    COALESCE(mf.home_off_rtg_trend, 0.0) - COALESCE(mf.away_off_rtg_trend, 0.0) as off_rtg_trend_diff,
    COALESCE(mf.home_efg_pct_trend, 0.0) - COALESCE(mf.away_efg_pct_trend, 0.0) as efg_pct_trend_diff,
    COALESCE(mf.home_ts_pct_trend, 0.0) - COALESCE(mf.away_ts_pct_trend, 0.0) as ts_pct_trend_diff,
    COALESCE(mf.home_opp_ppg_trend, 0.0) - COALESCE(mf.away_opp_ppg_trend, 0.0) as opp_ppg_trend_diff,
    COALESCE(mf.home_def_rtg_trend, 0.0) - COALESCE(mf.away_def_rtg_trend, 0.0) as def_rtg_trend_diff,
    -- Iteration 3: Recent momentum (rate of change between 3-game and 5-game windows - more immediate than 5-game vs 10-game trends)
    -- Home team recent momentum: Positive = improving (last 3 games better than last 5), Negative = declining
    COALESCE(mf.home_recent_win_pct_momentum, 0.0) as home_recent_win_pct_momentum,
    COALESCE(mf.home_recent_net_rtg_momentum, 0.0) as home_recent_net_rtg_momentum,
    COALESCE(mf.home_recent_ppg_momentum, 0.0) as home_recent_ppg_momentum,
    COALESCE(mf.home_recent_off_rtg_momentum, 0.0) as home_recent_off_rtg_momentum,
    COALESCE(mf.home_recent_efg_pct_momentum, 0.0) as home_recent_efg_pct_momentum,
    COALESCE(mf.home_recent_ts_pct_momentum, 0.0) as home_recent_ts_pct_momentum,
    COALESCE(mf.home_recent_opp_ppg_momentum, 0.0) as home_recent_opp_ppg_momentum,
    COALESCE(mf.home_recent_def_rtg_momentum, 0.0) as home_recent_def_rtg_momentum,
    -- Away team recent momentum: Positive = improving, Negative = declining
    COALESCE(mf.away_recent_win_pct_momentum, 0.0) as away_recent_win_pct_momentum,
    COALESCE(mf.away_recent_net_rtg_momentum, 0.0) as away_recent_net_rtg_momentum,
    COALESCE(mf.away_recent_ppg_momentum, 0.0) as away_recent_ppg_momentum,
    COALESCE(mf.away_recent_off_rtg_momentum, 0.0) as away_recent_off_rtg_momentum,
    COALESCE(mf.away_recent_efg_pct_momentum, 0.0) as away_recent_efg_pct_momentum,
    COALESCE(mf.away_recent_ts_pct_momentum, 0.0) as away_recent_ts_pct_momentum,
    COALESCE(mf.away_recent_opp_ppg_momentum, 0.0) as away_recent_opp_ppg_momentum,
    COALESCE(mf.away_recent_def_rtg_momentum, 0.0) as away_recent_def_rtg_momentum,
    -- Recent momentum differentials: Positive = home team has better recent momentum (favor home)
    COALESCE(mf.home_recent_win_pct_momentum, 0.0) - COALESCE(mf.away_recent_win_pct_momentum, 0.0) as recent_win_pct_momentum_diff,
    COALESCE(mf.home_recent_net_rtg_momentum, 0.0) - COALESCE(mf.away_recent_net_rtg_momentum, 0.0) as recent_net_rtg_momentum_diff,
    COALESCE(mf.home_recent_ppg_momentum, 0.0) - COALESCE(mf.away_recent_ppg_momentum, 0.0) as recent_ppg_momentum_diff,
    COALESCE(mf.home_recent_off_rtg_momentum, 0.0) - COALESCE(mf.away_recent_off_rtg_momentum, 0.0) as recent_off_rtg_momentum_diff,
    COALESCE(mf.home_recent_efg_pct_momentum, 0.0) - COALESCE(mf.away_recent_efg_pct_momentum, 0.0) as recent_efg_pct_momentum_diff,
    COALESCE(mf.home_recent_ts_pct_momentum, 0.0) - COALESCE(mf.away_recent_ts_pct_momentum, 0.0) as recent_ts_pct_momentum_diff,
    COALESCE(mf.home_recent_opp_ppg_momentum, 0.0) - COALESCE(mf.away_recent_opp_ppg_momentum, 0.0) as recent_opp_ppg_momentum_diff,
    COALESCE(mf.home_recent_def_rtg_momentum, 0.0) - COALESCE(mf.away_recent_def_rtg_momentum, 0.0) as recent_def_rtg_momentum_diff,
    
    -- Iteration 30: Matchup style performance features - team performance vs different opponent styles
    -- Home team performance vs different styles
    COALESCE(mf.home_win_pct_vs_fast_paced, 0.5) as home_win_pct_vs_fast_paced,
    COALESCE(mf.home_win_pct_vs_slow_paced, 0.5) as home_win_pct_vs_slow_paced,
    COALESCE(mf.home_win_pct_vs_high_scoring, 0.5) as home_win_pct_vs_high_scoring,
    COALESCE(mf.home_win_pct_vs_defensive, 0.5) as home_win_pct_vs_defensive,
    -- Away team performance vs different styles
    COALESCE(mf.away_win_pct_vs_fast_paced, 0.5) as away_win_pct_vs_fast_paced,
    COALESCE(mf.away_win_pct_vs_slow_paced, 0.5) as away_win_pct_vs_slow_paced,
    COALESCE(mf.away_win_pct_vs_high_scoring, 0.5) as away_win_pct_vs_high_scoring,
    COALESCE(mf.away_win_pct_vs_defensive, 0.5) as away_win_pct_vs_defensive,
    -- Differential features (home - away)
    COALESCE(mf.home_win_pct_vs_fast_paced, 0.5) - COALESCE(mf.away_win_pct_vs_fast_paced, 0.5) as win_pct_vs_fast_paced_diff,
    COALESCE(mf.home_win_pct_vs_slow_paced, 0.5) - COALESCE(mf.away_win_pct_vs_slow_paced, 0.5) as win_pct_vs_slow_paced_diff,
    COALESCE(mf.home_win_pct_vs_high_scoring, 0.5) - COALESCE(mf.away_win_pct_vs_high_scoring, 0.5) as win_pct_vs_high_scoring_diff,
    COALESCE(mf.home_win_pct_vs_defensive, 0.5) - COALESCE(mf.away_win_pct_vs_defensive, 0.5) as win_pct_vs_defensive_diff,
    
    -- Iteration 2: Opponent similarity performance features - team performance vs opponents with similar characteristics
    -- Home team performance vs similar opponents (opponents with similar pace, offensive/defensive ratings)
    COALESCE(mf.home_win_pct_vs_similar_opponents, 0.5) as home_win_pct_vs_similar_opponents,
    COALESCE(mf.home_similar_opponent_game_count, 0) as home_similar_opponent_game_count,
    COALESCE(mf.home_avg_point_diff_vs_similar_opponents, 0.0) as home_avg_point_diff_vs_similar_opponents,
    -- Away team performance vs similar opponents
    COALESCE(mf.away_win_pct_vs_similar_opponents, 0.5) as away_win_pct_vs_similar_opponents,
    COALESCE(mf.away_similar_opponent_game_count, 0) as away_similar_opponent_game_count,
    COALESCE(mf.away_avg_point_diff_vs_similar_opponents, 0.0) as away_avg_point_diff_vs_similar_opponents,
    -- Current game similarity score (how similar are home and away teams in this game?)
    COALESCE(mf.current_game_similarity_score, 0.5) as current_game_similarity_score,
    -- Differential features (home - away): Positive = home team performs better vs similar opponents (favor home)
    COALESCE(mf.home_win_pct_vs_similar_opponents, 0.5) - COALESCE(mf.away_win_pct_vs_similar_opponents, 0.5) as win_pct_vs_similar_opponents_diff,
    COALESCE(mf.home_avg_point_diff_vs_similar_opponents, 0.0) - COALESCE(mf.away_avg_point_diff_vs_similar_opponents, 0.0) as avg_point_diff_vs_similar_opponents_diff,
    
    -- Iteration 15: Performance vs similar quality opponents (team performance vs opponents with similar win_pct or net_rtg)
    COALESCE(mf.home_win_pct_vs_similar_quality, 0.5) as home_win_pct_vs_similar_quality,
    COALESCE(mf.home_avg_point_diff_vs_similar_quality, 0.0) as home_avg_point_diff_vs_similar_quality,
    COALESCE(mf.home_similar_quality_game_count, 0) as home_similar_quality_game_count,
    COALESCE(mf.away_win_pct_vs_similar_quality, 0.5) as away_win_pct_vs_similar_quality,
    COALESCE(mf.away_avg_point_diff_vs_similar_quality, 0.0) as away_avg_point_diff_vs_similar_quality,
    COALESCE(mf.away_similar_quality_game_count, 0) as away_similar_quality_game_count,
    COALESCE(mf.is_current_game_similar_quality, 0) as is_current_game_similar_quality,
    -- Differential features (home - away): Positive = home team performs better vs similar-quality opponents (favor home)
    COALESCE(mf.home_win_pct_vs_similar_quality, 0.5) - COALESCE(mf.away_win_pct_vs_similar_quality, 0.5) as win_pct_vs_similar_quality_diff,
    COALESCE(mf.home_avg_point_diff_vs_similar_quality, 0.0) - COALESCE(mf.away_avg_point_diff_vs_similar_quality, 0.0) as avg_point_diff_vs_similar_quality_diff,
    
    -- Iteration 28: Performance vs similar momentum opponents (team performance vs opponents with similar recent momentum)
    -- Home team performance vs similar-momentum opponents
    COALESCE(mf.home_win_pct_vs_similar_momentum, 0.5) as home_win_pct_vs_similar_momentum,
    COALESCE(mf.home_avg_point_diff_vs_similar_momentum, 0.0) as home_avg_point_diff_vs_similar_momentum,
    COALESCE(mf.home_similar_momentum_game_count, 0) as home_similar_momentum_game_count,
    -- Away team performance vs similar-momentum opponents
    COALESCE(mf.away_win_pct_vs_similar_momentum, 0.5) as away_win_pct_vs_similar_momentum,
    COALESCE(mf.away_avg_point_diff_vs_similar_momentum, 0.0) as away_avg_point_diff_vs_similar_momentum,
    COALESCE(mf.away_similar_momentum_game_count, 0) as away_similar_momentum_game_count,
    -- Current game momentum similarity indicator
    COALESCE(mf.is_current_game_similar_momentum, 0) as is_current_game_similar_momentum,
    -- Differential features (home - away): Positive = home team performs better vs similar-momentum opponents (favor home)
    COALESCE(mf.home_win_pct_vs_similar_momentum, 0.5) - COALESCE(mf.away_win_pct_vs_similar_momentum, 0.5) as win_pct_vs_similar_momentum_diff,
    COALESCE(mf.home_avg_point_diff_vs_similar_momentum, 0.0) - COALESCE(mf.away_avg_point_diff_vs_similar_momentum, 0.0) as avg_point_diff_vs_similar_momentum_diff,
    -- Iteration 33: Performance vs similar rest context features
    -- Captures how teams perform when both teams have similar rest levels (both well-rested, both on back-to-back, etc.)
    -- This is different from rest advantage - it's about the matchup context when both teams have similar rest
    COALESCE(mf.home_win_pct_vs_similar_rest, 0.5) as home_win_pct_vs_similar_rest,
    COALESCE(mf.home_avg_point_diff_vs_similar_rest, 0.0) as home_avg_point_diff_vs_similar_rest,
    COALESCE(mf.home_similar_rest_game_count, 0) as home_similar_rest_game_count,
    COALESCE(mf.away_win_pct_vs_similar_rest, 0.5) as away_win_pct_vs_similar_rest,
    COALESCE(mf.away_avg_point_diff_vs_similar_rest, 0.0) as away_avg_point_diff_vs_similar_rest,
    COALESCE(mf.away_similar_rest_game_count, 0) as away_similar_rest_game_count,
    COALESCE(mf.is_current_game_similar_rest_context, FALSE) as is_current_game_similar_rest_context,
    -- Differential features (home - away): Positive = home team performs better vs similar-rest opponents (favor home)
    COALESCE(mf.win_pct_vs_similar_rest_diff, 0.0) as win_pct_vs_similar_rest_diff,
    COALESCE(mf.avg_point_diff_vs_similar_rest_diff, 0.0) as avg_point_diff_vs_similar_rest_diff,
    -- Iteration 35: Performance in rest advantage scenarios features
    -- Captures how teams perform when they have rest advantage vs rest disadvantage
    -- Home team performance in rest advantage scenarios
    COALESCE(mf.home_win_pct_well_rested_vs_tired, 0.5) as home_win_pct_well_rested_vs_tired,
    COALESCE(mf.home_avg_point_diff_well_rested_vs_tired, 0.0) as home_avg_point_diff_well_rested_vs_tired,
    COALESCE(mf.home_well_rested_vs_tired_count, 0) as home_well_rested_vs_tired_count,
    COALESCE(mf.home_win_pct_well_rested_vs_moderate, 0.5) as home_win_pct_well_rested_vs_moderate,
    COALESCE(mf.home_avg_point_diff_well_rested_vs_moderate, 0.0) as home_avg_point_diff_well_rested_vs_moderate,
    COALESCE(mf.home_well_rested_vs_moderate_count, 0) as home_well_rested_vs_moderate_count,
    COALESCE(mf.home_win_pct_moderate_vs_tired, 0.5) as home_win_pct_moderate_vs_tired,
    COALESCE(mf.home_avg_point_diff_moderate_vs_tired, 0.0) as home_avg_point_diff_moderate_vs_tired,
    COALESCE(mf.home_moderate_vs_tired_count, 0) as home_moderate_vs_tired_count,
    COALESCE(mf.home_win_pct_with_rest_advantage, 0.5) as home_win_pct_with_rest_advantage,
    COALESCE(mf.home_avg_point_diff_with_rest_advantage, 0.0) as home_avg_point_diff_with_rest_advantage,
    COALESCE(mf.home_with_rest_advantage_count, 0) as home_with_rest_advantage_count,
    -- Away team performance in rest advantage scenarios
    COALESCE(mf.away_win_pct_well_rested_vs_tired, 0.5) as away_win_pct_well_rested_vs_tired,
    COALESCE(mf.away_avg_point_diff_well_rested_vs_tired, 0.0) as away_avg_point_diff_well_rested_vs_tired,
    COALESCE(mf.away_well_rested_vs_tired_count, 0) as away_well_rested_vs_tired_count,
    COALESCE(mf.away_win_pct_well_rested_vs_moderate, 0.5) as away_win_pct_well_rested_vs_moderate,
    COALESCE(mf.away_avg_point_diff_well_rested_vs_moderate, 0.0) as away_avg_point_diff_well_rested_vs_moderate,
    COALESCE(mf.away_well_rested_vs_moderate_count, 0) as away_well_rested_vs_moderate_count,
    COALESCE(mf.away_win_pct_moderate_vs_tired, 0.5) as away_win_pct_moderate_vs_tired,
    COALESCE(mf.away_avg_point_diff_moderate_vs_tired, 0.0) as away_avg_point_diff_moderate_vs_tired,
    COALESCE(mf.away_moderate_vs_tired_count, 0) as away_moderate_vs_tired_count,
    COALESCE(mf.away_win_pct_with_rest_advantage, 0.5) as away_win_pct_with_rest_advantage,
    COALESCE(mf.away_avg_point_diff_with_rest_advantage, 0.0) as away_avg_point_diff_with_rest_advantage,
    COALESCE(mf.away_with_rest_advantage_count, 0) as away_with_rest_advantage_count,
    -- Current game rest scenario indicators
    COALESCE(mf.is_well_rested_vs_tired, 0) as is_well_rested_vs_tired,
    COALESCE(mf.is_well_rested_vs_moderate, 0) as is_well_rested_vs_moderate,
    COALESCE(mf.is_moderate_vs_tired, 0) as is_moderate_vs_tired,
    COALESCE(mf.is_rest_advantage_scenario, 0) as is_rest_advantage_scenario,
    -- Differential features (home - away)
    COALESCE(mf.win_pct_well_rested_vs_tired_diff, 0.0) as win_pct_well_rested_vs_tired_diff,
    COALESCE(mf.win_pct_well_rested_vs_moderate_diff, 0.0) as win_pct_well_rested_vs_moderate_diff,
    COALESCE(mf.win_pct_moderate_vs_tired_diff, 0.0) as win_pct_moderate_vs_tired_diff,
    COALESCE(mf.win_pct_with_rest_advantage_diff, 0.0) as win_pct_with_rest_advantage_diff,
    -- Iteration 62: Team performance by rest day combination (specific home/away rest combinations)
    -- Captures how teams perform in games with specific rest day combinations (e.g., home 2 days rest vs away 1 day rest)
    -- Home team performance in this rest combination
    COALESCE(mf.home_win_pct_by_rest_combination, 0.5) as home_win_pct_by_rest_combination,
    COALESCE(mf.home_avg_point_diff_by_rest_combination, 0.0) as home_avg_point_diff_by_rest_combination,
    COALESCE(mf.home_rest_combination_count, 0) as home_rest_combination_count,
    -- Away team performance in this rest combination
    COALESCE(mf.away_win_pct_by_rest_combination, 0.5) as away_win_pct_by_rest_combination,
    COALESCE(mf.away_avg_point_diff_by_rest_combination, 0.0) as away_avg_point_diff_by_rest_combination,
    COALESCE(mf.away_rest_combination_count, 0) as away_rest_combination_count,
    -- Differential features (home - away)
    COALESCE(mf.rest_combination_win_pct_diff, 0.0) as rest_combination_win_pct_diff,
    COALESCE(mf.rest_combination_point_diff_diff, 0.0) as rest_combination_point_diff_diff,
    -- Iteration 34: Performance vs similar rest context × team quality interactions
    -- Better teams may perform differently in similar rest contexts than weaker teams
    -- For example, strong teams facing strong teams when both are well-rested is a different matchup than weak teams facing weak teams
    COALESCE(mf.home_win_pct_vs_similar_rest, 0.5) * COALESCE(h.home_rolling_10_win_pct, 0.5) as home_win_pct_vs_similar_rest_x_home_win_pct_10,
    COALESCE(mf.away_win_pct_vs_similar_rest, 0.5) * COALESCE(a.away_rolling_10_win_pct, 0.5) as away_win_pct_vs_similar_rest_x_away_win_pct_10,
    COALESCE(mf.win_pct_vs_similar_rest_diff, 0.0) * COALESCE(h.home_rolling_10_win_pct - a.away_rolling_10_win_pct, 0.0) as win_pct_vs_similar_rest_diff_x_win_pct_diff_10,
    COALESCE(mf.is_current_game_similar_rest_context, FALSE)::integer * COALESCE(h.home_rolling_10_win_pct - a.away_rolling_10_win_pct, 0.0) as is_current_game_similar_rest_context_x_win_pct_diff_10,
    -- Iteration 36: Performance vs opponent quality by rest days
    -- Captures how teams perform vs strong/weak opponents when they have different rest levels
    -- Home team's win % vs strong opponents by rest bucket
    COALESCE(mf.home_win_pct_vs_strong_by_rest, 0.5) as home_win_pct_vs_strong_by_rest,
    COALESCE(mf.home_games_vs_strong_by_rest, 0) as home_games_vs_strong_by_rest,
    -- Home team's win % vs weak opponents by rest bucket
    COALESCE(mf.home_win_pct_vs_weak_by_rest, 0.5) as home_win_pct_vs_weak_by_rest,
    COALESCE(mf.home_games_vs_weak_by_rest, 0) as home_games_vs_weak_by_rest,
    -- Away team's win % vs strong opponents by rest bucket
    COALESCE(mf.away_win_pct_vs_strong_by_rest, 0.5) as away_win_pct_vs_strong_by_rest,
    COALESCE(mf.away_games_vs_strong_by_rest, 0) as away_games_vs_strong_by_rest,
    -- Away team's win % vs weak opponents by rest bucket
    COALESCE(mf.away_win_pct_vs_weak_by_rest, 0.5) as away_win_pct_vs_weak_by_rest,
    COALESCE(mf.away_games_vs_weak_by_rest, 0) as away_games_vs_weak_by_rest,
    -- Differential features (home - away)
    COALESCE(mf.win_pct_vs_strong_by_rest_diff, 0.0) as win_pct_vs_strong_by_rest_diff,
    COALESCE(mf.win_pct_vs_weak_by_rest_diff, 0.0) as win_pct_vs_weak_by_rest_diff,
    
    -- Iteration 31: Rivalry indicators - matchup frequency and intensity
    COALESCE(mf.rivalry_total_matchups, 0) as rivalry_total_matchups,
    COALESCE(mf.rivalry_recent_matchups, 0) as rivalry_recent_matchups,
    -- Iteration 38: Opponent tier performance by home/away context (team performance vs opponent quality tiers split by home/away)
    -- Home team's performance vs current opponent tier at home
    COALESCE(mf.home_win_pct_vs_opponent_tier_at_home, 0.5) as home_win_pct_vs_opponent_tier_at_home,
    COALESCE(mf.home_avg_point_diff_vs_opponent_tier_at_home, 0.0) as home_avg_point_diff_vs_opponent_tier_at_home,
    COALESCE(mf.home_game_count_vs_opponent_tier_at_home, 0) as home_game_count_vs_opponent_tier_at_home,
    -- Home team's performance vs current opponent tier on the road (for reference)
    COALESCE(mf.home_win_pct_vs_opponent_tier_on_road, 0.5) as home_win_pct_vs_opponent_tier_on_road,
    COALESCE(mf.home_avg_point_diff_vs_opponent_tier_on_road, 0.0) as home_avg_point_diff_vs_opponent_tier_on_road,
    COALESCE(mf.home_game_count_vs_opponent_tier_on_road, 0) as home_game_count_vs_opponent_tier_on_road,
    -- Away team's performance vs current opponent tier at home (for reference)
    COALESCE(mf.away_win_pct_vs_opponent_tier_at_home, 0.5) as away_win_pct_vs_opponent_tier_at_home,
    COALESCE(mf.away_avg_point_diff_vs_opponent_tier_at_home, 0.0) as away_avg_point_diff_vs_opponent_tier_at_home,
    COALESCE(mf.away_game_count_vs_opponent_tier_at_home, 0) as away_game_count_vs_opponent_tier_at_home,
    -- Away team's performance vs current opponent tier on the road
    COALESCE(mf.away_win_pct_vs_opponent_tier_on_road, 0.5) as away_win_pct_vs_opponent_tier_on_road,
    COALESCE(mf.away_avg_point_diff_vs_opponent_tier_on_road, 0.0) as away_avg_point_diff_vs_opponent_tier_on_road,
    COALESCE(mf.away_game_count_vs_opponent_tier_on_road, 0) as away_game_count_vs_opponent_tier_on_road,
    -- Differential features: home team at home vs away team on road
    COALESCE(mf.win_pct_vs_opponent_tier_home_away_diff, 0.0) as win_pct_vs_opponent_tier_home_away_diff,
    COALESCE(mf.avg_point_diff_vs_opponent_tier_home_away_diff, 0.0) as avg_point_diff_vs_opponent_tier_home_away_diff,
    -- Iteration 7: Opponent-specific performance features (team performance vs specific opponents in different contexts)
    -- Home team performance vs this specific opponent (home context)
    COALESCE(mf.home_vs_opponent_home_win_pct, 0.5) as home_vs_opponent_home_win_pct,
    COALESCE(mf.home_vs_opponent_home_games, 0) as home_vs_opponent_home_games,
    COALESCE(mf.home_vs_opponent_home_avg_point_diff, 0.0) as home_vs_opponent_home_avg_point_diff,
    -- Home team performance vs this specific opponent (away context)
    COALESCE(mf.home_vs_opponent_away_win_pct, 0.5) as home_vs_opponent_away_win_pct,
    COALESCE(mf.home_vs_opponent_away_games, 0) as home_vs_opponent_away_games,
    COALESCE(mf.home_vs_opponent_away_avg_point_diff, 0.0) as home_vs_opponent_away_avg_point_diff,
    -- Away team performance vs this specific opponent (home context)
    COALESCE(mf.away_vs_opponent_home_win_pct, 0.5) as away_vs_opponent_home_win_pct,
    COALESCE(mf.away_vs_opponent_home_games, 0) as away_vs_opponent_home_games,
    COALESCE(mf.away_vs_opponent_home_avg_point_diff, 0.0) as away_vs_opponent_home_avg_point_diff,
    -- Away team performance vs this specific opponent (away context)
    COALESCE(mf.away_vs_opponent_away_win_pct, 0.5) as away_vs_opponent_away_win_pct,
    COALESCE(mf.away_vs_opponent_away_games, 0) as away_vs_opponent_away_games,
    COALESCE(mf.away_vs_opponent_away_avg_point_diff, 0.0) as away_vs_opponent_away_avg_point_diff,
    -- Differential features (home - away)
    COALESCE(mf.opponent_specific_win_pct_diff, 0.0) as opponent_specific_win_pct_diff,
    COALESCE(mf.opponent_specific_avg_point_diff_diff, 0.0) as opponent_specific_avg_point_diff_diff,
    COALESCE(mf.rivalry_avg_point_margin, 0.0) as rivalry_avg_point_margin,
    COALESCE(mf.rivalry_close_game_pct, 0.0) as rivalry_close_game_pct,
    COALESCE(mf.rivalry_very_close_game_pct, 0.0) as rivalry_very_close_game_pct,
    COALESCE(mf.rivalry_recent_close_game_pct, 0.0) as rivalry_recent_close_game_pct,
    COALESCE(mf.rivalry_recent_avg_margin, 0.0) as rivalry_recent_avg_margin,
    
    -- Iteration 30: Season progress features - captures game context (early/mid/late season)
    -- Days since season start (approximate: NBA season typically starts in mid-October)
    EXTRACT(DOY FROM g.game_date) - 280 as days_since_season_start,
    -- Season progress: 0.0 = early season (Oct-Dec), 0.5 = mid season (Jan-Feb), 1.0 = late season (Mar-Apr)
    -- Normalize by typical season length (~180 days from Oct to Apr)
    LEAST(GREATEST((EXTRACT(DOY FROM g.game_date) - 280) / 180.0, 0.0), 1.0) as season_progress,
    -- Is late season (last 30% of season, typically March-April when playoff implications matter)
    CASE WHEN (EXTRACT(DOY FROM g.game_date) - 280) / 180.0 >= 0.7 THEN 1 ELSE 0 END as is_late_season,
    
    -- Iteration 32: Playoff race context features - game importance and urgency
    COALESCE(mf.home_playoff_rank, 30) as home_playoff_rank,
    COALESCE(mf.home_is_in_playoff_position, 0) as home_is_in_playoff_position,
    COALESCE(mf.home_games_back_from_cutoff, 0) as home_games_back_from_cutoff,
    COALESCE(mf.home_win_pct_diff_from_cutoff, 0.0) as home_win_pct_diff_from_cutoff,
    COALESCE(mf.home_is_close_to_cutoff, 0) as home_is_close_to_cutoff,
    COALESCE(mf.home_is_fighting_for_playoff_spot, 0) as home_is_fighting_for_playoff_spot,
    COALESCE(mf.away_playoff_rank, 30) as away_playoff_rank,
    COALESCE(mf.away_is_in_playoff_position, 0) as away_is_in_playoff_position,
    COALESCE(mf.away_games_back_from_cutoff, 0) as away_games_back_from_cutoff,
    COALESCE(mf.away_win_pct_diff_from_cutoff, 0.0) as away_win_pct_diff_from_cutoff,
    COALESCE(mf.away_is_close_to_cutoff, 0) as away_is_close_to_cutoff,
    COALESCE(mf.away_is_fighting_for_playoff_spot, 0) as away_is_fighting_for_playoff_spot,
    -- Differential features
    COALESCE(mf.home_playoff_rank, 30) - COALESCE(mf.away_playoff_rank, 30) as playoff_rank_diff,
    COALESCE(mf.home_is_in_playoff_position, 0) - COALESCE(mf.away_is_in_playoff_position, 0) as playoff_position_diff,
    COALESCE(mf.home_games_back_from_cutoff, 0) - COALESCE(mf.away_games_back_from_cutoff, 0) as games_back_from_cutoff_diff,
    COALESCE(mf.home_win_pct_diff_from_cutoff, 0.0) - COALESCE(mf.away_win_pct_diff_from_cutoff, 0.0) as win_pct_diff_from_cutoff_diff,
    -- Game importance indicator: both teams fighting for playoff spot
    CASE 
        WHEN COALESCE(mf.home_is_fighting_for_playoff_spot, 0) = 1 
         AND COALESCE(mf.away_is_fighting_for_playoff_spot, 0) = 1 
        THEN 1 
        ELSE 0 
    END as is_playoff_race_game,
    -- High-stakes game: at least one team close to cutoff
    CASE 
        WHEN COALESCE(mf.home_is_close_to_cutoff, 0) = 1 
         OR COALESCE(mf.away_is_close_to_cutoff, 0) = 1 
        THEN 1 
        ELSE 0 
    END as is_high_stakes_game,
    
    -- Iteration 43: Season timing performance features (team performance by season phase)
    -- Early season performance (first 20 games)
    COALESCE(mf.home_early_season_win_pct, 0.5) as home_early_season_win_pct,
    COALESCE(mf.home_early_season_game_count, 0) as home_early_season_game_count,
    COALESCE(mf.home_early_season_avg_point_diff, 0.0) as home_early_season_avg_point_diff,
    -- Mid season performance (games 21-60)
    COALESCE(mf.home_mid_season_win_pct, 0.5) as home_mid_season_win_pct,
    COALESCE(mf.home_mid_season_game_count, 0) as home_mid_season_game_count,
    COALESCE(mf.home_mid_season_avg_point_diff, 0.0) as home_mid_season_avg_point_diff,
    -- Late season performance (games 61-82)
    COALESCE(mf.home_late_season_win_pct, 0.5) as home_late_season_win_pct,
    COALESCE(mf.home_late_season_game_count, 0) as home_late_season_game_count,
    COALESCE(mf.home_late_season_avg_point_diff, 0.0) as home_late_season_avg_point_diff,
    -- Playoff push performance (last 10 games)
    COALESCE(mf.home_playoff_push_win_pct, 0.5) as home_playoff_push_win_pct,
    COALESCE(mf.home_playoff_push_game_count, 0) as home_playoff_push_game_count,
    COALESCE(mf.home_playoff_push_avg_point_diff, 0.0) as home_playoff_push_avg_point_diff,
    -- Current game season phase indicators
    COALESCE(mf.is_home_early_season, 0) as is_home_early_season,
    COALESCE(mf.is_home_mid_season, 0) as is_home_mid_season,
    COALESCE(mf.is_home_late_season, 0) as is_home_late_season,
    COALESCE(mf.is_home_playoff_push, 0) as is_home_playoff_push,
    -- Away team season timing features
    COALESCE(mf.away_early_season_win_pct, 0.5) as away_early_season_win_pct,
    COALESCE(mf.away_early_season_game_count, 0) as away_early_season_game_count,
    COALESCE(mf.away_early_season_avg_point_diff, 0.0) as away_early_season_avg_point_diff,
    COALESCE(mf.away_mid_season_win_pct, 0.5) as away_mid_season_win_pct,
    COALESCE(mf.away_mid_season_game_count, 0) as away_mid_season_game_count,
    COALESCE(mf.away_mid_season_avg_point_diff, 0.0) as away_mid_season_avg_point_diff,
    COALESCE(mf.away_late_season_win_pct, 0.5) as away_late_season_win_pct,
    COALESCE(mf.away_late_season_game_count, 0) as away_late_season_game_count,
    COALESCE(mf.away_late_season_avg_point_diff, 0.0) as away_late_season_avg_point_diff,
    COALESCE(mf.away_playoff_push_win_pct, 0.5) as away_playoff_push_win_pct,
    COALESCE(mf.away_playoff_push_game_count, 0) as away_playoff_push_game_count,
    COALESCE(mf.away_playoff_push_avg_point_diff, 0.0) as away_playoff_push_avg_point_diff,
    COALESCE(mf.is_away_early_season, 0) as is_away_early_season,
    COALESCE(mf.is_away_mid_season, 0) as is_away_mid_season,
    COALESCE(mf.is_away_late_season, 0) as is_away_late_season,
    COALESCE(mf.is_away_playoff_push, 0) as is_away_playoff_push,
    -- Differential features (home - away)
    COALESCE(mf.early_season_win_pct_diff, 0.0) as early_season_win_pct_diff,
    COALESCE(mf.early_season_avg_point_diff_diff, 0.0) as early_season_avg_point_diff_diff,
    COALESCE(mf.mid_season_win_pct_diff, 0.0) as mid_season_win_pct_diff,
    COALESCE(mf.mid_season_avg_point_diff_diff, 0.0) as mid_season_avg_point_diff_diff,
    COALESCE(mf.late_season_win_pct_diff, 0.0) as late_season_win_pct_diff,
    COALESCE(mf.late_season_avg_point_diff_diff, 0.0) as late_season_avg_point_diff_diff,
    COALESCE(mf.playoff_push_win_pct_diff, 0.0) as playoff_push_win_pct_diff,
    COALESCE(mf.playoff_push_avg_point_diff_diff, 0.0) as playoff_push_avg_point_diff_diff,
    -- Iteration 34: Game outcome quality features (how teams win/lose - close vs blowouts)
    COALESCE(mf.home_avg_margin_in_wins_5, 0.0) as home_avg_margin_in_wins_5,
    COALESCE(mf.home_avg_margin_in_losses_5, 0.0) as home_avg_margin_in_losses_5,
    COALESCE(mf.home_close_game_win_pct_5, 0.5) as home_close_game_win_pct_5,
    COALESCE(mf.home_blowout_game_win_pct_5, 0.5) as home_blowout_game_win_pct_5,
    COALESCE(mf.home_avg_margin_in_wins_10, 0.0) as home_avg_margin_in_wins_10,
    COALESCE(mf.home_avg_margin_in_losses_10, 0.0) as home_avg_margin_in_losses_10,
    COALESCE(mf.home_close_game_win_pct_10, 0.5) as home_close_game_win_pct_10,
    COALESCE(mf.home_blowout_game_win_pct_10, 0.5) as home_blowout_game_win_pct_10,
    COALESCE(mf.away_avg_margin_in_wins_5, 0.0) as away_avg_margin_in_wins_5,
    COALESCE(mf.away_avg_margin_in_losses_5, 0.0) as away_avg_margin_in_losses_5,
    COALESCE(mf.away_close_game_win_pct_5, 0.5) as away_close_game_win_pct_5,
    COALESCE(mf.away_blowout_game_win_pct_5, 0.5) as away_blowout_game_win_pct_5,
    COALESCE(mf.away_avg_margin_in_wins_10, 0.0) as away_avg_margin_in_wins_10,
    COALESCE(mf.away_avg_margin_in_losses_10, 0.0) as away_avg_margin_in_losses_10,
    COALESCE(mf.away_close_game_win_pct_10, 0.5) as away_close_game_win_pct_10,
    COALESCE(mf.away_blowout_game_win_pct_10, 0.5) as away_blowout_game_win_pct_10,
    -- Differential features (home - away)
    COALESCE(mf.home_avg_margin_in_wins_5, 0.0) - COALESCE(mf.away_avg_margin_in_wins_5, 0.0) as avg_margin_in_wins_diff_5,
    COALESCE(mf.home_avg_margin_in_losses_5, 0.0) - COALESCE(mf.away_avg_margin_in_losses_5, 0.0) as avg_margin_in_losses_diff_5,
    COALESCE(mf.home_close_game_win_pct_5, 0.5) - COALESCE(mf.away_close_game_win_pct_5, 0.5) as close_game_win_pct_diff_5,
    COALESCE(mf.home_blowout_game_win_pct_5, 0.5) - COALESCE(mf.away_blowout_game_win_pct_5, 0.5) as blowout_game_win_pct_diff_5,
    COALESCE(mf.home_avg_margin_in_wins_10, 0.0) - COALESCE(mf.away_avg_margin_in_wins_10, 0.0) as avg_margin_in_wins_diff_10,
    COALESCE(mf.home_avg_margin_in_losses_10, 0.0) - COALESCE(mf.away_avg_margin_in_losses_10, 0.0) as avg_margin_in_losses_diff_10,
    COALESCE(mf.home_close_game_win_pct_10, 0.5) - COALESCE(mf.away_close_game_win_pct_10, 0.5) as close_game_win_pct_diff_10,
    COALESCE(mf.home_blowout_game_win_pct_10, 0.5) - COALESCE(mf.away_blowout_game_win_pct_10, 0.5) as blowout_game_win_pct_diff_10,
    -- Iteration 35: Contextualized streak features (streaks weighted by opponent quality and home/away context)
    COALESCE(mf.home_weighted_win_streak, 0.0) as home_weighted_win_streak,
    COALESCE(mf.home_weighted_loss_streak, 0.0) as home_weighted_loss_streak,
    COALESCE(mf.home_win_streak_avg_opponent_quality, 0.5) as home_win_streak_avg_opponent_quality,
    COALESCE(mf.home_loss_streak_avg_opponent_quality, 0.5) as home_loss_streak_avg_opponent_quality,
    COALESCE(mf.away_weighted_win_streak, 0.0) as away_weighted_win_streak,
    COALESCE(mf.away_weighted_loss_streak, 0.0) as away_weighted_loss_streak,
    COALESCE(mf.away_win_streak_avg_opponent_quality, 0.5) as away_win_streak_avg_opponent_quality,
    COALESCE(mf.away_loss_streak_avg_opponent_quality, 0.5) as away_loss_streak_avg_opponent_quality,
    -- Differential features (home - away)
    COALESCE(mf.home_weighted_win_streak, 0.0) - COALESCE(mf.away_weighted_win_streak, 0.0) as weighted_win_streak_diff,
    COALESCE(mf.home_weighted_loss_streak, 0.0) - COALESCE(mf.away_weighted_loss_streak, 0.0) as weighted_loss_streak_diff,
    COALESCE(mf.home_win_streak_avg_opponent_quality, 0.5) - COALESCE(mf.away_win_streak_avg_opponent_quality, 0.5) as win_streak_avg_opponent_quality_diff,
    COALESCE(mf.home_loss_streak_avg_opponent_quality, 0.5) - COALESCE(mf.away_loss_streak_avg_opponent_quality, 0.5) as loss_streak_avg_opponent_quality_diff,
    -- Iteration 4: Quality-adjusted performance features - captures strength of recent wins/losses, not just win percentage
    -- Home team quality-adjusted features (5-game window)
    COALESCE(mf.home_avg_opponent_strength_in_wins_5, 0.5) as home_avg_opponent_strength_in_wins_5,
    COALESCE(mf.home_avg_opponent_strength_in_losses_5, 0.5) as home_avg_opponent_strength_in_losses_5,
    COALESCE(mf.home_avg_opponent_strength_5, 0.5) as home_avg_opponent_strength_5,
    COALESCE(mf.home_quality_adjusted_win_pct_5, 0.5) as home_quality_adjusted_win_pct_5,
    COALESCE(mf.home_avg_point_diff_in_wins_5, 0.0) as home_avg_point_diff_in_wins_5,
    COALESCE(mf.home_avg_point_diff_in_losses_5, 0.0) as home_avg_point_diff_in_losses_5,
    COALESCE(mf.home_wins_count_5, 0) as home_wins_count_5,
    COALESCE(mf.home_losses_count_5, 0) as home_losses_count_5,
    COALESCE(mf.home_total_games_5, 0) as home_total_games_5,
    -- Home team quality-adjusted features (10-game window)
    COALESCE(mf.home_avg_opponent_strength_in_wins_10, 0.5) as home_avg_opponent_strength_in_wins_10,
    COALESCE(mf.home_avg_opponent_strength_in_losses_10, 0.5) as home_avg_opponent_strength_in_losses_10,
    COALESCE(mf.home_avg_opponent_strength_10, 0.5) as home_avg_opponent_strength_10,
    COALESCE(mf.home_quality_adjusted_win_pct_10, 0.5) as home_quality_adjusted_win_pct_10,
    COALESCE(mf.home_avg_point_diff_in_wins_10, 0.0) as home_avg_point_diff_in_wins_10,
    COALESCE(mf.home_avg_point_diff_in_losses_10, 0.0) as home_avg_point_diff_in_losses_10,
    COALESCE(mf.home_wins_count_10, 0) as home_wins_count_10,
    COALESCE(mf.home_losses_count_10, 0) as home_losses_count_10,
    COALESCE(mf.home_total_games_10, 0) as home_total_games_10,
    -- Away team quality-adjusted features (5-game window)
    COALESCE(mf.away_avg_opponent_strength_in_wins_5, 0.5) as away_avg_opponent_strength_in_wins_5,
    COALESCE(mf.away_avg_opponent_strength_in_losses_5, 0.5) as away_avg_opponent_strength_in_losses_5,
    COALESCE(mf.away_avg_opponent_strength_5, 0.5) as away_avg_opponent_strength_5,
    COALESCE(mf.away_quality_adjusted_win_pct_5, 0.5) as away_quality_adjusted_win_pct_5,
    COALESCE(mf.away_avg_point_diff_in_wins_5, 0.0) as away_avg_point_diff_in_wins_5,
    COALESCE(mf.away_avg_point_diff_in_losses_5, 0.0) as away_avg_point_diff_in_losses_5,
    COALESCE(mf.away_wins_count_5, 0) as away_wins_count_5,
    COALESCE(mf.away_losses_count_5, 0) as away_losses_count_5,
    COALESCE(mf.away_total_games_5, 0) as away_total_games_5,
    -- Away team quality-adjusted features (10-game window)
    COALESCE(mf.away_avg_opponent_strength_in_wins_10, 0.5) as away_avg_opponent_strength_in_wins_10,
    COALESCE(mf.away_avg_opponent_strength_in_losses_10, 0.5) as away_avg_opponent_strength_in_losses_10,
    COALESCE(mf.away_avg_opponent_strength_10, 0.5) as away_avg_opponent_strength_10,
    COALESCE(mf.away_quality_adjusted_win_pct_10, 0.5) as away_quality_adjusted_win_pct_10,
    COALESCE(mf.away_avg_point_diff_in_wins_10, 0.0) as away_avg_point_diff_in_wins_10,
    COALESCE(mf.away_avg_point_diff_in_losses_10, 0.0) as away_avg_point_diff_in_losses_10,
    COALESCE(mf.away_wins_count_10, 0) as away_wins_count_10,
    COALESCE(mf.away_losses_count_10, 0) as away_losses_count_10,
    COALESCE(mf.away_total_games_10, 0) as away_total_games_10,
    -- Differential features (home - away)
    COALESCE(mf.quality_adjusted_win_pct_diff_5, 0.0) as quality_adjusted_win_pct_diff_5,
    COALESCE(mf.quality_adjusted_win_pct_diff_10, 0.0) as quality_adjusted_win_pct_diff_10,
    COALESCE(mf.avg_opponent_strength_diff_5, 0.0) as avg_opponent_strength_diff_5,
    COALESCE(mf.avg_opponent_strength_diff_10, 0.0) as avg_opponent_strength_diff_10,
    -- Iteration 1: Recent form trend features (comparing recent 3 vs recent 5 games)
    -- Captures whether teams are improving or declining (direction of change)
    COALESCE(mf.home_form_trend_win_pct, 0.0) as home_form_trend_win_pct,
    COALESCE(mf.home_form_trend_point_diff, 0.0) as home_form_trend_point_diff,
    COALESCE(mf.away_form_trend_win_pct, 0.0) as away_form_trend_win_pct,
    COALESCE(mf.away_form_trend_point_diff, 0.0) as away_form_trend_point_diff,
    COALESCE(mf.form_trend_win_pct_diff, 0.0) as form_trend_win_pct_diff,
    COALESCE(mf.form_trend_point_diff_diff, 0.0) as form_trend_point_diff_diff,
    -- Iteration 5: Momentum acceleration features (rate of change in momentum)
    -- Captures whether momentum is accelerating or decelerating
    -- Positive values = accelerating momentum (improving faster), Negative = decelerating
    COALESCE(mf.home_momentum_acceleration, 0.0) as home_momentum_acceleration,
    COALESCE(mf.away_momentum_acceleration, 0.0) as away_momentum_acceleration,
    COALESCE(mf.momentum_acceleration_diff, 0.0) as momentum_acceleration_diff,
    -- Iteration 52: Performance acceleration features (comparing recent 3 games to previous 3 games)
    -- Captures rate of change in performance - teams improving vs declining
    -- Positive values = improving (recent 3 > previous 3), Negative = declining
    COALESCE(mf.home_win_pct_acceleration, 0.0) as home_win_pct_acceleration,
    COALESCE(mf.home_point_diff_acceleration, 0.0) as home_point_diff_acceleration,
    COALESCE(mf.home_ppg_acceleration, 0.0) as home_ppg_acceleration,
    COALESCE(mf.home_defensive_acceleration, 0.0) as home_defensive_acceleration,
    COALESCE(mf.home_net_rtg_acceleration, 0.0) as home_net_rtg_acceleration,
    COALESCE(mf.away_win_pct_acceleration, 0.0) as away_win_pct_acceleration,
    COALESCE(mf.away_point_diff_acceleration, 0.0) as away_point_diff_acceleration,
    COALESCE(mf.away_ppg_acceleration, 0.0) as away_ppg_acceleration,
    COALESCE(mf.away_defensive_acceleration, 0.0) as away_defensive_acceleration,
    COALESCE(mf.away_net_rtg_acceleration, 0.0) as away_net_rtg_acceleration,
    -- Differential features (home - away)
    COALESCE(mf.win_pct_acceleration_diff, 0.0) as win_pct_acceleration_diff,
    COALESCE(mf.point_diff_acceleration_diff, 0.0) as point_diff_acceleration_diff,
    COALESCE(mf.ppg_acceleration_diff, 0.0) as ppg_acceleration_diff,
    COALESCE(mf.defensive_acceleration_diff, 0.0) as defensive_acceleration_diff,
    COALESCE(mf.net_rtg_acceleration_diff, 0.0) as net_rtg_acceleration_diff,
    -- Iteration 72: Exponential-weighted momentum features (weights most recent games exponentially more)
    -- This captures current form better than simple rolling averages by emphasizing recent games
    -- Home team exponential momentum (last 10 games)
    COALESCE(mf.home_exp_weighted_win_pct_10, 0.5) as home_exp_weighted_win_pct_10,
    COALESCE(mf.home_exp_weighted_point_diff_10, 0.0) as home_exp_weighted_point_diff_10,
    COALESCE(mf.home_exp_weighted_ppg_10, 0.0) as home_exp_weighted_ppg_10,
    COALESCE(mf.home_exp_weighted_opp_ppg_10, 0.0) as home_exp_weighted_opp_ppg_10,
    -- Home team exponential momentum (last 5 games) - more recent focus
    COALESCE(mf.home_exp_weighted_win_pct_5, 0.5) as home_exp_weighted_win_pct_5,
    COALESCE(mf.home_exp_weighted_point_diff_5, 0.0) as home_exp_weighted_point_diff_5,
    -- Away team exponential momentum (last 10 games)
    COALESCE(mf.away_exp_weighted_win_pct_10, 0.5) as away_exp_weighted_win_pct_10,
    COALESCE(mf.away_exp_weighted_point_diff_10, 0.0) as away_exp_weighted_point_diff_10,
    COALESCE(mf.away_exp_weighted_ppg_10, 0.0) as away_exp_weighted_ppg_10,
    COALESCE(mf.away_exp_weighted_opp_ppg_10, 0.0) as away_exp_weighted_opp_ppg_10,
    -- Away team exponential momentum (last 5 games) - more recent focus
    COALESCE(mf.away_exp_weighted_win_pct_5, 0.5) as away_exp_weighted_win_pct_5,
    COALESCE(mf.away_exp_weighted_point_diff_5, 0.0) as away_exp_weighted_point_diff_5,
    -- Exponential momentum differentials (home - away): Positive = home team has better exponential momentum (favor home)
    COALESCE(mf.exp_weighted_win_pct_diff_10, 0.0) as exp_weighted_win_pct_diff_10,
    COALESCE(mf.exp_weighted_point_diff_diff_10, 0.0) as exp_weighted_point_diff_diff_10,
    COALESCE(mf.exp_weighted_ppg_diff_10, 0.0) as exp_weighted_ppg_diff_10,
    COALESCE(mf.exp_weighted_opp_ppg_diff_10, 0.0) as exp_weighted_opp_ppg_diff_10,
    COALESCE(mf.exp_weighted_win_pct_diff_5, 0.0) as exp_weighted_win_pct_diff_5,
    COALESCE(mf.exp_weighted_point_diff_diff_5, 0.0) as exp_weighted_point_diff_diff_5,
    -- Iteration 79: Cumulative fatigue features - capture game density over recent periods
    -- Home team cumulative fatigue
    COALESCE(mf.home_games_in_last_7_days, 0) as home_games_in_last_7_days,
    COALESCE(mf.home_games_in_last_10_days, 0) as home_games_in_last_10_days,
    COALESCE(mf.home_games_in_last_14_days, 0) as home_games_in_last_14_days,
    COALESCE(mf.home_avg_rest_days_last_7_days, 0.0) as home_avg_rest_days_last_7_days,
    COALESCE(mf.home_avg_rest_days_last_10_days, 0.0) as home_avg_rest_days_last_10_days,
    COALESCE(mf.home_game_density_7_days, 0.0) as home_game_density_7_days,
    COALESCE(mf.home_game_density_10_days, 0.0) as home_game_density_10_days,
    -- Away team cumulative fatigue
    COALESCE(mf.away_games_in_last_7_days, 0) as away_games_in_last_7_days,
    COALESCE(mf.away_games_in_last_10_days, 0) as away_games_in_last_10_days,
    COALESCE(mf.away_games_in_last_14_days, 0) as away_games_in_last_14_days,
    COALESCE(mf.away_avg_rest_days_last_7_days, 0.0) as away_avg_rest_days_last_7_days,
    COALESCE(mf.away_avg_rest_days_last_10_days, 0.0) as away_avg_rest_days_last_10_days,
    COALESCE(mf.away_game_density_7_days, 0.0) as away_game_density_7_days,
    COALESCE(mf.away_game_density_10_days, 0.0) as away_game_density_10_days,
    -- Cumulative fatigue differentials (home - away): Positive = home team has more fatigue (favor away)
    COALESCE(mf.games_in_last_7_days_diff, 0) as games_in_last_7_days_diff,
    COALESCE(mf.games_in_last_10_days_diff, 0) as games_in_last_10_days_diff,
    COALESCE(mf.games_in_last_14_days_diff, 0) as games_in_last_14_days_diff,
    COALESCE(mf.avg_rest_days_last_7_days_diff, 0.0) as avg_rest_days_last_7_days_diff,
    COALESCE(mf.avg_rest_days_last_10_days_diff, 0.0) as avg_rest_days_last_10_days_diff,
    COALESCE(mf.game_density_7_days_diff, 0.0) as game_density_7_days_diff,
    COALESCE(mf.game_density_10_days_diff, 0.0) as game_density_10_days_diff,
    -- Iteration 54: Team performance by pace context (fast-paced vs slow-paced games)
    -- Home team pace performance
    COALESCE(mf.home_fast_pace_win_pct, 0.5) as home_fast_pace_win_pct,
    COALESCE(mf.home_fast_pace_game_count, 0) as home_fast_pace_game_count,
    COALESCE(mf.home_fast_pace_avg_point_diff, 0.0) as home_fast_pace_avg_point_diff,
    COALESCE(mf.home_slow_pace_win_pct, 0.5) as home_slow_pace_win_pct,
    COALESCE(mf.home_slow_pace_game_count, 0) as home_slow_pace_game_count,
    COALESCE(mf.home_slow_pace_avg_point_diff, 0.0) as home_slow_pace_avg_point_diff,
    -- Away team pace performance
    COALESCE(mf.away_fast_pace_win_pct, 0.5) as away_fast_pace_win_pct,
    COALESCE(mf.away_fast_pace_game_count, 0) as away_fast_pace_game_count,
    COALESCE(mf.away_fast_pace_avg_point_diff, 0.0) as away_fast_pace_avg_point_diff,
    COALESCE(mf.away_slow_pace_win_pct, 0.5) as away_slow_pace_win_pct,
    COALESCE(mf.away_slow_pace_game_count, 0) as away_slow_pace_game_count,
    COALESCE(mf.away_slow_pace_avg_point_diff, 0.0) as away_slow_pace_avg_point_diff,
    -- Pace performance differentials (home - away)
    COALESCE(mf.fast_pace_win_pct_diff, 0.0) as fast_pace_win_pct_diff,
    COALESCE(mf.fast_pace_avg_point_diff_diff, 0.0) as fast_pace_avg_point_diff_diff,
    COALESCE(mf.slow_pace_win_pct_diff, 0.0) as slow_pace_win_pct_diff,
    COALESCE(mf.slow_pace_avg_point_diff_diff, 0.0) as slow_pace_avg_point_diff_diff,
    -- Iteration 14: Fourth quarter performance features (team performance in final quarter)
    -- Captures ability to close games, conditioning, and late-game execution
    -- Home team fourth quarter performance (rolling 5-game)
    COALESCE(mf.home_rolling_5_q4_ppg, 0.0) as home_rolling_5_q4_ppg,
    COALESCE(mf.home_rolling_5_q4_net_rtg, 0.0) as home_rolling_5_q4_net_rtg,
    COALESCE(mf.home_rolling_5_q4_win_pct, 0.5) as home_rolling_5_q4_win_pct,
    -- Home team fourth quarter performance (rolling 10-game)
    COALESCE(mf.home_rolling_10_q4_ppg, 0.0) as home_rolling_10_q4_ppg,
    COALESCE(mf.home_rolling_10_q4_net_rtg, 0.0) as home_rolling_10_q4_net_rtg,
    COALESCE(mf.home_rolling_10_q4_win_pct, 0.5) as home_rolling_10_q4_win_pct,
    -- Home team season fourth quarter win percentage when winning Q4
    COALESCE(mf.home_season_q4_win_pct_when_won_q4, 0.5) as home_season_q4_win_pct_when_won_q4,
    COALESCE(mf.home_q4_wins_count_season, 0) as home_q4_wins_count_season,
    -- Away team fourth quarter performance (rolling 5-game)
    COALESCE(mf.away_rolling_5_q4_ppg, 0.0) as away_rolling_5_q4_ppg,
    COALESCE(mf.away_rolling_5_q4_net_rtg, 0.0) as away_rolling_5_q4_net_rtg,
    COALESCE(mf.away_rolling_5_q4_win_pct, 0.5) as away_rolling_5_q4_win_pct,
    -- Away team fourth quarter performance (rolling 10-game)
    COALESCE(mf.away_rolling_10_q4_ppg, 0.0) as away_rolling_10_q4_ppg,
    COALESCE(mf.away_rolling_10_q4_net_rtg, 0.0) as away_rolling_10_q4_net_rtg,
    COALESCE(mf.away_rolling_10_q4_win_pct, 0.5) as away_rolling_10_q4_win_pct,
    -- Away team season fourth quarter win percentage when winning Q4
    COALESCE(mf.away_season_q4_win_pct_when_won_q4, 0.5) as away_season_q4_win_pct_when_won_q4,
    COALESCE(mf.away_q4_wins_count_season, 0) as away_q4_wins_count_season,
    -- Fourth quarter differentials (home - away): Positive = home team has better Q4 performance (favor home)
    COALESCE(mf.home_rolling_5_q4_ppg, 0.0) - COALESCE(mf.away_rolling_5_q4_ppg, 0.0) as q4_ppg_diff_5,
    COALESCE(mf.home_rolling_5_q4_net_rtg, 0.0) - COALESCE(mf.away_rolling_5_q4_net_rtg, 0.0) as q4_net_rtg_diff_5,
    COALESCE(mf.home_rolling_5_q4_win_pct, 0.5) - COALESCE(mf.away_rolling_5_q4_win_pct, 0.5) as q4_win_pct_diff_5,
    COALESCE(mf.home_rolling_10_q4_ppg, 0.0) - COALESCE(mf.away_rolling_10_q4_ppg, 0.0) as q4_ppg_diff_10,
    COALESCE(mf.home_rolling_10_q4_net_rtg, 0.0) - COALESCE(mf.away_rolling_10_q4_net_rtg, 0.0) as q4_net_rtg_diff_10,
    COALESCE(mf.home_rolling_10_q4_win_pct, 0.5) - COALESCE(mf.away_rolling_10_q4_win_pct, 0.5) as q4_win_pct_diff_10,
    COALESCE(mf.home_season_q4_win_pct_when_won_q4, 0.5) - COALESCE(mf.away_season_q4_win_pct_when_won_q4, 0.5) as season_q4_win_pct_when_won_q4_diff,
    
    -- Iteration 17: Team matchup compatibility features
    -- Captures how well two teams' styles match up in the current game
    -- Iteration 17: Team matchup compatibility features
    -- Pace compatibility (how similar are the teams' paces)
    COALESCE(mf.pace_difference, 0.0) as pace_difference,
    COALESCE(mf.pace_compatibility_score, 0.5) as pace_compatibility_score,
    -- Offensive/defensive style compatibility
    COALESCE(mf.home_off_vs_away_def_compatibility, 0.5) as home_off_vs_away_def_compatibility,
    COALESCE(mf.away_off_vs_home_def_compatibility, 0.5) as away_off_vs_home_def_compatibility,
    -- Style matchup advantage (which team's style is better suited)
    COALESCE(mf.style_matchup_advantage, 0.0) as style_matchup_advantage,
    -- Net rating compatibility
    COALESCE(mf.net_rtg_difference, 0.0) as net_rtg_difference,
    COALESCE(mf.net_rtg_compatibility_score, 0.5) as net_rtg_compatibility_score,
    
    -- Iteration 19: Matchup compatibility × team quality interactions
    -- Captures how matchup advantages are amplified by team quality differences
    -- Style matchup advantage × win percentage difference: Matchup advantage matters more when teams have different quality
    -- Positive when home team has style advantage AND is better (favor home)
    COALESCE(mf.style_matchup_advantage, 0.0) * COALESCE(h.home_rolling_10_win_pct - a.away_rolling_10_win_pct, 0.0) as style_matchup_advantage_x_win_pct_diff_10,
    -- Style matchup advantage × net rating difference: Matchup advantage matters more when teams have different quality
    COALESCE(mf.style_matchup_advantage, 0.0) * COALESCE(h.home_rolling_10_net_rtg - a.away_rolling_10_net_rtg, 0.0) as style_matchup_advantage_x_net_rtg_diff_10,
    -- Pace compatibility × win percentage difference: Pace compatibility matters more when teams have different quality
    -- Higher compatibility score with larger quality difference = more meaningful matchup
    COALESCE(mf.pace_compatibility_score, 0.5) * ABS(COALESCE(h.home_rolling_10_win_pct - a.away_rolling_10_win_pct, 0.0)) as pace_compatibility_x_win_pct_diff_10,
    -- Home offense vs away defense compatibility × win percentage difference: Compatibility matters more when teams have different quality
    COALESCE(mf.home_off_vs_away_def_compatibility, 0.5) * COALESCE(h.home_rolling_10_win_pct - a.away_rolling_10_win_pct, 0.0) as home_off_vs_away_def_compatibility_x_win_pct_diff_10,
    -- Away offense vs home defense compatibility × win percentage difference
    COALESCE(mf.away_off_vs_home_def_compatibility, 0.5) * COALESCE(a.away_rolling_10_win_pct - h.home_rolling_10_win_pct, 0.0) as away_off_vs_home_def_compatibility_x_win_pct_diff_10,
    
    -- Iteration 23: Recent form vs season average divergence (momentum vs regression to mean)
    -- Captures whether teams are playing above/below their season average recently
    -- Positive = playing above average (momentum), Negative = playing below average (regression)
    -- This is different from form_divergence which compares rolling 5 vs season - this compares rolling 10 vs season
    COALESCE(h.home_rolling_10_win_pct, 0.5) - COALESCE(hs.home_season_win_pct, 0.5) as home_form_vs_season_divergence_10,
    COALESCE(a.away_rolling_10_win_pct, 0.5) - COALESCE(aws.away_season_win_pct, 0.5) as away_form_vs_season_divergence_10,
    -- Differential: Which team is further from their season average (momentum advantage)
    (COALESCE(h.home_rolling_10_win_pct, 0.5) - COALESCE(hs.home_season_win_pct, 0.5)) - 
    (COALESCE(a.away_rolling_10_win_pct, 0.5) - COALESCE(aws.away_season_win_pct, 0.5)) as form_vs_season_divergence_diff_10,
    -- Net rating divergence: Performance vs season average (int_team_season_stats has point_diff, no net_rtg/season)
    COALESCE(h.home_rolling_10_net_rtg, 0.0) - COALESCE(hss.point_diff, 0.0) as home_net_rtg_vs_season_divergence_10,
    COALESCE(a.away_rolling_10_net_rtg, 0.0) - COALESCE(ass.point_diff, 0.0) as away_net_rtg_vs_season_divergence_10,
    (COALESCE(h.home_rolling_10_net_rtg, 0.0) - COALESCE(hss.point_diff, 0.0)) - 
    (COALESCE(a.away_rolling_10_net_rtg, 0.0) - COALESCE(ass.point_diff, 0.0)) as net_rtg_vs_season_divergence_diff_10
FROM game_base g
LEFT JOIN home_stats h ON h.game_id = g.game_id
LEFT JOIN away_stats a ON a.game_id = g.game_id
LEFT JOIN momentum_chunk mf ON mf.game_id = g.game_id
LEFT JOIN injury_chunk inf ON inf.game_id = g.game_id
LEFT JOIN star_avail_chunk spa ON spa.game_id = g.game_id
LEFT JOIN star_features_chunk hsf ON hsf.game_id = g.game_id AND hsf.team_id = g.home_team_id
LEFT JOIN star_features_chunk asf ON asf.game_id = g.game_id AND asf.team_id = g.away_team_id
LEFT JOIN home_season hs ON hs.team_id = g.home_team_id
LEFT JOIN away_season aws ON aws.team_id = g.away_team_id
LEFT JOIN season_stats_chunk hss ON hss.team_id = g.home_team_id
LEFT JOIN season_stats_chunk ass ON ass.team_id = g.away_team_id
LEFT JOIN upset_chunk ut ON ut.game_id = g.game_id
WHERE g.is_completed
ORDER BY g.game_date DESC, g.game_id;
