"""
Backfill historical injury data from ESPN.

This script scrapes ESPN injury pages for past dates to build historical injury dataset.
Uses the existing ESPN scraper logic but allows specifying target dates.
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

import requests
from bs4 import BeautifulSoup
import re
import psycopg2
from psycopg2.extras import RealDictCursor

# Import existing scraper logic
from ingestion.dlt.pipelines.nba_injuries import (
    HEADERS,
    TEAM_ABBREV_MAP,
    _parse_injury_status,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ESPN Injuries URL (current - may need Wayback Machine for very old dates)
ESPN_INJURIES_URL = "https://www.espn.com/nba/injuries"


def scrape_injuries_for_date(target_date: str) -> list:
    """
    Scrape injury data for a specific date.
    
    Note: ESPN's current injuries page only shows today's injuries.
    For historical dates, we'll try the current page (may not work for old dates).
    For very old dates, would need Wayback Machine integration.
    
    Args:
        target_date: Date in YYYY-MM-DD format
        
    Returns:
        List of injury records
    """
    # ESPN's current page only shows today's injuries
    # For historical dates, this may not work, but we'll try
    # The page structure should be the same
    
    url = ESPN_INJURIES_URL
    logger.info(f"Scraping injuries for {target_date} from {url} (note: ESPN shows current injuries, may not work for historical dates)")
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        injuries = []
        
        # Find injury tables - ESPN structures by team
        team_sections = soup.find_all('div', class_='ResponsiveTable')
        
        if not team_sections:
            team_sections = soup.find_all('section', class_='Card')
        
        for section in team_sections:
            team_header = section.find(['h2', 'div'], class_=re.compile(r'(Table__Title|headline)'))
            if not team_header:
                continue
                
            team_name = team_header.get_text(strip=True)
            team_abbrev = TEAM_ABBREV_MAP.get(team_name, "UNK")
            
            table = section.find('table')
            if not table:
                continue
                
            rows = table.find_all('tr')
            
            for row in rows:
                cells = row.find_all(['td', 'th'])
                if len(cells) < 3:
                    continue
                    
                if row.find('th'):
                    continue
                    
                try:
                    player_cell = cells[0]
                    player_link = player_cell.find('a')
                    player_name = player_link.get_text(strip=True) if player_link else player_cell.get_text(strip=True)
                    
                    player_espn_id = None
                    if player_link and 'href' in player_link.attrs:
                        href = player_link['href']
                        match = re.search(r'/id/(\d+)/', href)
                        if match:
                            player_espn_id = match.group(1)
                    
                    status_text = cells[1].get_text(strip=True) if len(cells) > 1 else "Unknown"
                    comment = cells[2].get_text(strip=True) if len(cells) > 2 else ""
                    
                    status = _parse_injury_status(status_text)
                    
                    injury_id = f"{target_date}_{team_abbrev}_{player_name.replace(' ', '_')}"
                    
                    injury_record = {
                        "injury_id": injury_id,
                        "capture_date": target_date,
                        "player_name": player_name,
                        "player_espn_id": player_espn_id,
                        "team_name": team_name,
                        "team_abbrev": team_abbrev,
                        "status": status,
                        "status_raw": status_text,
                        "injury_details": comment,
                        "captured_at": datetime.now().isoformat(),
                    }
                    
                    injuries.append(injury_record)
                    
                except Exception as e:
                    logger.warning(f"Error parsing injury row: {e}")
                    continue
        
        logger.info(f"Found {len(injuries)} injuries for {target_date}")
        return injuries
        
    except requests.RequestException as e:
        logger.error(f"Error fetching ESPN injuries for {target_date}: {e}")
        return []


def save_injuries_to_db(injuries: list, conn):
    """Save injury records to database."""
    cursor = conn.cursor()
    
    inserted = 0
    for injury in injuries:
        try:
            cursor.execute(
                """
                INSERT INTO raw_dev.injuries (
                    injury_id, capture_date, player_name, player_espn_id,
                    team_name, team_abbrev, status, status_raw, injury_details, captured_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (injury_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    status_raw = EXCLUDED.status_raw,
                    injury_details = EXCLUDED.injury_details,
                    captured_at = EXCLUDED.captured_at
                """,
                (
                    injury['injury_id'],
                    injury['capture_date'],
                    injury['player_name'],
                    injury['player_espn_id'],
                    injury['team_name'],
                    injury['team_abbrev'],
                    injury['status'],
                    injury['status_raw'],
                    injury['injury_details'],
                    injury['captured_at'],
                )
            )
            inserted += 1
        except Exception as e:
            logger.warning(f"Error inserting injury {injury['injury_id']}: {e}")
            continue
    
    conn.commit()
    return inserted


def backfill_date_range(start_date: str, end_date: str, db_conn):
    """
    Backfill injuries for a date range.
    
    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        db_conn: Database connection
    """
    start = datetime.strptime(start_date, '%Y-%m-%d')
    end = datetime.strptime(end_date, '%Y-%m-%d')
    
    current = start
    total_inserted = 0
    
    while current <= end:
        date_str = current.strftime('%Y-%m-%d')
        logger.info(f"Processing {date_str}...")
        
        injuries = scrape_injuries_for_date(date_str)
        if injuries:
            inserted = save_injuries_to_db(injuries, db_conn)
            total_inserted += inserted
            logger.info(f"  Inserted {inserted} injuries for {date_str}")
        
        # Rate limiting
        time.sleep(1)  # Be respectful to ESPN servers
        
        current += timedelta(days=1)
    
    logger.info(f"Backfill complete! Total injuries inserted: {total_inserted}")


def get_db_connection():
    """Get database connection."""
    database = os.getenv("POSTGRES_DB", "nba_analytics")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    
    return psycopg2.connect(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
    )


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Backfill historical injury data')
    parser.add_argument('--start-date', required=True, help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end-date', required=True, help='End date (YYYY-MM-DD)')
    parser.add_argument('--single-date', help='Single date to backfill (YYYY-MM-DD)')
    
    args = parser.parse_args()
    
    conn = get_db_connection()
    
    try:
        if args.single_date:
            logger.info(f"Backfilling single date: {args.single_date}")
            injuries = scrape_injuries_for_date(args.single_date)
            if injuries:
                inserted = save_injuries_to_db(injuries, conn)
                logger.info(f"Inserted {inserted} injuries")
        else:
            logger.info(f"Backfilling date range: {args.start_date} to {args.end_date}")
            backfill_date_range(args.start_date, args.end_date, conn)
    finally:
        conn.close()
