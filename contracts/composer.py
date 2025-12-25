#!/usr/bin/env python3
"""
Contract Composer - Combine schemas and quality rules into complete ODCS contracts.

This tool composes complete ODCS contracts from modular components:
- Schema definitions from contracts/schemas/
- Quality rules from contracts/quality/
- Metadata (owner, lineage, SLA)
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.odcs_models import ODCSContract, ODCSSchema, ODCSQualityConfig, ODCSMetadata

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_yaml(filepath: Path) -> Dict[str, Any]:
    """Load YAML file."""
    if not filepath.exists():
        raise FileNotFoundError(f"File not found: {filepath}")
    
    with open(filepath, 'r') as f:
        return yaml.safe_load(f)


def save_yaml(data: Dict[str, Any], filepath: Path) -> None:
    """Save data to YAML file."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    
    with open(filepath, 'w') as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def load_schema(schema_name: str, schemas_dir: Path) -> Dict[str, Any]:
    """Load schema definition."""
    schema_file = schemas_dir / f"{schema_name}.yml"
    if not schema_file.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_file}")
    
    return load_yaml(schema_file)


def load_quality(quality_name: str, quality_dir: Path) -> Dict[str, Any]:
    """Load quality rules."""
    quality_file = quality_dir / f"{quality_name}.yml"
    if not quality_file.exists():
        raise FileNotFoundError(f"Quality file not found: {quality_file}")
    
    return load_yaml(quality_file)


def compose_contract(
    schema_name: str,
    quality_rules_name: str,
    quality_thresholds_name: Optional[str],
    output_name: str,
    metadata: Optional[Dict[str, Any]] = None,
    schemas_dir: Path = Path("contracts/schemas"),
    quality_dir: Path = Path("contracts/quality"),
    contracts_dir: Path = Path("contracts/contracts"),
) -> Path:
    """
    Compose a complete ODCS contract from components.
    
    Args:
        schema_name: Name of schema file (without .yml)
        quality_rules_name: Name of quality rules file (without .yml)
        quality_thresholds_name: Name of quality thresholds file (optional)
        output_name: Name for output contract file
        metadata: Additional metadata to include
        schemas_dir: Directory containing schemas
        quality_dir: Directory containing quality rules
        contracts_dir: Directory to write composed contracts
        
    Returns:
        Path to created contract file
    """
    # Load schema
    logger.info(f"Loading schema: {schema_name}")
    schema_data = load_schema(schema_name, schemas_dir)
    
    # Load quality rules
    logger.info(f"Loading quality rules: {quality_rules_name}")
    quality_rules_data = load_quality(quality_rules_name, quality_dir)
    
    # Load quality thresholds if specified
    thresholds_data = {}
    if quality_thresholds_name:
        logger.info(f"Loading quality thresholds: {quality_thresholds_name}")
        thresholds_data = load_quality(quality_thresholds_name, quality_dir)
    
    # Merge quality config
    quality_config = {
        "validation_rules": quality_rules_data.get("validation_rules", []),
        "thresholds": thresholds_data.get("thresholds", quality_rules_data.get("thresholds", {})),
        "profiling_enabled": quality_rules_data.get("profiling", {}).get("enabled", True),
        "profiling_schedule": quality_rules_data.get("profiling", {}).get("schedule"),
        "drift_detection_enabled": quality_rules_data.get("drift_detection", {}).get("enabled", True),
        "drift_detection_strategy": quality_rules_data.get("drift_detection", {}).get("strategy", "absolute_threshold"),
    }
    
    # Prepare metadata
    metadata_config = metadata or {}
    if "owner" not in metadata_config:
        metadata_config["owner"] = "data-engineering@company.com"
    
    # Compose complete contract
    contract = {
        "version": "1.0",
        "name": output_name,
        "schema": {
            "name": schema_data.get("name", output_name),
            "columns": schema_data.get("columns", []),
            "primary_key": schema_data.get("primary_key"),
            "indexes": schema_data.get("indexes"),
            "description": schema_data.get("description"),
        },
        "quality": quality_config,
        "metadata": metadata_config,
    }
    
    # Validate contract structure
    try:
        validated_contract = ODCSContract(**contract)
        contract_dict = validated_contract.model_dump(exclude_none=True)
    except Exception as e:
        logger.error(f"Contract validation failed: {e}")
        raise ValueError(f"Invalid contract structure: {e}")
    
    # Write contract
    output_file = contracts_dir / f"{output_name}.yml"
    logger.info(f"Writing contract to: {output_file}")
    save_yaml(contract_dict, output_file)
    
    logger.info(f"Successfully composed contract: {output_file}")
    return output_file


