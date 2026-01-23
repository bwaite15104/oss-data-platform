"""
Download and load Kaggle NBA historical injury dataset.

Dataset: https://www.kaggle.com/datasets/loganlauton/nba-injury-stats-1951-2023

Usage:
    # Set Kaggle API credentials (use provided API key)
    export KAGGLE_USERNAME=your_username  # Optional if using API key directly
    export KAGGLE_KEY=KGAT_b46c082560c714b21bf1fe104c3ee07e
    
    # Download and inspect the dataset
    python scripts/download_kaggle_injuries.py --inspect-only
    
    # Download and load into database
    python scripts/download_kaggle_injuries.py --load
"""

import sys
import os
from pathlib import Path
import pandas as pd
import logging
from datetime import datetime
import argparse
import json

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

try:
    from kaggle.api.kaggle_api_extended import KaggleApi
    KAGGLE_AVAILABLE = True
except ImportError:
    KAGGLE_AVAILABLE = False
    logger.error("kaggle package not installed. Install with: pip install kaggle")


def download_kaggle_dataset(dataset_name: str, output_dir: Path) -> Path:
    """Download dataset from Kaggle."""
    if not KAGGLE_AVAILABLE:
        raise ImportError("Kaggle API not available. Install with: pip install kaggle")
    
    logger.info(f"Downloading dataset: {dataset_name}")
    
    # Try environment variables first
    username = os.getenv("KAGGLE_USERNAME")
    key = os.getenv("KAGGLE_KEY")
    
    api = KaggleApi()
    
    if username and key:
        logger.info("Using environment variables for authentication")
        # Write credentials to config file
        kaggle_dir = Path.home() / ".kaggle"
        kaggle_dir.mkdir(exist_ok=True)
        kaggle_json = kaggle_dir / "kaggle.json"
        config = {"username": username, "key": key}
        with open(kaggle_json, 'w') as f:
            json.dump(config, f)
        logger.info(f"Wrote credentials to {kaggle_json}")
    
    # Authenticate
    try:
        api.authenticate()
        logger.info("Authenticated successfully")
    except Exception as e:
        raise Exception(
            f"Kaggle authentication failed: {e}\n"
            "Please set environment variables: KAGGLE_USERNAME and KAGGLE_KEY"
        )
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Download dataset files
    api.dataset_download_files(dataset_name, path=str(output_dir), unzip=True)
    
    logger.info(f"Downloaded to: {output_dir}")
    
    # Find CSV files
    csv_files = list(output_dir.glob("*.csv"))
    if csv_files:
        logger.info(f"Found {len(csv_files)} CSV files")
        for csv_file in csv_files:
            logger.info(f"  - {csv_file.name} ({csv_file.stat().st_size / (1024*1024):.1f} MB)")
        
        # Look for injury file specifically
        injury_files = [f for f in csv_files if 'injury' in f.name.lower() or 'Injury' in f.name]
        if injury_files:
            logger.info(f"Found injury file: {injury_files[0].name}")
            return injury_files[0]
        
        # Fallback to first CSV
        return csv_files[0]
    
    raise FileNotFoundError(f"No CSV files found in {output_dir}")


def inspect_dataset(csv_path: Path):
    """Inspect the dataset structure."""
    logger.info(f"Inspecting dataset: {csv_path}")
    
    # Read first few rows to understand structure
    df_sample = pd.read_csv(csv_path, nrows=100)
    
    logger.info(f"\nDataset Info:")
    logger.info(f"  Total columns: {len(df_sample.columns)}")
    logger.info(f"  Sample rows: {len(df_sample)}")
    logger.info(f"\nColumns:")
    for col in df_sample.columns:
        logger.info(f"  - {col}")
    
    logger.info(f"\nSample data (first 5 rows):")
    logger.info(df_sample.head().to_string())
    
    logger.info(f"\nData types:")
    logger.info(df_sample.dtypes.to_string())
    
    logger.info(f"\nNull counts:")
    logger.info(df_sample.isnull().sum().to_string())
    
    # Get full row count (may be slow for large files)
    logger.info(f"\nCounting total rows...")
    row_count = sum(1 for _ in open(csv_path)) - 1  # Subtract header
    logger.info(f"  Total rows: {row_count:,}")
    
    return df_sample


