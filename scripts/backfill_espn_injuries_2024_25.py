"""
Backfill ESPN injury data for 2024-25 season.

This script uses the DLT pipeline to backfill injury data from 2024-10-01 to present.
Since ESPN only shows current injuries, we'll run the scraper for each date
and it will capture whatever injuries are currently reported (which may include
historical context in some cases).

For true historical backfill, we'd need Wayback Machine integration.
"""

import sys
from pathlib import Path
import os
from datetime import datetime, timedelta
import logging

# Add project root to path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import dlt
from ingestion.dlt.pipelines.nba_injuries import nba_injuries_source

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def backfill_injuries_date_range(start_date: str, end_date: str, database_url: str):
    """
    Backfill injuries for a date range using DLT pipeline.
    
    Note: ESPN only shows current injuries, so this will capture
    whatever injuries are currently reported. For true historical
    backfill, we'd need Wayback Machine or alternative sources.
    
    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        database_url: Database connection string
    """
    start = datetime.strptime(start_date, '%Y-%m-%d')
    end = datetime.strptime(end_date, '%Y-%m-%d')
    
    current = start
    total_inserted = 0
    
    logger.info(f"Starting backfill from {start_date} to {end_date}")
    logger.warning("Note: ESPN only shows current injuries. This will capture current injury reports, not historical snapshots.")
    
    while current <= end:
        date_str = current.strftime('%Y-%m-%d')
        logger.info(f"Processing {date_str}...")
        
        try:
            # Run DLT pipeline - it will capture current injuries
            # The pipeline uses merge mode, so it will upsert based on injury_id
            pipeline = dlt.pipeline(
                pipeline_name="nba_injuries_backfill",
                destination="postgres",
                dataset_name="raw_dev",
            )
            
            # Load current injuries (ESPN shows current, not historical)
            info = pipeline.run(nba_injuries_source())
            
            if info:
                logger.info(f"  Loaded injuries for {date_str}: {info}")
                total_inserted += info.load_packages[0].jobs[0].file_info.file_size if info.load_packages else 0
            else:
                logger.warning(f"  No data loaded for {date_str}")
        
        except Exception as e:
            logger.error(f"Error processing {date_str}: {e}")
            continue
        
        current += timedelta(days=1)
    
    logger.info(f"Backfill complete! Processed {total_inserted} injury records")


def backfill_2024_25_season():
    """Backfill 2024-25 NBA season (Oct 2024 - present)."""
    # NBA season typically starts in October
    start_date = "2024-10-01"
    end_date = datetime.now().strftime("%Y-%m-%d")
    
    # Get database URL from environment
    database = os.getenv("POSTGRES_DB", "nba_analytics")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    
    database_url = f"postgresql://{user}:{password}@{host}:{port}/{database}"
    
    logger.info("=" * 60)
    logger.info("ESPN Injury Data Backfill - 2024-25 Season")
    logger.info("=" * 60)
    logger.warning("IMPORTANT: ESPN only shows CURRENT injuries, not historical snapshots.")
    logger.warning("This script will capture current injury reports, which may include")
    logger.warning("some historical context, but won't be true historical snapshots.")
    logger.warning("")
    logger.warning("For true historical backfill, we'd need:")
    logger.warning("  1. Wayback Machine integration")
    logger.warning("  2. Alternative data sources")
    logger.warning("  3. Manual data collection")
    logger.info("=" * 60)
    
    backfill_injuries_date_range(start_date, end_date, database_url)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Backfill ESPN injury data for 2024-25 season')
    parser.add_argument('--start-date', default='2024-10-01', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end-date', help='End date (YYYY-MM-DD), defaults to today')
    parser.add_argument('--single-date', help='Single date to backfill (YYYY-MM-DD)')
    
    args = parser.parse_args()
    
    if args.single_date:
        logger.info(f"Backfilling single date: {args.single_date}")
        end_date = args.single_date
        start_date = args.single_date
    else:
        start_date = args.start_date
        end_date = args.end_date or datetime.now().strftime("%Y-%m-%d")
    
    # Get database URL from environment
    database = os.getenv("POSTGRES_DB", "nba_analytics")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    
    database_url = f"postgresql://{user}:{password}@{host}:{port}/{database}"
    
    backfill_injuries_date_range(start_date, end_date, database_url)
