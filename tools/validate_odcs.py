#!/usr/bin/env python3
"""
Validate ODCS configurations and composed contracts.
"""

import argparse
import logging
import sys
from pathlib import Path

import yaml

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.odcs_models import ODCSConfig, ODCSContract

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_yaml(filepath: Path) -> dict:
    """Load YAML file."""
    with open(filepath, 'r') as f:
        return yaml.safe_load(f)


def validate_odcs_config(config_path: Path) -> bool:
    """Validate ODCS configuration file."""
    logger.info(f"Validating ODCS config: {config_path}")
    
    try:
        config_dict = load_yaml(config_path)
        
        # Load connections if separate file
        if "connections" not in config_dict:
            connections_file = config_path.parent / "connections.yml"
            if connections_file.exists():
                connections_dict = load_yaml(connections_file)
                config_dict["connections"] = connections_dict.get("connections", {})
        
        # Load datasets if separate file
        if "datasets" not in config_dict:
            datasets_file = config_path.parent / "datasets.yml"
            if datasets_file.exists():
                datasets_dict = load_yaml(datasets_file)
                config_dict["datasets"] = datasets_dict.get("datasets", [])
        
        # Validate structure
        odcs_config = ODCSConfig(**config_dict)
        
        # Validate connections
        for conn_name, conn in odcs_config.connections.items():
            if not conn.type:
                logger.error(f"Connection '{conn_name}' missing type")
                return False
        
        # Validate datasets
        for dataset in odcs_config.datasets:
            if dataset.source not in odcs_config.connections:
                logger.error(f"Dataset '{dataset.name}' references unknown connection: {dataset.source}")
                return False
        
        logger.info("✅ ODCS config is valid")
        return True
        
    except Exception as e:
        logger.error(f"❌ ODCS config validation failed: {e}")
        return False


def validate_contract(contract_path: Path) -> bool:
    """Validate composed contract file."""
    logger.info(f"Validating contract: {contract_path}")
    
    try:
        contract_dict = load_yaml(contract_path)
        contract = ODCSContract(**contract_dict)
        
        # Validate schema
        if not contract.schema.columns:
            logger.error("Contract schema has no columns")
            return False
        
        # Validate quality config
        if not contract.quality:
            logger.warning("Contract has no quality configuration")
        
        logger.info(f"✅ Contract '{contract.name}' is valid")
        return True
        
    except Exception as e:
        logger.error(f"❌ Contract validation failed: {e}")
        return False


def validate_all_contracts(contracts_dir: Path) -> bool:
    """Validate all contracts in directory."""
    if not contracts_dir.exists():
        logger.warning(f"Contracts directory not found: {contracts_dir}")
        return True  # Not an error if directory doesn't exist yet
    
    contract_files = list(contracts_dir.glob("*.yml"))
    
    if not contract_files:
        logger.warning("No contract files found")
        return True
    
    all_valid = True
    for contract_file in contract_files:
        if not validate_contract(contract_file):
            all_valid = False
    
    return all_valid


def main():
    parser = argparse.ArgumentParser(description="Validate ODCS configs and contracts")
    parser.add_argument(
        "--odcs-config",
        type=Path,
        default=Path("configs/odcs/datasets.yml"),
        help="Path to ODCS config file",
    )
    parser.add_argument(
        "--contracts-dir",
        type=Path,
        default=Path("contracts/contracts"),
        help="Directory containing composed contracts",
    )
    parser.add_argument(
        "--check-contracts",
        action="store_true",
        help="Also validate composed contracts",
    )
    
    args = parser.parse_args()
    
    # Change to project root
    project_root = Path(__file__).parent.parent
    os.chdir(project_root)
    
    # Validate ODCS config
    odcs_valid = validate_odcs_config(args.odcs_config)
    
    # Validate contracts if requested
    contracts_valid = True
    if args.check_contracts:
        contracts_valid = validate_all_contracts(args.contracts_dir)
    
    if odcs_valid and contracts_valid:
        logger.info("✅ All validations passed")
        return 0
    else:
        logger.error("❌ Validation failed")
        return 1


if __name__ == "__main__":
    import os
    sys.exit(main())

