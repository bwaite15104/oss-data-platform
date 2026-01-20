# Running Historical Backfill Locally

This guide explains how to run the historical NBA backfill pipeline locally with Chrome (instead of in Docker).

## Prerequisites

### 1. Install Chrome and ChromeDriver

**Windows:**
```powershell
# Chrome should already be installed. Check version:
chrome --version

# Download ChromeDriver matching your Chrome version:
# 1. Check Chrome version: chrome://version/
# 2. Download from: https://googlechromelabs.github.io/chrome-for-testing/
# 3. Extract chromedriver.exe to a folder in your PATH (e.g., C:\Windows\System32)
#    OR add the folder to your PATH environment variable
```

**macOS:**
```bash
# Install ChromeDriver via Homebrew
brew install --cask chromedriver

# Or download manually from:
# https://googlechromelabs.github.io/chrome-for-testing/
```

**Linux:**
```bash
# Install Chrome
wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | sudo apt-key add -
sudo sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list'
sudo apt-get update
sudo apt-get install -y google-chrome-stable

# Install ChromeDriver
CHROMEDRIVER_VERSION=$(curl -s "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json" | python3 -c "import sys, json; data = json.load(sys.stdin); print(data['channels']['Stable']['version'])")
wget "https://storage.googleapis.com/chrome-for-testing-public/${CHROMEDRIVER_VERSION}/linux64/chromedriver-linux64.zip"
unzip chromedriver-linux64.zip
sudo mv chromedriver-linux64/chromedriver /usr/local/bin/chromedriver
sudo chmod +x /usr/local/bin/chromedriver
```

### 2. Install Python Dependencies

```bash
cd oss-data-platform
pip install -r requirements.txt
```

### 3. Ensure PostgreSQL is Running

Make sure your PostgreSQL database is accessible. If using Docker:

```bash
# Start PostgreSQL container
docker-compose up -d postgres

# Or if running PostgreSQL locally, ensure it's running on localhost:5432
```

## Configuration

### Set dlt PostgreSQL Credentials

dlt needs PostgreSQL connection credentials. Set them via environment variables:

**Windows PowerShell:**
```powershell
$env:NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__HOST="localhost"
$env:NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__PORT="5432"
$env:NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__DATABASE="nba_analytics"
$env:NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__USERNAME="postgres"
$env:NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__PASSWORD="postgres"
```

**Windows CMD:**
```cmd
set NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__HOST=localhost
set NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__PORT=5432
set NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__DATABASE=nba_analytics
set NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__USERNAME=postgres
set NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__PASSWORD=postgres
```

**macOS/Linux:**
```bash
export NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__HOST=localhost
export NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__PORT=5432
export NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__DATABASE=nba_analytics
export NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__USERNAME=postgres
export NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__PASSWORD=postgres
```

**Or create a `.env` file** (if using python-dotenv):
```env
NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__HOST=localhost
NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__PORT=5432
NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__DATABASE=nba_analytics
NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__USERNAME=postgres
NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__PASSWORD=postgres
```

## Running the Backfill

### Quick Start (Windows PowerShell)

```powershell
cd oss-data-platform

# Set environment variables
. .\scripts\setup_local_backfill.ps1

# Run backfill
python scripts/run_historical_backfill.py --season 2023-24 --team LAL --limit 5
```

### Quick Start (macOS/Linux)

```bash
cd oss-data-platform

# Set environment variables
source scripts/setup_local_backfill.sh

# Run backfill
python scripts/run_historical_backfill.py --season 2023-24 --team LAL --limit 5
```

### Option 1: Using the Standalone Script (Manual Setup)

```bash
cd oss-data-platform

# Set environment variables manually (see Configuration section above)
# Then run:
python scripts/run_historical_backfill.py --season 2023-24 --team LAL --limit 5
```

### Option 2: Direct Python Script

```python
import os
import dlt

# Set credentials (or use environment variables)
os.environ['NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__HOST'] = 'localhost'
os.environ['NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__PORT'] = '5432'
os.environ['NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__DATABASE'] = 'nba_analytics'
os.environ['NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__USERNAME'] = 'postgres'
os.environ['NBA_HISTORICAL_BACKFILL__DESTINATION__POSTGRES__CREDENTIALS__PASSWORD'] = 'postgres'

from ingestion.dlt.pipelines.nba_historical_backfill import nba_historical_backfill

pipeline = dlt.pipeline(
    pipeline_name="nba_historical_backfill",
    destination="postgres",
    dataset_name="nba_analytics"
)

load_info = pipeline.run(
    nba_historical_backfill(
        season="2023-24",
        team="LAL",  # Optional: specific team
        skip_existing=True,
        limit=5  # Optional: limit for testing
    )
)

print(load_info)
```

### Option 3: Via Dagster Asset (Local)

If running Dagster locally:

```bash
dagster asset materialize \
  -m orchestration.dagster.definitions \
  --select nba_historical_backfill \
  --config '{"ops": {"nba_historical_backfill": {"config": {"season": "2023-24", "team": "LAL", "limit": 5}}}}'
```

## Troubleshooting

### Chrome/ChromeDriver Issues

**Error: "chromedriver not found"**
- Ensure ChromeDriver is in your PATH
- Or set `CHROMEDRIVER_PATH` environment variable to the full path

**Error: "Chrome version mismatch"**
- Download ChromeDriver matching your Chrome version
- Check Chrome version: `chrome --version` (or `google-chrome --version` on Linux)
- Download matching ChromeDriver from: https://googlechromelabs.github.io/chrome-for-testing/

**Error: "session not created"**
- This usually means ChromeDriver version doesn't match Chrome version
- Re-download the correct ChromeDriver version

### Database Connection Issues

**Error: "connection refused"**
- Ensure PostgreSQL is running
- Check host/port are correct (localhost:5432 for local, or check docker-compose.yml for Docker)
- If using Docker, ensure the container is running: `docker ps`

**Error: "authentication failed"**
- Verify username/password match your PostgreSQL setup
- Check docker-compose.yml for the correct credentials

### Import Errors

**Error: "basketball-reference-scraper not installed"**
```bash
pip install basketball-reference-scraper
```

**Error: "selenium not installed"**
```bash
pip install selenium
```

## Notes

- **Local execution**: The Chrome patching in the pipeline only applies in Docker. Locally, Chrome will run normally (not headless).
- **Rate limiting**: The scraper includes 2-second delays between games to be respectful to Basketball Reference.
- **Incremental loading**: The pipeline automatically skips games that already exist in the database.
- **Time estimate**: Backfilling a full season (30 teams × ~82 games) takes ~1.5 hours due to rate limiting.

## Example: Backfill Full Season

```bash
# Backfill entire 2023-24 season (will take ~1.5 hours)
python scripts/run_historical_backfill.py --season 2023-24

# Or backfill specific team first to test
python scripts/run_historical_backfill.py --season 2023-24 --team LAL
```
