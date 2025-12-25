"""
Airbyte adapter - generates Airbyte connection configs from ODCS.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict

from .base import ConfigAdapter

logger = logging.getLogger(__name__)


class AirbyteAdapter(ConfigAdapter):
    """Converts ODCS configs to Airbyte connection format."""
    
    def generate_config(self, output_path: str) -> None:
        """Generate Airbyte connection configs from ODCS."""
        connections = []
        
        for dataset in self.odcs_config.datasets:
            # Create Airbyte connection config
            conn = self._convert_connection(dataset.source)
            
            connection_config = {
                "name": f"{dataset.name}_connection",
                "source": {
                    "sourceDefinitionId": self._get_source_definition_id(conn["type"]),
                    "connectionConfiguration": self._convert_to_airbyte_source_config(conn),
                },
                "destination": {
                    "destinationDefinitionId": self._get_destination_definition_id("postgres"),
                    "connectionConfiguration": self._convert_to_airbyte_dest_config(conn),
                },
                "schedule": {
                    "units": 24,
                    "timeUnit": "hours",
                },
                "streamPrefix": dataset.schema or "public",
            }
            
            connections.append(connection_config)
        
        # Write to file
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w') as f:
            json.dump(connections, f, indent=2)
        
        logger.info(f"Generated Airbyte config: {output_file}")
    
    def _get_source_definition_id(self, source_type: str) -> str:
        """Get Airbyte source definition ID."""
        # Placeholder IDs - in real implementation, these would be looked up
        source_ids = {
            "postgres": "postgres-source-id",
            "snowflake": "snowflake-source-id",
            "api": "http-source-id",
        }
        return source_ids.get(source_type, "generic-source-id")
    
    def _get_destination_definition_id(self, dest_type: str) -> str:
        """Get Airbyte destination definition ID."""
        dest_ids = {
            "postgres": "postgres-dest-id",
            "snowflake": "snowflake-dest-id",
        }
        return dest_ids.get(dest_type, "postgres-dest-id")
    
    def _convert_to_airbyte_source_config(self, conn: Dict[str, Any]) -> Dict[str, Any]:
        """Convert connection to Airbyte source config."""
        if conn["type"] == "postgres":
            return {
                "host": conn.get("host"),
                "port": conn.get("port"),
                "database": conn.get("database"),
                "username": conn.get("username"),
                "password": conn.get("password"),
                "schemas": [conn.get("schema", "public")],
            }
        else:
            return conn
    
    def _convert_to_airbyte_dest_config(self, conn: Dict[str, Any]) -> Dict[str, Any]:
        """Convert connection to Airbyte destination config."""
        if conn["type"] == "postgres":
            return {
                "host": conn.get("host"),
                "port": conn.get("port"),
                "database": conn.get("database"),
                "username": conn.get("username"),
                "password": conn.get("password"),
                "schema": conn.get("schema", "public"),
            }
        else:
            return conn
    
    def validate(self) -> bool:
        """Validate ODCS config for Airbyte compatibility."""
        if not self.odcs_config.datasets:
            return False
        
        for dataset in self.odcs_config.datasets:
            if dataset.source not in self.odcs_config.connections:
                return False
        
        return True

