# Data Catalog

This directory contains DataHub configuration for metadata management.

## DataHub

DataHub ingestion configs are generated from ODCS contracts and stored in `datahub/ingestion/`.

### Starting DataHub

DataHub services are defined in `datahub/docker-compose.yml` and can be started with:

```bash
cd datahub
docker-compose up -d
```

Access DataHub UI at http://localhost:8080

## Metadata Ingestion

Metadata is automatically ingested from:
- PostgreSQL databases
- Dagster runs
- Baselinr profiling results