def map_kaggle_to_schema(df: pd.DataFrame) -> pd.DataFrame:
    """
    Map Kaggle injury dataset columns to our raw_dev.injuries schema.
    
    Kaggle dataset format:
    - Date: Date of transaction
    - Team: Team name
    - Acquired: Player returning from injury (or None)
    - Relinquished: Player going on injury list (or None)
    - Notes: Injury details
    
    Our schema columns:
    - injury_id (primary key)
    - capture_date
    - player_name
    - player_espn_id (optional)
    - team_name
    - team_abbrev
    - status (Out, Doubtful, Questionable, etc.)
    - status_raw
    - injury_details
    - captured_at
    """
    logger.info("Mapping Kaggle columns to our schema...")
    
    # Create a copy to avoid modifying original
    df_mapped = df.copy()
    
    # Drop Unnamed: 0 column if present
    if 'Unnamed: 0' in df_mapped.columns:
        df_mapped = df_mapped.drop(columns=['Unnamed: 0'])
    
    # This dataset tracks transactions (acquired/relinquished)
    # We need to create separate records for players going on IL and returning
    injury_records = []
    
    for _, row in df_mapped.iterrows():
        date = row.get('Date', None)
        team = row.get('Team', None)
        acquired = row.get('Acquired', None)
        relinquished = row.get('Relinquished', None)
        notes = row.get('Notes', '')
        
        if pd.isna(date) or pd.isna(team):
            continue
        
        # Parse date
        try:
            capture_date = pd.to_datetime(date).strftime('%Y-%m-%d')
        except:
            logger.warning(f"Could not parse date: {date}")
            continue
        
        # Process relinquished (player going on injury list)
        if pd.notna(relinquished) and str(relinquished).strip():
            player_name = str(relinquished).strip()
            
            # Infer status from notes
            notes_lower = str(notes).lower()
            if 'out' in notes_lower or 'placed on' in notes_lower:
                status = 'Out'
            elif 'doubtful' in notes_lower:
                status = 'Doubtful'
            elif 'questionable' in notes_lower:
                status = 'Questionable'
            elif 'probable' in notes_lower:
                status = 'Probable'
            else:
                status = 'Out'  # Default for relinquished
            
            injury_id = f"{capture_date}_{team}_{player_name.replace(' ', '_')}"
            
            injury_records.append({
                'injury_id': injury_id,
                'capture_date': capture_date,
                'player_name': player_name,
                'player_espn_id': None,
                'team_name': team,
                'team_abbrev': None,  # Will map later
                'status': status,
                'status_raw': status,
                'injury_details': str(notes) if pd.notna(notes) else '',
                'captured_at': datetime.now().isoformat(),
            })
        
        # Process acquired (player returning from injury)
        if pd.notna(acquired) and str(acquired).strip():
            player_name = str(acquired).strip()
            status = 'Active'  # Player is returning
            
            injury_id = f"{capture_date}_{team}_{player_name.replace(' ', '_')}_return"
            
            injury_records.append({
                'injury_id': injury_id,
                'capture_date': capture_date,
                'player_name': player_name,
                'player_espn_id': None,
                'team_name': team,
                'team_abbrev': None,  # Will map later
                'status': status,
                'status_raw': status,
                'injury_details': f"Returned from injury: {str(notes) if pd.notna(notes) else ''}",
                'captured_at': datetime.now().isoformat(),
            })
    
    # Convert to DataFrame
    if not injury_records:
        logger.warning("No injury records found in dataset")
        df_mapped = pd.DataFrame(columns=[
            'injury_id', 'capture_date', 'player_name', 'player_espn_id',
            'team_name', 'team_abbrev', 'status', 'status_raw', 'injury_details', 'captured_at'
        ])
    else:
        df_mapped = pd.DataFrame(injury_records)
    
    # Create injury_id if not present (unique identifier)
    if 'injury_id' not in df_mapped.columns:
        # Try to create from available columns
        if 'capture_date' in df_mapped.columns and 'player_name' in df_mapped.columns:
            df_mapped['injury_id'] = (
                df_mapped['capture_date'].astype(str) + '_' +
                df_mapped['player_name'].astype(str).str.replace(' ', '_')
            )
        else:
            # Fallback: use index
            df_mapped['injury_id'] = df_mapped.index.astype(str)
    
    # Ensure capture_date is in correct format
    if 'capture_date' in df_mapped.columns:
        # Try to parse date if it's a string
        if df_mapped['capture_date'].dtype == 'object':
            try:
                df_mapped['capture_date'] = pd.to_datetime(df_mapped['capture_date']).dt.strftime('%Y-%m-%d')
            except:
                logger.warning("Could not parse capture_date, keeping as-is")
        else:
            df_mapped['capture_date'] = df_mapped['capture_date'].astype(str)
    
    # Map team names to abbreviations
    if len(df_mapped) > 0 and 'team_abbrev' in df_mapped.columns:
        # Try to look up team abbreviations from database
        try:
            import psycopg2
            database = os.getenv("POSTGRES_DB", "nba_analytics")
            host = os.getenv("POSTGRES_HOST", "localhost")
            port = int(os.getenv("POSTGRES_PORT", "5432"))
            user = os.getenv("POSTGRES_USER", "postgres")
            password = os.getenv("POSTGRES_PASSWORD", "postgres")
            
            conn = psycopg2.connect(host=host, port=port, database=database, user=user, password=password)
            cursor = conn.cursor()
            cursor.execute("SELECT team_name, team_abbreviation FROM raw_dev.teams")
            team_map = {}
            for row in cursor.fetchall():
                if row[0] and row[1]:
                    team_map[row[0]] = row[1]
                    # Also map common variations
                    if ' ' in row[0]:
                        parts = row[0].split()
                        team_map[parts[-1]] = row[1]  # Last word (e.g., "Lakers")
            cursor.close()
            conn.close()
            
            # Map team names to abbreviations
            df_mapped['team_abbrev'] = df_mapped['team_name'].map(team_map)
            logger.info(f"Mapped {df_mapped['team_abbrev'].notna().sum()} / {len(df_mapped)} team abbreviations")
        except Exception as e:
            logger.warning(f"Could not map team abbreviations: {e}")
            df_mapped['team_abbrev'] = None
    
    # Select only required columns
    required_cols = [
        'injury_id', 'capture_date', 'player_name', 'player_espn_id',
        'team_name', 'team_abbrev', 'status', 'status_raw', 'injury_details', 'captured_at'
    ]
    
    # Ensure all required columns exist
    for col in required_cols:
        if col not in df_mapped.columns:
            df_mapped[col] = None
    
    logger.info(f"Mapped dataset: {len(df_mapped)} rows")
    if len(df_mapped) > 0:
        logger.info(f"Sample mapped data:")
        logger.info(df_mapped[required_cols].head().to_string())
    
    return df_mapped[required_cols]


