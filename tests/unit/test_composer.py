"""Tests for contract composer."""

import pytest
from pathlib import Path
import yaml

from contracts.composer import compose_contract, load_schema, load_quality


def test_load_schema(tmp_path):
    """Test loading schema file."""
    schema_file = tmp_path / "test_schema.yml"
    schema_file.write_text("""
name: test
columns:
  - name: id
    type: integer
    nullable: false
""")
    
    schema = load_schema("test_schema", tmp_path)
    assert schema["name"] == "test"
    assert len(schema["columns"]) == 1


def test_compose_contract(tmp_path):
    """Test composing a contract."""
    schemas_dir = tmp_path / "schemas"
    quality_dir = tmp_path / "quality"
    contracts_dir = tmp_path / "contracts"
    
    schemas_dir.mkdir()
    quality_dir.mkdir()
    contracts_dir.mkdir()
    
    # Create schema
    schema_file = schemas_dir / "test.yml"
    schema_file.write_text("""
name: test
columns:
  - name: id
    type: integer
    nullable: false
""")
    
    # Create quality rules
    quality_file = quality_dir / "rules.yml"
    quality_file.write_text("""
validation_rules:
  - type: not_null
    columns: [id]
""")
    
    # Compose contract
    output_path = compose_contract(
        schema_name="test",
        quality_rules_name="rules",
        quality_thresholds_name=None,
        output_name="test",
        schemas_dir=schemas_dir,
        quality_dir=quality_dir,
        contracts_dir=contracts_dir,
    )
    
    assert output_path.exists()
    
    # Verify contract structure
    with open(output_path) as f:
        contract = yaml.safe_load(f)
    
    assert contract["name"] == "test"
    assert "schema" in contract
    assert "quality" in contract
    assert "metadata" in contract

