"""
Dagster adapter - generates Dagster asset definitions from ODCS contracts.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List

import yaml

from .base import ConfigAdapter

logger = logging.getLogger(__name__)


class DagsterAdapter(ConfigAdapter):
    """Converts ODCS configs to Dagster asset definitions."""
    
    def generate_config(self, output_path: str) -> None:
        """Generate Dagster asset definitions from ODCS."""
        assets = []
        
        for dataset in self.odcs_config.datasets:
            # Load composed contract
            contract = self._load_composed_contract(dataset.contract)
            
            # Create asset definition
            asset = {
                "key": f"{dataset.name}_asset",
                "description": contract.metadata.description or f"Asset for {dataset.name}",
                "metadata": {
                    "owner": contract.metadata.owner or dataset.owner,
                    "table": dataset.table,
                    "schema": dataset.schema or "public",
                },
            }
            
            assets.append(asset)
        
        # Create Dagster config structure
        config = {
            "assets": assets,
            "resources": {
                "postgres": {
                    "config": {
                        "connection_string": self._build_connection_string(
                            self.odcs_config.datasets[0].source
                        ),
                    }
                }
            },
        }
        
        # Write to file
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        
        logger.info(f"Generated Dagster config: {output_file}")
    
    def _build_connection_string(self, conn_name: str) -> str:
        """Build connection string from connection config."""
        conn = self._convert_connection(conn_name)
        
        if conn["type"] == "postgres":
            return f"postgresql://{conn['username']}:{conn['password']}@{conn['host']}:{conn['port']}/{conn['database']}"
        else:
            # Return placeholder for other types
            return f"{conn['type']}://..."
    
    def validate(self) -> bool:
        """Validate ODCS config for Dagster compatibility."""
        if not self.odcs_config.datasets:
            return False
        
        # Validate contracts exist
        for dataset in self.odcs_config.datasets:
            try:
                self._load_composed_contract(dataset.contract)
            except FileNotFoundError:
                logger.error(f"Contract file not found: {dataset.contract}")
                return False
        
        return True

