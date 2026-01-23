"""
Backfill 2024-25 season injury data from prosportstransactions.com

This uses the same source as the Kaggle dataset we loaded earlier.
Based on: https://github.com/logan-lauton/nba_webscrape
"""

import sys
from pathlib import Path
import os
from datetime import datetime, timedelta
import time
import logging

# Add project root to path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pandas as pd
import requests
from bs4 import BeautifulSoup
import psycopg2
from psycopg2.extras import execute_values
import uuid

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Prosportstransactions.com URL
BASE_URL = "https://www.prosportstransactions.com/basketball/Search/SearchResults.php"

# Headers to avoid bot detection
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.prosportstransactions.com/basketball/",
}

# Team name to abbreviation mapping (from our existing code)
TEAM_ABBREV_MAP = {
    "Atlanta Hawks": "ATL",
    "Boston Celtics": "BOS",
    "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI",
    "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL",
    "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW",
    "Houston Rockets": "HOU",
    "Indiana Pacers": "IND",
    "LA Clippers": "LAC",
    "Los Angeles Clippers": "LAC",
    "Los Angeles Lakers": "LAL",
    "LA Lakers": "LAL",
    "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR",
    "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA",
    "Washington Wizards": "WAS",
}


def scrape_prosportstransactions(begin_date: str, end_date: str) -> pd.DataFrame:
    """
    Scrape injury data from prosportstransactions.com for a date range.
    
    Based on: https://github.com/logan-lauton/nba_webscrape
    
    Args:
        begin_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
    
    Returns:
        DataFrame with columns: Date, Team, Acquired, Relinquished, Notes
    """
    logger.info(f"Scraping prosportstransactions.com from {begin_date} to {end_date}")
    
    # Create a session to maintain cookies
    session = requests.Session()
    session.headers.update(HEADERS)
    
    # First, visit the main page to get cookies
    logger.info("Visiting main page to establish session...")
    try:
        session.get("https://www.prosportstransactions.com/basketball/", timeout=30)
        time.sleep(1)  # Small delay
    except Exception as e:
        logger.warning(f"Could not visit main page: {e}")
    
    all_data = []
    num = 0  # Start at page 0
    page_count = 0
    
    while True:
        # Build URL with date range and pagination
        url = (
            f"{BASE_URL}?"
            f"Player=&"
            f"Team=&"
            f"BeginDate={begin_date}&"
            f"EndDate={end_date}&"
            f"ILChkBx=yes&"  # Injury List checkbox
            f"Submit=Search&"
            f"start={num}"
        )
        
        logger.info(f"Fetching page {page_count + 1} (start={num})...")
        
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'lxml')
            table = soup.find('table', attrs={'class': 'datatable center'})
            
            if not table:
                logger.warning(f"No table found on page {page_count + 1}. May have reached end.")
                break
            
            # Parse table to DataFrame
            df_page = pd.read_html(str(table))[0]
            
            # Check if we got data
            if len(df_page) == 0:
                logger.info("No more data found. Reached end of results.")
                break
            
            # Check if this is just the header row
            if len(df_page) == 1 and df_page.iloc[0, 0] == 'Date':
                logger.info("Only header row found. Reached end of results.")
                break
            
            all_data.append(df_page)
            page_count += 1
            num += 25  # Increment by 25 for next page
            
            # Rate limiting: 2.38 seconds between requests (≈ 25 requests/minute)
            time.sleep(2.38)
            
        except requests.RequestException as e:
            logger.error(f"Error fetching page {page_count + 1}: {e}")
            break
        except Exception as e:
            logger.error(f"Error parsing page {page_count + 1}: {e}")
            break
    
    if not all_data:
        logger.warning("No data scraped!")
        return pd.DataFrame(columns=['Date', 'Team', 'Acquired', 'Relinquished', 'Notes'])
    
    # Combine all pages
    df = pd.concat(all_data, ignore_index=True)
    
    # Clean column names
    df.columns = ['Date', 'Team', 'Acquired', 'Relinquished', 'Notes']
    
    # Remove header rows that may have been included
    df = df[df['Date'] != 'Date']
    
    # Clean data
    df = df.reset_index(drop=True)
    
    # Remove bullet points from player names
    if 'Acquired' in df.columns:
        df['Acquired'] = df['Acquired'].astype(str).str.replace('•', '', regex=False)
    if 'Relinquished' in df.columns:
        df['Relinquished'] = df['Relinquished'].astype(str).str.replace('•', '', regex=False)
    
    # Parse dates
    df['Date'] = pd.to_datetime(df['Date'].astype(str).str.strip(), format='%Y-%m-%d', errors='coerce')
    
    logger.info(f"Scraped {len(df)} injury records from {page_count} pages")
    
    return df


