"""
dlt pipeline for customers data.

Generated from ODCS configuration.
"""

import dlt

@dlt.resource
def customers_source():
    """Source for customers data."""
    # TODO: Implement source extraction
    yield []

@dlt.source
def customers():
    """Customers source."""
    return customers_source()

if __name__ == "__main__":
    pipeline = dlt.pipeline(
        pipeline_name="customers",
        destination="postgres",
    )
    
    load_info = pipeline.run(customers())
    print(load_info)