def load_to_database(df: pd.DataFrame, batch_size: int = 1000):
    """Load mapped data into raw_dev.injuries table."""
    import psycopg2
    from psycopg2.extras import execute_values
    import uuid
    
    database = os.getenv("POSTGRES_DB", "nba_analytics")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    
    conn = psycopg2.connect(host=host, port=port, database=database, user=user, password=password)
    cursor = conn.cursor()
    
    # Check existing count
    cursor.execute("SELECT COUNT(*) FROM raw_dev.injuries")
    existing_count = cursor.fetchone()[0]
    logger.info(f"Existing injuries in database: {existing_count:,}")
    
    # Add DLT required fields
    load_id = f"kaggle_injuries_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    df['_dlt_load_id'] = load_id
    df['_dlt_id'] = [str(uuid.uuid4()) for _ in range(len(df))]
    
    # Prepare data for insert
    columns = [
        'injury_id', 'capture_date', 'player_name', 'player_espn_id',
        'team_name', 'team_abbrev', 'status', 'status_raw', 'injury_details', 'captured_at',
        '_dlt_load_id', '_dlt_id'
    ]
    
    # Convert DataFrame to list of tuples
    data_tuples = [tuple(row) for row in df[columns].values]
    
    # Insert with conflict handling - check if exists first, then update or insert
    # Since there's no primary key on injury_id, we'll use a simple insert
    # and handle duplicates by checking first
    insert_query = """
        INSERT INTO raw_dev.injuries (
            injury_id, capture_date, player_name, player_espn_id,
            team_name, team_abbrev, status, status_raw, injury_details, captured_at,
            _dlt_load_id, _dlt_id
        ) VALUES %s
    """
    
    # Check for existing records to avoid duplicates
    cursor.execute("SELECT injury_id FROM raw_dev.injuries")
    existing_ids = set(row[0] for row in cursor.fetchall())
    
    # Filter out duplicates
    data_tuples_filtered = [tup for tup in data_tuples if tup[0] not in existing_ids]
    
    if len(data_tuples_filtered) < len(data_tuples):
        logger.info(f"Filtered out {len(data_tuples) - len(data_tuples_filtered)} duplicate records")
    
    data_tuples = data_tuples_filtered
    
    inserted = 0
    for i in range(0, len(data_tuples), batch_size):
        batch = data_tuples[i:i+batch_size]
        execute_values(cursor, insert_query, batch)
        inserted += len(batch)
        logger.info(f"  Inserted batch: {inserted:,} / {len(data_tuples):,}")
    
    conn.commit()
    
    # Check new count
    cursor.execute("SELECT COUNT(*) FROM raw_dev.injuries")
    new_count = cursor.fetchone()[0]
    logger.info(f"New total injuries in database: {new_count:,}")
    logger.info(f"Added/updated: {new_count - existing_count:,} injuries")
    
    cursor.close()
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download and load Kaggle NBA injury dataset")
    parser.add_argument("--dataset", type=str, 
                       default="loganlauton/nba-injury-stats-1951-2023",
                       help="Kaggle dataset name (format: username/dataset-name)")
    parser.add_argument("--download-dir", type=str, default="data/kaggle",
                       help="Directory to download dataset")
    parser.add_argument("--inspect-only", action="store_true",
                       help="Only inspect dataset, don't load")
    parser.add_argument("--load", action="store_true",
                       help="Load dataset into database")
    
    args = parser.parse_args()
    
    if not KAGGLE_AVAILABLE:
        logger.error("Kaggle API not available. Install with: pip install kaggle")
        sys.exit(1)
    
    download_dir = Path(args.download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # Download dataset
        csv_path = download_kaggle_dataset(args.dataset, download_dir)
        
        # Inspect dataset
        df_sample = inspect_dataset(csv_path)
        
        if args.inspect_only:
            logger.info("\nInspection complete. Use --load to load into database.")
            sys.exit(0)
        
        if args.load:
            logger.info("\nLoading full dataset...")
            # Read full dataset in chunks
            chunk_size = 10000
            total_rows = 0
            
            for chunk_num, chunk_df in enumerate(pd.read_csv(csv_path, chunksize=chunk_size)):
                logger.info(f"Processing chunk {chunk_num + 1} ({len(chunk_df)} rows)...")
                df_mapped = map_kaggle_to_schema(chunk_df)
                load_to_database(df_mapped)
                total_rows += len(chunk_df)
            
            logger.info(f"\n✅ Complete! Loaded {total_rows:,} injury records")
        else:
            logger.info("\nUse --load to load dataset into database.")
            
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        sys.exit(1)
