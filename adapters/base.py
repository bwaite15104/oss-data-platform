"""
Base adapter class for converting ODCS configs to tool-specific formats.
"""

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict

import yaml

from .odcs_models import ODCSConfig, ODCSContract

logger = logging.getLogger(__name__)


class ConfigAdapter(ABC):
    """Base class for converting ODCS configs to tool-specific formats."""
    
    def __init__(self, odcs_config_path: str, contracts_dir: str = "contracts/contracts"):
        """
        Initialize adapter.
        
        Args:
            odcs_config_path: Path to ODCS configuration file
            contracts_dir: Directory containing composed contracts
        """
        self.odcs_config_path = Path(odcs_config_path)
        self.contracts_dir = Path(contracts_dir)
        self.odcs_config = self._load_odcs()
    
    def _load_odcs(self) -> ODCSConfig:
        """Load ODCS configuration."""
        if not self.odcs_config_path.exists():
            raise FileNotFoundError(f"ODCS config file not found: {self.odcs_config_path}")
        
        with open(self.odcs_config_path, 'r') as f:
            if self.odcs_config_path.suffix in ['.yaml', '.yml']:
                config_dict = yaml.safe_load(f)
            elif self.odcs_config_path.suffix == '.json':
                config_dict = json.load(f)
            else:
                raise ValueError(f"Unsupported config file format: {self.odcs_config_path.suffix}")
        
        return ODCSConfig(**config_dict)
    
    def _load_composed_contract(self, contract_path: str) -> ODCSContract:
        """
        Load composed contract from contracts directory.
        
        Args:
            contract_path: Path to contract file (relative to contracts_dir or absolute)
            
        Returns:
            ODCSContract instance
        """
        # Handle both relative and absolute paths
        if Path(contract_path).is_absolute():
            contract_file = Path(contract_path)
        else:
            # Try relative to contracts_dir first
            contract_file = self.contracts_dir / contract_path
            if not contract_file.exists():
                # Try as-is (might be relative to project root)
                contract_file = Path(contract_path)
        
        if not contract_file.exists():
            raise FileNotFoundError(f"Contract file not found: {contract_path}")
        
        with open(contract_file, 'r') as f:
            contract_dict = yaml.safe_load(f)
        
        return ODCSContract(**contract_dict)
    
    def _convert_connection(self, conn_name: str) -> Dict[str, Any]:
        """
        Convert ODCS connection to tool-specific connection format.
        
        Args:
            conn_name: Name of connection in ODCS config
            
        Returns:
            Dictionary with connection parameters
        """
        if conn_name not in self.odcs_config.connections:
            raise ValueError(f"Connection '{conn_name}' not found in ODCS config")
        
        conn = self.odcs_config.connections[conn_name]
        return conn.model_dump(exclude_none=True)
    
    @abstractmethod
    def generate_config(self, output_path: str) -> None:
        """
        Generate tool-specific configuration from ODCS.
        
        Args:
            output_path: Path where generated config should be written
        """
        pass
    
    @abstractmethod
    def validate(self) -> bool:
        """
        Validate that ODCS config can be converted.
        
        Returns:
            True if valid, False otherwise
        """
        pass

