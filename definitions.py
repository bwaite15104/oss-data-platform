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

from dagster import Definitions, in_process_executor, load_assets_from_modules

# Import asset modules
from orchestration.dagster.assets import ingestion, transformation, quality, ml

# Import schedules
from orchestration.dagster.schedules import schedules, jobs

# Load assets from modules
all_assets = load_assets_from_modules([ingestion, transformation, quality, ml])

# Extract resources from Baselinr definitions if quality assets are loaded
resources = {}
if hasattr(quality, 'baselinr_defs') and quality.baselinr_defs:
    # Baselinr definitions include the 'baselinr' resource
    # Definitions.resources is a dict-like object
    if hasattr(quality.baselinr_defs, 'resources'):
        # Convert to dict if it's not already
        baselinr_resources = quality.baselinr_defs.resources
        if hasattr(baselinr_resources, 'keys'):
            # It's dict-like, extract the resources
            for key in baselinr_resources.keys():
                resources[key] = baselinr_resources[key]
        elif isinstance(baselinr_resources, dict):
            resources.update(baselinr_resources)

# Default executor: in-process (serial) to avoid Docker OOM/CPU overload when
# materializing many assets (e.g. group:transformations). Override via run config
# if needed: execution.config.multiprocess.max_concurrent: 2
defs = Definitions(
    assets=all_assets,
    resources=resources if resources else None,
    jobs=jobs,
    schedules=schedules,
    executor=in_process_executor,
)
