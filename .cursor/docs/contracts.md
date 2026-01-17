# ODCS Contracts

## Overview
Contracts are the **single source of truth** for data schemas in this platform. They define:
- Column names and types
- Primary keys and indexes
- Nullability constraints
- Quality validation rules

## Directory Structure
```
contracts/
├── schemas/           # Schema definitions (columns, types)
│   ├── nba_teams.yml
│   ├── nba_players.yml
│   ├── nba_games.yml
│   ├── nba_betting_odds.yml
│   ├── nba_boxscores.yml
│   └── nba_team_boxscores.yml
├── quality/           # Quality rules
│   ├── nba_rules.yml
│   └── thresholds.yml
├── contracts/         # Composed contracts (schema + quality)
└── composer.py        # Composes schemas + quality into contracts
```

## Schema Format
```yaml
name: nba_teams
description: NBA team information

columns:
  - name: team_id
    type: integer          # string, integer, float, boolean, date, timestamp
    nullable: false
    primary_key: true
    description: Unique team identifier
    
  - name: team_name
    type: string
    nullable: false
    unique: true           # Optional constraint
    description: Team name

primary_key:
  - team_id

indexes:
  - columns: [team_name]
    unique: true
```

## Type Mappings
| Contract Type | dlt Type | PostgreSQL Type |
|---------------|----------|-----------------|
| string | text | TEXT |
| integer | bigint | BIGINT |
| float | double | DOUBLE PRECISION |
| boolean | bool | BOOLEAN |
| date | date | DATE |
| timestamp | timestamp | TIMESTAMP |

## Contract Loader
```python
from ingestion.dlt.contract_loader import (
    get_dlt_columns,      # Get column definitions for dlt
    get_primary_key,       # Get primary key column(s)
    load_contract_schema,  # Load full schema dict
    list_available_contracts,  # List all contracts
)

# Usage in dlt resource
columns = get_dlt_columns("nba_teams")
pk = get_primary_key("nba_teams")
```

## Composing Contracts
```bash
# Compose all contracts (combines schemas + quality rules)
make compose-contracts

# Or directly
python contracts/composer.py --all
```

## Adding a New Contract

1. **Create schema file**
   ```yaml
   # contracts/schemas/nba_new_table.yml
   name: nba_new_table
   description: Description here
   columns:
     - name: id
       type: integer
       primary_key: true
       ...
   ```

2. **Use in dlt pipeline**
   ```python
   @dlt.resource(
       columns=_get_contract_columns("nba_new_table"),
       primary_key=_get_contract_pk("nba_new_table"),
   )
   def nba_new_table_resource():
       ...
   ```

3. **Compose to include quality rules**
   ```bash
   make compose-contracts
   ```

## Current Contracts
| Contract | Columns | Primary Key | Description |
|----------|---------|-------------|-------------|
| nba_teams | 8 | team_id | Team info |
| nba_players | 21 | player_id | Player rosters |
| nba_games | 16 | game_id | Season schedule |
| nba_todays_games | 15 | game_id | Live scoreboard |
| nba_betting_odds | 11 | game_id, book_name, market_type | Betting lines |
| nba_boxscores | 29 | game_id, player_id | Player game stats |
| nba_team_boxscores | 19 | game_id, team_id | Team game stats |
