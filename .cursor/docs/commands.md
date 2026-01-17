# Commands Reference

## Quick Start
```bash
make setup             # Install dependencies (first time)
make docker-up         # Start services (Postgres, Dagster, Metabase)
make dagster-dev       # Start local Dagster dev server
```

## Make Commands

### Setup & Installation
```bash
make setup             # Install all dependencies (first time setup)
make install           # Install package in dev mode
```

### Services
```bash
make docker-up         # Start infrastructure (Postgres, Dagster, Metabase)
make docker-down       # Stop all services
make dagster-dev       # Start local Dagster dev server (UI at :3000)
make dagster-list      # List all available assets
```

### Database Utilities
```bash
make db-schemas        # List all database schemas
make db-tables         # List tables in raw_dev
make db-counts         # Show row counts in raw_dev
make db-psql           # Open psql shell to nba_analytics
```

### Configuration
```bash
make compose-contracts # Compose contracts from schemas + quality rules
make generate-configs  # Generate tool configs from ODCS
make validate          # Validate ODCS configs
```

### Testing & Cleanup
```bash
make test              # Run test suite
make clean             # Clean caches and generated files
```

## Database Query Utility

### Basic Usage
```bash
# Custom SQL query
python scripts/db_query.py "SELECT * FROM raw_dev.teams LIMIT 5"

# List schemas
python scripts/db_query.py --schemas

# List tables in a schema
python scripts/db_query.py --tables raw_dev

# Row counts
python scripts/db_query.py --counts raw_dev

# Execute SQL file
python scripts/db_query.py --file queries/my_query.sql

# Different output formats
python scripts/db_query.py "SELECT * FROM raw_dev.teams" --format csv
python scripts/db_query.py "SELECT * FROM raw_dev.teams" --format grid
```

### Example Queries
```bash
# Team standings
python scripts/db_query.py "SELECT team_name, conference, division FROM raw_dev.teams ORDER BY conference, division"

# Game counts by status
python scripts/db_query.py "SELECT game_status, count(*) FROM raw_dev.games GROUP BY game_status"

# Today's games
python scripts/db_query.py "SELECT * FROM raw_dev.todays_games"

# Player counts by team
python scripts/db_query.py "SELECT team_name, count(*) FROM raw_dev.players GROUP BY team_name ORDER BY count(*) DESC"
```

## Local Dagster CLI

### Environment Variables (PowerShell)
```powershell
$env:POSTGRES_HOST = "localhost"
$env:POSTGRES_DB = "nba_analytics"
$env:DATA_ENV = "dev"  # dev, staging, or prod
$env:NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__HOST = "localhost"
$env:NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__PORT = "5432"
$env:NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__DATABASE = "nba_analytics"
$env:NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__USERNAME = "postgres"
$env:NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__PASSWORD = "postgres"
```

### Asset Commands
```bash
# List all assets
dagster asset list -f definitions.py

# Materialize specific asset
dagster asset materialize -f definitions.py --select nba_teams
dagster asset materialize -f definitions.py --select nba_games
dagster asset materialize -f definitions.py --select nba_players
dagster asset materialize -f definitions.py --select nba_todays_games
dagster asset materialize -f definitions.py --select nba_betting_odds

# Start dev server
dagster dev -f definitions.py
```

## Docker Commands

### Container Management
```bash
# View running containers
docker ps

# View logs
docker logs nba_analytics_dagster_webserver -f
docker logs nba_analytics_postgres -f

# Restart service
docker restart nba_analytics_dagster_webserver

# Rebuild after code changes
docker-compose build
docker-compose up -d
```

### Direct Database Access
```bash
# Open psql shell
docker exec -it nba_analytics_postgres psql -U postgres -d nba_analytics

# Quick queries
docker exec nba_analytics_postgres psql -U postgres -d nba_analytics -c "\dt raw_dev.*"
docker exec nba_analytics_postgres psql -U postgres -d nba_analytics -c "SELECT count(*) FROM raw_dev.teams"
```

## Service URLs
| Service | URL |
|---------|-----|
| Dagster UI | http://localhost:3000 |
| Metabase | http://localhost:3002 |
| PostgreSQL | localhost:5432 |
| Grafana | http://localhost:3001 |
| Prometheus | http://localhost:9090 |

## Troubleshooting

### dlt Pipeline State Issues
If assets fail with file rename errors:
```powershell
# Clear dlt pipeline state
$homeDlt = Join-Path $env:USERPROFILE ".dlt\pipelines\nba_stats"
Remove-Item -Path $homeDlt -Recurse -Force
```

### Docker Postgres Connection
```bash
# Check if postgres is healthy
docker exec nba_analytics_postgres pg_isready -U postgres

# Check databases
docker exec nba_analytics_postgres psql -U postgres -c "\l"
```
