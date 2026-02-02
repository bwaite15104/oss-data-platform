MODEL (
    name intermediate.int_team_roll_lkp,
    kind INCREMENTAL_BY_TIME_RANGE(
        time_column game_date,
        batch_size 1  -- One year per backfill job for measurable progress
    ),
    description 'Pre-computed lookup: latest rolling stats before each game. Incremental by game_date with year-by-year backfill. Uses LATERAL join per chunk for efficiency.',
    start '1946-11-01',  -- NBA BAA start; first season
    cron '@yearly'
);

-- Process only games in the current interval (@start_ds to @end_ds).
-- LATERAL join is efficient per chunk (one year ≈ 1–2k games per team).
WITH games_in_range AS (
    SELECT g.game_id, g.game_date, g.home_team_id AS team_id
    FROM staging.stg_games g
    WHERE g.is_completed
      AND g.game_date >= @start_ds
      AND g.game_date < @end_ds

    UNION ALL

    SELECT g.game_id, g.game_date, g.away_team_id AS team_id
    FROM staging.stg_games g
    WHERE g.is_completed
      AND g.game_date >= @start_ds
      AND g.game_date < @end_ds
)

SELECT
    ag.game_id,
    ag.game_date,
    ag.team_id,
    r.rolling_5_ppg,
    r.rolling_5_opp_ppg,
    r.rolling_5_win_pct,
    r.rolling_5_apg,
    r.rolling_5_rpg,
    r.rolling_5_fg_pct,
    r.rolling_5_fg3_pct,
    r.rolling_5_efg_pct,
    r.rolling_5_ts_pct,
    r.rolling_5_off_rtg,
    r.rolling_5_def_rtg,
    r.rolling_5_net_rtg,
    r.rolling_5_pace,
    r.rolling_5_possessions,
    r.rolling_5_tov_rate,
    r.rolling_5_assist_rate,
    r.rolling_5_steal_rate,
    r.rolling_5_block_rate,
    r.wins_last_5,
    r.rolling_5_ppg_stddev,
    r.rolling_5_opp_ppg_stddev,
    r.rolling_5_ppg_cv,
    r.rolling_5_opp_ppg_cv,
    r.rolling_10_ppg,
    r.rolling_10_opp_ppg,
    r.rolling_10_win_pct,
    r.rolling_10_efg_pct,
    r.rolling_10_ts_pct,
    r.rolling_10_off_rtg,
    r.rolling_10_def_rtg,
    r.rolling_10_net_rtg,
    r.rolling_10_pace,
    r.rolling_10_possessions,
    r.rolling_10_tov_rate,
    r.rolling_10_assist_rate,
    r.rolling_10_steal_rate,
    r.rolling_10_block_rate,
    r.wins_last_10,
    r.rolling_10_ppg_stddev,
    r.rolling_10_opp_ppg_stddev,
    r.rolling_10_ppg_cv,
    r.rolling_10_opp_ppg_cv
FROM games_in_range ag
LEFT JOIN LATERAL (
    SELECT
        rolling_5_ppg, rolling_5_opp_ppg, rolling_5_win_pct, rolling_5_apg, rolling_5_rpg,
        rolling_5_fg_pct, rolling_5_fg3_pct, rolling_5_efg_pct, rolling_5_ts_pct,
        rolling_5_off_rtg, rolling_5_def_rtg, rolling_5_net_rtg, rolling_5_pace,
        rolling_5_possessions, rolling_5_tov_rate, rolling_5_assist_rate,
        rolling_5_steal_rate, rolling_5_block_rate, wins_last_5,
        rolling_5_ppg_stddev, rolling_5_opp_ppg_stddev, rolling_5_ppg_cv, rolling_5_opp_ppg_cv,
        rolling_10_ppg, rolling_10_opp_ppg, rolling_10_win_pct, rolling_10_efg_pct,
        rolling_10_ts_pct, rolling_10_off_rtg, rolling_10_def_rtg, rolling_10_net_rtg,
        rolling_10_pace, rolling_10_possessions, rolling_10_tov_rate,
        rolling_10_assist_rate, rolling_10_steal_rate, rolling_10_block_rate,
        wins_last_10,
        rolling_10_ppg_stddev, rolling_10_opp_ppg_stddev, rolling_10_ppg_cv, rolling_10_opp_ppg_cv
    FROM intermediate.int_team_rolling_stats r
    WHERE r.team_id = ag.team_id
      AND r.game_date < ag.game_date
    ORDER BY r.game_date DESC
    LIMIT 1
) r ON TRUE
