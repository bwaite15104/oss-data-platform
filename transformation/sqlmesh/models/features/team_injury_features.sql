MODEL (
    name features_dev.team_injury_features,
    kind FULL,
    description 'Team injury status aggregated by capture date',
    grain (capture_date, team_abbrev)
);

SELECT 
    capture_date,
    team_abbrev,
    COUNT(*) as injured_players,
    SUM(CASE WHEN status = 'Out' THEN 1 ELSE 0 END) as players_out,
    SUM(CASE WHEN status = 'Doubtful' THEN 1 ELSE 0 END) as players_doubtful,
    SUM(CASE WHEN status = 'Questionable' THEN 1 ELSE 0 END) as players_questionable,
    SUM(CASE WHEN status = 'Probable' THEN 1 ELSE 0 END) as players_probable,
    STRING_AGG(player_name || ' (' || status || ')', ', ') as injury_list,
    CURRENT_TIMESTAMP as updated_at
FROM staging.stg_injuries
WHERE status != 'N/A'
GROUP BY capture_date, team_abbrev
