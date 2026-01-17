# OSS Data Platform

A complete end-to-end open-source data engineering platform using only fully open-source tools. This platform provides a unified configuration system based on the Open Data Contract Standard (ODCS) with automatic generation of tool-specific configurations.

## Architecture

The platform uses ODCS as the single source of truth for configurations, with a modular contract system where schemas and quality rules are composed into complete contracts. Adapters then generate tool-specific configs from these composed contracts.

```mermaid
flowchart TB
    Schemas[Schema Definitions<br/>contracts/schemas/] --> Composer[Contract Composer<br/>contracts/composer.py]
    Quality[Quality Rules<br/>contracts/quality/] --> Composer
    Metadata[Metadata Config] --> Composer
    Composer --> Contracts[Complete ODCS Contracts<br/>contracts/contracts/]
    
    ODCS[ODCS Configs<br/>configs/odcs/] --> Adapters[Config Adapters<br/>adapters/]
    Contracts --> Adapters
    Adapters --> Airbyte[Airbyte Config]
    Adapters --> SQLMesh[SQLMesh Config]
    Adapters --> Dagster[Dagster Config]
    Adapters --> Baselinr[Baselinr Config]
    Adapters --> DataHub[DataHub Config]
    
    Dagster --> Orchestrates[Orchestrates Pipeline]
    Orchestrates --> Ingestion[Airbyte/dlt]
    Orchestrates --> Transform[SQLMesh]
    Orchestrates --> Quality[Baselinr]
    
    Ingestion --> Warehouse[(PostgreSQL<br/>DuckDB)]
    Transform --> Warehouse
    Quality --> Warehouse
    
    DataHub --> Catalog[Metadata Catalog]
    Prometheus --> Metrics[Grafana Dashboards]
```

## Tool Stack

### Data Ingestion
- **Airbyte** - ELT platform for data integration
- **dlt** - Python-based data load tool

### Data Transformation
- **SQLMesh** - Open-source SQL transformation engine (dbt alternative)

### Orchestration
- **Dagster** - Modern data orchestration platform

### Data Quality
- **Baselinr** - Data profiling, drift detection, and quality monitoring

### Data Catalog
- **DataHub** - Open-source metadata platform

### Monitoring
- **Prometheus** - Metrics collection
- **Grafana** - Metrics visualization

### Storage
- **PostgreSQL** - Primary data warehouse
- **DuckDB** - Analytics engine (optional)

## Quick Start

1. **Clone and setup**
   ```bash
   git clone <repo-url>
   cd oss-data-platform
   make setup
   ```

2. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your connection details
   ```

3. **Compose contracts**
   ```bash
   make compose-contracts
   ```

4. **Generate tool configs**
   ```bash
   make generate-configs
   ```

5. **Start infrastructure**
   ```bash
   make docker-up
   ```

6. **Ingest NBA data**
   ```bash
   # Use Dagster to run NBA ingestion assets
   # Or run the dlt pipeline directly:
   python ingestion/dlt/pipelines/nba_stats.py
   ```

7. **Start Dagster**
   ```bash
   cd orchestration/dagster
   dagster dev
   ```

## Contract Composition Workflow

1. **Define schema**: Create schema in `contracts/schemas/nba_games.yml`
2. **Define quality rules**: Create or reference quality rules in `contracts/quality/nba_rules.yml`
3. **Compose contract**: Run `make compose-contracts` or `python contracts/composer.py --schema nba_games --quality nba_rules --output nba_games`
4. **Reference in config**: `configs/odcs/datasets.yml` references `contracts/contracts/nba_games.yml`
5. **Generate tool configs**: Run `make generate-configs` which:
   - Composes contracts if needed
   - Generates tool configs using adapters that read composed contracts

## Project Structure

```
oss-data-platform/
├── contracts/          # ODCS data contracts (modular)
│   ├── schemas/        # Reusable schema definitions
│   ├── quality/        # Reusable quality rule sets
│   └── contracts/      # Complete ODCS contracts (composed)
├── configs/            # Configuration management
│   ├── odcs/           # ODCS source configs
│   └── generated/      # Auto-generated tool configs
├── adapters/           # ODCS → Tool config converters
├── ingestion/          # Data ingestion (Airbyte, dlt)
├── transformation/     # Data transformation (SQLMesh)
├── orchestration/      # Workflow orchestration (Dagster)
├── quality/            # Data quality (Baselinr)
├── catalog/            # Data catalog (DataHub)
├── monitoring/         # Observability (Prometheus, Grafana)
└── storage/            # Data warehouse configs
```

## NBA Data Example

This platform includes a complete NBA data ingestion example:

- **ODCS Contracts**: Schemas and quality rules for NBA data
- **dlt Pipeline**: NBA Stats API integration
- **Dagster Assets**: Orchestrated NBA data ingestion
- **Data Quality**: Baselinr monitoring for NBA datasets

See [docs/guides/nba-ingestion.md](docs/guides/nba-ingestion.md) for the NBA ingestion guide.

## Development

See [docs/guides/getting-started.md](docs/guides/getting-started.md) for detailed setup instructions.

## License

Apache 2.0

