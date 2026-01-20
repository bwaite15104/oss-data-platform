"""
Download and load Kaggle NBA historical dataset.

Dataset: https://www.kaggle.com/datasets/eoinamoore/historical-nba-data-and-player-box-scores

Usage:
    # First time: Set up Kaggle API credentials
    # 1. Go to https://www.kaggle.com/settings -> API -> Create New Token
    # 2. Save kaggle.json to ~/.kaggle/kaggle.json (or C:\\Users\\<username>\\.kaggle\\kaggle.json on Windows)
    
    # Download and load the dataset
    python scripts/download_kaggle_nba.py --table boxscores --season 2023-24
"""

import sys
import os
from pathlib import Path
import pandas as pd
import logging
from datetime import datetime
import argparse

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
    
    # Try environment variables first (more reliable)
    username = os.getenv("KAGGLE_USERNAME")
    key = os.getenv("KAGGLE_KEY")
    
    api = KaggleApi()
    
    if username and key:
        logger.info("Using environment variables for authentication")
        # Write credentials to config file temporarily
        import json
        kaggle_dir = Path.home() / ".kaggle"
        kaggle_dir.mkdir(exist_ok=True)
        kaggle_json = kaggle_dir / "kaggle.json"
        config = {"username": username, "key": key}
        with open(kaggle_json, 'w') as f:
            json.dump(config, f)
        logger.info(f"Wrote credentials to {kaggle_json}")
    
    # Authenticate (will use kaggle.json file)
    try:
        api.authenticate()
        logger.info("Authenticated successfully")
    except Exception as e:
        raise Exception(
            f"Kaggle authentication failed: {e}\n"
            "Please either:\n"
            "1. Set environment variables: KAGGLE_USERNAME and KAGGLE_KEY, OR\n"
            "2. Set up kaggle.json file in ~/.kaggle/kaggle.json"
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
        
        # Prefer PlayerStatistics.csv for boxscores
        player_stats = [f for f in csv_files if f.name == "PlayerStatistics.csv"]
        if player_stats:
            return player_stats[0]
        
        return csv_files[0]  # Return first CSV if PlayerStatistics not found
    
    raise FileNotFoundError(f"No CSV files found in {output_dir}")


def inspect_dataset(csv_path: Path):
    """Inspect the dataset structure."""
    logger.info(f"Inspecting dataset: {csv_path}")
    
    # Read first few rows to understand structure
    df_sample = pd.read_csv(csv_path, nrows=100, low_memory=False)
    
    logger.info(f"Dataset shape (sample): {df_sample.shape}")
    logger.info(f"Columns ({len(df_sample.columns)}):")
    for col in df_sample.columns:
        dtype = df_sample[col].dtype
        null_count = df_sample[col].isna().sum()
        logger.info(f"  - {col}: {dtype} ({null_count} nulls)")
    
    logger.info("\nFirst few rows:")
    print(df_sample.head())
    
    return df_sample


def map_kaggle_to_schema(df: pd.DataFrame) -> pd.DataFrame:
    """
    Map Kaggle PlayerStatistics.csv columns to our raw_dev.boxscores schema.
    
    Kaggle columns:
    - personId, gameId, gameDateTimeEst, firstName, lastName
    - playerteamCity, playerteamName, opponentteamCity, opponentteamName
    - home, numMinutes, points, assists, blocks, steals, etc.
    """
    logger.info("Mapping Kaggle columns to our schema...")
    logger.info(f"Input columns: {list(df.columns)}")
    
    # Create a copy to avoid modifying original
    df_mapped = df.copy()
    
    # Map player identifiers
    if 'personId' in df_mapped.columns:
        df_mapped['player_id'] = df_mapped['personId'].astype(int)
    elif 'player_id' not in df_mapped.columns:
        logger.warning("No player_id column found")
    
    # Map game identifiers - convert gameId to our format
    if 'gameId' in df_mapped.columns:
        # Kaggle gameId is like 22500607 (integer), convert to string
        # Ensure it's a string to match our schema
        df_mapped['game_id'] = df_mapped['gameId'].astype(str)
    elif 'game_id' not in df_mapped.columns:
        logger.warning("No game_id column found")
    
    # Map game date - handle ISO8601 format with timezone
    if 'gameDateTimeEst' in df_mapped.columns:
        # Parse with utc=True to handle mixed timezones, then convert to date string
        df_mapped['game_date'] = pd.to_datetime(df_mapped['gameDateTimeEst'], utc=True, errors='coerce')
        # Extract just the date part (YYYY-MM-DD)
        df_mapped['game_date'] = df_mapped['game_date'].dt.date.astype(str)
        # Handle any NaT values
        df_mapped['game_date'] = df_mapped['game_date'].replace('NaT', None)
    elif 'game_date' not in df_mapped.columns:
        logger.warning("No game_date column found")
    
    # Map player name
    if 'firstName' in df_mapped.columns and 'lastName' in df_mapped.columns:
        df_mapped['player_name'] = (df_mapped['firstName'] + ' ' + df_mapped['lastName']).str.strip()
    elif 'player_name' not in df_mapped.columns:
        logger.warning("No player_name column found")
    
    # Map team_id - need to look up from team name
    # For now, we'll need to query our teams table or use a mapping
    # TODO: Implement team name to team_id mapping
    df_mapped['team_id'] = None  # Will need to be filled from teams table
    
    # Map is_home
    if 'home' in df_mapped.columns:
        df_mapped['is_home'] = df_mapped['home'].astype(bool)
    elif 'is_home' not in df_mapped.columns:
        df_mapped['is_home'] = None
    
    # Map is_starter - not in Kaggle dataset, set to None
    df_mapped['is_starter'] = None
    
    # Map minutes
    if 'numMinutes' in df_mapped.columns:
        df_mapped['minutes'] = df_mapped['numMinutes'].fillna(0).astype(float)
        # Also create minutes_calculated as string format
        df_mapped['minutes_calculated'] = df_mapped['minutes'].apply(
            lambda x: f"PT{int(x)}M{int((x % 1) * 60)}S" if pd.notna(x) and x > 0 else None
        )
    else:
        df_mapped['minutes'] = None
        df_mapped['minutes_calculated'] = None
    
    # Map basic stats
    stat_mapping = {
        'points': 'points',
        'assists': 'assists',
        'reboundsTotal': 'rebounds_total',
        'reboundsOffensive': 'rebounds_offensive',
        'reboundsDefensive': 'rebounds_defensive',
        'steals': 'steals',
        'blocks': 'blocks',
        'turnovers': 'turnovers',
        'foulsPersonal': 'fouls_personal',
        'fieldGoalsMade': 'field_goals_made',
        'fieldGoalsAttempted': 'field_goals_attempted',
        'fieldGoalsPercentage': 'field_goal_pct',
        'threePointersMade': 'three_pointers_made',
        'threePointersAttempted': 'three_pointers_attempted',
        'threePointersPercentage': 'three_point_pct',
        'freeThrowsMade': 'free_throws_made',
        'freeThrowsAttempted': 'free_throws_attempted',
        'freeThrowsPercentage': 'free_throw_pct',
        'plusMinusPoints': 'plus_minus',
    }
    
    for kaggle_col, our_col in stat_mapping.items():
        if kaggle_col in df_mapped.columns:
            df_mapped[our_col] = df_mapped[kaggle_col]
        elif our_col not in df_mapped.columns:
            df_mapped[our_col] = None
    
    # Set missing columns
    df_mapped['fouls_technical'] = None
    df_mapped['points_fast_break'] = None
    df_mapped['points_in_paint'] = None
    df_mapped['points_second_chance'] = None
    
    # Look up team_id from team names
    try:
        import psycopg2
        database = os.getenv("POSTGRES_DB", "nba_analytics")
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = int(os.getenv("POSTGRES_PORT", "5432"))
        user = os.getenv("POSTGRES_USER", "postgres")
        password = os.getenv("POSTGRES_PASSWORD", "postgres")
        
        conn = psycopg2.connect(host=host, port=port, database=database, user=user, password=password)
        cursor = conn.cursor()
        cursor.execute("SELECT team_id, team_name, team_abbreviation, city FROM raw_dev.teams")
        team_map = {}
        for row in cursor.fetchall():
            team_id, team_name, team_abbr, city = row
            # Map by team name
            if team_name:
                team_map[team_name] = team_id
            # Map by abbreviation
            if team_abbr:
                team_map[team_abbr] = team_id
            # Map by full name (city + name) - Kaggle uses "City Name" format
            if city and team_name:
                full_name = f"{city} {team_name}".strip()
                team_map[full_name] = team_id
            # Also try just the name part (e.g., "Lakers" from "Los Angeles Lakers")
            if team_name and ' ' in team_name:
                name_parts = team_name.split()
                if len(name_parts) > 1:
                    team_map[name_parts[-1]] = team_id  # Last word (e.g., "Lakers")
        
        cursor.close()
        conn.close()
        
        # Map team names to team_ids
        if 'playerteamCity' in df_mapped.columns and 'playerteamName' in df_mapped.columns:
            df_mapped['team_full_name'] = (df_mapped['playerteamCity'] + ' ' + df_mapped['playerteamName']).str.strip()
            df_mapped['team_id'] = df_mapped['team_full_name'].map(team_map)
            # Fallback to just team name
            df_mapped['team_id'] = df_mapped['team_id'].fillna(
                df_mapped['playerteamName'].map(team_map)
            )
            df_mapped = df_mapped.drop(columns=['team_full_name'])
        
        logger.info(f"Mapped {df_mapped['team_id'].notna().sum()} team_ids from {len(team_map)} teams")
    except Exception as e:
        logger.warning(f"Could not map team_ids: {e}. Will need to be filled manually.")
    
    # Set created_at
    df_mapped['created_at'] = datetime.now().isoformat()
    
    # Add dlt-specific columns (required by table schema)
    import uuid
    load_id = f"kaggle_backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    df_mapped['_dlt_load_id'] = load_id
    df_mapped['_dlt_id'] = [str(uuid.uuid4()) for _ in range(len(df_mapped))]
    
    # Select only required columns (including dlt columns)
    required_cols = [
        'game_id', 'player_id', 'game_date', 'player_name', 'team_id',
        'is_home', 'is_starter', 'minutes', 'minutes_calculated',
        'points', 'assists', 'rebounds_total', 'rebounds_offensive', 'rebounds_defensive',
        'steals', 'blocks', 'turnovers', 'fouls_personal', 'fouls_technical',
        'field_goals_made', 'field_goals_attempted', 'field_goal_pct',
        'three_pointers_made', 'three_pointers_attempted', 'three_point_pct',
        'free_throws_made', 'free_throws_attempted', 'free_throw_pct',
        'plus_minus', 'points_fast_break', 'points_in_paint', 'points_second_chance',
        'created_at', '_dlt_load_id', '_dlt_id'
    ]
    
    # Ensure all required columns exist
    for col in required_cols:
        if col not in df_mapped.columns:
            df_mapped[col] = None
    
    logger.info(f"Mapped dataset: {len(df_mapped)} rows")
    logger.info(f"Sample mapped data:")
    logger.info(df_mapped[required_cols].head())
    
    return df_mapped[required_cols]
    
    # Rename columns that match
    df_mapped = df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns})
    
    # Required columns for our schema
    required_cols = [
        'game_id', 'player_id', 'game_date', 'player_name', 'team_id',
        'is_home', 'is_starter', 'minutes', 'minutes_calculated',
        'points', 'assists', 'rebounds_total', 'rebounds_offensive', 'rebounds_defensive',
        'steals', 'blocks', 'turnovers', 'fouls_personal', 'fouls_technical',
        'field_goals_made', 'field_goals_attempted', 'field_goal_pct',
        'three_pointers_made', 'three_pointers_attempted', 'three_point_pct',
        'free_throws_made', 'free_throws_attempted', 'free_throw_pct',
        'plus_minus', 'points_fast_break', 'points_in_paint', 'points_second_chance',
        'created_at'
    ]
    
    # Add missing columns with None
    for col in required_cols:
        if col not in df_mapped.columns:
            df_mapped[col] = None
    
    # Convert minutes to float if it's a string (e.g., "36:00" -> 36.0)
    if 'minutes' in df_mapped.columns and df_mapped['minutes'].dtype == 'object':
        def parse_minutes(val):
            if pd.isna(val):
                return None
            if isinstance(val, str):
                if ':' in val:
                    parts = val.split(':')
                    return float(parts[0]) + float(parts[1]) / 60.0
                try:
                    return float(val)
                except:
                    return None
            return float(val) if val else None
        
        df_mapped['minutes'] = df_mapped['minutes'].apply(parse_minutes)
        df_mapped['minutes_calculated'] = df_mapped['minutes']
    
    # Set created_at
    df_mapped['created_at'] = datetime.now().isoformat()
    
    # Ensure data types
    numeric_cols = ['points', 'assists', 'rebounds_total', 'rebounds_offensive', 
                    'rebounds_defensive', 'steals', 'blocks', 'turnovers', 
                    'fouls_personal', 'field_goals_made', 'field_goals_attempted',
                    'three_pointers_made', 'three_pointers_attempted',
                    'free_throws_made', 'free_throws_attempted', 'plus_minus']
    
    for col in numeric_cols:
        if col in df_mapped.columns:
            df_mapped[col] = pd.to_numeric(df_mapped[col], errors='coerce')
    
    # Boolean columns
    bool_cols = ['is_home', 'is_starter']
    for col in bool_cols:
        if col in df_mapped.columns:
            df_mapped[col] = df_mapped[col].astype(bool, errors='ignore')
    
    logger.info(f"Mapped dataset: {len(df_mapped)} rows, {len(df_mapped.columns)} columns")
    logger.info(f"Columns after mapping: {list(df_mapped.columns)}")
    
    return df_mapped[required_cols]


