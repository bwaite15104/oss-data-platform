# Data Ingestion

This directory contains data ingestion pipelines using Airbyte and dlt.

## Airbyte

Airbyte connection configurations are generated from ODCS and stored in `airbyte/connections/`.

## dlt

dlt pipelines are generated from ODCS and stored in `dlt/pipelines/`.

### Running dlt Pipelines

```bash
cd dlt/pipelines
python customers_pipeline.py
```

## Integration with Dagster

Ingestion assets are defined in `orchestration/dagster/assets/ingestion/` and can be orchestrated via Dagster.

