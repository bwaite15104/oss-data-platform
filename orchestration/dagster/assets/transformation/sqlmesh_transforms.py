"""
Dagster assets for SQLMesh transformations.

Orchestrates SQLMesh plan and apply operations with visibility into:
- Query progress and duration
- Blocking locks
- Materialization status
- Model dependencies

Each SQLMesh model is represented as an individual Dagster asset for better observability.
"""

import os
import subprocess
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime

import psycopg2
from dagster import (
    asset,
    AssetExecutionContext,
    MetadataValue,
    RetryPolicy,
    AutomationCondition,
)
from pydantic import BaseModel

# Import ingestion assets to create dependencies
from orchestration.dagster.assets.ingestion.nba import (
    nba_teams,
    nba_players,
    nba_games,
    nba_boxscores,
    nba_team_boxscores,
    nba_injuries,
)

logger = logging.getLogger(__name__)

# SQLMesh project path
SQLMESH_PROJECT_PATH = Path(__file__).parent.parent.parent.parent.parent / "transformation" / "sqlmesh"


class SQLMeshConfig(BaseModel):
    """Configuration for SQLMesh execution."""
    project_path: Path = SQLMESH_PROJECT_PATH
    gateway: str = "local"
    auto_apply: bool = True
    select_model: Optional[str] = None  # If set, only process specific model
    backfill: bool = False  # If True, use backfill command to force rematerialization
    backfill_start_date: str = "2010-10-01"  # Start date for backfill (YYYY-MM-DD)


def get_postgres_connection() -> psycopg2.extensions.connection:
    """Get PostgreSQL connection for monitoring."""
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        database=os.getenv("POSTGRES_DB", "nba_analytics"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", "postgres"),
    )


def get_active_queries(conn) -> List[Dict[str, Any]]:
    """Get active queries from PostgreSQL."""
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            pid,
            state,
            wait_event_type,
            wait_event,
            NOW() - query_start as duration,
            LEFT(query, 200) as query_preview
        FROM pg_stat_activity 
        WHERE datname = current_database()
            AND state != 'idle'
            AND pid != pg_backend_pid()
        ORDER BY query_start;
    """)
    rows = cur.fetchall()
    cur.close()
    return [
        {
            "pid": r[0],
            "state": r[1],
            "wait_type": r[2],
            "wait_event": r[3],
            "duration": str(r[4]),
            "query": r[5],
        }
        for r in rows
    ]


def get_blocking_locks(conn) -> List[Dict[str, Any]]:
    """Get blocking lock information."""
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            blocked_locks.pid AS blocked_pid,
            blocking_locks.pid AS blocking_pid,
            NOW() - blocked_activity.query_start AS blocked_duration
        FROM pg_catalog.pg_locks blocked_locks
        JOIN pg_catalog.pg_stat_activity blocked_activity 
            ON blocked_activity.pid = blocked_locks.pid
        JOIN pg_catalog.pg_locks blocking_locks 
            ON blocking_locks.locktype = blocked_locks.locktype
            AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
            AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
            AND blocking_locks.pid != blocked_locks.pid
        JOIN pg_catalog.pg_stat_activity blocking_activity 
            ON blocking_activity.pid = blocking_locks.pid
        WHERE NOT blocked_locks.granted;
    """)
    rows = cur.fetchall()
    cur.close()
    return [
        {
            "blocked_pid": r[0],
            "blocking_pid": r[1],
            "blocked_duration": str(r[2]),
        }
        for r in rows
    ]


