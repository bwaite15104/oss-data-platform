"""
Alternative backfill using shufinskiy/nba_data GitHub repo.

This script clones and uses the nba_data repo to extract historical data.
The repo has scripts to download and process NBA data automatically.
"""

import subprocess
import sys
import os
from pathlib import Path
import logging
import shutil

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

NBA_DATA_REPO = "https://github.com/shufinskiy/nba_data.git"


def clone_nba_data_repo(output_dir: Path) -> Path:
    """Clone the nba_data GitHub repo."""
    repo_dir = output_dir / "nba_data"
    
    if repo_dir.exists():
        logger.info(f"Repo already exists at {repo_dir}, skipping clone")
        return repo_dir
    
    logger.info(f"Cloning nba_data repo to {repo_dir}...")
    try:
        subprocess.run(
            ["git", "clone", NBA_DATA_REPO, str(repo_dir)],
            check=True,
            capture_output=True
        )
        logger.info("✅ Repo cloned successfully")
        return repo_dir
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to clone repo: {e}")
        raise
    except FileNotFoundError:
        logger.error("git not found. Please install git or clone manually.")
        logger.info(f"Manual clone: git clone {NBA_DATA_REPO} {repo_dir}")
        raise


def run_nba_data_scripts(repo_dir: Path, season: int):
    """Run the nba_data repo scripts to extract data."""
    logger.info(f"Running nba_data scripts for season {season}...")
    
    # The repo structure may vary - we'll need to check what scripts are available
    # Common patterns: build_dataset.py, loading/ scripts, etc.
    
    scripts_dir = repo_dir / "loading"
    if not scripts_dir.exists():
        scripts_dir = repo_dir
    
    logger.info(f"Looking for scripts in {scripts_dir}")
    
    # List available scripts
    py_files = list(scripts_dir.glob("*.py"))
    logger.info(f"Found {len(py_files)} Python scripts")
    
    # For now, provide instructions
    logger.warning("nba_data repo scripts need to be run manually or configured")
    logger.info("See: https://github.com/shufinskiy/nba_data for usage instructions")
    
    return None


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Backfill using nba_data GitHub repo")
    parser.add_argument("--season", type=int, required=True, help="Season year (e.g., 2023)")
    parser.add_argument("--output-dir", type=str, default="data/nba_data", help="Output directory")
    
    args = parser.parse_args()
    
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    repo_dir = clone_nba_data_repo(output_dir)
    run_nba_data_scripts(repo_dir, args.season)
