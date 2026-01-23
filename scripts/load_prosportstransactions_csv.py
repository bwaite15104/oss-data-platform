"""
Load prosportstransactions CSV file into database.

Use this script to load a previously saved CSV file into the database.
"""

import sys
from pathlib import Path
import os
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
import uuid
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def load_csv_to_database(csv_path: Path):
    """Load CSV file into database."""
    logger.info(f"Loading CSV: {csv_path}")
    
    # Read CSV
    df = pd.read_csv(csv_path)
    logger.info(f"Read {len(df)} records from CSV")
    
    # Get database config
    db_config = {
        'host': os.getenv('POSTGRES_HOST', 'localhost'),
        'port': int(os.getenv('POSTGRES_PORT', '5432')),
        'database': os.getenv('POSTGRES_DB', 'nba_analytics'),
        'user': os.getenv('POSTGRES_USER', 'postgres'),
        'password': os.getenv('POSTGRES_PASSWORD', 'postgres'),
    }
    
    logger.info(f"Connecting to: {db_config['host']}:{db_config['port']}/{db_config['database']}")
    
    # Connect to database
    conn = psycopg2.connect(
        host=db_config['host'],
        port=db_config['port'],
        database=db_config['database'],
        user=db_config['user'],
        password=db_config['password']
    )
    
    try:
        cursor = conn.cursor()
        
        # Check for existing injury_ids
        existing_ids_query = "SELECT injury_id FROM raw_dev.injuries WHERE injury_id = ANY(%s)"
        existing_ids = set()
        
        batch_size = 1000
        for i in range(0, len(df), batch_size):
            batch = df.iloc[i:i+batch_size]
            injury_ids = batch['injury_id'].tolist()
            cursor.execute(existing_ids_query, (injury_ids,))
            existing_ids.update(row[0] for row in cursor.fetchall())
        
        # Filter out existing records
        df_new = df[~df['injury_id'].isin(existing_ids)]
        
        if len(df_new) == 0:
            logger.info("All records already exist in database")
            return 0
        
        logger.info(f"Inserting {len(df_new)} new records (skipping {len(df) - len(df_new)} duplicates)")
        
        # Add DLT-required columns if not present
        if '_dlt_load_id' not in df_new.columns:
            df_new['_dlt_load_id'] = str(uuid.uuid4())
        if '_dlt_id' not in df_new.columns:
            df_new['_dlt_id'] = [str(uuid.uuid4()) for _ in range(len(df_new))]
        
        # Prepare data for insertion
        columns = [
            'injury_id', 'capture_date', 'player_name', 'player_espn_id',
            'team_name', 'team_abbrev', 'status', 'status_raw',
            'injury_details', 'captured_at', '_dlt_load_id', '_dlt_id'
        ]
        
        values = [
            tuple(row[col] if pd.notna(row[col]) else None for col in columns)
            for _, row in df_new.iterrows()
        ]
        
        # Insert records (no ON CONFLICT since injury_id is not a unique constraint)
        insert_query = """
            INSERT INTO raw_dev.injuries (
                injury_id, capture_date, player_name, player_espn_id,
                team_name, team_abbrev, status, status_raw,
                injury_details, captured_at, _dlt_load_id, _dlt_id
            ) VALUES %s
        """
        
        # Insert in batches
        batch_size = 1000
        inserted = 0
        for i in range(0, len(values), batch_size):
            batch = values[i:i+batch_size]
            execute_values(cursor, insert_query, batch)
            inserted += len(batch)
            logger.info(f"  Inserted batch: {inserted:,} / {len(values):,}")
        
        conn.commit()
        
        logger.info(f"✅ Successfully loaded {len(df_new)} records")
        return len(df_new)
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Error loading to database: {e}")
        raise
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Load prosportstransactions CSV into database')
    parser.add_argument('csv_path', type=Path, help='Path to CSV file')
    
    args = parser.parse_args()
    
    if not args.csv_path.exists():
        logger.error(f"CSV file not found: {args.csv_path}")
        sys.exit(1)
    
    logger.info("=" * 60)
    logger.info("Loading Prosportstransactions CSV to Database")
    logger.info("=" * 60)
    
    try:
        records_loaded = load_csv_to_database(args.csv_path)
        logger.info("\n" + "=" * 60)
        logger.info(f"✅ Complete! Loaded {records_loaded} new records.")
        logger.info("=" * 60)
    except Exception as e:
        logger.error(f"\n❌ Failed: {e}")
        sys.exit(1)
