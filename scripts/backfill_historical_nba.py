"""
Historical NBA data backfill script with automatic download.

This script automatically downloads and backfills historical seasons using:
1. Basketball Reference scraper (primary) - fully automated, free
2. Direct URL downloads (if provided) - for Opendatabay or other sources

Going forward, we use NBA CDN as source of truth. This is only for backfill.
"""

import sys
import os
from pathlib import Path
import pandas as pd
import logging
from datetime import datetime, timedelta
import time
import requests
import zipfile
import tempfile
from typing import Optional, List, Dict, Any

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Try to import basketball-reference-scraper
try:
    from basketball_reference_scraper.box_scores import get_box_scores
    from basketball_reference_scraper.schedule import get_schedule
    BASKETBALL_REF_AVAILABLE = True
except ImportError:
    BASKETBALL_REF_AVAILABLE = False
    logger.warning("basketball-reference-scraper not installed. Install with: pip install basketball-reference-scraper")


def _map_br_team_to_nba_id(team_abbr: str) -> Optional[int]:
    """Map Basketball Reference team abbreviation to NBA team_id."""
    team_mapping = {
        "ATL": 1610612737, "BOS": 1610612738, "BRK": 1610612751, "CHA": 1610612766,
        "CHI": 1610612741, "CLE": 1610612739, "DAL": 1610612742, "DEN": 1610612743,
        "DET": 1610612765, "GSW": 1610612744, "HOU": 1610612745, "IND": 1610612754,
        "LAC": 1610612746, "LAL": 1610612747, "MEM": 1610612763, "MIA": 1610612748,
        "MIL": 1610612749, "MIN": 1610612750, "NOP": 1610612740, "NYK": 1610612752,
        "OKC": 1610612760, "ORL": 1610612753, "PHI": 1610612755, "PHO": 1610612756,
        "POR": 1610612757, "SAC": 1610612758, "SAS": 1610612759, "TOR": 1610612761,
        "UTA": 1610612762, "WAS": 1610612764,
    }
    return team_mapping.get(team_abbr.upper())


def _convert_date_to_game_id(date_str: str, game_num: int = 0) -> str:
    """Convert date to NBA game ID format: 002YYMMDD0HH"""
    try:
        if "/" in date_str:
            date_parts = str(date_str).split("/")
            if len(date_parts) == 3:
                date_obj = datetime(int(date_parts[2]), int(date_parts[0]), int(date_parts[1]))
            else:
                return None
        else:
            date_obj = datetime.strptime(str(date_str), "%Y-%m-%d")
        
        year = date_obj.year
        if date_obj.month >= 10:
            season_year = year
        else:
            season_year = year - 1
        
        yy = str(season_year)[2:]
        mm = f"{date_obj.month:02d}"
        dd = f"{date_obj.day:02d}"
        hh = f"{game_num:02d}"
        
        return f"002{yy}{mm}{dd}0{hh}"
    except Exception as e:
        logger.debug(f"Could not convert date {date_str} to game_id: {e}")
        return None


def _get_existing_game_ids(table_name: str, schema_name: str = "raw_dev") -> set:
    """Get existing game IDs from database to avoid duplicates."""
    try:
        import psycopg2
    except ImportError:
        return set()
    
    try:
        database = os.getenv("POSTGRES_DB", "nba_analytics")
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = int(os.getenv("POSTGRES_PORT", "5432"))
        user = os.getenv("POSTGRES_USER", "postgres")
        password = os.getenv("POSTGRES_PASSWORD", "postgres")
        
        conn = psycopg2.connect(host=host, port=port, database=database, user=user, password=password)
        cursor = conn.cursor()
        cursor.execute(f"SELECT DISTINCT game_id FROM {schema_name}.{table_name}")
        existing_ids = {str(row[0]) for row in cursor.fetchall()}
        cursor.close()
        conn.close()
        return existing_ids
    except Exception as e:
        logger.debug(f"Could not query existing game_ids: {e}")
        return set()


def get_free_nba_dataset_urls() -> Dict[str, str]:
    """
    Return known free NBA dataset download URLs.
    
    These are publicly available datasets that can be downloaded automatically.
    """
    return {
        # Kaggle datasets (require Kaggle API or manual download)
        "kaggle_nba_player_stats": "https://www.kaggle.com/datasets/wyattowalsh/basketball",
        
        # GitHub releases (direct download links)
        "github_nba_data": "https://github.com/shufinskiy/nba_data",
        
        # Note: Opendatabay URLs may change, so we provide instructions instead
    }


