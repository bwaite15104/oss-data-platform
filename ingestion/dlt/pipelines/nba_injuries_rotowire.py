"""
NBA Injury Data Pipeline using RotoWire scraping.

Alternative to ESPN scraper - scrapes RotoWire's injury report page.
Free data source - no API key required.
"""

import dlt
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import Iterator, Dict, Any
import logging
import re
import time

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# RotoWire Injuries URL
ROTOWIRE_INJURIES_URL = "https://www.rotowire.com/basketball/injury-report.php"

# Headers to avoid bot detection
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Team name mappings (RotoWire uses abbreviations)
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
    """Parse injury status from RotoWire text."""
    status_lower = status_text.lower()
    if "out" in status_lower:
        return "Out"
    elif "doubtful" in status_lower:
        return "Doubtful"
    elif "questionable" in status_lower:
        return "Questionable"
    elif "probable" in status_lower:
        return "Probable"
    elif "available" in status_lower or "active" in status_lower:
        return "Active"
    else:
        return status_text


@dlt.resource(
    name="injuries_rotowire",
    write_disposition="append",  # Append mode (incremental)
    primary_key="injury_id",
)
def nba_injuries_rotowire_resource() -> Iterator[Dict[str, Any]]:
    """
    Scrape current NBA injury data from RotoWire.
    
    Note: RotoWire only shows current injuries, not historical snapshots.
    This will capture today's injury reports.
    """
    logger.info(f"Fetching injury data from RotoWire: {ROTOWIRE_INJURIES_URL}")
    
    try:
        response = requests.get(ROTOWIRE_INJURIES_URL, headers=HEADERS, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        injury_count = 0
        captured_at = datetime.now().isoformat()
        capture_date = datetime.now().strftime("%Y-%m-%d")
        
        # RotoWire structure - need to find injury table/rows
        # Look for injury rows - structure may vary
        injury_rows = soup.find_all('tr', class_=re.compile(r'injury|player', re.I))
        
        # If no specific class, look for table rows with player data
        if not injury_rows:
            # Try finding tables
            tables = soup.find_all('table')
            for table in tables:
                rows = table.find_all('tr')
                for row in rows:
                    cells = row.find_all(['td', 'th'])
                    if len(cells) >= 3:
                        # Check if this looks like an injury row (has player name, status)
                        text_content = ' '.join([cell.get_text(strip=True) for cell in cells])
                        if any(keyword in text_content.lower() for keyword in ['out', 'questionable', 'doubtful', 'probable']):
                            injury_rows.append(row)
        
        # Process injury rows
        for row in injury_rows:
            cells = row.find_all(['td', 'th'])
            if len(cells) < 3:
                continue
            
            # Skip header rows
            if row.find('th'):
                continue
            
            try:
                # RotoWire typical structure: Player | Team | Status | Injury Details
                # May vary - need to inspect actual page structure
                player_cell = cells[0]
                player_name = player_cell.get_text(strip=True)
                
                # Try to find team (may be in different column)
                team_name = None
                team_abbrev = None
                for i, cell in enumerate(cells):
                    cell_text = cell.get_text(strip=True)
                    # Check if this looks like a team name/abbreviation
                    if cell_text.upper() in TEAM_ABBREV_MAP.values():
                        team_abbrev = cell_text.upper()
                        # Find team name from abbreviation
                        for full_name, abbrev in TEAM_ABBREV_MAP.items():
                            if abbrev == team_abbrev:
                                team_name = full_name
                                break
                    elif cell_text in TEAM_ABBREV_MAP:
                        team_name = cell_text
                        team_abbrev = TEAM_ABBREV_MAP.get(cell_text, "UNK")
                
                # Find status (look for Out, Questionable, etc.)
                status_text = "Unknown"
                injury_details = ""
                for cell in cells:
                    cell_text = cell.get_text(strip=True)
                    if any(keyword in cell_text.lower() for keyword in ['out', 'questionable', 'doubtful', 'probable', 'available']):
                        status_text = cell_text
                    elif len(cell_text) > 10 and 'injury' in cell_text.lower():
                        injury_details = cell_text
                
                # Parse status
                status = _parse_injury_status(status_text)
                
                # Create unique injury ID
                injury_id = f"{capture_date}_{team_abbrev or 'UNK'}_{player_name.replace(' ', '_')}"
                
                injury_record = {
                    "injury_id": injury_id,
                    "capture_date": capture_date,
                    "player_name": player_name,
                    "player_espn_id": None,  # RotoWire doesn't provide ESPN IDs
                    "team_name": team_name or "Unknown",
                    "team_abbrev": team_abbrev or "UNK",
                    "status": status,
                    "status_raw": status_text,
                    "injury_details": injury_details,
                    "captured_at": captured_at,
                }
                
                injury_count += 1
                logger.debug(f"  {player_name} ({team_abbrev or 'UNK'}): {status} - {injury_details[:50]}")
                yield injury_record
                
            except Exception as e:
                logger.warning(f"Error parsing injury row: {e}")
                continue
        
        logger.info(f"Injury extraction complete! Found {injury_count} injured players")
        
        # If no injuries found, yield a marker record
        if injury_count == 0:
            logger.warning("No injuries found - RotoWire page structure may have changed")
            yield {
                "injury_id": f"{capture_date}_NO_INJURIES_ROTOWIRE",
                "capture_date": capture_date,
                "player_name": "_NO_INJURIES_FOUND",
                "player_espn_id": None,
                "team_name": "N/A",
                "team_abbrev": "N/A",
                "status": "N/A",
                "status_raw": "No injuries found or page structure changed",
                "injury_details": "Check RotoWire page structure",
                "captured_at": captured_at,
            }
            
    except requests.RequestException as e:
        logger.error(f"Error fetching RotoWire injuries: {e}")
        raise
    except Exception as e:
        logger.error(f"Error parsing RotoWire injuries: {e}")
        raise


@dlt.source(name="nba_injuries_rotowire")
def nba_injuries_rotowire_source():
    """NBA injuries data source from RotoWire."""
    return [nba_injuries_rotowire_resource()]


# For testing
if __name__ == "__main__":
    print("Testing RotoWire injury scraper...")
    
    injuries = list(nba_injuries_rotowire_resource())
    print(f"\nFound {len(injuries)} injuries:")
    
    for injury in injuries[:10]:
        print(f"  {injury['player_name']} ({injury['team_abbrev']}): {injury['status']} - {injury['injury_details'][:50]}...")
