"""Populate team_boxscores from boxscores historical data."""

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
    
    logger.info("Creating team boxscores from player boxscores historical data...")
    
    # Insert team boxscores by aggregating player boxscores
    insert_query = """
        INSERT INTO raw_dev.team_boxscores (
            game_id, game_date, team_id, is_home,
            points, assists, rebounds_total, steals, blocks, turnovers,
            field_goals_made, field_goals_attempted, field_goal_pct,
            three_pointers_made, three_pointers_attempted, three_point_pct,
            free_throws_made, free_throws_attempted, free_throw_pct,
            created_at, _dlt_load_id, _dlt_id
        )
        SELECT 
            b.game_id::text as game_id,
            b.game_date::date as game_date,
            b.team_id,
            b.is_home,
            SUM(b.points)::int as points,
            SUM(b.assists)::int as assists,
            SUM(b.rebounds_total)::int as rebounds_total,
            SUM(b.steals)::int as steals,
            SUM(b.blocks)::int as blocks,
            SUM(b.turnovers)::int as turnovers,
            SUM(b.field_goals_made)::int as field_goals_made,
            SUM(b.field_goals_attempted)::int as field_goals_attempted,
            CASE 
                WHEN SUM(b.field_goals_attempted) > 0 
                THEN SUM(b.field_goals_made)::numeric / SUM(b.field_goals_attempted)::numeric
                ELSE 0 
            END as field_goal_pct,
            SUM(b.three_pointers_made)::int as three_pointers_made,
            SUM(b.three_pointers_attempted)::int as three_pointers_attempted,
            CASE 
                WHEN SUM(b.three_pointers_attempted) > 0 
                THEN SUM(b.three_pointers_made)::numeric / SUM(b.three_pointers_attempted)::numeric
                ELSE 0 
            END as three_point_pct,
            SUM(b.free_throws_made)::int as free_throws_made,
            SUM(b.free_throws_attempted)::int as free_throws_attempted,
            CASE 
                WHEN SUM(b.free_throws_attempted) > 0 
                THEN SUM(b.free_throws_made)::numeric / SUM(b.free_throws_attempted)::numeric
                ELSE 0 
            END as free_throw_pct,
            CURRENT_TIMESTAMP as created_at,
            gen_random_uuid()::text as _dlt_load_id,
            gen_random_uuid()::text as _dlt_id
        FROM raw_dev.boxscores b
        WHERE b.game_date IS NOT NULL
          AND b.team_id IS NOT NULL
          AND (b.game_id, b.team_id) NOT IN (
              SELECT game_id, team_id FROM raw_dev.team_boxscores 
              WHERE game_id IS NOT NULL AND team_id IS NOT NULL
          )
        GROUP BY b.game_id, b.game_date, b.team_id, b.is_home
        HAVING SUM(b.points) IS NOT NULL
    """
    
    cursor.execute(insert_query)
    inserted = cursor.rowcount
    conn.commit()
    
    logger.info(f"Inserted {inserted} historical team boxscores")
    
    # Verify
    cursor.execute("SELECT COUNT(*) FROM raw_dev.team_boxscores")
    total = cursor.fetchone()[0]
    logger.info(f"Total team boxscores: {total}")
    
    cursor.close()
    conn.close()

if __name__ == "__main__":
    main()
