"""Dagster schedules for automated data collection."""

from dagster import (
    ScheduleDefinition,
    AssetSelection,
    define_asset_job,
)

# Daily data refresh job - runs every day at 6 AM ET
daily_refresh_job = define_asset_job(
    name="daily_data_refresh",
    selection=AssetSelection.keys(
        "nba_games",           # Refresh game schedule
        "nba_todays_games",    # Get today's games
        "nba_betting_odds",    # Get today's odds
        "nba_injuries",        # Get current injuries
    ),
    description="Daily refresh of games, odds, and injury data",
)

# Boxscore backfill job - can be triggered manually or scheduled weekly
boxscore_backfill_job = define_asset_job(
    name="boxscore_backfill",
    selection=AssetSelection.keys(
        "nba_boxscores",
        "nba_team_boxscores",
    ),
    description="Backfill player and team boxscores for completed games",
)

# Daily schedule at 6 AM ET (11:00 UTC)
daily_refresh_schedule = ScheduleDefinition(
    job=daily_refresh_job,
    cron_schedule="0 11 * * *",  # 6 AM ET = 11:00 UTC
    execution_timezone="US/Eastern",
    description="Daily data refresh at 6 AM ET",
)

# Weekly boxscore backfill on Sundays at 3 AM ET
weekly_boxscore_schedule = ScheduleDefinition(
    job=boxscore_backfill_job,
    cron_schedule="0 8 * * 0",  # 3 AM ET Sunday = 8:00 UTC
    execution_timezone="US/Eastern",
    description="Weekly boxscore backfill at 3 AM ET on Sundays",
)

schedules = [daily_refresh_schedule, weekly_boxscore_schedule]
jobs = [daily_refresh_job, boxscore_backfill_job]
