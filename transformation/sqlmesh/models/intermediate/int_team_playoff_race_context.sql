MODEL (
    name intermediate.int_team_playoff_ctx,
    kind INCREMENTAL_BY_TIME_RANGE (
        time_column game_date
    ),
    start '1946-11-01',  -- Updated for full history backfill,
    grains [
        team_id,
        game_date
    ],
    cron '@daily',
    description 'Playoff race context features - captures game importance and urgency based on playoff standings'
);

-- Calculate playoff race context for each team at each game date
-- This captures when teams are fighting for playoff spots vs playing meaningless games
WITH completed_games AS (
    SELECT 
        g.game_id,
        g.game_date::date AS game_date,
        g.home_team_id,
        g.away_team_id,
        g.winner_team_id
    FROM staging.stg_games g
    WHERE g.is_completed
      AND g.game_date < @end_ds  -- Only include games up to end of current chunk
),

-- Get game dates in current chunk only (for output)
game_dates AS (
    SELECT DISTINCT game_date::date AS game_date
    FROM staging.stg_games
    WHERE is_completed
      AND game_date >= @start_ds
      AND game_date < @end_ds
    ORDER BY game_date
),

-- Calculate cumulative standings for each team up to each game date
-- Include all historical games before @end_ds for context, but only output dates in current chunk
team_standings_at_date AS (
    SELECT 
        gd.game_date,
        tb.team_id,
        COUNT(*) FILTER (WHERE cg.winner_team_id = tb.team_id AND cg.game_date <= gd.game_date) as wins,
        COUNT(*) FILTER (WHERE cg.winner_team_id != tb.team_id AND cg.winner_team_id IS NOT NULL AND cg.game_date <= gd.game_date) as losses,
        COUNT(*) FILTER (WHERE cg.game_date <= gd.game_date) as games_played,
        ROUND(
            COUNT(*) FILTER (WHERE cg.winner_team_id = tb.team_id AND cg.game_date <= gd.game_date)::DECIMAL / 
            NULLIF(COUNT(*) FILTER (WHERE cg.game_date <= gd.game_date), 0), 
            3
        ) as win_pct
    FROM game_dates gd
    CROSS JOIN (SELECT DISTINCT team_id FROM staging.stg_team_boxscores) tb
    LEFT JOIN completed_games cg ON (
        (cg.home_team_id = tb.team_id OR cg.away_team_id = tb.team_id)
        AND cg.game_date <= gd.game_date
        AND cg.game_date < @end_ds  -- Only use games up to end of current chunk
    )
    GROUP BY gd.game_date, tb.team_id
),

-- Calculate conference standings (simplified: use overall standings as proxy)
-- In real NBA, we'd need conference data, but for now we'll use overall standings
conference_standings AS (
    SELECT 
        game_date,
        team_id,
        wins,
        losses,
        win_pct,
        -- Rank teams by win_pct (higher is better), then by wins for tiebreaker
        ROW_NUMBER() OVER (PARTITION BY game_date ORDER BY win_pct DESC, wins DESC) as overall_rank,
        -- Assume top 16 teams make playoffs (8 per conference, simplified)
        -- In reality, NBA has 8 per conference, but we'll use top 16 overall as approximation
        CASE 
            WHEN ROW_NUMBER() OVER (PARTITION BY game_date ORDER BY win_pct DESC, wins DESC) <= 16 
            THEN 1 
            ELSE 0 
        END as is_in_playoff_position
    FROM team_standings_at_date
    WHERE games_played > 0  -- Only include teams that have played games
),

-- Calculate games back from playoff cutoff (16th place)
playoff_cutoff AS (
    SELECT 
        game_date,
        -- Get win_pct of 16th place team (playoff cutoff)
        MAX(CASE WHEN overall_rank = 16 THEN win_pct ELSE NULL END) as cutoff_win_pct,
        -- Get wins of 16th place team
        MAX(CASE WHEN overall_rank = 16 THEN wins ELSE NULL END) as cutoff_wins
    FROM conference_standings
    GROUP BY game_date
),

-- Calculate playoff race context for each team
playoff_context AS (
    SELECT 
        cs.game_date,
        cs.team_id,
        cs.wins,
        cs.losses,
        cs.win_pct,
        cs.overall_rank,
        cs.is_in_playoff_position,
        COALESCE(pc.cutoff_win_pct, 0.0) as cutoff_win_pct,
        COALESCE(pc.cutoff_wins, 0) as cutoff_wins,
        -- Games back from playoff cutoff (simplified: difference in wins)
        GREATEST(0, COALESCE(pc.cutoff_wins, 0) - cs.wins) as games_back_from_cutoff,
        -- Win percentage difference from cutoff
        cs.win_pct - COALESCE(pc.cutoff_win_pct, 0.0) as win_pct_diff_from_cutoff,
        -- Is close to playoff cutoff (within 3 games)
        CASE 
            WHEN GREATEST(0, COALESCE(pc.cutoff_wins, 0) - cs.wins) <= 3 
            THEN 1 
            ELSE 0 
        END as is_close_to_cutoff,
        -- Is fighting for playoff spot (ranked 12-20, close to cutoff)
        CASE 
            WHEN cs.overall_rank BETWEEN 12 AND 20 
            THEN 1 
            ELSE 0 
        END as is_fighting_for_playoff_spot
    FROM conference_standings cs
    LEFT JOIN playoff_cutoff pc ON cs.game_date = pc.game_date
)

SELECT 
    game_date,
    team_id,
    overall_rank,
    is_in_playoff_position,
    games_back_from_cutoff,
    win_pct_diff_from_cutoff,
    is_close_to_cutoff,
    is_fighting_for_playoff_spot
FROM playoff_context
WHERE game_date >= @start_ds
  AND game_date < @end_ds
ORDER BY game_date, team_id
