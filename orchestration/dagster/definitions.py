"""
Dagster definitions for OSS Data Platform.

This file loads asset definitions generated from ODCS contracts.
"""

from dagster import Definitions, load_assets_from_modules

from assets import ingestion, transformation, quality

# Load assets from modules
all_assets = load_assets_from_modules([ingestion, transformation, quality])

# Extract resources from Baselinr definitions if quality assets are loaded
resources = {}
if hasattr(quality, 'baselinr_defs') and quality.baselinr_defs:
    # Baselinr definitions include the 'baselinr' resource
    # Definitions.resources is a dict-like object
    if hasattr(quality.baselinr_defs, 'resources'):
        # Convert to dict if it's not already
        baselinr_resources = quality.baselinr_defs.resources
        if hasattr(baselinr_resources, 'keys'):
            # It's dict-like, extract the resources
            for key in baselinr_resources.keys():
                resources[key] = baselinr_resources[key]
        elif isinstance(baselinr_resources, dict):
            resources.update(baselinr_resources)

# Define resources
# Note: Assets use dlt pipelines which handle database connections directly
# If you need a postgres resource for other assets, create a custom resource:
# from dagster import resource
# import psycopg2
# 
# @resource(config_schema={"conn_string": str})
# def postgres_resource(context):
#     return psycopg2.connect(context.resource_config["conn_string"])

defs = Definitions(
    assets=all_assets,
    resources=resources if resources else None,
)

