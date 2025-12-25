"""
dlt adapter - generates dlt pipeline configs from ODCS.
"""

import logging
from pathlib import Path
from typing import Any, Dict

from .base import ConfigAdapter

logger = logging.getLogger(__name__)


class DltAdapter(ConfigAdapter):
    """Converts ODCS configs to dlt pipeline format."""
    
    def generate_config(self, output_path: str) -> None:
        """Generate dlt pipeline configs from ODCS."""
        pipelines = []
        
        for dataset in self.odcs_config.datasets:
            conn = self._convert_connection(dataset.source)
            
            pipeline_config = {
                "name": f"{dataset.name}_pipeline",
                "destination": self._convert_to_dlt_destination(conn),
                "tables": [
                    {
                        "name": dataset.table,
                        "schema": dataset.schema or "public",
                    }
                ],
            }
            
            pipelines.append(pipeline_config)
        
        # Write Python pipeline file
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        pipeline_code = self._generate_pipeline_code(pipelines)
        
        with open(output_file, 'w') as f:
            f.write(pipeline_code)
        
        logger.info(f"Generated dlt pipeline: {output_file}")
    
    def _convert_to_dlt_destination(self, conn: Dict[str, Any]) -> Dict[str, Any]:
        """Convert connection to dlt destination config."""
        if conn["type"] == "postgres":
            return {
                "type": "postgres",
                "credentials": {
                    "host": conn.get("host"),
                    "port": conn.get("port"),
                    "database": conn.get("database"),
                    "username": conn.get("username"),
                    "password": conn.get("password"),
                },
            }
        else:
            return {"type": conn["type"], "credentials": conn}
    
    def _generate_pipeline_code(self, pipelines: list) -> str:
        """Generate Python code for dlt pipelines."""
        code = '''"""
dlt pipelines generated from ODCS configuration.
"""

import dlt

'''
        
        for pipeline in pipelines:
            code += f'''
@pipeline(name="{pipeline['name']}")
def {pipeline['name']}():
    """Pipeline for {pipeline['name']}"""
    # TODO: Implement source extraction
    # data = extract_from_source()
    # load(data, destination="{pipeline['destination']['type']}")
    pass

'''
        
        return code
    
    def validate(self) -> bool:
        """Validate ODCS config for dlt compatibility."""
        if not self.odcs_config.datasets:
            return False
        
        for dataset in self.odcs_config.datasets:
            if dataset.source not in self.odcs_config.connections:
                return False
        
        return True

