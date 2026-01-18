"""Declarative automation for Dagster assets.

Assets now use AutomationCondition directly in their definitions instead of jobs/schedules.
This allows assets to run independently, avoiding dlt pipeline state conflicts.
"""

# With declarative automation, we no longer need explicit jobs/schedules.
# Assets define their own automation_condition (on_cron or eager).
# The default_automation_condition_sensor (enabled in definitions.py) handles execution.

# Empty lists - automation is handled by asset conditions
schedules = []
jobs = []