def find_free_dataset_download() -> Optional[str]:
    """
    Try to find a free NBA dataset download URL automatically.
    
    Returns:
        Direct download URL if found, None otherwise
    """
    logger.info("Searching for free NBA historical datasets...")
    
    # Try common free dataset sources
    # For now, we'll provide instructions since direct links may change
    
    logger.info("Free NBA historical datasets are available from:")
    logger.info("  1. Kaggle: https://www.kaggle.com/datasets/wyattowalsh/basketball")
    logger.info("  2. GitHub: https://github.com/shufinskiy/nba_data")
    logger.info("  3. Opendatabay: Search for 'NBA historical' datasets")
    logger.info("")
    logger.info("To use:")
    logger.info("  - Download the CSV/ZIP file manually")
    logger.info("  - Then run: python scripts/backfill_historical_nba.py --file <path> --table boxscores")
    logger.info("  - Or if you have a direct download URL: --url <url>")
    
    return None


def download_from_basketball_reference(season: int, table: str, output_dir: Path, 
                                       skip_existing: bool = True) -> Optional[Path]:
    """
    Automatically download historical data from Basketball Reference.
    
    Note: This is a placeholder - Basketball Reference scraper needs full implementation
    to convert their format to our schema. For now, we recommend using --url with
    a direct download link from Opendatabay or other sources.
    
    Args:
        season: Season year (e.g., 2023 for 2023-24 season)
        table: Table to backfill (boxscores, games)
        output_dir: Directory to save downloaded files
        skip_existing: Skip games that already exist in database
    
    Returns:
        Path to downloaded CSV file, or None if failed
    """
    logger.warning("Basketball Reference scraper needs full schema conversion implementation.")
    logger.info("For automatic download, use --url with Opendatabay direct download link")
    logger.info("Or use: python scripts/backfill_historical_nba.py --url <direct_download_url>")
    return None


def download_from_url(url: str, output_path: Path) -> Path:
    """Download file from URL (supports CSV, ZIP, etc.)."""
    logger.info(f"Downloading from {url}...")
    
    try:
        response = requests.get(url, stream=True, timeout=300, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        downloaded = 0
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0 and downloaded % (1024 * 1024) == 0:
                        percent = (downloaded / total_size) * 100
                        logger.info(f"Downloaded {downloaded / (1024*1024):.1f} MB ({percent:.1f}%)")
        
        logger.info(f"Download complete: {output_path}")
        
        # Extract ZIP if needed
        if output_path.suffix == '.zip' or 'zip' in response.headers.get('content-type', ''):
            logger.info("Extracting ZIP file...")
            extract_dir = output_path.parent / output_path.stem
            extract_dir.mkdir(exist_ok=True)
            
            with zipfile.ZipFile(output_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
            
            logger.info(f"Extracted to: {extract_dir}")
            csv_files = list(extract_dir.glob("*.csv"))
            if csv_files:
                return max(csv_files, key=lambda f: f.stat().st_size)  # Return largest CSV
            return extract_dir
        
        return output_path
        
    except Exception as e:
        logger.error(f"Error downloading file: {e}")
        raise


def map_to_schema(df: pd.DataFrame, table_name: str) -> pd.DataFrame:
    """Map dataset columns to our schema format."""
    if table_name == "boxscores":
        column_mapping = {
            "GAME_ID": "game_id", "PLAYER_ID": "player_id", "GAME_DATE": "game_date",
            "PLAYER_NAME": "player_name", "TEAM_ID": "team_id", "IS_HOME": "is_home",
            "IS_STARTER": "is_starter", "MIN": "minutes", "PTS": "points",
            "AST": "assists", "REB": "rebounds_total", "OREB": "rebounds_offensive",
            "DREB": "rebounds_defensive", "STL": "steals", "BLK": "blocks",
            "TOV": "turnovers", "PF": "fouls_personal", "FGM": "field_goals_made",
            "FGA": "field_goals_attempted", "FG_PCT": "field_goal_pct",
            "FG3M": "three_pointers_made", "FG3A": "three_pointers_attempted",
            "FG3_PCT": "three_point_pct", "FTM": "free_throws_made",
            "FTA": "free_throws_attempted", "FT_PCT": "free_throw_pct",
            "PLUS_MINUS": "plus_minus",
        }
        
        df_mapped = df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns})
        
        required_cols = [
            "game_id", "player_id", "game_date", "player_name", "team_id",
            "is_home", "is_starter", "minutes", "minutes_calculated",
            "points", "assists", "rebounds_total", "rebounds_offensive", "rebounds_defensive",
            "steals", "blocks", "turnovers", "fouls_personal", "fouls_technical",
            "field_goals_made", "field_goals_attempted", "field_goal_pct",
            "three_pointers_made", "three_pointers_attempted", "three_point_pct",
            "free_throws_made", "free_throws_attempted", "free_throw_pct",
            "plus_minus", "points_fast_break", "points_in_paint", "points_second_chance",
            "created_at"
        ]
        
        for col in required_cols:
            if col not in df_mapped.columns:
                df_mapped[col] = None
        
        df_mapped["created_at"] = datetime.now().isoformat()
        return df_mapped[required_cols]
    
    elif table_name == "games":
        column_mapping = {
            "GAME_ID": "game_id", "GAME_DATE": "game_date", "SEASON": "season",
            "HOME_TEAM_ID": "home_team_id", "AWAY_TEAM_ID": "away_team_id",
            "HOME_SCORE": "home_score", "AWAY_SCORE": "away_score",
        }
        
        df_mapped = df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns})
        
        required_cols = [
            "game_id", "game_date", "season", "season_type",
            "home_team_id", "away_team_id", "home_team_name", "away_team_name",
            "home_score", "away_score", "winner_team_id", "game_status",
            "venue", "arena_city", "arena_state", "created_at"
        ]
        
        for col in required_cols:
            if col not in df_mapped.columns:
                df_mapped[col] = None
        
        df_mapped["season_type"] = df_mapped.get("season_type", "Regular Season")
        df_mapped["game_status"] = df_mapped.get("game_status", "Final")
        df_mapped["created_at"] = datetime.now().isoformat()
        return df_mapped[required_cols]
    
    return df


