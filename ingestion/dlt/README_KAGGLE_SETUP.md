# Kaggle Dataset Setup Guide

This guide explains how to download and load the Kaggle NBA historical dataset.

## Dataset
- **URL**: https://www.kaggle.com/datasets/eoinamoore/historical-nba-data-and-player-box-scores
- **Description**: Historical NBA player box scores from 1949 to present
- **Format**: CSV files
- **Free**: Yes (requires Kaggle account)

## Setup Kaggle API

### Step 1: Create Kaggle Account
1. Go to https://www.kaggle.com and create an account (if you don't have one)
2. Sign in to your account

### Step 2: Get API Credentials
1. Go to https://www.kaggle.com/settings
2. Scroll down to "API" section
3. Click "Create New Token"
4. This downloads a `kaggle.json` file

### Step 3: Install Credentials

**Windows:**
```powershell
# Create the .kaggle directory if it doesn't exist
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.kaggle"

# Copy kaggle.json to the directory
# (Replace <path-to-downloaded-file> with where you saved kaggle.json)
Copy-Item "<path-to-downloaded-file>\kaggle.json" "$env:USERPROFILE\.kaggle\kaggle.json"
```

**macOS/Linux:**
```bash
# Create the .kaggle directory
mkdir -p ~/.kaggle

# Copy kaggle.json to the directory
# (Replace <path-to-downloaded-file> with where you saved kaggle.json)
cp <path-to-downloaded-file>/kaggle.json ~/.kaggle/kaggle.json

# Set proper permissions (required on Linux/macOS)
chmod 600 ~/.kaggle/kaggle.json
```

### Step 4: Verify Setup
```bash
# Test the API
kaggle datasets list --search "nba"
```

## Usage

### Option 1: Inspect Dataset First (Recommended)
```bash
cd oss-data-platform
python scripts/download_kaggle_nba.py --inspect-only
```

This will:
- Download the dataset
- Show you the column structure
- Display sample rows
- **NOT** load to database (safe to run)

### Option 2: Download and Load Full Dataset
```bash
cd oss-data-platform

# Set environment variables (if not already set)
. .\scripts\setup_local_backfill.ps1  # Windows PowerShell
# OR
source scripts/setup_local_backfill.sh  # macOS/Linux

# Download and load the dataset
python scripts/download_kaggle_nba.py --table boxscores
```

### Option 3: Load Specific Season
```bash
python scripts/download_kaggle_nba.py --table boxscores --season 2023-24
```

## What the Script Does

1. **Downloads** the dataset from Kaggle using the API
2. **Inspects** the dataset structure (columns, data types, sample rows)
3. **Maps** Kaggle columns to our `raw_dev.boxscores` schema
4. **Filters** out existing records (incremental loading)
5. **Loads** data into PostgreSQL database

## Column Mapping

The script automatically maps common column names:
- `GAME_ID` / `game_id` → `game_id`
- `PLAYER_ID` / `player_id` → `player_id`
- `GAME_DATE` / `game_date` / `DATE` → `game_date`
- `PLAYER_NAME` / `player_name` / `NAME` → `player_name`
- `PTS` / `points` → `points`
- `AST` / `assists` → `assists`
- `REB` / `rebounds` → `rebounds_total`
- And many more...

## Troubleshooting

### Error: "Could not find kaggle.json"
- Make sure you've downloaded the token from Kaggle settings
- Verify the file is in the correct location:
  - Windows: `C:\Users\<username>\.kaggle\kaggle.json`
  - macOS/Linux: `~/.kaggle/kaggle.json`
- On Linux/macOS, ensure permissions are correct: `chmod 600 ~/.kaggle/kaggle.json`

### Error: "403 Forbidden" or "Unauthorized"
- Your API token may have expired
- Go to Kaggle settings and create a new token
- Replace the old `kaggle.json` with the new one

### Error: "Dataset not found"
- Verify the dataset name is correct: `eoinamoore/historical-nba-data-and-player-box-scores`
- Check that you've accepted the dataset's terms of use on Kaggle

### Column Mapping Issues
- Run with `--inspect-only` first to see the actual column names
- The script will show you what columns are available
- We can adjust the mapping if needed

## Next Steps

After loading the data:
1. Verify data was loaded:
   ```sql
   SELECT COUNT(*) FROM raw_dev.boxscores WHERE game_date < '2025-01-01';
   ```

2. Run SQLMesh transformations to update features:
   ```bash
   make sqlmesh-plan
   make sqlmesh-run
   ```

3. Retrain ML models with the new historical data
