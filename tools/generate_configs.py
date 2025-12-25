#!/usr/bin/env python3
"""
Generate tool-specific configs from ODCS source.

This tool:
1. Composes contracts if needed
2. Generates tool configs using adapters
"""

import argparse
import logging
import subprocess
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters import (
    BaselinrAdapter,
    SQLMeshAdapter,
    DagsterAdapter,
    AirbyteAdapter,
    DltAdapter,
    DataHubAdapter,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def compose_contracts_if_needed(contracts_dir: Path) -> None:
    """Compose contracts if they don't exist."""
    contracts_path = contracts_dir
    if not contracts_path.exists() or not list(contracts_path.glob("*.yml")):
        logger.info("Composing contracts...")
        result = subprocess.run(
            ["python", "contracts/composer.py", "--all"],
            cwd=Path(__file__).parent.parent,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error(f"Contract composition failed: {result.stderr}")
            raise RuntimeError("Failed to compose contracts")
        logger.info("Contracts composed successfully")


def main():
    parser = argparse.ArgumentParser(description="Generate tool configs from ODCS")
    parser.add_argument(
        "--odcs-config",
        type=str,
        default="configs/odcs/datasets.yml",
        help="Path to ODCS config file",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="configs/generated",
        help="Output directory for generated configs",
    )
    parser.add_argument(
        "--tools",
        nargs="+",
        choices=["baselinr", "sqlmesh", "dagster", "airbyte", "dlt", "datahub", "all"],
        default=["all"],
        help="Tools to generate configs for",
    )
    parser.add_argument(
        "--skip-compose",
        action="store_true",
        help="Skip contract composition",
    )
    
    args = parser.parse_args()
    
    # Change to project root
    project_root = Path(__file__).parent.parent
    os.chdir(project_root)
    
    # Compose contracts if needed
    if not args.skip_compose:
        contracts_dir = project_root / "contracts" / "contracts"
        compose_contracts_if_needed(contracts_dir)
    
    # Determine which tools to generate
    if "all" in args.tools:
        tools_to_generate = ["baselinr", "sqlmesh", "dagster", "airbyte", "dlt", "datahub"]
    else:
        tools_to_generate = args.tools
    
    # Map tool names to adapter classes
    adapters = {
        "baselinr": (BaselinrAdapter, "baselinr_config.yml"),
        "sqlmesh": (SQLMeshAdapter, "config.yaml"),
        "dagster": (DagsterAdapter, "dagster_assets.yml"),
        "airbyte": (AirbyteAdapter, "connections.json"),
        "dlt": (DltAdapter, "pipelines.py"),
        "datahub": (DataHubAdapter, "ingestion.yml"),
    }
    
    output_dir = Path(args.output_dir)
    odcs_config_path = Path(args.odcs_config)
    
    # Merge ODCS config files
    # In a real implementation, you'd merge connections.yml, datasets.yml, quality.yml
    # For now, we'll use datasets.yml as the main file and expect it to reference others
    
    for tool_name in tools_to_generate:
        if tool_name not in adapters:
            logger.warning(f"Unknown tool: {tool_name}")
            continue
        
        adapter_class, output_filename = adapters[tool_name]
        
        try:
            adapter = adapter_class(str(odcs_config_path))
            
            if not adapter.validate():
                logger.error(f"{tool_name}: Invalid ODCS config")
                continue
            
            output_path = output_dir / tool_name / output_filename
            adapter.generate_config(str(output_path))
            logger.info(f"✅ {tool_name}: Generated {output_path}")
            
        except Exception as e:
            logger.error(f"❌ {tool_name}: {e}")
            continue
    
    logger.info("Config generation complete")


if __name__ == "__main__":
    import os
    sys.exit(main())

