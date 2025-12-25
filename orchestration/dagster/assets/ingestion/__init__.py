"""Ingestion assets."""

from dagster import asset

@asset
def raw_customers():
    """Raw customers data from source."""
    # TODO: Implement ingestion
    pass

@asset
def raw_orders():
    """Raw orders data from source."""
    # TODO: Implement ingestion
    pass

