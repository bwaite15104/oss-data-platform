"""
NBA Injury Data Pipeline - Historical Backfill Support

Modified version of nba_injuries.py that supports scraping historical dates.
Can use Wayback Machine for very old dates.
"""

import dlt
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import Iterator, Dict, Any, Optional
import logging
import re
import time

# Import shared logic from main scraper
from ingestion.dlt.pipelines.nba_injuries import (
    HEADERS,
    TEAM_ABBREV_MAP,
    _parse_injury_status,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ESPN Injuries URL
ESPN_INJURIES_URL = "https://www.espn.com/nba/injuries"


def get_wayback_url(target_date: str) -> Optional[str]:
    """
    Get Wayback Machine URL for ESPN injuries page on a specific date.
    
    Args:
        target_date: Date in YYYY-MM-DD format
        
    Returns:
        Wayback Machine URL or None if not available
    """
    # Wayback Machine CDX API format
    # https://web.archive.org/web/{timestamp}/{url}
    try:
        date_obj = datetime.strptime(target_date, '%Y-%m-%d')
        timestamp = date_obj.strftime('%Y%m%d')
        wayback_url = f"https://web.archive.org/web/{timestamp}/{ESPN_INJURIES_URL}"
        return wayback_url
    except Exception as e:
        logger.warning(f"Error constructing Wayback URL for {target_date}: {e}")
        return None


@dlt.resource(
    name="injuries_historical",
    write_disposition="append",  # Append for historical backfill
    primary_key="injury_id",
)
def nba_injuries_resource_for_date(target_date: str, use_wayback: bool = False) -> Iterator[Dict[str, Any]]:
    """
    Scrape NBA injury data from ESPN for a specific date.
    
    Args:
        target_date: Date in YYYY-MM-DD format
        use_wayback: If True, try Wayback Machine for historical dates
        
    Yields:
        Injury records for the specified date
    """
    logger.info(f"Fetching injury data for {target_date}")
    
    # Determine URL to use
    if use_wayback:
        url = get_wayback_url(target_date)
        if not url:
            logger.warning(f"Could not construct Wayback URL for {target_date}, skipping")
            return
    else:
        url = ESPN_INJURIES_URL
        # Note: ESPN current page may not show historical data
        # For historical dates, use_wayback=True is recommended
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        injury_count = 0
        captured_at = datetime.now().isoformat()
        capture_date = target_date  # Use provided date, not current date
        
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
                    
                    injury_id = f"{capture_date}_{team_abbrev}_{player_name.replace(' ', '_')}"
                    
                    injury_record = {
                        "injury_id": injury_id,
                        "capture_date": capture_date,
                        "player_name": player_name,
                        "player_espn_id": player_espn_id,
                        "team_name": team_name,
                        "team_abbrev": team_abbrev,
                        "status": status,
                        "status_raw": status_text,
                        "injury_details": comment,
                        "captured_at": captured_at,
                    }
                    
                    injury_count += 1
                    yield injury_record
                    
                except Exception as e:
                    logger.warning(f"Error parsing injury row: {e}")
                    continue
        
        logger.info(f"Injury extraction complete for {target_date}! Found {injury_count} injured players")
        
        if injury_count == 0:
            logger.warning(f"No injuries found for {target_date} - may need Wayback Machine or different source")
            
    except requests.RequestException as e:
        logger.error(f"Error fetching ESPN injuries for {target_date}: {e}")
        if not use_wayback:
            logger.info(f"Trying Wayback Machine for {target_date}...")
            # Recursively try Wayback Machine
            for record in nba_injuries_resource_for_date(target_date, use_wayback=True):
                yield record
        else:
            raise
    except Exception as e:
        logger.error(f"Error parsing ESPN injuries for {target_date}: {e}")
        raise


@dlt.source(name="nba_injuries_historical")
def nba_injuries_historical_source(target_date: str, use_wayback: bool = False):
    """NBA injuries data source for historical dates."""
    return [nba_injuries_resource_for_date(target_date, use_wayback)]


# For testing
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python nba_injuries_historical.py YYYY-MM-DD [--wayback]")
        sys.exit(1)
    
    target_date = sys.argv[1]
    use_wayback = "--wayback" in sys.argv
    
    print(f"Testing historical injury scraper for {target_date}...")
    
    injuries = list(nba_injuries_resource_for_date(target_date, use_wayback))
    print(f"\nFound {len(injuries)} injuries:")
    
    for injury in injuries[:10]:
        print(f"  {injury['player_name']} ({injury['team_abbrev']}): {injury['status']} - {injury['injury_details'][:50]}...")
