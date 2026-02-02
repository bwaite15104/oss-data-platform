MODEL (
    name intermediate.int_game_injury_features,
    kind VIEW,
    description 'Calculates injury impact features for games by matching injuries to actual star players; falls back to boxscore DNP (star did not play) when injury data is missing'
);

-- Match injuries to games by date and team, then match to actual star players
WITH game_team_dates AS (
    SELECT 
        g.game_id,
        g.game_date,
        g.home_team_id,
        g.away_team_id,
        ht.team_abbreviation as home_team_abbrev,
        at.team_abbreviation as away_team_abbrev
    FROM staging.stg_games g
    LEFT JOIN raw_dev.teams ht ON g.home_team_id = ht.team_id
    LEFT JOIN raw_dev.teams at ON g.away_team_id = at.team_id
),
-- Match injuries to actual star players by player name and team
-- Note: Since ESPN scraper may not correctly parse status, we treat all players
-- in injuries table as potentially injured (conservative approach)
injury_star_matches AS (
    SELECT 
        i.injury_id,
        i.capture_date,
        i.player_name,
        i.player_espn_id,
        i.team_abbrev,
        i.status,
        i.status_raw,
        i.injury_details,
        p.player_id,
        p.team_id as player_team_id,  -- Player's current team
        sp.player_tier,
        sp.impact_score,
        -- Use injury team if known, otherwise use player's team
        COALESCE(t.team_id, p.team_id) as injury_team_id,
        -- Infer injury status: if status_raw contains injury keywords, use that
        -- Otherwise, treat as "Out" (conservative - if they're in injuries table, assume significant)
        CASE 
            WHEN UPPER(i.status_raw) LIKE '%OUT%' OR UPPER(i.injury_details) LIKE '%OUT%' THEN 'Out'
            WHEN UPPER(i.status_raw) LIKE '%DOUBTFUL%' OR UPPER(i.injury_details) LIKE '%DOUBTFUL%' THEN 'Doubtful'
            WHEN UPPER(i.status_raw) LIKE '%QUESTIONABLE%' OR UPPER(i.injury_details) LIKE '%QUESTIONABLE%' THEN 'Questionable'
            WHEN UPPER(i.status_raw) LIKE '%PROBABLE%' OR UPPER(i.injury_details) LIKE '%PROBABLE%' THEN 'Probable'
            WHEN UPPER(i.injury_details) LIKE '%SEASON%' AND UPPER(i.injury_details) LIKE '%END%' THEN 'Out'  -- Season ending = Out
            WHEN i.status_raw != 'N/A' AND i.status NOT IN ('Out', 'Doubtful', 'Questionable', 'Probable', 'Day-to-Day') THEN 'Out'  -- Default to Out if in injuries table
            ELSE 'Out'  -- Conservative: if in injuries table, assume Out
        END as inferred_status
    FROM staging.stg_injuries i
    LEFT JOIN raw_dev.teams t ON i.team_abbrev = t.team_abbreviation
    LEFT JOIN LATERAL (
        -- Try to match player: first by name+team, then by name only if team is UNK
        SELECT p.player_id, p.player_name, p.team_id, p.team_abbreviation
        FROM raw_dev.players p
        WHERE UPPER(TRIM(i.player_name)) = UPPER(TRIM(p.player_name))
          AND (
              -- Match by team if team is known and matches
              (i.team_abbrev != 'UNK' AND i.team_abbrev = p.team_abbreviation)
              OR
              -- If team is UNK, match by name only (take first match - will use player's team)
              (i.team_abbrev = 'UNK')
          )
        ORDER BY 
            -- Prefer exact team match
            CASE WHEN i.team_abbrev != 'UNK' AND i.team_abbrev = p.team_abbreviation THEN 1 ELSE 2 END,
            -- Then prefer players with more recent data
            p.player_id DESC
        LIMIT 1
    ) p ON TRUE
    LEFT JOIN intermediate.int_star_players sp ON p.player_id = sp.player_id
    WHERE i.status != 'N/A'  -- Exclude marker records
      AND sp.player_id IS NOT NULL  -- Only include if player is a star/key player
),
-- Get most recent injury data for each team before/on game date
home_team_injuries AS (
    SELECT 
        gtd.game_id,
        gtd.game_date,
        gtd.home_team_id,
        -- Count injured star players (actual counts, using inferred_status)
        COUNT(DISTINCT CASE WHEN ism.inferred_status = 'Out' AND ism.player_tier = 'star' THEN ism.player_id END) as home_star_players_out,
        COUNT(DISTINCT CASE WHEN ism.inferred_status = 'Out' AND ism.player_tier = 'key_player' THEN ism.player_id END) as home_key_players_out,
        COUNT(DISTINCT CASE WHEN ism.inferred_status = 'Doubtful' AND ism.player_tier = 'star' THEN ism.player_id END) as home_star_players_doubtful,
        COUNT(DISTINCT CASE WHEN ism.inferred_status = 'Doubtful' AND ism.player_tier = 'key_player' THEN ism.player_id END) as home_key_players_doubtful,
        COUNT(DISTINCT CASE WHEN ism.inferred_status = 'Questionable' AND ism.player_tier = 'star' THEN ism.player_id END) as home_star_players_questionable,
        COUNT(DISTINCT CASE WHEN ism.inferred_status = 'Questionable' AND ism.player_tier = 'key_player' THEN ism.player_id END) as home_key_players_questionable,
        -- Calculate injury impact score (weighted by player tier and status severity)
        SUM(CASE 
            WHEN ism.inferred_status = 'Out' AND ism.player_tier = 'star' THEN ism.impact_score * 1.0
            WHEN ism.inferred_status = 'Out' AND ism.player_tier = 'key_player' THEN ism.impact_score * 0.7
            WHEN ism.inferred_status = 'Doubtful' AND ism.player_tier = 'star' THEN ism.impact_score * 0.6
            WHEN ism.inferred_status = 'Doubtful' AND ism.player_tier = 'key_player' THEN ism.impact_score * 0.4
            WHEN ism.inferred_status = 'Questionable' AND ism.player_tier = 'star' THEN ism.impact_score * 0.3
            WHEN ism.inferred_status = 'Questionable' AND ism.player_tier = 'key_player' THEN ism.impact_score * 0.2
            ELSE 0
        END) as home_injury_impact_score,
        -- Total injured star/key players (all statuses)
        COUNT(DISTINCT CASE WHEN ism.inferred_status IN ('Out', 'Doubtful') THEN ism.player_id END) as home_injured_star_players_count,
        -- Flag if any key players are out
        MAX(CASE WHEN ism.inferred_status IN ('Out', 'Doubtful') AND ism.player_tier IN ('star', 'key_player') THEN 1 ELSE 0 END) as home_has_key_injury
    FROM game_team_dates gtd
    LEFT JOIN LATERAL (
        SELECT 
            ism.*
        FROM injury_star_matches ism
        WHERE ism.injury_team_id = gtd.home_team_id
          AND ism.capture_date <= gtd.game_date + INTERVAL '1 day'  -- Allow 1 day buffer for injury data capture timing
        ORDER BY ism.capture_date DESC
        LIMIT 100  -- Get recent injuries
    ) ism ON TRUE
    GROUP BY gtd.game_id, gtd.game_date, gtd.home_team_id
),
away_team_injuries AS (
    SELECT 
        gtd.game_id,
        gtd.game_date,
        gtd.away_team_id,
        -- Count injured star players
        COUNT(DISTINCT CASE WHEN ism.inferred_status = 'Out' AND ism.player_tier = 'star' THEN ism.player_id END) as away_star_players_out,
        COUNT(DISTINCT CASE WHEN ism.inferred_status = 'Out' AND ism.player_tier = 'key_player' THEN ism.player_id END) as away_key_players_out,
        COUNT(DISTINCT CASE WHEN ism.inferred_status = 'Doubtful' AND ism.player_tier = 'star' THEN ism.player_id END) as away_star_players_doubtful,
        COUNT(DISTINCT CASE WHEN ism.inferred_status = 'Doubtful' AND ism.player_tier = 'key_player' THEN ism.player_id END) as away_key_players_doubtful,
        COUNT(DISTINCT CASE WHEN ism.inferred_status = 'Questionable' AND ism.player_tier = 'star' THEN ism.player_id END) as away_star_players_questionable,
        COUNT(DISTINCT CASE WHEN ism.inferred_status = 'Questionable' AND ism.player_tier = 'key_player' THEN ism.player_id END) as away_key_players_questionable,
        -- Calculate injury impact score
        SUM(CASE 
            WHEN ism.inferred_status = 'Out' AND ism.player_tier = 'star' THEN ism.impact_score * 1.0
            WHEN ism.inferred_status = 'Out' AND ism.player_tier = 'key_player' THEN ism.impact_score * 0.7
            WHEN ism.inferred_status = 'Doubtful' AND ism.player_tier = 'star' THEN ism.impact_score * 0.6
            WHEN ism.inferred_status = 'Doubtful' AND ism.player_tier = 'key_player' THEN ism.impact_score * 0.4
            WHEN ism.inferred_status = 'Questionable' AND ism.player_tier = 'star' THEN ism.impact_score * 0.3
            WHEN ism.inferred_status = 'Questionable' AND ism.player_tier = 'key_player' THEN ism.impact_score * 0.2
            ELSE 0
        END) as away_injury_impact_score,
        -- Total injured star/key players
        COUNT(DISTINCT CASE WHEN ism.inferred_status IN ('Out', 'Doubtful') THEN ism.player_id END) as away_injured_star_players_count,
        -- Flag if any key players are out
        MAX(CASE WHEN ism.inferred_status IN ('Out', 'Doubtful') AND ism.player_tier IN ('star', 'key_player') THEN 1 ELSE 0 END) as away_has_key_injury
    FROM game_team_dates gtd
    LEFT JOIN LATERAL (
        SELECT 
            ism.*
        FROM injury_star_matches ism
        WHERE ism.injury_team_id = gtd.away_team_id
          AND ism.capture_date <= gtd.game_date + INTERVAL '1 day'  -- Allow 1 day buffer for injury data capture timing
        ORDER BY ism.capture_date DESC
        LIMIT 100  -- Get recent injuries
    ) ism ON TRUE
    GROUP BY gtd.game_id, gtd.game_date, gtd.away_team_id
),
-- Boxscore fallback: star players who did not play (DNP) = inferred "out" when injury data is missing
-- Use only for completed games (boxscores exist). Prefer injury-reported data when present.
players_who_played AS (
    SELECT b.game_id, b.player_id
    FROM staging.stg_player_boxscores b
    JOIN staging.stg_games g ON b.game_id = g.game_id
    WHERE g.is_completed AND b.minutes_played > 0
),
home_boxscore_fallback AS (
    SELECT
        gtd.game_id,
        COUNT(DISTINCT CASE WHEN sp.player_tier = 'star' THEN sp.player_id END) as home_star_players_out,
        COUNT(DISTINCT CASE WHEN sp.player_tier = 'key_player' THEN sp.player_id END) as home_key_players_out,
        0::BIGINT as home_star_players_doubtful,
        0::BIGINT as home_key_players_doubtful,
        0::BIGINT as home_star_players_questionable,
        0::BIGINT as home_key_players_questionable,
        COALESCE(SUM(sp.impact_score), 0) as home_injury_impact_score,
        COUNT(DISTINCT sp.player_id) as home_injured_star_players_count,
        CASE WHEN COUNT(DISTINCT sp.player_id) > 0 THEN 1 ELSE 0 END as home_has_key_injury
    FROM game_team_dates gtd
    JOIN staging.stg_games g ON gtd.game_id = g.game_id AND g.is_completed
    JOIN intermediate.int_star_players sp ON sp.team_id = gtd.home_team_id
    LEFT JOIN players_who_played pwp ON pwp.game_id = gtd.game_id AND pwp.player_id = sp.player_id
    WHERE pwp.player_id IS NULL
    GROUP BY gtd.game_id
),
away_boxscore_fallback AS (
    SELECT
        gtd.game_id,
        COUNT(DISTINCT CASE WHEN sp.player_tier = 'star' THEN sp.player_id END) as away_star_players_out,
        COUNT(DISTINCT CASE WHEN sp.player_tier = 'key_player' THEN sp.player_id END) as away_key_players_out,
        0::BIGINT as away_star_players_doubtful,
        0::BIGINT as away_key_players_doubtful,
        0::BIGINT as away_star_players_questionable,
        0::BIGINT as away_key_players_questionable,
        COALESCE(SUM(sp.impact_score), 0) as away_injury_impact_score,
        COUNT(DISTINCT sp.player_id) as away_injured_star_players_count,
        CASE WHEN COUNT(DISTINCT sp.player_id) > 0 THEN 1 ELSE 0 END as away_has_key_injury
    FROM game_team_dates gtd
    JOIN staging.stg_games g ON gtd.game_id = g.game_id AND g.is_completed
    JOIN intermediate.int_star_players sp ON sp.team_id = gtd.away_team_id
    LEFT JOIN players_who_played pwp ON pwp.game_id = gtd.game_id AND pwp.player_id = sp.player_id
    WHERE pwp.player_id IS NULL
    GROUP BY gtd.game_id
)
SELECT 
    gtd.game_id,
    gtd.game_date,
    gtd.home_team_id,
    gtd.away_team_id,
    -- Home team injury features: injury-reported when present, else boxscore DNP fallback
    COALESCE(NULLIF(hti.home_star_players_out, 0), hbf.home_star_players_out, 0)::BIGINT as home_star_players_out,
    COALESCE(NULLIF(hti.home_key_players_out, 0), hbf.home_key_players_out, 0)::BIGINT as home_key_players_out,
    COALESCE(hti.home_star_players_doubtful, 0) as home_star_players_doubtful,
    COALESCE(hti.home_key_players_doubtful, 0) as home_key_players_doubtful,
    COALESCE(hti.home_star_players_questionable, 0) as home_star_players_questionable,
    COALESCE(hti.home_key_players_questionable, 0) as home_key_players_questionable,
    COALESCE(NULLIF(hti.home_injury_impact_score, 0), hbf.home_injury_impact_score, 0) as home_injury_impact_score,
    COALESCE(NULLIF(hti.home_injured_star_players_count, 0), hbf.home_injured_star_players_count, 0) as home_injured_players_count,
    COALESCE(NULLIF(hti.home_has_key_injury, 0), hbf.home_has_key_injury, 0) as home_has_key_injury,
    -- Away team injury features
    COALESCE(NULLIF(ati.away_star_players_out, 0), abf.away_star_players_out, 0)::BIGINT as away_star_players_out,
    COALESCE(NULLIF(ati.away_key_players_out, 0), abf.away_key_players_out, 0)::BIGINT as away_key_players_out,
    COALESCE(ati.away_star_players_doubtful, 0) as away_star_players_doubtful,
    COALESCE(ati.away_key_players_doubtful, 0) as away_key_players_doubtful,
    COALESCE(ati.away_star_players_questionable, 0) as away_star_players_questionable,
    COALESCE(ati.away_key_players_questionable, 0) as away_key_players_questionable,
    COALESCE(NULLIF(ati.away_injury_impact_score, 0), abf.away_injury_impact_score, 0) as away_injury_impact_score,
    COALESCE(NULLIF(ati.away_injured_star_players_count, 0), abf.away_injured_star_players_count, 0) as away_injured_players_count,
    COALESCE(NULLIF(ati.away_has_key_injury, 0), abf.away_has_key_injury, 0) as away_has_key_injury,
    -- Differential features (use same COALESCE logic for consistency)
    (COALESCE(NULLIF(hti.home_injury_impact_score, 0), hbf.home_injury_impact_score, 0)
     - COALESCE(NULLIF(ati.away_injury_impact_score, 0), abf.away_injury_impact_score, 0)) as injury_impact_diff,
    (COALESCE(NULLIF(hti.home_star_players_out, 0), hbf.home_star_players_out, 0)
     - COALESCE(NULLIF(ati.away_star_players_out, 0), abf.away_star_players_out, 0)) as star_players_out_diff
FROM game_team_dates gtd
LEFT JOIN home_team_injuries hti ON gtd.game_id = hti.game_id
LEFT JOIN away_team_injuries ati ON gtd.game_id = ati.game_id
LEFT JOIN home_boxscore_fallback hbf ON gtd.game_id = hbf.game_id
LEFT JOIN away_boxscore_fallback abf ON gtd.game_id = abf.game_id
