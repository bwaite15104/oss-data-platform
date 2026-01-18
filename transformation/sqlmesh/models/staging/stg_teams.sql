MODEL (
    name staging.stg_teams,
    kind VIEW,
    description 'Cleaned NBA teams data'
);

SELECT 
    team_id::INTEGER as team_id,
    team_name,
    team_abbreviation,
    city as team_city,
    conference,
    division
FROM raw_dev.teams
