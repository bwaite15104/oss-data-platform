"""
DataHub adapter - generates DataHub ingestion configs from ODCS contracts.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List

import yaml

from .base import ConfigAdapter

logger = logging.getLogger(__name__)


class DataHubAdapter(ConfigAdapter):
    """Converts ODCS contracts to DataHub ingestion configs."""
    
    def generate_config(self, output_path: str) -> None:
        """Generate DataHub ingestion configs from ODCS."""
        sources = []
        
        for dataset in self.odcs_config.datasets:
            # Load composed contract
            contract = self._load_composed_contract(dataset.contract)
            conn = self._convert_connection(dataset.source)
            
            # Create DataHub source config
            source_config = {
                "source": {
                    "type": "postgres" if conn["type"] == "postgres" else conn["type"],
                    "config": {
                        "host_port": f"{conn.get('host')}:{conn.get('port')}",
                        "database": conn.get("database"),
                        "username": conn.get("username"),
                        "password": conn.get("password"),
                        "schema_pattern": {
                            "allow": [dataset.schema or "public"],
                        },
                        "table_pattern": {
                            "allow": [dataset.table],
                        },
                    },
                },
                "sink": {
                    "type": "datahub-rest",
                    "config": {
                        "server": "${DATAHUB_GMS_HOST}:${DATAHUB_GMS_PORT}",
                    },
                },
            }
            
            # Add metadata from contract
            if contract.metadata:
                source_config["metadata"] = {
                    "owner": contract.metadata.owner,
                    "description": contract.metadata.description,
                    "tags": contract.metadata.tags or [],
                }
            
            sources.append(source_config)
        
        # Write to file
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w') as f:
            yaml.dump(sources, f, default_flow_style=False, sort_keys=False)
        
        logger.info(f"Generated DataHub config: {output_file}")
    
    def validate(self) -> bool:
        """Validate ODCS config for DataHub compatibility."""
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

