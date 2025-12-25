"""Quality assets."""

from dagster import asset
from baselinr.integrations.dagster import build_baselinr_definitions

# Build Baselinr definitions from generated config
baselinr_defs = build_baselinr_definitions(
    config_path="configs/generated/baselinr/baselinr_config.yml",
    asset_prefix="quality",
    job_name="quality_checks",
    enable_sensor=True,
)

# Export Baselinr assets
quality_assets = baselinr_defs.assets if hasattr(baselinr_defs, 'assets') else []

