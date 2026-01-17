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
	POSTGRES_HOST=localhost \
	NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__HOST=localhost \
	NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__PORT=5432 \
	NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__DATABASE=oss_data_platform \
	NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__USERNAME=postgres \
	NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__PASSWORD=postgres \
	dagster dev -f definitions.py

dagster-list:
	@POSTGRES_HOST=localhost \
	NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__HOST=localhost \
	dagster asset list -f definitions.py

compose-contracts:
	python contracts/composer.py --all

generate-configs: compose-contracts
	python tools/generate_configs.py --tools all

validate:
	python tools/validate_odcs.py

docker-up:
	docker-compose up -d

docker-down:
	docker-compose down

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

