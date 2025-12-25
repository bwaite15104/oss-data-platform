# Monitoring

This directory contains Prometheus and Grafana configuration for observability.

## Prometheus

Prometheus configuration is in `prometheus/prometheus.yml`.

Access Prometheus at http://localhost:9090

## Grafana

Grafana datasources and dashboards are configured in `grafana/`.

Access Grafana at http://localhost:3001

Default credentials:
- Username: admin
- Password: admin

## Metrics

The platform collects metrics from:
- PostgreSQL
- Dagster
- Baselinr
- DataHub

