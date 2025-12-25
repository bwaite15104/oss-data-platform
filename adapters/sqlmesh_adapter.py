"""
SQLMesh adapter - converts ODCS contracts to SQLMesh configuration.
"""

import logging
from pathlib import Path
from typing import Any, Dict

import yaml

from .base import ConfigAdapter

logger = logging.getLogger(__name__)


class SQLMeshAdapter(ConfigAdapter):
    """Converts ODCS configs to SQLMesh format."""
    
    def generate_config(self, output_path: str) -> None:
        """Generate SQLMesh config from ODCS."""
        # Get source connection
        if not self.odcs_config.datasets:
            raise ValueError("No datasets defined in ODCS config")
        
        source_dataset = self.odcs_config.datasets[0]
        source_conn = self._convert_connection(source_dataset.source)
        
        # Build SQLMesh config
        config = {
            "gateways": {
                "default": {
                    "connection": self._convert_to_sqlmesh_connection(source_conn),
                }
            },
            "default_gateway": "default",
            "model_defaults": {
                "kind": "VIEW",
            },
        }
        
        # Write to file
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        
        logger.info(f"Generated SQLMesh config: {output_file}")
    
    def _convert_to_sqlmesh_connection(self, conn: Dict[str, Any]) -> Dict[str, Any]:
        """Convert ODCS connection to SQLMesh connection format."""
        conn_type = conn.get("type", "postgres")
        
        if conn_type == "postgres":
            return {
                "type": "postgres",
                "host": conn.get("host"),
                "port": conn.get("port"),
                "user": conn.get("username"),
                "password": conn.get("password"),
                "database": conn.get("database"),
            }
        elif conn_type == "snowflake":
            return {
                "type": "snowflake",
                "account": conn.get("account"),
                "user": conn.get("username"),
                "password": conn.get("password"),
                "warehouse": conn.get("warehouse"),
                "database": conn.get("database"),
                "role": conn.get("role"),
            }
        else:
            # Generic connection
            return conn
    
    def validate(self) -> bool:
        """Validate ODCS config for SQLMesh compatibility."""
        if not self.odcs_config.datasets:
            return False
        
        # Check that connections exist
        for dataset in self.odcs_config.datasets:
            if dataset.source not in self.odcs_config.connections:
                logger.error(f"Connection '{dataset.source}' not found")
                return False
        
        return True

