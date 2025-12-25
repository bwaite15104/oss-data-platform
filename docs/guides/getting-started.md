# Getting Started Guide

This guide will help you set up and run the OSS Data Platform.

## Prerequisites

- Python 3.10+
- Docker and Docker Compose
- Git

## Setup Steps

### 1. Clone and Install

```bash
git clone <repo-url>
cd oss-data-platform
make setup
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your connection details
```

### 3. Compose Contracts

```bash
make compose-contracts
```

This combines schemas and quality rules into complete ODCS contracts.

### 4. Generate Tool Configs

```bash
make generate-configs
```

This generates configurations for all tools (Baselinr, SQLMesh, Dagster, etc.).

### 5. Start Infrastructure

```bash
make docker-up
```

This starts PostgreSQL, Dagster, DataHub, Prometheus, and Grafana.

### 6. Start Dagster

```bash
cd orchestration/dagster
dagster dev
```

Access Dagster UI at http://localhost:3000

## Next Steps

- See [ODCS Guide](odcs-guide.md) for configuration details
- See [Contract Composition](contract-composition.md) for contract workflow
- See [Tool Integration](tool-integration.md) for adding new tools

