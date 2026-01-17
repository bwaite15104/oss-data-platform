# Data Quality with Baselinr

This directory contains Baselinr configuration and quality profiles.

## Generated Configs

The Baselinr configuration is generated from ODCS contracts using the Baselinr adapter:

```bash
make generate-configs
```

This creates `configs/generated/baselinr/baselinr_config.yml` which is used by:
- Dagster assets (see `orchestration/dagster/assets/quality/`)
- CLI commands
- Quality Studio UI

## Usage

### Run Profiling

```bash
baselinr profile --config configs/generated/baselinr/baselinr_config.yml
```

### Detect Drift

```bash
baselinr drift --config configs/generated/baselinr/baselinr_config.yml --dataset games
```

### Start Quality Studio

```bash
baselinr ui --config configs/generated/baselinr/baselinr_config.yml
```

## Integration with Dagster

Baselinr assets are automatically created in `orchestration/dagster/assets/quality/` using the Dagster integration.