def compose_all(
    schemas_dir: Path = Path("contracts/schemas"),
    quality_dir: Path = Path("contracts/quality"),
    contracts_dir: Path = Path("contracts/contracts"),
) -> List[Path]:
    """
    Compose all schemas into contracts.
    
    Args:
        schemas_dir: Directory containing schemas
        quality_dir: Directory containing quality rules
        contracts_dir: Directory to write composed contracts
        
    Returns:
        List of created contract file paths
    """
    created_contracts = []
    
    # Find all schema files
    schema_files = list(schemas_dir.glob("*.yml"))
    
    if not schema_files:
        logger.warning(f"No schema files found in {schemas_dir}")
        return created_contracts
    
    # Default quality files
    default_rules = "rules"
    default_thresholds = "thresholds"
    
    for schema_file in schema_files:
        schema_name = schema_file.stem
        
        # Check if quality files exist
        if not (quality_dir / f"{default_rules}.yml").exists():
            logger.warning(f"Quality rules file not found: {quality_dir}/{default_rules}.yml")
            continue
        
        try:
            output_path = compose_contract(
                schema_name=schema_name,
                quality_rules_name=default_rules,
                quality_thresholds_name=default_thresholds if (quality_dir / f"{default_thresholds}.yml").exists() else None,
                output_name=schema_name,
                schemas_dir=schemas_dir,
                quality_dir=quality_dir,
                contracts_dir=contracts_dir,
            )
            created_contracts.append(output_path)
        except Exception as e:
            logger.error(f"Failed to compose contract for {schema_name}: {e}")
    
    return created_contracts


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Compose ODCS contracts from schemas and quality rules"
    )
    parser.add_argument(
        "--schema",
        type=str,
        help="Schema name (without .yml extension)",
    )
    parser.add_argument(
        "--quality",
        type=str,
        default="rules",
        help="Quality rules name (default: rules)",
    )
    parser.add_argument(
        "--thresholds",
        type=str,
        help="Quality thresholds name (optional)",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output contract name (default: same as schema)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Compose all schemas",
    )
    parser.add_argument(
        "--schemas-dir",
        type=Path,
        default=Path("contracts/schemas"),
        help="Schemas directory",
    )
    parser.add_argument(
        "--quality-dir",
        type=Path,
        default=Path("contracts/quality"),
        help="Quality directory",
    )
    parser.add_argument(
        "--contracts-dir",
        type=Path,
        default=Path("contracts/contracts"),
        help="Output contracts directory",
    )
    parser.add_argument(
        "--owner",
        type=str,
        help="Contract owner (email)",
    )
    
    args = parser.parse_args()
    
    if args.all:
        # Compose all contracts
        created = compose_all(
            schemas_dir=args.schemas_dir,
            quality_dir=args.quality_dir,
            contracts_dir=args.contracts_dir,
        )
        logger.info(f"Composed {len(created)} contracts")
        return 0
    
    if not args.schema:
        parser.error("--schema is required (or use --all)")
    
    output_name = args.output or args.schema
    
    metadata = {}
    if args.owner:
        metadata["owner"] = args.owner
    
    try:
        compose_contract(
            schema_name=args.schema,
            quality_rules_name=args.quality,
            quality_thresholds_name=args.thresholds,
            output_name=output_name,
            metadata=metadata if metadata else None,
            schemas_dir=args.schemas_dir,
            quality_dir=args.quality_dir,
            contracts_dir=args.contracts_dir,
        )
        return 0
    except Exception as e:
        logger.error(f"Failed to compose contract: {e}")
        return 1


if __name__ == "__main__":
    import os
    os.chdir(Path(__file__).parent.parent)
    sys.exit(main())

