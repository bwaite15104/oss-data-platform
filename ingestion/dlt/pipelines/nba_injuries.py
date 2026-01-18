"""
NBA Injury Data Pipeline using ESPN scraping.

This module scrapes injury data from ESPN's NBA injuries page.
Free data source - no API key required.
"""

import dlt
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import Iterator, Dict, Any, Optional
import logging
import re
import time

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ESPN Injuries URL
ESPN_INJURIES_URL = "https://www.espn.com/nba/injuries"

# Headers to avoid bot detection
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Team name mappings (ESPN uses full names)
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


def _parse_injury_status(status_text: str) -> str:
    """Parse injury status from ESPN text."""
    status_lower = status_text.lower()
    if "out" in status_lower:
        return "Out"
    elif "doubtful" in status_lower:
        return "Doubtful"
    elif "questionable" in status_lower:
        return "Questionable"
    elif "probable" in status_lower:
        return "Probable"
    elif "day-to-day" in status_lower:
        return "Day-to-Day"
    else:
        return status_text


@dlt.resource(
    name="injuries",
    write_disposition="replace",  # Replace all injuries each time (point-in-time snapshot)
    primary_key="injury_id",
)
def nba_injuries_resource() -> Iterator[Dict[str, Any]]:
    """
    Scrape current NBA injury data from ESPN.
    
    This is a point-in-time snapshot - injuries should be collected regularly
    to build historical data.
    """
    logger.info(f"Fetching injury data from ESPN: {ESPN_INJURIES_URL}")
    
    try:
        response = requests.get(ESPN_INJURIES_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find injury tables - ESPN structures by team
        injury_count = 0
        captured_at = datetime.now().isoformat()
        capture_date = datetime.now().strftime("%Y-%m-%d")
        
        # Look for team sections
        team_sections = soup.find_all('div', class_='ResponsiveTable')
        
        if not team_sections:
            # Try alternative structure
            team_sections = soup.find_all('section', class_='Card')
        
        for section in team_sections:
            # Find team name
            team_header = section.find(['h2', 'div'], class_=re.compile(r'(Table__Title|headline)'))
            if not team_header:
                continue
                
            team_name = team_header.get_text(strip=True)
            team_abbrev = TEAM_ABBREV_MAP.get(team_name, "UNK")
            
            # Find injury rows
            table = section.find('table')
            if not table:
                continue
                
            rows = table.find_all('tr')
            
            for row in rows:
                cells = row.find_all(['td', 'th'])
                if len(cells) < 3:
                    continue
                
                # Skip header rows
                if row.find('th'):
                    continue
                
                try:
                    # ESPN typical structure: Name | Status | Comment
                    player_cell = cells[0]
                    player_link = player_cell.find('a')
                    player_name = player_link.get_text(strip=True) if player_link else player_cell.get_text(strip=True)
                    
                    # Try to extract player ID from ESPN link
                    player_espn_id = None
                    if player_link and 'href' in player_link.attrs:
                        href = player_link['href']
                        match = re.search(r'/id/(\d+)/', href)
                        if match:
                            player_espn_id = match.group(1)
                    
                    status_text = cells[1].get_text(strip=True) if len(cells) > 1 else "Unknown"
                    comment = cells[2].get_text(strip=True) if len(cells) > 2 else ""
                    
                    # Parse status
                    status = _parse_injury_status(status_text)
                    
                    # Create unique injury ID
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
                    logger.debug(f"  {player_name} ({team_abbrev}): {status} - {comment}")
                    yield injury_record
                    
                except Exception as e:
                    logger.warning(f"Error parsing injury row: {e}")
                    continue
        
        logger.info(f"Injury extraction complete! Found {injury_count} injured players")
        
        # If no injuries found, yield a marker record
        if injury_count == 0:
            logger.warning("No injuries found - ESPN page structure may have changed")
            yield {
                "injury_id": f"{capture_date}_NO_INJURIES",
                "capture_date": capture_date,
                "player_name": "_NO_INJURIES_FOUND",
                "player_espn_id": None,
                "team_name": "N/A",
                "team_abbrev": "N/A",
                "status": "N/A",
                "status_raw": "No injuries found or page structure changed",
                "injury_details": "Check ESPN page structure",
                "captured_at": captured_at,
            }
            
    except requests.RequestException as e:
        logger.error(f"Error fetching ESPN injuries: {e}")
        raise
    except Exception as e:
        logger.error(f"Error parsing ESPN injuries: {e}")
        raise


@dlt.source(name="nba_injuries")
def nba_injuries_source():
    """NBA injuries data source."""
    return [nba_injuries_resource()]


# For testing
if __name__ == "__main__":
    print("Testing NBA injuries scraper...")
    
    injuries = list(nba_injuries_resource())
    print(f"\nFound {len(injuries)} injuries:")
    
    for injury in injuries[:10]:
        print(f"  {injury['player_name']} ({injury['team_abbrev']}): {injury['status']} - {injury['injury_details'][:50]}...")
