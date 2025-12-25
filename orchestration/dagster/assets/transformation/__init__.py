"""Transformation assets."""

from dagster import asset
from ..ingestion import raw_customers, raw_orders

@asset(deps=[raw_customers])
def stg_customers():
    """Staging customers model."""
    # TODO: Implement SQLMesh transformation
    pass

@asset(deps=[raw_customers, raw_orders])
def customer_orders():
    """Customer orders aggregation."""
    # TODO: Implement SQLMesh transformation
    pass

