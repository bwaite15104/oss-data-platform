"""
Contract Loader - Integrates ODCS contracts with dlt pipelines.

This module reads contract schema definitions and generates dlt-compatible
column specifications, ensuring ingestion pipelines match contract definitions.
"""

import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional
from functools import lru_cache


# Map contract types to dlt data types
CONTRACT_TO_DLT_TYPES = {
    "string": "text",
    "integer": "bigint",
    "float": "double",
    "decimal": "decimal",
    "boolean": "bool",
    "date": "date",
    "timestamp": "timestamp",
    "datetime": "timestamp",
    "time": "time",
    "json": "json",
    "array": "json",
    "object": "json",
}


def get_contracts_dir() -> Path:
    """Get the contracts directory path."""
    # Try relative paths for both local and Docker environments
    possible_paths = [
        Path(__file__).parent.parent.parent.parent / "contracts" / "schemas",  # Local
        Path("/app/contracts/schemas"),  # Docker
        Path("contracts/schemas"),  # CWD
    ]
    
    for path in possible_paths:
        if path.exists():
            return path
    
    raise FileNotFoundError(f"Could not find contracts/schemas directory. Tried: {possible_paths}")


@lru_cache(maxsize=32)
def load_contract_schema(contract_name: str) -> Dict[str, Any]:
    """
    Load a contract schema by name.
    
    Args:
        contract_name: Name of the contract (e.g., "nba_teams", "nba_games")
        
    Returns:
        Dictionary containing the contract schema
    """
    contracts_dir = get_contracts_dir()
    schema_file = contracts_dir / f"{contract_name}.yml"
    
    if not schema_file.exists():
        raise FileNotFoundError(f"Contract schema not found: {schema_file}")
    
    with open(schema_file, "r") as f:
        return yaml.safe_load(f)


def get_dlt_columns(contract_name: str) -> Dict[str, Dict[str, Any]]:
    """
    Get dlt column definitions from a contract schema.
    
    Args:
        contract_name: Name of the contract
        
    Returns:
        Dictionary of column definitions compatible with dlt @resource decorator
        
    Example:
        columns = get_dlt_columns("nba_teams")
        # Returns: {"team_id": {"data_type": "bigint", "nullable": False}, ...}
    """
    schema = load_contract_schema(contract_name)
    columns = {}
    
    for col in schema.get("columns", []):
        col_name = col["name"]
        col_type = col.get("type", "string")
        dlt_type = CONTRACT_TO_DLT_TYPES.get(col_type, "text")
        
        columns[col_name] = {
            "data_type": dlt_type,
            "nullable": col.get("nullable", True),
        }
        
        # Add primary key info
        if col.get("primary_key", False):
            columns[col_name]["primary_key"] = True
            
        # Add unique constraint
        if col.get("unique", False):
            columns[col_name]["unique"] = True
    
    return columns


def get_contract_column_names(contract_name: str) -> List[str]:
    """
    Get list of column names from a contract.
    
    Args:
        contract_name: Name of the contract
        
    Returns:
        List of column names
    """
    schema = load_contract_schema(contract_name)
    return [col["name"] for col in schema.get("columns", [])]


def get_primary_key(contract_name: str) -> Optional[List[str]]:
    """
    Get primary key column(s) from a contract.
    
    Args:
        contract_name: Name of the contract
        
    Returns:
        List of primary key column names, or None
    """
    schema = load_contract_schema(contract_name)
    
    # Check explicit primary_key definition
    if "primary_key" in schema:
        return schema["primary_key"]
    
    # Fall back to columns marked as primary_key
    pk_cols = [
        col["name"] 
        for col in schema.get("columns", []) 
        if col.get("primary_key", False)
    ]
    
    return pk_cols if pk_cols else None


def validate_record_against_contract(
    record: Dict[str, Any], 
    contract_name: str,
    strict: bool = False
) -> Dict[str, Any]:
    """
    Validate and filter a record against a contract schema.
    
    Args:
        record: The data record to validate
        contract_name: Name of the contract
        strict: If True, only include columns defined in contract
        
    Returns:
        Filtered/validated record
    """
    schema = load_contract_schema(contract_name)
    contract_columns = {col["name"]: col for col in schema.get("columns", [])}
    
    if strict:
        # Only include columns defined in contract
        return {
            k: v for k, v in record.items() 
            if k in contract_columns
        }
    else:
        # Include all columns (dlt will handle extras)
        return record


def create_contract_aware_resource(
    contract_name: str,
    resource_name: Optional[str] = None,
    write_disposition: str = "merge",
):
    """
    Decorator factory to create a contract-aware dlt resource.
    
    Usage:
        @create_contract_aware_resource("nba_teams")
        def my_teams_resource():
            yield {"team_id": 1, "team_name": "Lakers", ...}
    
    Args:
        contract_name: Name of the contract to use
        resource_name: Override resource name (defaults to contract_name)
        write_disposition: dlt write disposition
        
    Returns:
        Decorator function
    """
    import dlt
    
    columns = get_dlt_columns(contract_name)
    primary_key = get_primary_key(contract_name)
    name = resource_name or contract_name.replace("nba_", "")
    
    def decorator(func):
        return dlt.resource(
            name=name,
            write_disposition=write_disposition,
            primary_key=primary_key[0] if primary_key and len(primary_key) == 1 else primary_key,
            columns=columns,
        )(func)
    
    return decorator


# Convenience function to list available contracts
def list_available_contracts() -> List[str]:
    """List all available contract schemas."""
    contracts_dir = get_contracts_dir()
    return [f.stem for f in contracts_dir.glob("*.yml")]


if __name__ == "__main__":
    # Test the contract loader
    print("Available contracts:", list_available_contracts())
    print()
    
    for contract in ["nba_teams", "nba_games", "nba_players"]:
        print(f"=== {contract} ===")
        columns = get_dlt_columns(contract)
        pk = get_primary_key(contract)
        print(f"Primary key: {pk}")
        print(f"Columns: {list(columns.keys())}")
        print()
