# ODCS Contracts

This directory contains Open Data Contract Standard (ODCS) contracts organized in a modular structure.

## Structure

- **`schemas/`** - Reusable schema definitions (columns, types, constraints)
- **`quality/`** - Reusable quality rule sets (validation rules, thresholds)
- **`contracts/`** - Complete ODCS contracts (composed from schemas + quality + metadata)

## Contract Composition

Contracts are composed from modular components:

1. **Schema** - Define table structure in `schemas/`
2. **Quality Rules** - Define validation rules in `quality/`
3. **Compose** - Run `python contracts/composer.py` to create complete contracts
4. **Use** - Reference composed contracts in `configs/odcs/datasets.yml`

## Example

```bash
# Compose a contract
python contracts/composer.py --schema nba_games --quality nba_rules --output nba_games

# This creates contracts/contracts/nba_games.yml with:
# - Schema from contracts/schemas/nba_games.yml
# - Quality rules from contracts/quality/nba_rules.yml
# - Metadata (owner, lineage, etc.)
```

## Contract Format

Complete contracts follow ODCS standard:

```yaml
version: "1.0"
name: nba_games
schema:
  name: nba_games
  columns: [...]
quality:
  validation_rules: [...]
  thresholds: {...}
metadata:
  owner: data-engineering@company.com
  lineage: [...]
```

