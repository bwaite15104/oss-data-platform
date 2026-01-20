"""Populate games table from boxscores historical data."""

import psycopg2
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    database = os.getenv("POSTGRES_DB", "nba_analytics")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    
    conn = psycopg2.connect(
        host=host, port=port, database=database, user=user, password=password
    )
    cursor = conn.cursor()
    
    logger.info("Creating games from boxscores historical data...")
    
    # Insert games from boxscores (aggregating team stats)
    # First check schema - determine home/away from is_home column
    insert_query = """
        INSERT INTO raw_dev.games (
            game_id, game_date, season, season_type, home_team_id, away_team_id,
            home_score, away_score, winner_team_id, created_at, _dlt_load_id, _dlt_id
        )
        SELECT DISTINCT ON (b.game_id)
            b.game_id::text as game_id,
            b.game_date::date as game_date,
            -- Calculate season (e.g., 2024-10-01 to 2025-09-30 = 2024-25)
            CASE 
                WHEN EXTRACT(MONTH FROM b.game_date::date) >= 10
                THEN (EXTRACT(YEAR FROM b.game_date::date)::text || '-' || 
                      SUBSTRING((EXTRACT(YEAR FROM b.game_date::date) + 1)::text, 3, 2))
                ELSE ((EXTRACT(YEAR FROM b.game_date::date) - 1)::text || '-' || 
                      SUBSTRING(EXTRACT(YEAR FROM b.game_date::date)::text, 3, 2))
            END as season,
            'Regular Season' as season_type,  -- Default, can be updated later
            -- Determine home/away from boxscores
            MAX(CASE WHEN b.is_home = true THEN b.team_id END) as home_team_id,
            MAX(CASE WHEN b.is_home = false THEN b.team_id END) as away_team_id,
            -- Calculate scores from boxscores
            SUM(CASE WHEN b.is_home = true THEN b.points ELSE 0 END)::int as home_score,
            SUM(CASE WHEN b.is_home = false THEN b.points ELSE 0 END)::int as away_score,
            -- Determine winner
            CASE 
                WHEN SUM(CASE WHEN b.is_home = true THEN b.points ELSE 0 END) >
                     SUM(CASE WHEN b.is_home = false THEN b.points ELSE 0 END)
                THEN MAX(CASE WHEN b.is_home = true THEN b.team_id END)
                ELSE MAX(CASE WHEN b.is_home = false THEN b.team_id END)
            END as winner_team_id,
            CURRENT_TIMESTAMP as created_at,
            gen_random_uuid()::text as _dlt_load_id,
            gen_random_uuid()::text as _dlt_id
        FROM (
            SELECT DISTINCT 
                bs.game_id,
                bs.game_date,
                bs.team_id,
                bs.is_home,
                SUM(bs.points) OVER (PARTITION BY bs.game_id, bs.team_id) as points
            FROM raw_dev.boxscores bs
            WHERE bs.game_date IS NOT NULL
        ) b
        WHERE b.game_id NOT IN (SELECT game_id FROM raw_dev.games WHERE game_id IS NOT NULL)
        GROUP BY b.game_id, b.game_date
        HAVING MAX(CASE WHEN b.is_home = true THEN b.team_id END) IS NOT NULL
           AND MAX(CASE WHEN b.is_home = false THEN b.team_id END) IS NOT NULL
    """
    
    cursor.execute(insert_query)
    inserted = cursor.rowcount
    conn.commit()
    
    logger.info(f"Inserted {inserted} historical games")
    
    # Verify
    cursor.execute("SELECT COUNT(*) FROM raw_dev.games WHERE home_score IS NOT NULL")
    total = cursor.fetchone()[0]
    logger.info(f"Total games with scores: {total}")
    
    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()
