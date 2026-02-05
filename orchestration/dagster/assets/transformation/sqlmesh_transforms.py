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
import re
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
    DailyPartitionsDefinition,
    BackfillPolicy,
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

# Daily partitions for transformation assets (2010-01-01 for downstream training)
TRANSFORMATION_DAILY_PARTITIONS = DailyPartitionsDefinition(start_date="2010-01-01")

# Bundle N partitions per run to limit parallelism and avoid overwhelming Docker
TRANSFORMATION_BACKFILL_POLICY = BackfillPolicy.multi_run(max_partitions_per_run=30)


def _get_plan_date_range(context: AssetExecutionContext) -> tuple[str, str]:
    """Get (plan_start_date, plan_end_date) from context - supports single partition or multi-run range."""
    if hasattr(context, "partition_keys") and context.partition_keys:
        # BackfillPolicy.multi_run: one run covers multiple partitions
        start_date = min(context.partition_keys)
        end_date = (
            datetime.strptime(max(context.partition_keys), "%Y-%m-%d") + timedelta(days=1)
        ).strftime("%Y-%m-%d")
        return start_date, end_date
    # Single partition
    pk = context.partition_key
    end_date = (datetime.strptime(pk, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    return pk, end_date


# SQLMesh project path
SQLMESH_PROJECT_PATH = Path(__file__).parent.parent.parent.parent.parent / "transformation" / "sqlmesh"

# Single source of truth: logical table name for mart_game_features when using gateway "local".
# SQLMesh creates schema__environment.table (e.g. marts__local.mart_game_features). ML assets
# must read from this same table so they see the data the mart asset writes.
MART_GAME_FEATURES_TABLE = "marts__local.mart_game_features"


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


def _is_docker() -> bool:
    """True when running inside a Docker container (e.g. Dagster worker)."""
    return os.path.exists("/.dockerenv") or (
        os.path.exists("/proc/1/cgroup")
        and "docker" in Path("/proc/1/cgroup").read_text()
    )


def get_postgres_connection() -> psycopg2.extensions.connection:
    """Get PostgreSQL connection for monitoring and metadata.
    In Docker, use host 'postgres' so .env (POSTGRES_HOST=localhost) does not break connections."""
    host = "postgres" if _is_docker() else os.getenv("POSTGRES_HOST", "localhost")
    return psycopg2.connect(
        host=host,
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
    """Get approximate row count for a table. Tries schema__local as fallback (SQLMesh dev env)."""
    schemas_to_try = [schema]
    if schema in ("intermediate", "marts", "features_dev"):
        schemas_to_try.append(f"{schema}__local")
    for s in schemas_to_try:
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT reltuples::bigint AS estimate
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname = %s AND n.nspname = %s
            """, (table, s))
            result = cur.fetchone()
            cur.close()
            if result and result[0] is not None and result[0] >= 0:
                return int(result[0])
            if result and result[0] == -1:
                # Stats not run; use exact COUNT(*)
                cur = conn.cursor()
                cur.execute(f'SELECT COUNT(*) FROM "{s}"."{table}"')
                cnt = cur.fetchone()[0]
                cur.close()
                return int(cnt)
        except Exception as e:
            logger.debug(f"Could not get row count for {s}.{table}: {e}")
            continue
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
        elif 'cb_closeout_perf' in model_short or 'clutch_perf' in model_short or 'blowout_perf' in model_short or 'ot_perf' in model_short or '4q_perf' in model_short:
            # Game-level models: game_id for joins, game_date for range queries
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


# Staging model names (VIEWs); must be applied before any intermediate model runs
STAGING_MODELS = [
    "staging.stg_games",
    "staging.stg_team_boxscores",
    "staging.stg_teams",
    "staging.stg_injuries",
    "staging.stg_player_boxscores",
]


def _get_sqlmesh_exe() -> str:
    """Return path to sqlmesh executable (Docker vs local)."""
    is_docker = os.path.exists("/.dockerenv") or os.path.exists("/proc/1/cgroup")
    if is_docker:
        return "sqlmesh"
    sqlmesh_exe = Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python312" / "Scripts" / "sqlmesh.exe"
    return str(sqlmesh_exe) if sqlmesh_exe.exists() else "sqlmesh"


def execute_sqlmesh_plan_staging(context: AssetExecutionContext) -> None:
    """Run SQLMesh plan for all staging models so views exist before intermediate models."""
    start_time = datetime.now()
    project_dir = str(SQLMESH_PROJECT_PATH.absolute())
    original_dir = os.getcwd()
    os.chdir(project_dir)
    Path(project_dir).joinpath("logs").mkdir(exist_ok=True)
    env = os.environ.copy()
    if not (os.path.exists("/.dockerenv") or os.path.exists("/proc/1/cgroup")):
        env["POSTGRES_HOST"] = env.get("POSTGRES_HOST", "localhost")
    cmd = [_get_sqlmesh_exe(), "plan", "local", "--auto-apply", "--no-prompts"]
    for m in STAGING_MODELS:
        cmd.extend(["--select-model", m])
    context.log.info(f"Executing SQLMesh plan for staging views: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
        duration = (datetime.now() - start_time).total_seconds()
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            context.log.error(f"SQLMesh staging plan failed: {output[:1500]}")
            raise RuntimeError(f"SQLMesh plan for staging views failed (exit {result.returncode}): {output[:500]}")
        context.log.info(f"Staging views applied in {duration:.2f}s")
    finally:
        os.chdir(original_dir)


def execute_sqlmesh_plan_for_model(
    context: AssetExecutionContext,
    model_name: str,
    config: SQLMeshConfig
) -> None:
    """Execute SQLMesh plan for a specific model and return results with monitoring."""
    start_time = datetime.now()
    sqlmesh_exe = _get_sqlmesh_exe()
    
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
    cmd.append("--no-prompts")
    
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
        output = result.stdout or ""
        output += result.stderr or ""
        if result.returncode != 0:
            # If we reached "Updating virtual layer", model batches completed (VL runs after batches)
            batches_executed = (
                "Model batches executed" in output
                or "Executing model batches" in output
                or "Updating virtual layer" in output
            )
            virtual_layer_failed = "Updating virtual layer" in output and "Execution failed for node" in output
            # Check if the failed node is OUR model (not a downstream like game_features)
            failed_node_match = re.search(r"Execution failed for node\s+(SnapshotId<[^>]+>|[^\n]+)", output)
            our_table = model_name.split(".")[-1]  # e.g. int_team_clutch_perf
            failed_node_mentions_our_model = (
                bool(failed_node_match) and our_table in (failed_node_match.group(1) or "")
            )
            # Same logic as backfill_incremental_chunked.py: if our data was written but virtual
            # layer failed on a different model (race from concurrent plans), treat as success.
            if batches_executed and virtual_layer_failed and not failed_node_mentions_our_model:
                context.log.warning(
                    f"SQLMesh virtual layer update failed for a different model (likely concurrent plan race), "
                    f"but {model_name} data was materialized successfully. State may reconcile on next plan."
                )
                # Fall through to return success
            else:
                context.log.error(f"SQLMesh plan failed for {model_name}: {output[:2000]}")
                if blocking_locks_after:
                    context.log.error(f"Blocking locks detected after failure: {blocking_locks_after}")
                raise RuntimeError(f"SQLMesh plan failed for {model_name} with return code {result.returncode}: {output[:500]}")
        
        output = result.stdout or ""
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


# Staging views (must run before any intermediate model that reads from staging)
@asset(
    group_name="transformations",
    description="Apply SQLMesh staging views (stg_games, stg_team_boxscores, etc.) so intermediate models can run",
    deps=[nba_team_boxscores, nba_games, nba_boxscores, nba_injuries, nba_teams, nba_players],
)
def staging_views(context: AssetExecutionContext) -> None:
    """Create staging schema views that read from raw_dev.*."""
    execute_sqlmesh_plan_staging(context)


# Intermediate Models - Materialized tables
@asset(
    group_name="transformations",
    description="Materialize intermediate.int_team_rolling_stats - Team rolling statistics (last 5 and 10 games)",
    deps=[staging_views, nba_team_boxscores, nba_games],  # Staging views first, then raw data
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_team_rolling_stats(context: AssetExecutionContext) -> None:
    """Materialize intermediate.int_team_rolling_stats model with monitoring."""
    start_date, end_date = _get_plan_date_range(context)
    config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
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
    
    return None  # Opt out of IO manager so multi-partition backfills work


@asset(
    group_name="transformations",
    description="Materialize intermediate.int_team_roll_lkp - Pre-computed lookup table for latest rolling stats before each game",
    deps=[staging_views, int_team_rolling_stats, nba_games],  # SQL uses staging.stg_games, intermediate.int_team_rolling_stats
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_team_rolling_stats_lookup(context: AssetExecutionContext) -> None:
    """Materialize intermediate.int_team_roll_lkp - one partition = one day."""
    start_date, end_date = _get_plan_date_range(context)
    config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
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
    
    return None  # Opt out of IO manager so multi-partition backfills work


@asset(
    group_name="transformations",
    description="Materialize intermediate.int_team_season_stats - Team season aggregated statistics",
    deps=[staging_views, nba_team_boxscores, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_team_season_stats(context: AssetExecutionContext) -> None:
    """Materialize intermediate.int_team_season_stats model with monitoring."""
    start_date, end_date = _get_plan_date_range(context)
    config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
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
    
    return None  # Opt out of IO manager so multi-partition backfills work


@asset(
    group_name="transformations",
    description="Materialize intermediate.int_star_players - Identifies star players (top players by PPG)",
    deps=[staging_views, nba_boxscores],  # SQL uses staging.stg_player_boxscores, stg_games
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_star_players(context: AssetExecutionContext) -> None:
    """Materialize intermediate.int_star_players model with monitoring."""
    start_date, end_date = _get_plan_date_range(context)
    config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
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
    
    return None  # Opt out of IO manager so multi-partition backfills work


@asset(
    group_name="transformations",
    description="Materialize intermediate.int_star_player_avail - Star player game availability",
    deps=[staging_views, int_star_players, nba_boxscores],  # SQL uses staging.stg_player_boxscores, stg_games
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_star_player_availability(context: AssetExecutionContext) -> None:
    """Materialize intermediate.int_star_player_avail model with monitoring."""
    start_date, end_date = _get_plan_date_range(context)
    config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
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
    
    return None  # Opt out of IO manager so multi-partition backfills work


@asset(
    group_name="transformations",
    description="Materialize intermediate.int_game_injury_features - Injury impact features by game (VIEW)",
    deps=[staging_views, int_star_players, nba_games, nba_injuries, nba_boxscores],  # SQL uses staging.stg_*
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_game_injury_features(context: AssetExecutionContext) -> None:
    """Materialize intermediate.int_game_injury_features model with monitoring."""
    start_date, end_date = _get_plan_date_range(context)
    config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
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

    return None  # Opt out of IO manager so multi-partition backfills work


@asset(
    group_name="transformations",
    description="Materialize intermediate.int_team_star_feats - Team-level star player features",
    deps=[int_star_player_availability],  # View over star player availability only
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_team_star_player_features(context: AssetExecutionContext) -> None:
    """Materialize intermediate.int_team_star_feats model with monitoring."""
    start_date, end_date = _get_plan_date_range(context)
    config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
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
    
    return None  # Opt out of IO manager so multi-partition backfills work


@asset(
    group_name="transformations",
    description="Materialize intermediate.int_team_streaks - Team win/loss streaks",
    deps=[nba_games],  # Depends on games data
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_team_streaks(context: AssetExecutionContext) -> None:
    """Materialize intermediate.int_team_streaks model with monitoring."""
    start_date, end_date = _get_plan_date_range(context)
    config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
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
    
    return None  # Opt out of IO manager so multi-partition backfills work


@asset(
    group_name="transformations",
    description="Materialize intermediate.int_team_rest_days - Team rest days and back-to-back flags",
    deps=[nba_games],  # Depends on games data
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_team_rest_days(context: AssetExecutionContext) -> None:
    """Materialize intermediate.int_team_rest_days model with monitoring."""
    start_date, end_date = _get_plan_date_range(context)
    config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
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
    
    return None  # Opt out of IO manager so multi-partition backfills work


@asset(
    group_name="transformations",
    description="Materialize intermediate.int_team_h2h_stats - Head-to-head historical stats between teams",
    deps=[nba_games],  # Depends on games data
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_team_h2h_stats(context: AssetExecutionContext) -> None:
    """Materialize intermediate.int_team_h2h_stats model with monitoring."""
    start_date, end_date = _get_plan_date_range(context)
    config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
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
    
    return None  # Opt out of IO manager so multi-partition backfills work


@asset(
    group_name="transformations",
    description="Materialize intermediate.int_team_opponent_quality - Average win % of recent opponents",
    deps=[int_team_rolling_stats, int_team_season_stats, nba_games],  # SQL references int_team_rolling_stats (LATERAL)
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_team_opponent_quality(context: AssetExecutionContext) -> None:
    """Materialize intermediate.int_team_opponent_quality model with monitoring."""
    start_date, end_date = _get_plan_date_range(context)
    config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
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
    
    return None  # Opt out of IO manager so multi-partition backfills work


@asset(
    group_name="transformations",
    description="Materialize intermediate.int_away_upset_tend - Away team upset tendency (INCREMENTAL)",
    deps=[staging_views, int_team_rolling_stats, int_team_season_stats, nba_games],  # SQL uses staging.stg_games
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_away_team_upset_tendency(context: AssetExecutionContext) -> None:
    """Materialize intermediate.int_away_upset_tend - one partition = one day."""
    start_date, end_date = _get_plan_date_range(context)
    config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
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

    return None  # Opt out of IO manager so multi-partition backfills work


@asset(
    group_name="transformations",
    description="Materialize intermediate.int_team_home_road_perf - Team performance at home vs on the road",
    deps=[nba_games],  # Depends on games data
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_team_home_road_performance(context: AssetExecutionContext) -> None:
    """Materialize intermediate.int_team_home_road_perf model with monitoring."""
    start_date, end_date = _get_plan_date_range(context)
    config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
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
    
    return None  # Opt out of IO manager so multi-partition backfills work


# =============================================================================
# MOMENTUM UPSTREAM MODELS - Individual models that feed into the 5 groups
# =============================================================================
# Each can be run individually in Dagster for observability and selective backfill.

def _make_momentum_upstream_asset(model_name: str, description: str, deps: list):
    """Factory for momentum upstream assets - supports multi-run (N partitions per run)."""
    def _impl(context: AssetExecutionContext) -> None:
        start_date, end_date = _get_plan_date_range(context)
        config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
        result = execute_sqlmesh_plan_for_model(context, model_name, config)
        conn = get_postgres_connection()
        try:
            row_count = get_table_row_count(conn, "intermediate", model_name.split(".")[-1])
            indexes_created = create_indexes_for_sqlmesh_snapshot(conn, "intermediate", model_name)
            context.add_output_metadata({
                "duration_seconds": MetadataValue.float(result["duration_seconds"]),
                "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
                "indexes_created": MetadataValue.int(len(indexes_created)),
            })
        finally:
            conn.close()
        return None  # Opt out of IO manager so multi-partition backfills work
    return _impl

# Basic group upstreams (5 new - int_team_streaks, int_team_rest_days already exist)
int_team_cumulative_fatigue = asset(
    name="int_team_cumulative_fatigue",
    group_name="transformations",
    description="Materialize intermediate.int_team_cum_fatigue - Cumulative fatigue per team",
    deps=[int_team_rest_days, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_cum_fatigue", "Cumulative fatigue", []))

int_team_weighted_momentum = asset(
    name="int_team_weighted_momentum",
    group_name="transformations",
    description="Materialize intermediate.int_team_weighted_momentum - Weighted momentum",
    deps=[int_team_rolling_stats, int_team_rolling_stats_lookup, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_weighted_momentum", "Weighted momentum", []))

int_team_win_streak_quality = asset(
    name="int_team_win_streak_quality",
    group_name="transformations",
    description="Materialize intermediate.int_team_streak_qual - Win streak quality",
    deps=[int_team_streaks, int_team_rolling_stats_lookup, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_streak_qual", "Win streak quality", []))

int_team_contextualized_streaks = asset(
    name="int_team_contextualized_streaks",
    group_name="transformations",
    description="Materialize intermediate.int_team_ctx_streaks - Contextualized streaks",
    deps=[int_team_streaks, int_team_rolling_stats_lookup, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_ctx_streaks", "Contextualized streaks", []))

int_team_recent_momentum_exponential = asset(
    name="int_team_recent_momentum_exponential",
    group_name="transformations",
    description="Materialize intermediate.int_team_recent_mom_exp - Exponential momentum",
    deps=[staging_views, int_team_rolling_stats, nba_games],  # SQL uses staging
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_recent_mom_exp", "Exponential momentum", []))

# H2H group upstreams (2 new - int_team_h2h_stats already exists)
int_team_rivalry_indicators = asset(
    name="int_team_rivalry_indicators",
    group_name="transformations",
    description="Materialize intermediate.int_team_rivalry_ind - Rivalry indicators",
    deps=[staging_views, int_team_h2h_stats, nba_games],  # SQL uses staging.stg_games
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_rivalry_ind", "Rivalry indicators", []))

int_team_opponent_specific_performance = asset(
    name="int_team_opponent_specific_performance",
    group_name="transformations",
    description="Materialize intermediate.int_team_opp_specific_perf - Opponent-specific performance",
    deps=[staging_views, int_team_h2h_stats, int_team_rolling_stats_lookup, nba_games],  # SQL uses staging.stg_games
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_opp_specific_perf", "Opponent-specific perf", []))

# Opponent group upstreams (7 new - int_team_opponent_quality already exists)
int_team_opponent_tier_performance = asset(
    name="int_team_opponent_tier_performance",
    group_name="transformations",
    description="Materialize intermediate.int_team_opp_tier_perf - Opponent tier performance",
    deps=[int_team_opponent_quality, int_team_rolling_stats, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_opp_tier_perf", "Opponent tier perf", []))

int_team_opponent_tier_performance_by_home_away = asset(
    name="int_team_opponent_tier_performance_by_home_away",
    group_name="transformations",
    description="Materialize intermediate.int_team_opp_tier_ha - Tier perf by home/away",
    deps=[int_team_opponent_tier_performance, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_opp_tier_ha", "Tier perf home/away", []))

int_team_performance_vs_opponent_quality_by_rest = asset(
    name="int_team_performance_vs_opponent_quality_by_rest",
    group_name="transformations",
    description="Materialize intermediate.int_team_perf_vs_opp_qual_rest - Perf vs opp quality by rest",
    deps=[int_team_opponent_quality, int_team_rest_days, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_perf_vs_opp_qual_rest", "Perf vs opp qual rest", []))

int_team_performance_vs_similar_quality = asset(
    name="int_team_performance_vs_similar_quality",
    group_name="transformations",
    description="Materialize intermediate.int_team_perf_vs_similar_qual - Perf vs similar quality",
    deps=[int_team_opponent_quality, int_team_rolling_stats_lookup, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_perf_vs_similar_qual", "Perf vs similar qual", []))

int_team_quality_adjusted_performance = asset(
    name="int_team_quality_adjusted_performance",
    group_name="transformations",
    description="Materialize intermediate.int_team_quality_adj_perf - Quality-adjusted performance",
    deps=[int_team_opponent_quality, int_team_rolling_stats, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_quality_adj_perf", "Quality-adj perf", []))

int_team_opponent_similarity_performance = asset(
    name="int_team_opponent_similarity_performance",
    group_name="transformations",
    description="Materialize intermediate.int_team_opp_similarity_perf - Opponent similarity perf",
    deps=[int_team_opponent_quality, int_team_rolling_stats_lookup, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_opp_similarity_perf", "Opp similarity perf", []))

int_team_recent_performance_weighted_by_opponent_quality = asset(
    name="int_team_recent_performance_weighted_by_opponent_quality",
    group_name="transformations",
    description="Materialize intermediate.int_team_recent_perf_wt_opp_qual - Recent perf weighted by opp",
    deps=[int_team_opponent_quality, int_team_rolling_stats, int_team_rolling_stats_lookup, nba_games],  # SQL uses int_team_rolling_stats
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_recent_perf_wt_opp_qual", "Recent perf wt opp", []))

# Context group upstreams (13 new - int_team_home_road_performance already exists)
int_team_home_away_opp_qual = asset(
    name="int_team_home_away_opp_qual",
    group_name="transformations",
    description="Materialize intermediate.int_team_home_away_opp_qual - Home/away splits by opp quality",
    deps=[int_team_home_road_performance, int_team_opponent_quality, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_home_away_opp_qual", "Home/away opp qual", []))

int_team_home_away_rest_performance = asset(
    name="int_team_home_away_rest_performance",
    group_name="transformations",
    description="Materialize intermediate.int_team_home_away_rest_perf - Home/away rest performance",
    deps=[int_team_rest_days, int_team_rolling_stats_lookup, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_home_away_rest_perf", "Home/away rest perf", []))

int_team_clutch_performance = asset(
    name="int_team_clutch_performance",
    group_name="transformations",
    description="Materialize intermediate.int_team_clutch_perf - Clutch performance",
    deps=[int_team_rolling_stats_lookup, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_clutch_perf", "Clutch perf", []))

int_team_blowout_performance = asset(
    name="int_team_blowout_performance",
    group_name="transformations",
    description="Materialize intermediate.int_team_blowout_perf - Blowout performance",
    deps=[int_team_rolling_stats_lookup, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_blowout_perf", "Blowout perf", []))

int_team_comeback_closeout_performance = asset(
    name="int_team_comeback_closeout_performance",
    group_name="transformations",
    description="Materialize intermediate.int_team_cb_closeout_perf - Comeback/closeout perf",
    deps=[int_team_rolling_stats_lookup, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_cb_closeout_perf", "Comeback closeout", []))

int_team_overtime_performance = asset(
    name="int_team_overtime_performance",
    group_name="transformations",
    description="Materialize intermediate.int_team_ot_perf - Overtime performance",
    deps=[staging_views, int_team_rolling_stats_lookup, nba_games],  # SQL uses staging.stg_games
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_ot_perf", "Overtime perf", []))

int_team_fourth_quarter_performance = asset(
    name="int_team_fourth_quarter_performance",
    group_name="transformations",
    description="Materialize intermediate.int_team_4q_perf - Fourth quarter performance",
    deps=[staging_views, int_team_rolling_stats_lookup, nba_games],  # SQL uses staging.stg_*
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_4q_perf", "4th quarter perf", []))

int_team_game_outcome_quality = asset(
    name="int_team_game_outcome_quality",
    group_name="transformations",
    description="Materialize intermediate.int_team_outcome_qual - Game outcome quality",
    deps=[int_team_rolling_stats_lookup, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_outcome_qual", "Outcome quality", []))

int_team_upset_resistance = asset(
    name="int_team_upset_resistance",
    group_name="transformations",
    description="Materialize intermediate.int_team_upset_resistance - Upset resistance",
    deps=[int_team_rolling_stats, int_team_rolling_stats_lookup, nba_games],  # SQL uses int_team_rolling_stats
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_upset_resistance", "Upset resistance", []))

int_team_favored_underdog_performance = asset(
    name="int_team_favored_underdog_performance",
    group_name="transformations",
    description="Materialize intermediate.int_team_favored_underdog_perf - Favored/underdog perf",
    deps=[int_team_rolling_stats, int_team_rolling_stats_lookup, int_team_season_stats, nba_games],  # SQL uses int_team_rolling_stats
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_favored_underdog_perf", "Favored underdog", []))

int_team_recent_road_performance = asset(
    name="int_team_recent_road_performance",
    group_name="transformations",
    description="Materialize intermediate.int_team_road_perf - Recent road performance",
    deps=[int_team_rolling_stats_lookup, int_team_home_road_performance, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_road_perf", "Road perf", []))

int_team_season_timing_performance = asset(
    name="int_team_season_timing_performance",
    group_name="transformations",
    description="Materialize intermediate.int_team_timing_perf - Season timing performance",
    deps=[int_team_rolling_stats, int_team_season_stats, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_timing_perf", "Season timing", []))

int_team_playoff_race_context = asset(
    name="int_team_playoff_race_context",
    group_name="transformations",
    description="Materialize intermediate.int_team_playoff_ctx - Playoff race context",
    deps=[staging_views, int_team_season_stats, int_team_rolling_stats, nba_games],  # SQL uses staging
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_playoff_ctx", "Playoff context", []))

# Trends group upstreams (14 new)
int_team_form_divergence = asset(
    name="int_team_form_divergence",
    group_name="transformations",
    description="Materialize intermediate.int_team_form_divergence - Form divergence",
    deps=[staging_views, int_team_rolling_stats, int_team_season_stats, nba_games],  # SQL uses staging
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_form_divergence", "Form divergence", []))

int_team_performance_trends = asset(
    name="int_team_performance_trends",
    group_name="transformations",
    description="Materialize intermediate.int_team_perf_trends - Performance trends",
    deps=[int_team_rolling_stats, int_team_rolling_stats_lookup, nba_games],  # SQL uses int_team_rolling_stats
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_perf_trends", "Perf trends", []))

int_team_recent_momentum = asset(
    name="int_team_recent_momentum",
    group_name="transformations",
    description="Materialize intermediate.int_team_recent_momentum - Recent momentum",
    deps=[staging_views, int_team_rolling_stats, int_team_rolling_stats_lookup, nba_games],  # SQL uses staging
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_recent_momentum", "Recent momentum", []))

int_team_momentum_acceleration = asset(
    name="int_team_momentum_acceleration",
    group_name="transformations",
    description="Materialize intermediate.int_team_mom_accel - Momentum acceleration",
    deps=[staging_views, int_team_recent_momentum, int_team_rolling_stats_lookup, nba_games],  # SQL uses staging.stg_games
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_mom_accel", "Momentum accel", []))

int_team_performance_acceleration = asset(
    name="int_team_performance_acceleration",
    group_name="transformations",
    description="Materialize intermediate.int_team_perf_accel - Performance acceleration",
    deps=[staging_views, int_team_rolling_stats_lookup, nba_games],  # SQL uses staging
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_perf_accel", "Perf accel", []))

int_team_recent_form_trend = asset(
    name="int_team_recent_form_trend",
    group_name="transformations",
    description="Materialize intermediate.int_team_recent_form_trend - Recent form trend",
    deps=[staging_views, int_team_rolling_stats, int_team_rolling_stats_lookup, nba_games],  # SQL uses staging.stg_games
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_recent_form_trend", "Recent form", []))

int_team_performance_consistency = asset(
    name="int_team_performance_consistency",
    group_name="transformations",
    description="Materialize intermediate.int_team_perf_consistency - Performance consistency",
    deps=[int_team_rolling_stats_lookup, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_perf_consistency", "Perf consistency", []))

int_team_matchup_style_performance = asset(
    name="int_team_matchup_style_performance",
    group_name="transformations",
    description="Materialize intermediate.int_team_matchup_perf - Matchup style performance",
    deps=[int_team_rolling_stats_lookup, int_team_opponent_quality, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_matchup_perf", "Matchup style", []))

int_team_performance_vs_similar_momentum = asset(
    name="int_team_performance_vs_similar_momentum",
    group_name="transformations",
    description="Materialize intermediate.int_team_perf_vs_similar_mom - Perf vs similar momentum",
    deps=[int_team_recent_momentum, int_team_rolling_stats_lookup, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_perf_vs_similar_mom", "Perf vs similar mom", []))

int_team_performance_vs_similar_rest_context = asset(
    name="int_team_performance_vs_similar_rest_context",
    group_name="transformations",
    description="Materialize intermediate.int_team_perf_vs_rest_ctx - Perf vs rest context",
    deps=[int_team_rest_days, int_team_rolling_stats_lookup, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_perf_vs_rest_ctx", "Perf vs rest ctx", []))

int_team_performance_in_rest_advantage_scenarios = asset(
    name="int_team_performance_in_rest_advantage_scenarios",
    group_name="transformations",
    description="Materialize intermediate.int_team_perf_rest_adv - Perf in rest advantage scenarios",
    deps=[int_team_rest_days, int_team_rolling_stats_lookup, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_perf_rest_adv", "Rest advantage", []))

int_team_performance_by_rest_combination = asset(
    name="int_team_performance_by_rest_combination",
    group_name="transformations",
    description="Materialize intermediate.int_team_perf_by_rest_combo - Perf by rest combination",
    deps=[int_team_rest_days, int_team_rolling_stats_lookup, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_perf_by_rest_combo", "Rest combo", []))

int_team_matchup_compatibility = asset(
    name="int_team_matchup_compatibility",
    group_name="transformations",
    description="Materialize intermediate.int_team_matchup_compat - Matchup compatibility",
    deps=[int_team_rolling_stats, int_team_matchup_style_performance, int_team_opponent_quality, nba_games],  # SQL uses int_team_rolling_stats
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_matchup_compat", "Matchup compat", []))

int_team_performance_by_pace_context = asset(
    name="int_team_performance_by_pace_context",
    group_name="transformations",
    description="Materialize intermediate.int_team_perf_by_pace_ctx - Perf by pace context",
    deps=[staging_views, int_team_rolling_stats_lookup, nba_games],  # SQL uses staging.stg_team_boxscores
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)(_make_momentum_upstream_asset("intermediate.int_team_perf_by_pace_ctx", "Pace context", []))


# =============================================================================
# MOMENTUM FEATURE GROUPS - Pre-computed aggregations for efficient backfill
# =============================================================================
# These 5 groups split the 50+ upstream models into manageable chunks.
# Each group can be backfilled independently, then int_game_momentum_features
# just joins the 5 groups (fast).

@asset(
    group_name="transformations",
    description="Feature Group 1: Basic momentum (streaks, rest, fatigue, weighted momentum)",
    deps=[
        int_team_streaks, int_team_rest_days,
        int_team_cumulative_fatigue, int_team_weighted_momentum, int_team_win_streak_quality,
        int_team_contextualized_streaks, int_team_recent_momentum_exponential,
        nba_games,
    ],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_momentum_group_basic(context: AssetExecutionContext) -> None:
    """Materialize intermediate.int_momentum_group_basic - 7 upstream models aggregated."""
    start_date, end_date = _get_plan_date_range(context)
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
    return None  # Opt out of IO manager so multi-partition backfills work


@asset(
    group_name="transformations",
    description="Feature Group 2: Head-to-head and rivalry features",
    deps=[int_team_h2h_stats, int_team_rivalry_indicators, int_team_opponent_specific_performance, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_momentum_group_h2h(context: AssetExecutionContext) -> None:
    """Materialize intermediate.int_momentum_group_h2h - 3 upstream models aggregated."""
    start_date, end_date = _get_plan_date_range(context)
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
    return None  # Opt out of IO manager so multi-partition backfills work


@asset(
    group_name="transformations",
    description="Feature Group 3: Opponent quality and tier performance features",
    deps=[
        int_team_opponent_quality, int_team_opponent_tier_performance,
        int_team_opponent_tier_performance_by_home_away,
        int_team_performance_vs_opponent_quality_by_rest, int_team_performance_vs_similar_quality,
        int_team_quality_adjusted_performance, int_team_opponent_similarity_performance,
        int_team_recent_performance_weighted_by_opponent_quality,
        int_team_season_stats, nba_games,
    ],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_momentum_group_opponent(context: AssetExecutionContext) -> None:
    """Materialize intermediate.int_momentum_group_opponent - 8 upstream models aggregated."""
    start_date, end_date = _get_plan_date_range(context)
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
    return None  # Opt out of IO manager so multi-partition backfills work


@asset(
    group_name="transformations",
    description="Feature Group 4: Game context (home/away, clutch, season timing, playoffs)",
    deps=[
        int_team_home_road_performance, int_team_home_away_opp_qual, int_team_home_away_rest_performance,
        int_team_clutch_performance, int_team_blowout_performance, int_team_comeback_closeout_performance,
        int_team_overtime_performance, int_team_fourth_quarter_performance, int_team_game_outcome_quality,
        int_team_upset_resistance, int_team_favored_underdog_performance, int_team_recent_road_performance,
        int_team_season_timing_performance, int_team_playoff_race_context,
        nba_games,
    ],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_momentum_group_context(context: AssetExecutionContext) -> None:
    """Materialize intermediate.int_momentum_group_context - 14 upstream models aggregated."""
    start_date, end_date = _get_plan_date_range(context)
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
    return None  # Opt out of IO manager so multi-partition backfills work


@asset(
    group_name="transformations",
    description="Feature Group 5: Trends, acceleration, consistency, and matchup features",
    deps=[
        int_team_form_divergence, int_team_performance_trends, int_team_recent_momentum,
        int_team_momentum_acceleration, int_team_performance_acceleration, int_team_recent_form_trend,
        int_team_performance_consistency, int_team_matchup_style_performance,
        int_team_performance_vs_similar_momentum, int_team_performance_vs_similar_rest_context,
        int_team_performance_in_rest_advantage_scenarios, int_team_performance_by_rest_combination,
        int_team_matchup_compatibility, int_team_performance_by_pace_context,
        int_team_rolling_stats, nba_games,
    ],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_momentum_group_trends(context: AssetExecutionContext) -> None:
    """Materialize intermediate.int_momentum_group_trends - 14 upstream models aggregated."""
    start_date, end_date = _get_plan_date_range(context)
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
    return None  # Opt out of IO manager so multi-partition backfills work


# =============================================================================
# COMBINED MOMENTUM FEATURES - Joins the 5 pre-computed groups
# =============================================================================

@asset(
    group_name="transformations",
    description="Materialize intermediate.int_game_momentum_features - Joins 5 pre-computed feature groups",
    deps=[int_momentum_group_basic, int_momentum_group_h2h, int_momentum_group_opponent, int_momentum_group_context, int_momentum_group_trends],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def int_game_momentum_features(context: AssetExecutionContext) -> None:
    """Materialize intermediate.int_game_momentum_features - joins 5 pre-computed groups."""
    start_date, end_date = _get_plan_date_range(context)
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
    
    return None  # Opt out of IO manager so multi-partition backfills work


# Mart: game features table (ML and predictions read from this directly)
@asset(
    group_name="transformations",
    description="Materialize marts.mart_game_features - ML-ready game features table (incremental)",
    deps=[staging_views, int_team_rolling_stats_lookup, int_team_season_stats, int_star_player_availability, int_team_star_player_features, int_game_injury_features, int_away_team_upset_tendency, int_game_momentum_features, nba_games],
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def mart_game_features(context: AssetExecutionContext) -> None:
    """Materialize marts.mart_game_features for ML training and predictions.
    Writes to SQLMesh 'local' environment; ML assets read from MART_GAME_FEATURES_TABLE."""
    start_date, end_date = _get_plan_date_range(context)
    config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
    result = execute_sqlmesh_plan_for_model(context, "marts.mart_game_features", config)
    conn = get_postgres_connection()
    try:
        row_count = get_table_row_count(conn, "marts", "mart_game_features")
        indexes_created = create_indexes_for_sqlmesh_snapshot(
            conn, "marts", "marts.mart_game_features"
        )
        context.add_output_metadata({
            "table": MetadataValue.text(MART_GAME_FEATURES_TABLE),
            "duration_seconds": MetadataValue.float(result["duration_seconds"]),
            "row_count": MetadataValue.int(row_count) if row_count is not None else MetadataValue.text("N/A"),
            "indexes_created": MetadataValue.int(len(indexes_created)),
        })
    finally:
        conn.close()
    return None


# Feature Models
@asset(
    group_name="transformations",
    description="Materialize features_dev.team_features - Team-level features",
    deps=[int_team_season_stats],  # Full model over season stats only
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def team_features(context: AssetExecutionContext) -> None:
    """Materialize features_dev.team_features - one partition = one day."""
    start_date, end_date = _get_plan_date_range(context)
    config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
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
    
    return None  # Opt out of IO manager so multi-partition backfills work


@asset(
    group_name="transformations",
    description="Materialize features_dev.team_injury_features - Team injury features",
    deps=[staging_views, nba_injuries],  # SQL uses staging.stg_injuries
    partitions_def=TRANSFORMATION_DAILY_PARTITIONS,
    backfill_policy=TRANSFORMATION_BACKFILL_POLICY,
    automation_condition=AutomationCondition.eager(),  # Run automatically when upstreams complete
    retry_policy=RetryPolicy(max_retries=2, delay=60),
)
def team_injury_features(context: AssetExecutionContext) -> None:
    """Materialize features_dev.team_injury_features - one partition = one day."""
    start_date, end_date = _get_plan_date_range(context)
    config = SQLMeshConfig(plan_start_date=start_date, plan_end_date=end_date)
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
    
    return None  # Opt out of IO manager so multi-partition backfills work
