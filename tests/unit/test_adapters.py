"""Tests for adapters."""

import pytest
from pathlib import Path
import yaml

from adapters.base import ConfigAdapter
from adapters.baselinr_adapter import BaselinrAdapter
from adapters.odcs_models import ODCSConfig, ODCSConnection, ODCSDataset


@pytest.fixture
def sample_odcs_config(tmp_path):
    """Create a sample ODCS config file."""
    config_file = tmp_path / "config.yml"
    config_dict = {
        "environment": "test",
        "connections": {
            "postgres_warehouse": {
                "type": "postgres",
                "host": "localhost",
                "port": 5432,
                "database": "test",
                "username": "test",
                "password": "test",
            }
        },
        "datasets": [
            {
                "name": "nba_games",
                "source": "postgres",
                "schema": "nba",
                "table": "games",
                "contract": "contracts/contracts/nba_games.yml",
            }
        ],
    }
    
    with open(config_file, 'w') as f:
        yaml.dump(config_dict, f)
    
    return config_file


@pytest.fixture
def sample_contract(tmp_path):
    """Create a sample composed contract."""
    contracts_dir = tmp_path / "contracts" / "contracts"
    contracts_dir.mkdir(parents=True)
    
    contract_file = contracts_dir / "nba_games.yml"
    contract_dict = {
        "version": "1.0",
        "name": "nba_games",
        "schema": {
            "name": "nba_games",
            "columns": [
                {
                    "name": "customer_id",
                    "type": "integer",
                    "nullable": False,
                }
            ],
        },
        "quality": {
            "validation_rules": [],
            "thresholds": {
                "drift_low": 5.0,
                "drift_medium": 15.0,
                "drift_high": 30.0,
            },
        },
        "metadata": {
            "owner": "test@example.com",
        },
    }
    
    with open(contract_file, 'w') as f:
        yaml.dump(contract_dict, f)
    
    return contract_file


def test_baselinr_adapter_validate(sample_odcs_config, sample_contract, tmp_path):
    """Test Baselinr adapter validation."""
    adapter = BaselinrAdapter(
        str(sample_odcs_config),
        contracts_dir=str(tmp_path / "contracts"),
    )
    
    assert adapter.validate()


def test_baselinr_adapter_generate(sample_odcs_config, sample_contract, tmp_path):
    """Test Baselinr adapter config generation."""
    adapter = BaselinrAdapter(
        str(sample_odcs_config),
        contracts_dir=str(tmp_path / "contracts"),
    )
    
    output_file = tmp_path / "baselinr_config.yml"
    adapter.generate_config(str(output_file))
    
    assert output_file.exists()
    
    # Verify config structure
    with open(output_file) as f:
        config = yaml.safe_load(f)
    
    assert "source" in config
    assert "storage" in config
    assert "profiling" in config