def get_table_row_count(conn, schema: str, table: str) -> Optional[int]:
    """Get approximate row count for a table."""
    try:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT reltuples::bigint AS estimate
            FROM pg_class
            WHERE relname = '{table}'
            AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = '{schema}');
        """)
        result = cur.fetchone()
        cur.close()
        return int(result[0]) if result and result[0] else None
    except Exception as e:
        logger.warning(f"Could not get row count for {schema}.{table}: {e}")
        return None


def execute_sqlmesh_plan_for_model(
    context: AssetExecutionContext,
    model_name: str,
    config: SQLMeshConfig
) -> Dict[str, Any]:
    """Execute SQLMesh plan for a specific model and return results with monitoring."""
    start_time = datetime.now()
    
    # Get SQLMesh executable - in Docker it's just "sqlmesh", on Windows it might be sqlmesh.exe
    # Check if we're in Docker (common indicators)
    is_docker = os.path.exists("/.dockerenv") or os.path.exists("/proc/1/cgroup")
    if is_docker:
        sqlmesh_exe = "sqlmesh"
    else:
        # Try Windows path first
        sqlmesh_exe = Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python312" / "Scripts" / "sqlmesh.exe"
        if not sqlmesh_exe.exists():
            sqlmesh_exe = "sqlmesh"
        else:
            sqlmesh_exe = str(sqlmesh_exe)
    
    # Build command for specific model
    # SQLMesh uses environment as positional argument: `sqlmesh plan ENVIRONMENT [OPTIONS]`
    cmd = [
        str(sqlmesh_exe),
        "plan",
        config.gateway,  # Environment/gateway as positional argument (e.g., "local", "prod")
        "--select-model", model_name,
    ]
    
    # Add backfill options if requested (forces rematerialization)
    if config.backfill:
        cmd.extend(["--backfill", "--start", config.backfill_start_date])
        context.log.info(f"Using backfill mode with start date: {config.backfill_start_date}")
    
    if config.auto_apply:
        cmd.append("--auto-apply")
    
    # Change to SQLMesh project directory
    project_dir = str(config.project_path.absolute())
    original_dir = os.getcwd()
    os.chdir(project_dir)
    
    # Ensure logs directory exists to avoid FileNotFoundError when SQLMesh tries to clean old logs
    logs_dir = Path(project_dir) / "logs"
    try:
        logs_dir.mkdir(exist_ok=True)
    except Exception as e:
        context.log.warning(f"Could not create logs directory: {e}")
    
    # Set environment variables for SQLMesh to use correct PostgreSQL host
    # Inside Docker, postgres service name is "postgres", locally it's "localhost"
    is_docker = os.path.exists("/.dockerenv") or os.path.exists("/proc/1/cgroup")
    env = os.environ.copy()
    if is_docker:
        # SQLMesh config uses environment variables if available, or we can override config
        # For now, we'll rely on the config.yaml but ensure we're in the right directory
        context.log.info(f"Running in Docker - SQLMesh should use config.yaml from {project_dir}")
    else:
        # Local execution - ensure we use localhost
        env["POSTGRES_HOST"] = env.get("POSTGRES_HOST", "localhost")
    
    context.log.info(f"Executing SQLMesh plan for model: {model_name}")
    context.log.info(f"Command: {' '.join(cmd)}")
    context.log.info(f"Working directory: {project_dir}")
    
    # Monitor database during execution
    conn = None
    active_queries_before = []
    blocking_locks_before = []
    
    try:
        conn = get_postgres_connection()
        active_queries_before = get_active_queries(conn)
        blocking_locks_before = get_blocking_locks(conn)
        
        if active_queries_before:
            context.log.warning(f"Found {len(active_queries_before)} active queries before SQLMesh execution")
        if blocking_locks_before:
            context.log.warning(f"Found {len(blocking_locks_before)} blocking locks before SQLMesh execution")
            for lock in blocking_locks_before:
                context.log.warning(f"  Blocked PID {lock['blocked_pid']} waiting on PID {lock['blocking_pid']}")
    except Exception as e:
        context.log.warning(f"Could not connect to database for monitoring: {e}")
    
    # Execute SQLMesh plan
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,  # 1 hour timeout
            env=env,  # Pass environment variables
        )
        
        duration = (datetime.now() - start_time).total_seconds()
        
        # Monitor after execution
        active_queries_after = []
        blocking_locks_after = []
        if conn:
            try:
                active_queries_after = get_active_queries(conn)
                blocking_locks_after = get_blocking_locks(conn)
            except Exception:
                pass
            finally:
                conn.close()
        
        # Restore original directory
        os.chdir(original_dir)
        
        # Check results
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout
            context.log.error(f"SQLMesh plan failed for {model_name}: {error_msg}")
            
            # Log any remaining locks or queries
            if blocking_locks_after:
                context.log.error(f"Blocking locks detected after failure: {blocking_locks_after}")
            
            raise RuntimeError(f"SQLMesh plan failed for {model_name} with return code {result.returncode}: {error_msg}")
        
        output = result.stdout
        context.log.info(f"SQLMesh plan completed for {model_name} in {duration:.2f}s")
        
        # Extract metadata from output
        models_processed = output.count("Executing model batches") if output else 0
        failed_models = output.count("Failed models") if output else 0
        
        return {
            "success": True,
            "model_name": model_name,
            "duration_seconds": duration,
            "stdout": output,
            "stderr": result.stderr,
            "models_processed": models_processed,
            "failed_models": failed_models,
            "active_queries_before": len(active_queries_before),
            "blocking_locks_before": len(blocking_locks_before),
            "active_queries_after": len(active_queries_after),
            "blocking_locks_after": len(blocking_locks_after),
        }
        
    except subprocess.TimeoutExpired:
        duration = (datetime.now() - start_time).total_seconds()
        os.chdir(original_dir)
        if conn:
            try:
                # Get final state before closing
                active_queries_final = get_active_queries(conn)
                blocking_locks_final = get_blocking_locks(conn)
                context.log.error(f"SQLMesh plan timed out for {model_name} after {duration:.2f}s")
                context.log.error(f"Active queries at timeout: {len(active_queries_final)}")
                context.log.error(f"Blocking locks at timeout: {len(blocking_locks_final)}")
                for lock in blocking_locks_final:
                    context.log.error(f"  Blocked PID {lock['blocked_pid']} waiting on PID {lock['blocking_pid']}")
            except Exception:
                pass
            finally:
                conn.close()
        raise RuntimeError(f"SQLMesh plan timed out for {model_name} after {duration:.2f}s")
    except Exception as e:
        os.chdir(original_dir)
        if conn:
            conn.close()
        raise


# Intermediate Models - Materialized tables
@asset(
    group_name="transformations",
    description="Materialize intermediate.int_team_rolling_stats - Team rolling statistics (last 5 and 10 games)",
    deps=[nba_team_boxscores, nba_games],  # Depends on team boxscores and games data
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_team_rolling_stats(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_team_rolling_stats model with monitoring."""
    config = SQLMeshConfig()
    result = execute_sqlmesh_plan_for_model(context, "intermediate.int_team_rolling_stats", config)
    
    # Get table row count for metadata
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_team_rolling_stats")
        
        metadata = {
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
            "active_queries_before": MetadataValue.int(result["active_queries_before"]),
            "blocking_locks_before": MetadataValue.int(result["blocking_locks_before"]),
            "active_queries_after": MetadataValue.int(result["active_queries_after"]),
            "blocking_locks_after": MetadataValue.int(result["blocking_locks_after"]),
        }
        
        context.add_output_metadata(metadata)
    finally:
        conn.close()
    
    return result


