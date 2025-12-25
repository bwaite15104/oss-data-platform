"""
Baselinr adapter - converts ODCS contracts to Baselinr YAML configuration.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List

import yaml

from .base import ConfigAdapter
from .odcs_models import ODCSContract, ODCSValidationRule

logger = logging.getLogger(__name__)


class BaselinrAdapter(ConfigAdapter):
    """Converts ODCS configs to Baselinr format."""
    
    def generate_config(self, output_path: str) -> None:
        """Generate baselinr config from ODCS."""
        # Get source connection (first dataset's source)
        if not self.odcs_config.datasets:
            raise ValueError("No datasets defined in ODCS config")
        
        source_dataset = self.odcs_config.datasets[0]
        source_conn = self._convert_connection(source_dataset.source)
        
        # Get storage connection (use postgres_storage if available, else use source)
        storage_conn_name = "postgres_storage"
        if storage_conn_name not in self.odcs_config.connections:
            storage_conn_name = source_dataset.source
        storage_conn = self._convert_connection(storage_conn_name)
        
        # Build profiling tables from datasets
        profiling_tables = []
        validation_rules = []
        
        for dataset in self.odcs_config.datasets:
            # Load composed contract
            contract = self._load_composed_contract(dataset.contract)
            
            # Add to profiling tables
            table_config = {
                "table": dataset.table,
            }
            if dataset.schema:
                table_config["schema"] = dataset.schema
            
            profiling_tables.append(table_config)
            
            # Extract validation rules from contract
            for rule in contract.quality.validation_rules:
                validation_rules.append(self._convert_validation_rule(rule, dataset.table))
        
        # Build drift detection config
        drift_config = self._build_drift_config()
        
        # Build validation config
        validation_config = self._build_validation_config(validation_rules)
        
        # Compose Baselinr config
        config = {
            "environment": self.odcs_config.environment,
            "source": source_conn,
            "storage": {
                "connection": storage_conn,
                "results_table": "baselinr_results",
                "runs_table": "baselinr_runs",
                "create_tables": True,
                "enable_expectation_learning": True,
                "learning_window_days": 30,
                "min_samples": 5,
                "enable_anomaly_detection": True,
            },
            "profiling": {
                "tables": profiling_tables,
                "default_sample_ratio": 0.1,
                "compute_histograms": True,
                "histogram_bins": 10,
            },
            "drift_detection": drift_config,
        }
        
        if validation_config:
            config["validation"] = validation_config
        
        # Write to file
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)
        
        logger.info(f"Generated Baselinr config: {output_file}")
    
    def _convert_validation_rule(self, rule: ODCSValidationRule, table_name: str) -> Dict[str, Any]:
        """Convert ODCS validation rule to Baselinr format."""
        baselinr_rule = {
            "table": table_name,
        }
        
        if rule.type == "not_null":
            baselinr_rule["type"] = "not_null"
            if rule.column:
                baselinr_rule["column"] = rule.column
            elif rule.columns:
                baselinr_rule["columns"] = rule.columns
        elif rule.type == "format":
            baselinr_rule["type"] = "format"
            baselinr_rule["column"] = rule.column
            if rule.pattern:
                baselinr_rule["pattern"] = rule.pattern
        elif rule.type == "unique":
            baselinr_rule["type"] = "unique"
            if rule.column:
                baselinr_rule["column"] = rule.column
            elif rule.columns:
                baselinr_rule["columns"] = rule.columns
        elif rule.type == "range":
            baselinr_rule["type"] = "range"
            baselinr_rule["column"] = rule.column
            if rule.min_value is not None:
                baselinr_rule["min"] = rule.min_value
            if rule.max_value is not None:
                baselinr_rule["max"] = rule.max_value
        elif rule.type == "enum":
            baselinr_rule["type"] = "enum"
            baselinr_rule["column"] = rule.column
            if rule.enum_values:
                baselinr_rule["values"] = rule.enum_values
        elif rule.type == "referential_integrity":
            baselinr_rule["type"] = "referential_integrity"
            baselinr_rule["column"] = rule.column
            baselinr_rule["reference_table"] = rule.reference_table
            baselinr_rule["reference_column"] = rule.reference_column
        
        return baselinr_rule
    
    def _build_drift_config(self) -> Dict[str, Any]:
        """Build drift detection configuration."""
        # Get thresholds from first dataset's contract or global config
        thresholds = {
            "low_threshold": 5.0,
            "medium_threshold": 15.0,
            "high_threshold": 30.0,
        }
        
        if self.odcs_config.quality:
            if self.odcs_config.quality.thresholds:
                thresholds["low_threshold"] = self.odcs_config.quality.thresholds.drift_low
                thresholds["medium_threshold"] = self.odcs_config.quality.thresholds.drift_medium
                thresholds["high_threshold"] = self.odcs_config.quality.thresholds.drift_high
        
        return {
            "strategy": "absolute_threshold",
            "absolute_threshold": thresholds,
        }
    
    def _build_validation_config(self, validation_rules: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Build validation configuration."""
        if not validation_rules:
            return {}
        
        return {
            "enabled": True,
            "rules": validation_rules,
            "fail_on_error": True,
        }
    
    def validate(self) -> bool:
        """Validate ODCS config for baselinr compatibility."""
        required = ["connections", "datasets"]
        
        if not all(hasattr(self.odcs_config, key) for key in required):
            return False
        
        if not self.odcs_config.datasets:
            logger.warning("No datasets defined")
            return False
        
        # Validate that contract files exist
        for dataset in self.odcs_config.datasets:
            try:
                self._load_composed_contract(dataset.contract)
            except FileNotFoundError:
                logger.error(f"Contract file not found: {dataset.contract}")
                return False
        
        return True

