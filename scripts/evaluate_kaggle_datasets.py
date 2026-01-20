"""Evaluate additional Kaggle datasets to see if they're additive."""

import sys
import os
from pathlib import Path
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
sys.stdout.reconfigure(encoding='utf-8')

try:
    import kaggle
    KAGGLE_AVAILABLE = True
except ImportError:
    KAGGLE_AVAILABLE = False
    logger.warning("Kaggle API not available. Install with: pip install kaggle")

def inspect_kaggle_dataset(dataset_name, download_dir):
    """Download and inspect a Kaggle dataset."""
    if not KAGGLE_AVAILABLE:
        logger.error("Kaggle API not available")
        return None
    
    download_dir = Path(download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Downloading dataset: {dataset_name}")
    api = kaggle.api
    api.dataset_download_files(dataset_name, path=str(download_dir), unzip=True)
    
    logger.info(f"Downloaded to: {download_dir}")
    
    # List all files
    csv_files = list(download_dir.glob("*.csv"))
    logger.info(f"Found {len(csv_files)} CSV files")
    
    for csv_file in csv_files:
        size_mb = csv_file.stat().st_size / (1024 * 1024)
        logger.info(f"  - {csv_file.name} ({size_mb:.1f} MB)")
    
    return csv_files

if __name__ == "__main__":
    datasets = [
        "wyattowalsh/basketball",
        "sumitrodatta/nba-aba-baa-stats"
    ]
    
    for dataset_name in datasets:
        logger.info(f"\n{'='*60}")
        logger.info(f"Evaluating dataset: {dataset_name}")
        logger.info(f"{'='*60}")
        
        # Create separate directory for each dataset
        download_dir = Path(f"data/kaggle_eval/{dataset_name.replace('/', '_')}")
        
        try:
            csv_files = inspect_kaggle_dataset(dataset_name, download_dir)
            if csv_files:
                # Inspect first CSV file
                import pandas as pd
                first_csv = csv_files[0]
                logger.info(f"\nInspecting {first_csv.name}...")
                df_sample = pd.read_csv(first_csv, nrows=100)
                logger.info(f"Shape: {df_sample.shape}")
                logger.info(f"Columns: {list(df_sample.columns)}")
                logger.info(f"\nFirst few rows:")
                print(df_sample.head())
                logger.info(f"\nSample data types:")
                print(df_sample.dtypes)
        except Exception as e:
            logger.error(f"Error evaluating {dataset_name}: {e}")
            import traceback
            traceback.print_exc()
