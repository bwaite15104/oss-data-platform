.PHONY: help setup install compose-contracts generate-configs validate docker-up docker-down test clean dagster-dev dagster-list

help:
	@echo "OSS Data Platform - Makefile Commands"
	@echo ""
	@echo "Setup & Installation:"
	@echo "  make setup              - Install all dependencies (first time setup)"
	@echo "  make install            - Install package in development mode"
	@echo ""
	@echo "Local Development (Dagster CLI):"
	@echo "  make dagster-dev        - Start Dagster dev server locally"
	@echo "  make dagster-list       - List all available assets"
	@echo ""
	@echo "Docker Services:"
	@echo "  make docker-up          - Start infrastructure services (Postgres, Dagster, Metabase)"
	@echo "  make docker-down        - Stop infrastructure services"
	@echo ""
	@echo "Configuration:"
	@echo "  make compose-contracts  - Compose all contracts from schemas + quality"
	@echo "  make generate-configs   - Generate all tool configs (includes contract composition)"
	@echo "  make validate           - Validate ODCS configs and composed contracts"
	@echo ""
	@echo "Database:"
	@echo "  make db-schemas         - List all database schemas"
	@echo "  make db-tables          - List tables in raw_dev"
	@echo "  make db-counts          - Show row counts in raw_dev"
	@echo "  make db-psql            - Open psql shell"
	@echo ""
	@echo "Testing:"
	@echo "  make test               - Run tests"
	@echo "  make clean              - Clean generated files and caches"

setup:
	pip install --upgrade pip
	pip install -r requirements.txt
	pip install -e ".[dev]"
	@echo ""
	@echo "Setup complete! Next steps:"
	@echo "  1. Start services: make docker-up"
	@echo "  2. Run locally: make dagster-dev"
	@echo "  3. Or use Docker Dagster: http://localhost:3000"

install:
	pip install -e ".[dev]"

# Local Dagster development (requires Docker postgres to be running)
dagster-dev:
	@echo "Starting Dagster dev server..."
	@echo "Make sure Docker services are running (make docker-up)"
	@echo "Open http://localhost:3000"
	@echo "Data will be loaded to: raw_dev schema"
	POSTGRES_HOST=localhost \
	DATA_ENV=dev \
	NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__HOST=localhost \
	NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__PORT=5432 \
	NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__DATABASE=nba_analytics \
	NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__USERNAME=postgres \
	NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__PASSWORD=postgres \
	dagster dev -f definitions.py

dagster-list:
	@POSTGRES_HOST=localhost \
	DATA_ENV=dev \
	NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__HOST=localhost \
	NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__DATABASE=nba_analytics \
	dagster asset list -f definitions.py

compose-contracts:
	python contracts/composer.py --all

generate-configs: compose-contracts
	python tools/generate_configs.py --tools all

validate:
	python tools/validate_odcs.py

docker-up:
	docker-compose $(if $(wildcard .env),--env-file .env,) up -d

docker-down:
	docker-compose down

# Database utilities (connects to nba_analytics by default)
db-schemas:
	@python scripts/db_query.py --schemas

db-tables:
	@python scripts/db_query.py --tables raw_dev

db-counts:
	@python scripts/db_query.py --counts raw_dev

db-psql:
	docker exec -it nba_analytics_postgres psql -U postgres -d nba_analytics

db-query:
	@echo "Usage: python scripts/db_query.py \"SELECT * FROM raw_dev.teams LIMIT 5\""

# Index raw_dev.games for SQLMesh intermediate model performance (run after ingestion)
db-index-games:
	docker exec nba_analytics_dagster_webserver python /app/scripts/ensure_raw_dev_games_indexes.py

# Transformation & Feature Store (SQLMesh)
SQLMESH = $(LOCALAPPDATA)/Programs/Python/Python312/Scripts/sqlmesh.exe

sqlmesh-plan:
	cd transformation/sqlmesh && "$(SQLMESH)" plan --auto-apply

sqlmesh-run:
	cd transformation/sqlmesh && "$(SQLMESH)" run

sqlmesh-info:
	cd transformation/sqlmesh && "$(SQLMESH)" info

refresh-all: sqlmesh-plan
	@echo "SQLMesh models refreshed!"

# Daily data refresh (run manually or use Dagster schedules)
daily-refresh:
	@POSTGRES_DB=nba_analytics DATA_ENV=dev dagster asset materialize -f definitions.py \
		--select "nba_games" "nba_todays_games" "nba_betting_odds" "nba_injuries"

test:
	pytest tests/ -v --cov=adapters --cov=contracts --cov=tools

clean:
	find . -type d -name __pycache__ -exec rm -r {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type d -name "*.egg-info" -exec rm -r {} + 2>/dev/null || true
	rm -rf .pytest_cache
	rm -rf .coverage
	rm -rf htmlcov
	rm -rf build
	rm -rf dist