@asset(
    group_name="transformations",
    description="Materialize intermediate.int_team_season_stats - Team season aggregated statistics",
    deps=[nba_team_boxscores, nba_games],  # Depends on team boxscores and games data
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_team_season_stats(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_team_season_stats model with monitoring."""
    config = SQLMeshConfig()
    result = execute_sqlmesh_plan_for_model(context, "intermediate.int_team_season_stats", config)
    
    # Get table row count for metadata
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_team_season_stats")
        
        metadata = {
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
            "active_queries_before": MetadataValue.int(result["active_queries_before"]),
            "blocking_locks_before": MetadataValue.int(result["blocking_locks_before"]),
            "active_queries_after": MetadataValue.int(result["active_queries_after"]),
            "blocking_locks_after": MetadataValue.int(result["blocking_locks_after"]),
        }
        
        context.add_output_metadata(metadata)
    finally:
        conn.close()
    
    return result


@asset(
    group_name="transformations",
    description="Materialize intermediate.int_star_players - Identifies star players (top players by PPG)",
    deps=[nba_boxscores],  # Depends on player boxscores data
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_star_players(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_star_players model with monitoring."""
    config = SQLMeshConfig()
    result = execute_sqlmesh_plan_for_model(context, "intermediate.int_star_players", config)
    
    # Get table row count for metadata
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_star_players")
        
        metadata = {
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
            "active_queries_before": MetadataValue.int(result["active_queries_before"]),
            "blocking_locks_before": MetadataValue.int(result["blocking_locks_before"]),
            "active_queries_after": MetadataValue.int(result["active_queries_after"]),
            "blocking_locks_after": MetadataValue.int(result["blocking_locks_after"]),
        }
        
        context.add_output_metadata(metadata)
    finally:
        conn.close()
    
    return result


@asset(
    group_name="transformations",
    description="Materialize intermediate.int_star_player_availability - Star player game availability",
    deps=[int_star_players, nba_boxscores],  # Depends on star players and boxscores
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_star_player_availability(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_star_player_availability model with monitoring."""
    config = SQLMeshConfig()
    result = execute_sqlmesh_plan_for_model(context, "intermediate.int_star_player_availability", config)
    
    # Get table row count for metadata
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_star_player_availability")
        
        metadata = {
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
            "active_queries_before": MetadataValue.int(result["active_queries_before"]),
            "blocking_locks_before": MetadataValue.int(result["blocking_locks_before"]),
            "active_queries_after": MetadataValue.int(result["active_queries_after"]),
            "blocking_locks_after": MetadataValue.int(result["blocking_locks_after"]),
        }
        
        context.add_output_metadata(metadata)
    finally:
        conn.close()
    
    return result


@asset(
    group_name="transformations",
    description="Materialize intermediate.int_team_star_player_features - Team-level star player features",
    deps=[int_star_player_availability, int_team_rolling_stats],  # Depends on star player availability and team stats
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_team_star_player_features(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_team_star_player_features model with monitoring."""
    config = SQLMeshConfig()
    result = execute_sqlmesh_plan_for_model(context, "intermediate.int_team_star_player_features", config)
    
    # Get table row count for metadata
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_team_star_player_features")
        
        metadata = {
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
            "active_queries_before": MetadataValue.int(result["active_queries_before"]),
            "blocking_locks_before": MetadataValue.int(result["blocking_locks_before"]),
            "active_queries_after": MetadataValue.int(result["active_queries_after"]),
            "blocking_locks_after": MetadataValue.int(result["blocking_locks_after"]),
        }
        
        context.add_output_metadata(metadata)
    finally:
        conn.close()
    
    return result


@asset(
    group_name="transformations",
    description="Materialize intermediate.int_team_streaks - Team win/loss streaks",
    deps=[nba_games],  # Depends on games data
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_team_streaks(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_team_streaks model with monitoring."""
    config = SQLMeshConfig()
    result = execute_sqlmesh_plan_for_model(context, "intermediate.int_team_streaks", config)
    
    # Get table row count for metadata
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_team_streaks")
        
        metadata = {
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
            "active_queries_before": MetadataValue.int(result["active_queries_before"]),
            "blocking_locks_before": MetadataValue.int(result["blocking_locks_before"]),
            "active_queries_after": MetadataValue.int(result["active_queries_after"]),
            "blocking_locks_after": MetadataValue.int(result["blocking_locks_after"]),
        }
        
        context.add_output_metadata(metadata)
    finally:
        conn.close()
    
    return result


@asset(
    group_name="transformations",
    description="Materialize intermediate.int_team_rest_days - Team rest days and back-to-back flags",
    deps=[nba_games],  # Depends on games data
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_team_rest_days(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_team_rest_days model with monitoring."""
    config = SQLMeshConfig()
    result = execute_sqlmesh_plan_for_model(context, "intermediate.int_team_rest_days", config)
    
    # Get table row count for metadata
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_team_rest_days")
        
        metadata = {
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
            "active_queries_before": MetadataValue.int(result["active_queries_before"]),
            "blocking_locks_before": MetadataValue.int(result["blocking_locks_before"]),
            "active_queries_after": MetadataValue.int(result["active_queries_after"]),
            "blocking_locks_after": MetadataValue.int(result["blocking_locks_after"]),
        }
        
        context.add_output_metadata(metadata)
    finally:
        conn.close()
    
    return result


@asset(
    group_name="transformations",
    description="Materialize intermediate.int_team_h2h_stats - Head-to-head historical stats between teams",
    deps=[nba_games],  # Depends on games data
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_team_h2h_stats(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_team_h2h_stats model with monitoring."""
    config = SQLMeshConfig()
    result = execute_sqlmesh_plan_for_model(context, "intermediate.int_team_h2h_stats", config)
    
    # Get table row count for metadata
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_team_h2h_stats")
        
        metadata = {
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
            "active_queries_before": MetadataValue.int(result["active_queries_before"]),
            "active_queries_after": MetadataValue.int(result["active_queries_after"]),
        }
        context.add_output_metadata(metadata)
    finally:
        conn.close()
    
    return result


@asset(
    group_name="transformations",
    description="Materialize intermediate.int_team_opponent_quality - Average win % of recent opponents",
    deps=[nba_games, int_team_season_stats],  # Depends on games and season stats
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_team_opponent_quality(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_team_opponent_quality model with monitoring."""
    config = SQLMeshConfig()
    result = execute_sqlmesh_plan_for_model(context, "intermediate.int_team_opponent_quality", config)
    
    # Get table row count for metadata
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_team_opponent_quality")
        
        metadata = {
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
            "active_queries_before": MetadataValue.int(result["active_queries_before"]),
            "active_queries_after": MetadataValue.int(result["active_queries_after"]),
        }
        context.add_output_metadata(metadata)
    finally:
        conn.close()
    
    return result


@asset(
    group_name="transformations",
    description="Materialize intermediate.int_team_home_road_performance - Team performance at home vs on the road",
    deps=[nba_games],  # Depends on games data
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_team_home_road_performance(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_team_home_road_performance model with monitoring."""
    config = SQLMeshConfig()
    result = execute_sqlmesh_plan_for_model(context, "intermediate.int_team_home_road_performance", config)
    
    # Get table row count for metadata
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_team_home_road_performance")
        
        metadata = {
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
            "active_queries_before": MetadataValue.int(result["active_queries_before"]),
            "active_queries_after": MetadataValue.int(result["active_queries_after"]),
        }
        context.add_output_metadata(metadata)
    finally:
        conn.close()
    
    return result


@asset(
    group_name="transformations",
    description="Materialize intermediate.int_game_momentum_features - Combined momentum features (streaks, rest days, form divergence, h2h, opponent quality, home/road)",
    deps=[int_team_streaks, int_team_rest_days, int_team_rolling_stats, int_team_season_stats, int_team_h2h_stats, int_team_opponent_quality, int_team_home_road_performance],  # Depends on all momentum-related models
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_game_momentum_features(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_game_momentum_features model with monitoring."""
    config = SQLMeshConfig()
    result = execute_sqlmesh_plan_for_model(context, "intermediate.int_game_momentum_features", config)
    
    # Get table row count for metadata
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_game_momentum_features")
        
        metadata = {
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
            "active_queries_before": MetadataValue.int(result["active_queries_before"]),
            "blocking_locks_before": MetadataValue.int(result["blocking_locks_before"]),
            "active_queries_after": MetadataValue.int(result["active_queries_after"]),
            "blocking_locks_after": MetadataValue.int(result["blocking_locks_after"]),
        }
        
        context.add_output_metadata(metadata)
    finally:
        conn.close()
    
    return result


# Feature Models - Materialized tables
@asset(
    group_name="transformations",
    description="Materialize features_dev.game_features - ML-ready game features for prediction models",
    deps=[int_team_rolling_stats, int_team_season_stats, int_team_star_player_features, nba_games],  # Depends on all intermediate stats and games
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def game_features(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize features_dev.game_features model with monitoring."""
    config = SQLMeshConfig()
    result = execute_sqlmesh_plan_for_model(context, "features_dev.game_features", config)
    
    # Get table row count for metadata
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "features_dev", "game_features")
        
        metadata = {
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
            "active_queries_before": MetadataValue.int(result["active_queries_before"]),
            "blocking_locks_before": MetadataValue.int(result["blocking_locks_before"]),
            "active_queries_after": MetadataValue.int(result["active_queries_after"]),
            "blocking_locks_after": MetadataValue.int(result["blocking_locks_after"]),
        }
        
        context.add_output_metadata(metadata)
    finally:
        conn.close()
    
    return result


@asset(
    group_name="transformations",
    description="Materialize features_dev.team_features - Team-level features",
    deps=[int_team_season_stats, nba_teams],  # Depends on season stats and teams data
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def team_features(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize features_dev.team_features model with monitoring."""
    config = SQLMeshConfig()
    result = execute_sqlmesh_plan_for_model(context, "features_dev.team_features", config)
    
    # Get table row count for metadata
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "features_dev", "team_features")
        
        metadata = {
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
            "active_queries_before": MetadataValue.int(result["active_queries_before"]),
            "blocking_locks_before": MetadataValue.int(result["blocking_locks_before"]),
            "active_queries_after": MetadataValue.int(result["active_queries_after"]),
            "blocking_locks_after": MetadataValue.int(result["blocking_locks_after"]),
        }
        
        context.add_output_metadata(metadata)
    finally:
        conn.close()
    
    return result


@asset(
    group_name="transformations",
    description="Materialize features_dev.team_injury_features - Team injury features",
    deps=[nba_injuries],  # Depends on injuries data
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def team_injury_features(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize features_dev.team_injury_features model with monitoring."""
    config = SQLMeshConfig()
    result = execute_sqlmesh_plan_for_model(context, "features_dev.team_injury_features", config)
    
    # Get table row count for metadata
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "features_dev", "team_injury_features")
        
        metadata = {
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
            "active_queries_before": MetadataValue.int(result["active_queries_before"]),
            "blocking_locks_before": MetadataValue.int(result["blocking_locks_before"]),
            "active_queries_after": MetadataValue.int(result["active_queries_after"]),
            "blocking_locks_after": MetadataValue.int(result["blocking_locks_after"]),
        }
        
        context.add_output_metadata(metadata)
    finally:
        conn.close()
    
    return result
