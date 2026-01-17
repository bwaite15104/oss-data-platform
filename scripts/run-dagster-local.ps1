# PowerShell script to run Dagster locally with proper environment variables
# Usage: .\scripts\run-dagster-local.ps1 [command]
# Commands: dev, materialize <asset_name>, list

param(
    [Parameter(Position=0)]
    [string]$Command = "dev",
    
    [Parameter(Position=1)]
    [string]$AssetName = ""
)

# Set environment variables for PostgreSQL (connecting to Docker container)
$env:POSTGRES_HOST = "localhost"
$env:POSTGRES_PORT = "5432"
$env:POSTGRES_DB = "oss_data_platform"
$env:POSTGRES_USER = "postgres"
$env:POSTGRES_PASSWORD = "postgres"

# dlt configuration
$env:NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__HOST = "localhost"
$env:NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__PORT = "5432"
$env:NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__DATABASE = "oss_data_platform"
$env:NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__USERNAME = "postgres"
$env:NBA_STATS__DESTINATION__POSTGRES__CREDENTIALS__PASSWORD = "postgres"

# Set DAGSTER_HOME
$env:DAGSTER_HOME = Join-Path $PSScriptRoot ".." ".dagster"

# Add project to Python path
$env:PYTHONPATH = (Get-Location).Path

Write-Host "Environment configured for local Dagster" -ForegroundColor Green
Write-Host "  POSTGRES_HOST: $env:POSTGRES_HOST"
Write-Host "  POSTGRES_DB: $env:POSTGRES_DB"
Write-Host "  DAGSTER_HOME: $env:DAGSTER_HOME"
Write-Host ""

switch ($Command) {
    "dev" {
        Write-Host "Starting Dagster dev server..." -ForegroundColor Cyan
        Write-Host "Open http://localhost:3000 in your browser" -ForegroundColor Yellow
        dagster dev -f orchestration/dagster/definitions.py
    }
    "materialize" {
        if ($AssetName -eq "") {
            Write-Host "Error: Please specify an asset name" -ForegroundColor Red
            Write-Host "Usage: .\run-dagster-local.ps1 materialize <asset_name>" -ForegroundColor Yellow
            Write-Host ""
            Write-Host "Available assets:" -ForegroundColor Cyan
            dagster asset list -f orchestration/dagster/definitions.py
            exit 1
        }
        Write-Host "Materializing asset: $AssetName" -ForegroundColor Cyan
        dagster asset materialize -f orchestration/dagster/definitions.py --select $AssetName
    }
    "list" {
        Write-Host "Available assets:" -ForegroundColor Cyan
        dagster asset list -f orchestration/dagster/definitions.py
    }
    default {
        Write-Host "Unknown command: $Command" -ForegroundColor Red
        Write-Host ""
        Write-Host "Available commands:" -ForegroundColor Yellow
        Write-Host "  dev         - Start Dagster dev server"
        Write-Host "  materialize - Materialize a specific asset"
        Write-Host "  list        - List all available assets"
    }
}
