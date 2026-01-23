"""
Backfill ESPN injury data using Wayback Machine for historical dates.

This script backfills injury data for 2024-25 season by accessing archived
ESPN injury pages through Wayback Machine.
"""

import sys
from pathlib import Path
import os
from datetime import datetime, timedelta
import logging
import time
import uuid

# Add project root to path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import psycopg2
from psycopg2.extras import execute_values
from ingestion.dlt.pipelines.nba_injuries_historical import nba_injuries_resource_for_date

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def save_injuries_to_db(injuries: list, conn):
    """Save injury records to database."""
    cursor = conn.cursor()
    
    inserted = 0
    load_id = f"wayback_backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    for injury in injuries:
        try:
            # Add DLT required fields
            injury['_dlt_load_id'] = load_id
            injury['_dlt_id'] = str(uuid.uuid4())
            
            cursor.execute(
                """
                INSERT INTO raw_dev.injuries (
                    injury_id, capture_date, player_name, player_espn_id,
                    team_name, team_abbrev, status, status_raw, injury_details, captured_at,
                    _dlt_load_id, _dlt_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (injury_id) DO NOTHING
                """,
                (
                    injury['injury_id'],
                    injury['capture_date'],
                    injury['player_name'],
                    injury.get('player_espn_id'),
                    injury['team_name'],
                    injury['team_abbrev'],
                    injury['status'],
                    injury['status_raw'],
                    injury['injury_details'],
                    injury['captured_at'],
                    injury['_dlt_load_id'],
                    injury['_dlt_id'],
                )
            )
            inserted += 1
        except Exception as e:
            logger.warning(f"Error inserting injury {injury.get('injury_id', 'unknown')}: {e}")
            continue
    
    conn.commit()
    return inserted


def backfill_date_range(start_date: str, end_date: str):
    """
    Backfill injuries for a date range using Wayback Machine.
    
    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
    """
    start = datetime.strptime(start_date, '%Y-%m-%d')
    end = datetime.strptime(end_date, '%Y-%m-%d')
    
    current = start
    total_processed = 0
    total_inserted = 0
    failed_dates = []
    
    # Get database connection
    database = os.getenv("POSTGRES_DB", "nba_analytics")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    
    conn = psycopg2.connect(host=host, port=port, database=database, user=user, password=password)
    
    logger.info("=" * 60)
    logger.info("ESPN Injury Data Backfill via Wayback Machine")
    logger.info(f"Date Range: {start_date} to {end_date}")
    logger.info("=" * 60)
    
    try:
        while current <= end:
            date_str = current.strftime('%Y-%m-%d')
            logger.info(f"\nProcessing {date_str} ({total_processed + 1} / {(end - start).days + 1})...")
            
            try:
                # Scrape injuries for this date using Wayback Machine
                injuries = list(nba_injuries_resource_for_date(date_str, use_wayback=True))
                
                if injuries:
                    # Save to database
                    inserted = save_injuries_to_db(injuries, conn)
                    logger.info(f"  ✅ Found {len(injuries)} injuries, inserted {inserted} new records")
                    total_inserted += inserted
                else:
                    logger.warning(f"  ⚠️  No injuries found for {date_str} (may not be available in Wayback Machine)")
                
                total_processed += 1
                
                # Rate limiting - be respectful to Wayback Machine
                time.sleep(2)  # 2 second delay between requests
                
            except Exception as e:
                logger.error(f"  ❌ Error processing {date_str}: {e}")
                failed_dates.append(date_str)
                continue
            
            current += timedelta(days=1)
    
    finally:
        conn.close()
    
    logger.info("\n" + "=" * 60)
    logger.info("Backfill Summary")
    logger.info("=" * 60)
    logger.info(f"Total dates processed: {total_processed}")
    logger.info(f"Total records inserted: {total_inserted}")
    logger.info(f"Failed dates: {len(failed_dates)}")
    if failed_dates:
        logger.info(f"Failed dates: {', '.join(failed_dates[:10])}{'...' if len(failed_dates) > 10 else ''}")
    logger.info("=" * 60)


def backfill_2024_25_season():
    """Backfill 2024-25 NBA season (Oct 2024 - Jan 2025)."""
    # NBA season typically starts in October
    start_date = "2024-10-01"
    end_date = "2025-01-22"  # Up to the date we need
    
    logger.info("Starting backfill for 2024-25 NBA season...")
    logger.info("This will use Wayback Machine to access archived ESPN injury pages.")
    logger.info("Note: Some dates may not be available in Wayback Machine.\n")
    
    backfill_date_range(start_date, end_date)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Backfill ESPN injury data using Wayback Machine')
    parser.add_argument('--start-date', default='2024-10-01', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end-date', default='2025-01-22', help='End date (YYYY-MM-DD)')
    parser.add_argument('--single-date', help='Single date to backfill (YYYY-MM-DD)')
    
    args = parser.parse_args()
    
    if args.single_date:
        logger.info(f"Backfilling single date: {args.single_date}")
        backfill_date_range(args.single_date, args.single_date)
    else:
        backfill_date_range(args.start_date, args.end_date)