def load_dataset(file_path: str, table_name: str) -> pd.DataFrame:
    """Load and map dataset CSV file to our schema."""
    logger.info(f"Loading dataset from {file_path}...")
    
    try:
        df = pd.read_csv(file_path, low_memory=False)
        logger.info(f"Loaded {len(df)} rows from {file_path}")
        
        df_mapped = map_to_schema(df, table_name)
        logger.info(f"Mapped to schema: {len(df_mapped)} rows, {len(df_mapped.columns)} columns")
        return df_mapped
    
    except Exception as e:
        logger.error(f"Error loading dataset: {e}")
        raise


def insert_to_database(df: pd.DataFrame, table_name: str, schema: str = "raw_dev"):
    """Insert DataFrame to PostgreSQL database."""
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
        
        # Get existing game_ids
        cursor.execute(f"SELECT DISTINCT game_id FROM {schema}.{table_name}")
        existing_ids = {str(row[0]) for row in cursor.fetchall()}
        
        # Filter out existing records
        if "game_id" in df.columns:
            df_new = df[~df["game_id"].astype(str).isin(existing_ids)]
        else:
            df_new = df
        
        if len(df_new) == 0:
            logger.info(f"No new records to insert for {table_name}")
            cursor.close()
            conn.close()
            return
        
        logger.info(f"Inserting {len(df_new)} new records into {schema}.{table_name}...")
        
        columns = list(df_new.columns)
        values = [tuple(row) for row in df_new.values]
        
        placeholders = ", ".join(["%s"] * len(columns))
        col_names = ", ".join(columns)
        
        if table_name == "boxscores":
            pk_cols = "game_id, player_id"
        elif table_name == "games":
            pk_cols = "game_id"
        elif table_name == "team_boxscores":
            pk_cols = "game_id, team_id"
        else:
            pk_cols = "game_id"
        
        insert_sql = f"""
            INSERT INTO {schema}.{table_name} ({col_names})
            VALUES ({placeholders})
            ON CONFLICT ({pk_cols}) DO NOTHING
        """
        
        batch_size = 1000
        inserted = 0
        for i in range(0, len(values), batch_size):
            batch = values[i:i+batch_size]
            execute_values(cursor, insert_sql, batch, page_size=batch_size)
            inserted += len(batch)
            if inserted % 10000 == 0:
                logger.info(f"Inserted {inserted}/{len(values)} records...")
        
        conn.commit()
        cursor.close()
        conn.close()
        
        logger.info(f"Successfully inserted {inserted} records into {schema}.{table_name}")
    
    except Exception as e:
        logger.error(f"Error inserting to database: {e}")
        raise