def load_to_database(df: pd.DataFrame, table_name: str = 'boxscores', schema: str = 'raw_dev'):
    """Load DataFrame to PostgreSQL database."""
    try:
        import psycopg2
        from psycopg2.extras import execute_values
    except ImportError:
        logger.error("psycopg2 not available")
        return
    
    try:
        database = os.getenv("POSTGRES_DB", "nba_analytics")
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = int(os.getenv("POSTGRES_PORT", "5432"))
        user = os.getenv("POSTGRES_USER", "postgres")
        password = os.getenv("POSTGRES_PASSWORD", "postgres")
        
        conn = psycopg2.connect(host=host, port=port, database=database, user=user, password=password)
        cursor = conn.cursor()
        
        # Get existing game_ids to avoid duplicates
        cursor.execute(f"SELECT DISTINCT game_id FROM {schema}.{table_name}")
        existing_ids = {str(row[0]) for row in cursor.fetchall()}
        logger.info(f"Found {len(existing_ids)} existing game_ids in database")
        
        # Filter out existing records - check only for game_ids in this chunk (memory efficient)
        if 'game_id' in df.columns and 'player_id' in df.columns:
            df['game_player_key'] = df['game_id'].astype(str) + '_' + df['player_id'].astype(str)
            
            # Get existing records only for the game_ids in this chunk
            chunk_game_ids = df['game_id'].unique().astype(str).tolist()
            if len(chunk_game_ids) > 0:
                # Use parameterized query to avoid SQL injection
                placeholders = ','.join(['%s'] * len(chunk_game_ids))
                cursor.execute(
                    f"SELECT DISTINCT game_id, player_id FROM {schema}.{table_name} WHERE game_id IN ({placeholders})",
                    chunk_game_ids
                )
                existing_keys = {f"{str(row[0])}_{str(row[1])}" for row in cursor.fetchall()}
            else:
                existing_keys = set()
            
            df_new = df[~df['game_player_key'].isin(existing_keys)]
            df_new = df_new.drop(columns=['game_player_key'], errors='ignore')
            logger.info(f"Filtered out {len(df) - len(df_new)} existing records, {len(df_new)} new records to insert")
        else:
            df_new = df
        
        if len(df_new) == 0:
            logger.info("No new records to insert")
            cursor.close()
            conn.close()
            return
        
        logger.info(f"Inserting {len(df_new)} new records into {schema}.{table_name}...")
        
        columns = list(df_new.columns)
        col_names = ", ".join(columns)
        
        # Use batch insert with proper error handling
        from psycopg2.extras import execute_batch
        import numpy as np
        
        placeholders = ", ".join(["%s"] * len(columns))
        insert_sql = f"""
            INSERT INTO {schema}.{table_name} ({col_names})
            VALUES ({placeholders})
        """
        
        # Convert to Python native types, handling bigint range
        values = []
        for _, row in df_new.iterrows():
            row_values = []
            for col in columns:
                val = row[col]
                if pd.isna(val):
                    row_values.append(None)
                elif isinstance(val, (np.integer, np.int64)):
                    int_val = int(val)
                    # Check bigint range
                    if int_val > 9223372036854775807 or int_val < -9223372036854775808:
                        logger.warning(f"Value {int_val} in column {col} exceeds bigint range, setting to None")
                        row_values.append(None)
                    else:
                        row_values.append(int_val)
                elif isinstance(val, (np.floating, np.float64)):
                    row_values.append(float(val) if not pd.isna(val) else None)
                elif isinstance(val, np.bool_):
                    row_values.append(bool(val))
                elif isinstance(val, (int, float)):
                    # Python native types - check int range
                    if isinstance(val, int) and (val > 9223372036854775807 or val < -9223372036854775808):
                        logger.warning(f"Value {val} in column {col} exceeds bigint range, setting to None")
                        row_values.append(None)
                    else:
                        row_values.append(val)
                else:
                    row_values.append(val)
            values.append(tuple(row_values))
        
        batch_size = 1000
        inserted = 0
        failed_batches = 0
        for i in range(0, len(values), batch_size):
            batch = values[i:i+batch_size]
            try:
                execute_batch(cursor, insert_sql, batch, page_size=batch_size)
                inserted += len(batch)
                if inserted % 10000 == 0:
                    logger.info(f"Inserted {inserted:,}/{len(values):,} records...")
                    conn.commit()
            except Exception as e:
                conn.rollback()
                failed_batches += 1
                if failed_batches <= 3:  # Log first 3 failures
                    logger.warning(f"Batch {i} failed: {e}, skipping...")
                # Skip this batch and continue
                continue
        
        conn.commit()
        cursor.close()
        conn.close()
        
        logger.info(f"Successfully inserted {inserted} records into {schema}.{table_name}")
    
    except Exception as e:
        logger.error(f"Error inserting to database: {e}")
        import traceback
        traceback.print_exc()
        raise


