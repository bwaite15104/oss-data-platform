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
from datetime import datetime, timedelta

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
    # Limit plan range to leverage existing backfill; SQLMesh skips already-materialized intervals
    plan_start_date: Optional[str] = None  # e.g. "2026-01-01" or None for full range
    plan_end_date: Optional[str] = None    # e.g. "2026-02-01" (exclusive) or None
    # When set, only this model's missing intervals are backfilled (upstreams must already be materialized for the range)
    backfill_model: Optional[str] = None  # e.g. "intermediate.int_game_momentum_features"
    # Timeout in seconds for subprocess execution (default 4 hours for heavy models)
    timeout_seconds: int = 14400  # 4 hours default; heavy models like int_game_momentum_features need this


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


def create_indexes_for_sqlmesh_snapshot(
    conn,
    schema: str,
    model_name: str,
    snapshot_table_name: Optional[str] = None
) -> List[str]:
    """
    Create performance indexes on SQLMesh snapshot tables after materialization.
    
    Args:
        conn: PostgreSQL connection
        schema: Schema name (e.g., 'intermediate', 'features_dev')
        model_name: SQLMesh model name (e.g., 'intermediate.int_team_rolling_stats')
        snapshot_table_name: Optional explicit snapshot table name. If None, finds the latest snapshot.
    
    Returns:
        List of index names created
    """
    indexes_created = []
    cur = conn.cursor()
    
    try:
        # If snapshot_table_name not provided, find the latest snapshot for this model
        if not snapshot_table_name:
            # SQLMesh snapshot tables follow pattern: intermediate__model_name__hash
            # Extract model name from full model name (e.g., 'intermediate.int_team_rolling_stats' -> 'int_team_rolling_stats')
            model_short = model_name.split('.')[-1] if '.' in model_name else model_name
            pattern = f"intermediate__{model_short}__%" if schema == "intermediate" else f"{schema}__{model_short}__%"
            
            cur.execute(f"""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = %s 
                  AND table_name LIKE %s
                  AND table_type = 'BASE TABLE'
                ORDER BY table_name DESC
                LIMIT 1
            """, (schema, pattern))
            result = cur.fetchone()
            if not result:
                logger.warning(f"No snapshot table found for {schema}.{model_name}")
                return indexes_created
            snapshot_table_name = result[0]
        
        # Define index strategies based on model name
        model_short = model_name.split('.')[-1] if '.' in model_name else model_name
        
        if 'rolling_stats_lookup' in model_short.lower():
            # Indexes for rolling stats lookup: game_id + team_id for fast joins
            indexes = [
                (f"idx_{snapshot_table_name}_game_team", "(game_id, team_id)"),
                (f"idx_{snapshot_table_name}_team_date", "(team_id, game_date DESC)"),
            ]
        elif 'rolling_stats' in model_short.lower():
            # Indexes for rolling stats: team_id + game_date for joins, game_id for lookups
            indexes = [
                (f"idx_{snapshot_table_name}_team_date", "(team_id, game_date DESC)"),
                (f"idx_{snapshot_table_name}_game_id", "(game_id)"),
            ]
        elif 'star_players' in model_short.lower():
            # Indexes for star players: player_id + team_id
            indexes = [
                (f"idx_{snapshot_table_name}_player_team", "(player_id, team_id)"),
            ]
        elif 'season_stats' in model_short.lower():
            # Indexes for season stats: team_id
            indexes = [
                (f"idx_{snapshot_table_name}_team_id", "(team_id)"),
            ]
        elif 'game_features' in model_short.lower() or 'mart_game_features' in model_short.lower():
            # Indexes for game features: game_id, game_date
            indexes = [
                (f"idx_{snapshot_table_name}_game_id", "(game_id)"),
                (f"idx_{snapshot_table_name}_game_date", "(game_date)"),
            ]
        else:
            # Default: try to find common join columns
            cur.execute(f"""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_schema = %s 
                  AND table_name = %s
                  AND column_name IN ('game_id', 'team_id', 'player_id', 'game_date')
                ORDER BY CASE column_name
                    WHEN 'game_id' THEN 1
                    WHEN 'team_id' THEN 2
                    WHEN 'player_id' THEN 3
                    WHEN 'game_date' THEN 4
                END
            """, (schema, snapshot_table_name))
            columns = [row[0] for row in cur.fetchall()]
            indexes = []
            if 'game_id' in columns:
                indexes.append((f"idx_{snapshot_table_name}_game_id", "(game_id)"))
            if 'team_id' in columns and 'game_date' in columns:
                indexes.append((f"idx_{snapshot_table_name}_team_date", "(team_id, game_date DESC)"))
            elif 'team_id' in columns:
                indexes.append((f"idx_{snapshot_table_name}_team_id", "(team_id)"))
        
        # Create indexes
        for idx_name, columns_expr in indexes:
            try:
                # columns_expr is already the column expression like "(team_id, game_date DESC)"
                create_sql = f"CREATE INDEX IF NOT EXISTS {idx_name} ON {schema}.{snapshot_table_name}{columns_expr}"
                cur.execute(create_sql)
                indexes_created.append(idx_name)
                logger.info(f"Created index {idx_name} on {schema}.{snapshot_table_name}")
            except Exception as e:
                logger.warning(f"Failed to create index {idx_name}: {e}")
        
        # Run ANALYZE to update query planner statistics
        try:
            cur.execute(f"ANALYZE {schema}.{snapshot_table_name}")
            logger.info(f"Ran ANALYZE on {schema}.{snapshot_table_name}")
        except Exception as e:
            logger.warning(f"Failed to ANALYZE {schema}.{snapshot_table_name}: {e}")
        
        conn.commit()
        
    except Exception as e:
        logger.error(f"Error creating indexes for {schema}.{model_name}: {e}")
        conn.rollback()
    finally:
        cur.close()
    
    return indexes_created


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
        cmd.extend(["--backfill-model", model_name, "--start", config.backfill_start_date])
        context.log.info(f"Using backfill mode with start date: {config.backfill_start_date}")
    
    # Limit plan range to leverage existing backfill; SQLMesh skips already-materialized intervals
    if config.plan_start_date:
        cmd.extend(["--start", config.plan_start_date])
    if config.plan_end_date:
        cmd.extend(["--end", config.plan_end_date])
    if config.plan_start_date or config.plan_end_date:
        context.log.info(f"Plan range: --start {config.plan_start_date or '(default)'} --end {config.plan_end_date or '(default)'}")
    # Only backfill this model's intervals (assumes upstreams already materialized for the range)
    if config.backfill_model:
        cmd.extend(["--backfill-model", config.backfill_model])
        context.log.info(f"Backfill limited to model: {config.backfill_model}")
    
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
            timeout=config.timeout_seconds,  # Configurable timeout (default 4 hours for heavy models)
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
                context.log.error(f"SQLMesh plan timed out for {model_name} after {duration:.2f}s (timeout={config.timeout_seconds}s)")
                context.log.error(f"Active queries at timeout: {len(active_queries_final)}")
                context.log.error(f"Blocking locks at timeout: {len(blocking_locks_final)}")
                for lock in blocking_locks_final:
                    context.log.error(f"  Blocked PID {lock['blocked_pid']} waiting on PID {lock['blocking_pid']}")
                context.log.error(f"For full backfills of heavy models, use the chunked backfill script:")
                context.log.error(f"  docker exec nba_analytics_dagster_webserver python /app/scripts/backfill_incremental_chunked.py {model_name} --start-year 2020 --chunk-days 30 --timeout 3600")
            except Exception:
                pass
            finally:
                conn.close()
        raise RuntimeError(f"SQLMesh plan timed out for {model_name} after {duration:.2f}s (timeout={config.timeout_seconds}s). For full backfills, use: docker exec nba_analytics_dagster_webserver python /app/scripts/backfill_incremental_chunked.py {model_name} --start-year 2020 --chunk-days 30 --timeout 3600")
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
    
    # Get table row count and create indexes for performance
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_team_rolling_stats")
        
        # Create indexes on the newly materialized snapshot table
        indexes_created = create_indexes_for_sqlmesh_snapshot(
            conn, "intermediate", "intermediate.int_team_rolling_stats"
        )
        
        metadata = {
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
            "indexes_created": MetadataValue.int(len(indexes_created)),
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
    description="Materialize intermediate.int_team_roll_lkp - Pre-computed lookup table for latest rolling stats before each game",
    deps=[int_team_rolling_stats, nba_games],  # Depends on rolling stats and games
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_team_rolling_stats_lookup(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_team_roll_lkp (incremental by game_date, year-by-year chunks)."""
    # Use full-history start so backfill processes all yearly intervals; model has batch_size=1 for one year per job
    config = SQLMeshConfig(backfill=True, backfill_start_date="1946-11-01")
    result = execute_sqlmesh_plan_for_model(context, "intermediate.int_team_roll_lkp", config)
    
    # Get table row count and create indexes for performance
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_team_roll_lkp")
        
        # Create indexes on the newly materialized snapshot table
        indexes_created = create_indexes_for_sqlmesh_snapshot(
            conn, "intermediate", "intermediate.int_team_roll_lkp"
        )
        
        metadata = {
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
            "indexes_created": MetadataValue.int(len(indexes_created)),
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
    
    # Get table row count and create indexes for performance
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_star_players")
        
        # Create indexes on the newly materialized snapshot table
        indexes_created = create_indexes_for_sqlmesh_snapshot(
            conn, "intermediate", "intermediate.int_star_players"
        )
        
        metadata = {
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
            "indexes_created": MetadataValue.int(len(indexes_created)),
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
    description="Materialize intermediate.int_star_player_avail - Star player game availability",
    deps=[int_star_players, nba_boxscores],  # Depends on star players and boxscores
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_star_player_availability(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_star_player_avail model with monitoring."""
    config = SQLMeshConfig()
    result = execute_sqlmesh_plan_for_model(context, "intermediate.int_star_player_avail", config)
    
    # Get table row count for metadata
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_star_player_avail")
        
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
    description="Materialize intermediate.int_game_injury_features - Injury impact features by game (VIEW)",
    deps=[int_star_players, nba_games, nba_injuries, nba_boxscores],
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_game_injury_features(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_game_injury_features model with monitoring."""
    config = SQLMeshConfig()
    result = execute_sqlmesh_plan_for_model(context, "intermediate.int_game_injury_features", config)

    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_game_injury_features")
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
    description="Materialize intermediate.int_team_star_feats - Team-level star player features",
    deps=[int_star_player_availability],  # View over star player availability only
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_team_star_player_features(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_team_star_feats model with monitoring."""
    config = SQLMeshConfig()
    result = execute_sqlmesh_plan_for_model(context, "intermediate.int_team_star_feats", config)
    
    # Get table row count for metadata
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_team_star_feats")
        
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
    description="Materialize intermediate.int_away_upset_tend - Away team upset tendency (INCREMENTAL, needs backfill)",
    deps=[int_team_season_stats, int_team_rolling_stats, nba_games],
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_away_team_upset_tendency(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_away_upset_tend (incremental by game_date).
    Uses last-30-day plan range to leverage existing backfill. Full backfill: run
    backfill_incremental_chunked.py intermediate.int_away_upset_tend."""
    today = datetime.now().date()
    start_date = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")  # SQLMesh rejects end date in the future
    config = SQLMeshConfig(backfill=True, backfill_start_date=start_date, plan_start_date=start_date, plan_end_date=end_date)
    result = execute_sqlmesh_plan_for_model(context, "intermediate.int_away_upset_tend", config)

    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_away_upset_tend")
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
    description="Materialize intermediate.int_team_home_road_perf - Team performance at home vs on the road",
    deps=[nba_games],  # Depends on games data
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_team_home_road_performance(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_team_home_road_perf model with monitoring."""
    config = SQLMeshConfig()
    result = execute_sqlmesh_plan_for_model(context, "intermediate.int_team_home_road_perf", config)
    
    # Get table row count for metadata
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_team_home_road_perf")
        
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


# =============================================================================
# MOMENTUM FEATURE GROUPS - Pre-computed aggregations for efficient backfill
# =============================================================================
# These 5 groups split the 50+ upstream models into manageable chunks.
# Each group can be backfilled independently, then int_game_momentum_features
# just joins the 5 groups (fast).

@asset(
    group_name="transformations",
    description="Feature Group 1: Basic momentum (streaks, rest, fatigue, weighted momentum)",
    deps=[int_team_streaks, int_team_rest_days, nba_games],
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_momentum_group_basic(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_momentum_group_basic - 7 upstream models aggregated.
    
    Includes: streaks, rest days, cumulative fatigue, weighted momentum, win streak quality,
    contextualized streaks, exponential momentum.
    """
    today = datetime.now().date()
    start_date = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")
    config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
    result = execute_sqlmesh_plan_for_model(context, "intermediate.int_momentum_group_basic", config)
    
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_momentum_group_basic")
        context.add_output_metadata({
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
        })
    finally:
        conn.close()
    return result


@asset(
    group_name="transformations",
    description="Feature Group 2: Head-to-head and rivalry features",
    deps=[int_team_h2h_stats, nba_games],
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_momentum_group_h2h(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_momentum_group_h2h - 3 upstream models aggregated.
    
    Includes: h2h stats, rivalry indicators, opponent-specific performance.
    """
    today = datetime.now().date()
    start_date = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")
    config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
    result = execute_sqlmesh_plan_for_model(context, "intermediate.int_momentum_group_h2h", config)
    
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_momentum_group_h2h")
        context.add_output_metadata({
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
        })
    finally:
        conn.close()
    return result


@asset(
    group_name="transformations",
    description="Feature Group 3: Opponent quality and tier performance features",
    deps=[int_team_opponent_quality, int_team_season_stats, nba_games],
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_momentum_group_opponent(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_momentum_group_opponent - 8 upstream models aggregated.
    
    Includes: opponent quality, tier performance, quality-adjusted stats, similar opponents.
    """
    today = datetime.now().date()
    start_date = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")
    config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
    result = execute_sqlmesh_plan_for_model(context, "intermediate.int_momentum_group_opponent", config)
    
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_momentum_group_opponent")
        context.add_output_metadata({
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
        })
    finally:
        conn.close()
    return result


@asset(
    group_name="transformations",
    description="Feature Group 4: Game context (home/away, clutch, season timing, playoffs)",
    deps=[int_team_home_road_performance, nba_games],
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_momentum_group_context(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_momentum_group_context - 14 upstream models aggregated.
    
    Includes: home/road splits, clutch/blowout performance, overtime, fourth quarter,
    favored/underdog, season timing, playoff race context.
    """
    today = datetime.now().date()
    start_date = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")
    config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
    result = execute_sqlmesh_plan_for_model(context, "intermediate.int_momentum_group_context", config)
    
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_momentum_group_context")
        context.add_output_metadata({
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
        })
    finally:
        conn.close()
    return result


@asset(
    group_name="transformations",
    description="Feature Group 5: Trends, acceleration, consistency, and matchup features",
    deps=[int_team_rolling_stats, nba_games],
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_momentum_group_trends(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_momentum_group_trends - 14 upstream models aggregated.
    
    Includes: form divergence, performance trends, momentum acceleration,
    consistency, matchup style, pace context, rest scenarios.
    """
    today = datetime.now().date()
    start_date = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")
    config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
    result = execute_sqlmesh_plan_for_model(context, "intermediate.int_momentum_group_trends", config)
    
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "intermediate", "int_momentum_group_trends")
        context.add_output_metadata({
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
        })
    finally:
        conn.close()
    return result


# =============================================================================
# COMBINED MOMENTUM FEATURES - Joins the 5 pre-computed groups
# =============================================================================

@asset(
    group_name="transformations",
    description="Materialize intermediate.int_game_momentum_features - Joins 5 pre-computed feature groups",
    deps=[int_momentum_group_basic, int_momentum_group_h2h, int_momentum_group_opponent, int_momentum_group_context, int_momentum_group_trends],
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_game_momentum_features(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize intermediate.int_game_momentum_features - now just joins 5 pre-computed groups.
    
    This model is now FAST because it joins 5 materialized group tables instead of 50+ individual models.
    
    FULL HISTORICAL BACKFILL: First backfill each of the 5 groups, then this model:
    
        # Backfill groups (can run in parallel)
        docker exec nba_analytics_dagster_webserver python /app/scripts/backfill_incremental_chunked.py \\
            intermediate.int_momentum_group_basic --start-year 2020 --chunk-days 90 --timeout 1800
        
        # After groups are done, backfill the combined model (fast)
        docker exec nba_analytics_dagster_webserver python /app/scripts/backfill_incremental_chunked.py \\
            intermediate.int_game_momentum_features --start-year 2020 --chunk-days 90 --timeout 600
    """
    today = datetime.now().date()
    start_date = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")
    config = SQLMeshConfig(
        plan_start_date=start_date,
        plan_end_date=end_date,
        backfill_model="intermediate.int_game_momentum_features",
    )
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
    deps=[int_team_rolling_stats_lookup, int_team_season_stats, int_star_player_availability, int_team_star_player_features, int_game_injury_features, int_away_team_upset_tendency, int_game_momentum_features, nba_games],  # Via mart_game_features: all upstream intermediates
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def game_features(context: AssetExecutionContext) -> Dict[str, Any]:
    """Materialize features_dev.game_features model with monitoring.
    Uses last-30-day plan range; SQLMesh skips already-materialized intervals.
    
    FULL HISTORICAL BACKFILL: Ensure int_game_momentum_features is backfilled first, then:
        docker exec nba_analytics_dagster_webserver python /app/scripts/backfill_incremental_chunked.py \\
            features_dev.game_features --start-year 2020 --end-year 2026 --chunk-days 30 --timeout 3600
    """
    today = datetime.now().date()
    start_date = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")  # SQLMesh rejects end date in the future
    config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
    result = execute_sqlmesh_plan_for_model(context, "features_dev.game_features", config)
    
    # Get table row count and create indexes for performance
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "features_dev", "game_features")
        
        # Create indexes on the newly materialized snapshot table
        indexes_created = create_indexes_for_sqlmesh_snapshot(
            conn, "features_dev", "features_dev.game_features"
        )
        
        metadata = {
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
            "indexes_created": MetadataValue.int(len(indexes_created)),
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
    deps=[int_team_season_stats],  # Full model over season stats only
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
