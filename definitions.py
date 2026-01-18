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

# Import schedules
from orchestration.dagster.schedules import schedules, jobs

# Load assets from modules
all_assets = load_assets_from_modules([ingestion, transformation])

# Skip quality assets for now - requires Baselinr config that's complex to setup in Docker
# To enable: run `make generate-configs` and ensure baselinr_config.yml exists

defs = Definitions(
    assets=all_assets,
    jobs=jobs,
    schedules=schedules,
)