def main():
    parser = argparse.ArgumentParser(description="Download and load Kaggle NBA historical dataset")
    parser.add_argument("--dataset", type=str, 
                       default="eoinamoore/historical-nba-data-and-player-box-scores",
                       help="Kaggle dataset name (format: username/dataset-name)")
    parser.add_argument("--table", type=str, default="boxscores",
                       help="Table to load data into (boxscores, games, etc.)")
    parser.add_argument("--season", type=str, help="Filter by season (e.g., 2023-24)")
    parser.add_argument("--inspect-only", action="store_true",
                       help="Only inspect the dataset, don't load to database")
    parser.add_argument("--download-dir", type=str, default="data/kaggle",
                       help="Directory to download dataset files")
    
    args = parser.parse_args()
    
    if not KAGGLE_AVAILABLE:
        logger.error("Kaggle API not available. Install with: pip install kaggle")
        logger.info("Then set up credentials: https://www.kaggle.com/settings -> API")
        return 1
    
    download_dir = Path(args.download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        # Download dataset
        csv_path = download_kaggle_dataset(args.dataset, download_dir)
        
        # For boxscores table, use PlayerStatistics.csv
        if args.table == "boxscores":
            player_stats_path = download_dir / "PlayerStatistics.csv"
            if player_stats_path.exists():
                csv_path = player_stats_path
                logger.info(f"Using PlayerStatistics.csv for boxscores data")
            else:
                logger.warning(f"PlayerStatistics.csv not found, using {csv_path.name}")
        
        # Inspect dataset
        df_sample = inspect_dataset(csv_path)
        
        if args.inspect_only:
            logger.info("Inspect-only mode. Exiting without loading to database.")
            return 0
        
        # Process in chunks to avoid memory issues
        logger.info("Processing dataset in chunks...")
        chunk_size = 50000
        total_rows = 0
        total_inserted = 0
        
        # First pass: get date range and total count
        date_ranges = []
        for chunk in pd.read_csv(csv_path, low_memory=False, chunksize=chunk_size):
            total_rows += len(chunk)
            if 'gameDateTimeEst' in chunk.columns:
                chunk['game_date_temp'] = pd.to_datetime(chunk['gameDateTimeEst'], errors='coerce')
                date_ranges.append((chunk['game_date_temp'].min(), chunk['game_date_temp'].max()))
            if total_rows % 500000 == 0:
                logger.info(f"Scanned {total_rows:,} rows...")
        
        if date_ranges:
            min_date = min(d[0] for d in date_ranges if pd.notna(d[0]))
            max_date = max(d[1] for d in date_ranges if pd.notna(d[1]))
            logger.info(f"Date range: {min_date} to {max_date}")
        logger.info(f"Total rows in dataset: {total_rows:,}")
        
        # Second pass: process and load chunks
        chunk_num = 0
        for chunk in pd.read_csv(csv_path, low_memory=False, chunksize=chunk_size):
            chunk_num += 1
            
            # Filter by season if specified
            if args.season:
                season_year = int(args.season.split("-")[0])
                if 'gameDateTimeEst' in chunk.columns:
                    chunk['game_date_temp'] = pd.to_datetime(chunk['gameDateTimeEst'], errors='coerce')
                    chunk = chunk[chunk['game_date_temp'].dt.year == season_year]
                    chunk = chunk.drop(columns=['game_date_temp'])
            
            if len(chunk) == 0:
                continue
            
            logger.info(f"Processing chunk {chunk_num} ({len(chunk):,} rows)...")
            
            # Map to our schema
            try:
                df_mapped = map_kaggle_to_schema(chunk)
                # Load to database (this function now handles chunking internally)
                load_to_database(df_mapped, args.table)
                total_inserted += len(df_mapped)
                logger.info(f"Chunk {chunk_num} completed. Total inserted so far: {total_inserted:,}")
            except Exception as e:
                logger.error(f"Error processing chunk {chunk_num}: {e}")
                import traceback
                traceback.print_exc()
                continue
        
        logger.info("[SUCCESS] Dataset loaded successfully!")
        return 0
        
    except Exception as e:
        logger.error(f"[ERROR] Failed to process dataset: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
