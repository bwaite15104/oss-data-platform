# ODCS Configuration Guide

This guide explains how to configure the platform using ODCS (Open Data Contract Standard).

## Configuration Structure

ODCS configurations are organized in `configs/odcs/`:

- `connections.yml` - Database connections
- `datasets.yml` - Dataset definitions
- `quality.yml` - Global quality settings

## Connections

Define database connections in `configs/odcs/connections.yml`:

```yaml
connections:
  postgres_warehouse:
    type: postgres
    host: ${POSTGRES_HOST}
    port: ${POSTGRES_PORT}
    database: ${POSTGRES_DB}
    username: ${POSTGRES_USER}
    password: ${POSTGRES_PASSWORD}
```

## Datasets

Define datasets in `configs/odcs/datasets.yml`:

```yaml
datasets:
  - name: customers
    source: postgres_warehouse
    schema: public
    table: customers
    contract: contracts/contracts/customers.yml
    owner: data-engineering@company.com
```

Each dataset references a composed contract in `contracts/contracts/`.

## Quality Configuration

Global quality settings in `configs/odcs/quality.yml`:

```yaml
profiling:
  enabled: true
  default_schedule: "0 2 * * *"

drift_detection:
  enabled: true
  default_strategy: absolute_threshold
```

## Validation

Validate your ODCS configuration:

```bash
make validate
```

Or:

```bash
python tools/validate_odcs.py --check-contracts
```

