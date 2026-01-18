# Architecture

## Data Flow
```
                    ODCS Contracts (contracts/schemas/*.yml)
                              ↓
                       contract_loader.py
                              ↓
NBA CDN APIs → dlt (ingestion) → PostgreSQL → SQLMesh (transforms) → ML Models → Predictions
                    ↓                              ↓
              Dagster (orchestration)        Baselinr (quality)
```

## Contract-Driven Development
Schemas are defined ONCE in `contracts/schemas/` and used by:
- **dlt** - Column definitions and types
- **Baselinr** - Quality validation rules
- **Documentation** - Single source of truth

## Technology Stack
| Component | Tool | Purpose |
|-----------|------|---------|
| Orchestration | Dagster | Job scheduling, asset management (declarative automation) |
| Ingestion | dlt | Data extraction and loading |
| Storage | PostgreSQL | Primary data warehouse |
| Transformation | SQLMesh | SQL-based transforms |
| Quality | Baselinr | Data profiling, drift detection |
| Dashboards | Metabase | BI and visualization |
| Monitoring | Prometheus + Grafana | System metrics |

## Dagster Orchestration: Declarative Automation

**Assets use `AutomationCondition` instead of explicit jobs/schedules:**

- **`on_cron("@daily")`**: Assets run on a schedule (daily refresh)
  - `nba_teams`, `nba_players`, `nba_todays_games`, `nba_betting_odds`, `nba_injuries`
  
- **`eager()`**: Assets run reactively when upstream dependencies change
  - `nba_games` (runs when `nba_teams` updates)
  - `nba_boxscores`, `nba_team_boxscores` (run when `nba_games` updates)

**Benefits:**
- Assets run independently (no dlt pipeline state conflicts)
- Automatic dependency management
- Less boilerplate (no separate jobs/schedules)
- Assets materialize exactly when needed

**The `default_automation_condition_sensor` evaluates conditions and triggers materialization.**
Enable via CLI: `dagster sensor start -f definitions.py default_automation_condition_sensor`

## Service Ports
| Service | Port | URL |
|---------|------|-----|
| Dagster | 3000 | http://localhost:3000 |
| Metabase | 3001 | http://localhost:3001 |
| Grafana | 3002 | http://localhost:3002 |
| PostgreSQL | 5432 | localhost:5432 |
| Prometheus | 9090 | http://localhost:9090 |

## Key Directories
```
oss-data-platform/
├── ingestion/dlt/pipelines/    # dlt data pipelines
├── orchestration/dagster/      # Dagster definitions
│   └── assets/
│       ├── ingestion/          # Data ingestion assets
│       ├── transformation/     # Transform assets
│       └── quality/            # Quality/profiling assets
├── transformation/sqlmesh/     # SQL transformations
├── contracts/                  # ODCS data contracts
│   ├── schemas/                # Table schemas
│   ├── quality/                # Quality rules
│   └── contracts/              # Composed contracts
├── configs/
│   ├── odcs/                   # Central config files
│   └── generated/              # Auto-generated tool configs
└── docker-compose.yml          # Infrastructure services
```
