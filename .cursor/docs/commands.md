# Commands Reference

## Local Dagster CLI (Recommended for Development)

### Setup (one-time)
```powershell
pip install dagster dagster-webserver dagster-postgres dlt[postgres] requests
```

### Set Environment Variables (PowerShell)
```powershell
$env:POSTGRES_HOST = "localhost"
$env:POSTGRES_PORT = "5432"
$env:POSTGRES_DB = "oss_data_platform"
$env:POSTGRES_USER = "postgres"
$env:POSTGRES_PASSWORD = "postgres"
$env:NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__HOST = "localhost"
$env:NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__PORT = "5432"
$env:NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__DATABASE = "oss_data_platform"
$env:NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__USERNAME = "postgres"
$env:NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__PASSWORD = "postgres"
```

### Run Commands (from project root)
```powershell
# List all assets
dagster asset list -f definitions.py

# Materialize a specific asset
dagster asset materialize -f definitions.py --select nba_teams
dagster asset materialize -f definitions.py --select nba_betting_odds

# Start dev server (UI at http://localhost:3000)
dagster dev -f definitions.py
```

### Or use the helper script
```powershell
.\scripts\run-dagster-local.ps1 list
.\scripts\run-dagster-local.ps1 materialize nba_teams
.\scripts\run-dagster-local.ps1 dev
```

## Make Commands
```bash
make help              # Show all available commands
make setup             # Install dependencies
make install           # Install package in dev mode
make docker-up         # Start all infrastructure services
make docker-down       # Stop all services
make generate-configs  # Generate tool configs from ODCS contracts
make compose-contracts # Compose contracts from schemas + quality rules
make validate          # Validate ODCS configs
make test              # Run test suite
make clean             # Clean caches and generated files
```

## Docker Commands
```bash
# View running containers
docker ps

# View logs
docker logs oss_data_platform_dagster_webserver -f
docker logs oss_data_platform_postgres -f

# Restart specific service
docker restart oss_data_platform_dagster_webserver
docker restart oss_data_platform_dagster_daemon

# Execute command in container
docker exec -it oss_data_platform_postgres psql -U postgres -d oss_data_platform

# Rebuild after code changes
docker-compose build
docker-compose up -d
```

## Database Commands
```bash
# Connect to PostgreSQL
docker exec -it oss_data_platform_postgres psql -U postgres -d oss_data_platform

# Quick queries
docker exec oss_data_platform_postgres psql -U postgres -d oss_data_platform -c "SELECT * FROM nba.teams LIMIT 5;"
docker exec oss_data_platform_postgres psql -U postgres -d oss_data_platform -c "\dt nba.*"
```

## Dagster Commands
```bash
# Reload definitions (after code changes)
# Use UI: Deployment > Code Locations > Reload

# Or restart containers
docker restart oss_data_platform_dagster_webserver oss_data_platform_dagster_daemon
```

## Testing dlt Pipelines
```bash
# Dry run (print data without loading)
docker exec oss_data_platform_dagster_webserver python /app/ingestion/dlt/pipelines/nba_stats.py --dry-run --teams

# Run specific resource
docker exec oss_data_platform_dagster_webserver python -c "
from ingestion.dlt.pipelines.nba_stats import nba_teams_resource
for team in nba_teams_resource():
    print(team['team_name'])
"
```

## Config Generation
```bash
# Generate all tool configs
cd oss-data-platform
python tools/generate_configs.py --tools all

# Generate specific tool config
python tools/generate_configs.py --tools baselinr
python tools/generate_configs.py --tools dagster
```
