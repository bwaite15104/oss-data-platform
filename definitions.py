"""
Dagster definitions for local development.

This file is for running Dagster locally (outside Docker).
Use: dagster dev -f definitions.py
"""

import sys
from pathlib import Path

# Add project directories to path for local development
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "orchestration" / "dagster"))

from dagster import Definitions, load_assets_from_modules

# Import asset modules
from orchestration.dagster.assets import ingestion, transformation

# Load assets from modules (skip quality for local dev - requires baselinr config)
all_assets = load_assets_from_modules([ingestion, transformation])

# Try to load quality assets if baselinr config exists
try:
    from orchestration.dagster.assets import quality
    quality_assets = load_assets_from_modules([quality])
    all_assets = all_assets + quality_assets
except Exception as e:
    print(f"Note: Quality assets not loaded (run 'make generate-configs' first): {e}")

defs = Definitions(
    assets=all_assets,
)
