# Tool Integration Guide

This guide explains how to add new tools to the platform.

## Adding a New Adapter

### 1. Create Adapter Class

Create a new adapter in `adapters/`:

```python
# adapters/my_tool_adapter.py
from .base import ConfigAdapter

class MyToolAdapter(ConfigAdapter):
    def generate_config(self, output_path: str) -> None:
        # Convert ODCS to tool-specific format
        pass
    
    def validate(self) -> bool:
        # Validate ODCS config
        return True
```

### 2. Register in generate_configs.py

Add to `tools/generate_configs.py`:

```python
from adapters import MyToolAdapter

adapters = {
    # ...
    "my_tool": (MyToolAdapter, "my_tool_config.yml"),
}
```

### 3. Update Makefile

Add generation command if needed:

```makefile
generate-my-tool:
	python tools/generate_configs.py --tools my_tool
```

## Adapter Pattern

All adapters:
- Inherit from `ConfigAdapter`
- Load ODCS config via `self.odcs_config`
- Load composed contracts via `self._load_composed_contract()`
- Generate tool-specific configs
- Validate ODCS compatibility

## Example: Reading Contracts

```python
def generate_config(self, output_path: str) -> None:
    for dataset in self.odcs_config.datasets:
        # Load composed contract
        contract = self._load_composed_contract(dataset.contract)
        
        # Extract schema
        schema = contract.schema
        
        # Extract quality rules
        quality = contract.quality
        
        # Generate tool config
        # ...
```

