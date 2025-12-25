.PHONY: help setup install compose-contracts generate-configs validate docker-up docker-down test clean

help:
	@echo "OSS Data Platform - Makefile Commands"
	@echo ""
	@echo "  make setup              - Install dependencies"
	@echo "  make install            - Install package in development mode"
	@echo "  make compose-contracts  - Compose all contracts from schemas + quality"
	@echo "  make generate-configs  - Generate all tool configs (includes contract composition)"
	@echo "  make validate          - Validate ODCS configs and composed contracts"
	@echo "  make docker-up         - Start infrastructure services"
	@echo "  make docker-down       - Stop infrastructure services"
	@echo "  make test              - Run tests"
	@echo "  make clean             - Clean generated files and caches"

setup:
	pip install --upgrade pip
	pip install -r requirements.txt
	pip install -e ".[dev]"

install:
	pip install -e ".[dev]"

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

