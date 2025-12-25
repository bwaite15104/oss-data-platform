# Examples

This directory contains example workflows and end-to-end pipeline demonstrations.

## End-to-End Pipeline

Run the complete workflow:

```bash
python examples/end_to_end_pipeline.py
```

This demonstrates:
1. Contract composition
2. Config generation
3. Validation

## Contract Composition Example

```bash
# Compose a single contract
python contracts/composer.py --schema customers --quality rules --output customers

# Compose all contracts
make compose-contracts
```

## Tool Config Generation

```bash
# Generate all tool configs
make generate-configs

# Generate specific tool
python tools/generate_configs.py --tools baselinr
```

## Dagster Example

See `orchestration/dagster/definitions.py` for Dagster asset definitions.

## Baselinr Example

```bash
# Profile tables
baselinr profile --config configs/generated/baselinr/baselinr_config.yml

# Detect drift
baselinr drift --config configs/generated/baselinr/baselinr_config.yml --dataset customers

# Start Quality Studio
baselinr ui --config configs/generated/baselinr/baselinr_config.yml
```

