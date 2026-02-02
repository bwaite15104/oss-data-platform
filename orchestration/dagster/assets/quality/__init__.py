"""Quality assets."""

from pathlib import Path
from dagster import asset

# Check if Baselinr config exists before loading (Docker /app or project-relative)
config_path = Path("/app/configs/generated/baselinr/baselinr_config.yml")
if not config_path.exists():
    # Local dev: resolve from this file (orchestration/dagster/assets/quality) -> project root
    _project_root = Path(__file__).resolve().parent.parent.parent.parent.parent
    config_path = _project_root / "configs" / "generated" / "baselinr" / "baselinr_config.yml"

baselinr_defs = None
quality_assets = []

if config_path.exists():
    from baselinr.integrations.dagster import build_baselinr_definitions
    
    # Build Baselinr definitions from generated config
    baselinr_defs = build_baselinr_definitions(
        config_path=str(config_path),
        asset_prefix="quality",
        job_name="quality_checks",
        enable_sensor=True,
    )
    
    # Export Baselinr assets
    quality_assets = baselinr_defs.assets if hasattr(baselinr_defs, 'assets') else []
else:
    # Return empty list if config doesn't exist yet
    # Run `make generate-configs` to create the config file
    quality_assets = []

