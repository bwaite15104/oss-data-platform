# Contract Composition Guide

This guide explains the contract composition workflow.

## Overview

Contracts are composed from modular components:
- **Schemas** (`contracts/schemas/`) - Table structure definitions
- **Quality Rules** (`contracts/quality/`) - Validation rules and thresholds
- **Composed Contracts** (`contracts/contracts/`) - Complete ODCS contracts

## Workflow

### 1. Define Schema

Create a schema file in `contracts/schemas/`:

```yaml
# contracts/schemas/customers.yml
name: customers
columns:
  - name: customer_id
    type: integer
    nullable: false
    primary_key: true
  # ... more columns
```

### 2. Define Quality Rules

Create or reference quality rules in `contracts/quality/`:

```yaml
# contracts/quality/rules.yml
validation_rules:
  - type: not_null
    columns: [customer_id, email]
  - type: format
    column: email
    pattern: "^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$"
```

### 3. Compose Contract

Run the composer:

```bash
python contracts/composer.py --schema customers --quality rules --output customers
```

Or compose all:

```bash
make compose-contracts
```

### 4. Use in Configs

Reference the composed contract in `configs/odcs/datasets.yml`:

```yaml
datasets:
  - name: customers
    contract: contracts/contracts/customers.yml
    # ...
```

### 5. Generate Tool Configs

Generate tool-specific configs:

```bash
make generate-configs
```

## Benefits

- **Reusability**: Share quality rules across multiple schemas
- **Modularity**: Update schemas and quality rules independently
- **Consistency**: Single source of truth for data contracts
- **Validation**: Composed contracts are validated against ODCS schema