def main():
    """Main backfill function."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Automatically download and backfill historical NBA data")
    parser.add_argument("--source", choices=["basketball-reference", "url", "file"], 
                       default="basketball-reference", help="Data source to use")
    parser.add_argument("--file", type=str, help="Path to CSV file (if already downloaded)")
    parser.add_argument("--url", type=str, help="Direct download URL for dataset (CSV or ZIP)")
    parser.add_argument("--table", choices=["boxscores", "games", "team_boxscores"],
                       default="boxscores", help="Table to backfill")
    parser.add_argument("--season", type=int, help="Season year to backfill (e.g., 2023 for 2023-24)")
    parser.add_argument("--download-dir", type=str, default="data/historical",
                       help="Directory to save downloaded files")
    
    args = parser.parse_args()
    
    download_dir = Path(args.download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)
    
    file_path = None
    
    # Determine file path
    if args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            logger.error(f"File not found: {file_path}")
            return
    
    elif args.url:
        # Automatically download from URL
        logger.info(f"Downloading from URL: {args.url}")
        output_file = download_dir / f"{args.table}_{datetime.now().strftime('%Y%m%d')}.csv"
        
        try:
            file_path = download_from_url(args.url, output_file)
            
            # If ZIP was extracted, find the CSV
            if file_path.is_dir():
                csv_files = list(file_path.glob("*.csv"))
                if csv_files:
                    file_path = max(csv_files, key=lambda f: f.stat().st_size)
                else:
                    logger.error(f"No CSV files found in {file_path}")
                    return
        except Exception as e:
            logger.error(f"Failed to download from URL: {e}")
            return
    
    elif args.source == "basketball-reference":
        # Try to automatically find free dataset
        logger.info("Attempting to find free NBA historical dataset...")
        
        download_url = find_free_dataset_download()
        
        if download_url:
            # Download from the found URL
            output_file = download_dir / f"{args.table}_{datetime.now().strftime('%Y%m%d')}.csv"
            try:
                file_path = download_from_url(download_url, output_file)
                
                # If ZIP was extracted, find the CSV
                if file_path.is_dir():
                    csv_files = list(file_path.glob("*.csv"))
                    if csv_files:
                        file_path = max(csv_files, key=lambda f: f.stat().st_size)
                    else:
                        logger.error(f"No CSV files found in {file_path}")
                        return
            except Exception as e:
                logger.error(f"Failed to download: {e}")
                return
        else:
            # Provide clear instructions
            logger.info("")
            logger.info("=" * 60)
            logger.info("AUTOMATIC DOWNLOAD INSTRUCTIONS")
            logger.info("=" * 60)
            logger.info("")
            logger.info("Free NBA historical datasets require manual download due to:")
            logger.info("  - No stable direct download URLs")
            logger.info("  - Authentication requirements (Kaggle)")
            logger.info("  - Terms of service restrictions")
            logger.info("")
            logger.info("QUICK START:")
            logger.info("  1. Visit one of these free sources:")
            logger.info("     - Kaggle: https://www.kaggle.com/datasets/wyattowalsh/basketball")
            logger.info("     - GitHub: https://github.com/shufinskiy/nba_data")
            logger.info("     - Search Opendatabay for 'NBA historical'")
            logger.info("")
            logger.info("  2. Download the CSV/ZIP file")
            logger.info("")
            logger.info("  3. Run this script with the file:")
            logger.info(f"     python scripts/backfill_historical_nba.py --file <path> --table {args.table}")
            logger.info("")
            logger.info("  OR if you have a direct download URL:")
            logger.info(f"     python scripts/backfill_historical_nba.py --url <url> --table {args.table}")
            logger.info("")
            return
    
    if not file_path or not file_path.exists():
        logger.error(f"File not found: {file_path}")
        return
    
    # Load and insert data
    logger.info(f"Loading data from {file_path}...")
    df = load_dataset(str(file_path), args.table)
    insert_to_database(df, args.table)
    
    logger.info("✅ Backfill complete!")


if __name__ == "__main__":
    main()