def map_to_schema(df: pd.DataFrame) -> pd.DataFrame:
    """
    Map prosportstransactions.com data to our raw_dev.injuries schema.
    
    Reuses logic from download_kaggle_injuries.py
    """
    logger.info("Mapping to our schema...")
    
    injury_records = []
    
    for _, row in df.iterrows():
        date = row.get('Date', None)
        team = row.get('Team', None)
        acquired = row.get('Acquired', None)
        relinquished = row.get('Relinquished', None)
        notes = row.get('Notes', '')
        
        if pd.isna(date) or pd.isna(team):
            continue
        
        # Parse date
        if isinstance(date, str):
            try:
                date_obj = pd.to_datetime(date).date()
            except:
                continue
        else:
            date_obj = date.date() if hasattr(date, 'date') else date
        
        capture_date = date_obj.strftime('%Y-%m-%d')
        
        # Get team abbreviation
        team_name = str(team).strip()
        team_abbrev = TEAM_ABBREV_MAP.get(team_name, "UNK")
        
        # Create records for players going on IL (Relinquished)
        if pd.notna(relinquished) and str(relinquished).strip() and str(relinquished) != 'nan':
            player_name = str(relinquished).strip()
            
            # Parse status from notes
            status = "Out"  # Default
            status_raw = notes
            injury_details = notes
            
            # Try to extract status from notes
            notes_lower = str(notes).lower()
            if 'doubtful' in notes_lower:
                status = "Doubtful"
            elif 'questionable' in notes_lower:
                status = "Questionable"
            elif 'probable' in notes_lower:
                status = "Probable"
            elif 'out' in notes_lower:
                status = "Out"
            
            injury_id = f"{capture_date}_{team_abbrev}_{player_name.replace(' ', '_')}_OUT"
            
            injury_records.append({
                'injury_id': injury_id,
                'capture_date': capture_date,
                'player_name': player_name,
                'player_espn_id': None,
                'team_name': team_name,
                'team_abbrev': team_abbrev,
                'status': status,
                'status_raw': status_raw,
                'injury_details': injury_details,
                'captured_at': datetime.now().isoformat(),
            })
        
        # Create records for players returning from IL (Acquired)
        if pd.notna(acquired) and str(acquired).strip() and str(acquired) != 'nan':
            player_name = str(acquired).strip()
            
            injury_id = f"{capture_date}_{team_abbrev}_{player_name.replace(' ', '_')}_ACTIVE"
            
            injury_records.append({
                'injury_id': injury_id,
                'capture_date': capture_date,
                'player_name': player_name,
                'player_espn_id': None,
                'team_name': team_name,
                'team_abbrev': team_abbrev,
                'status': 'Active',
                'status_raw': 'Returned from injury list',
                'injury_details': notes,
                'captured_at': datetime.now().isoformat(),
            })
    
    if not injury_records:
        logger.warning("No injury records created from scraped data")
        return pd.DataFrame()
    
    df_mapped = pd.DataFrame(injury_records)
    
    logger.info(f"Created {len(df_mapped)} injury records")
    
    return df_mapped


def load_to_database(df: pd.DataFrame, db_config: dict):
    """
    Load injury data into PostgreSQL database.
    
    Reuses logic from download_kaggle_injuries.py
    """
    if df.empty:
        logger.warning("No data to load")
        return 0
    
    logger.info(f"Loading {len(df)} records to database...")
    
    # Get database connection
    conn = psycopg2.connect(
        host=db_config['host'],
        port=db_config['port'],
        database=db_config['database'],
        user=db_config['user'],
        password=db_config['password']
    )
    
    try:
        cursor = conn.cursor()
        
        # Check for existing injury_ids to avoid duplicates
        existing_ids_query = "SELECT injury_id FROM raw_dev.injuries WHERE injury_id = ANY(%s)"
        existing_ids = set()
        
        # Process in batches to avoid memory issues
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
        
        # Add DLT-required columns
        df_new['_dlt_load_id'] = str(uuid.uuid4())
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
        
        # Insert records
        insert_query = """
            INSERT INTO raw_dev.injuries (
                injury_id, capture_date, player_name, player_espn_id,
                team_name, team_abbrev, status, status_raw,
                injury_details, captured_at, _dlt_load_id, _dlt_id
            ) VALUES %s
            ON CONFLICT (injury_id) DO NOTHING
        """
        
        execute_values(cursor, insert_query, values)
        
        conn.commit()
        
        logger.info(f"Successfully loaded {len(df_new)} records")
        return len(df_new)
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Error loading to database: {e}")
        raise
    finally:
        cursor.close()
        conn.close()


def main():
    """Main function to backfill 2024-25 season injury data."""
    logger.info("=" * 60)
    logger.info("Prosportstransactions.com Injury Data Backfill - 2024-25 Season")
    logger.info("=" * 60)
    
    # Date range for 2024-25 season
    begin_date = "2024-10-01"
    end_date = "2025-01-22"  # Today's date or end of season
    
    # Scrape data
    df_raw = scrape_prosportstransactions(begin_date, end_date)
    
    if df_raw.empty:
        logger.error("No data scraped. Exiting.")
        return
    
    logger.info(f"\nScraped data summary:")
    logger.info(f"  Total records: {len(df_raw)}")
    logger.info(f"  Date range: {df_raw['Date'].min()} to {df_raw['Date'].max()}")
    logger.info(f"  Teams: {df_raw['Team'].nunique()}")
    
    # Map to our schema
    df_mapped = map_to_schema(df_raw)
    
    if df_mapped.empty:
        logger.error("No records created after mapping. Exiting.")
        return
    
    logger.info(f"\nMapped data summary:")
    logger.info(f"  Total injury records: {len(df_mapped)}")
    logger.info(f"  Unique dates: {df_mapped['capture_date'].nunique()}")
    logger.info(f"  Unique players: {df_mapped['player_name'].nunique()}")
    
    # Get database config from environment
    db_config = {
        'host': os.getenv('POSTGRES_HOST', 'localhost'),
        'port': os.getenv('POSTGRES_PORT', '5432'),
        'database': os.getenv('POSTGRES_DB', 'nba_data'),
        'user': os.getenv('POSTGRES_USER', 'postgres'),
        'password': os.getenv('POSTGRES_PASSWORD', 'postgres'),
    }
    
    # Load to database
    records_loaded = load_to_database(df_mapped, db_config)
    
    logger.info("\n" + "=" * 60)
    logger.info(f"Backfill complete! Loaded {records_loaded} new records.")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
