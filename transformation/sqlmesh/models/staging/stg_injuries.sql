MODEL (
    name staging.stg_injuries,
    kind VIEW,
    description 'Cleaned injuries data'
);

SELECT 
    injury_id,
    capture_date::DATE as capture_date,
    player_name,
    player_espn_id,
    team_name,
    team_abbrev,
    status,
    status_raw,
    injury_details,
    captured_at::TIMESTAMP as captured_at
FROM raw_dev.injuries
