"""
Dagster definitions for OSS Data Platform.

This file loads asset definitions generated from ODCS contracts.
"""

from dagster import Definitions, load_assets_from_modules
from dagster_postgres import PostgresResource

from .assets import ingestion, transformation, quality

# Load assets from modules
all_assets = load_assets_from_modules([ingestion, transformation, quality])

# Define resources
postgres_resource = PostgresResource(
    postgres_db={
        "username": {"env": "POSTGRES_USER"},
        "password": {"env": "POSTGRES_PASSWORD"},
        "hostname": {"env": "POSTGRES_HOST"},
        "db_name": {"env": "POSTGRES_DB"},
        "port": {"env": "POSTGRES_PORT"},
    }
)

defs = Definitions(
    assets=all_assets,
    resources={
        "postgres": postgres_resource,
    },
)

