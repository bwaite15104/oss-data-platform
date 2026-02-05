"""
SQLMesh Backfill Script - Force Rematerialization

This script addresses SQLMesh state management issues by forcing rematerialization
of models using SQLMesh's backfill command. Use this when:
- SQLMesh doesn't detect upstream data changes
- Manual tables were created outside SQLMesh's snapshot system
- Models need to be rebuilt from a specific start date

Based on recommendations from sqlmesh-change-detection-investigation.md
"""

import os
import subprocess
import sys
import argparse
from pathlib import Path
from typing import List, Optional
from datetime import datetime


def get_sqlmesh_executable() -> str:
    """Get SQLMesh executable path."""
    # Check if we're in Docker
    is_docker = os.path.exists("/.dockerenv") or os.path.exists("/proc/1/cgroup")
    
    if is_docker:
        return "sqlmesh"
    else:
        # Try Windows path
        sqlmesh_exe = Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python312" / "Scripts" / "sqlmesh.exe"
        if sqlmesh_exe.exists():
            return str(sqlmesh_exe)
        # Fallback to system PATH
        return "sqlmesh"


def run_sqlmesh_backfill(
    model_name: str,
    start_date: str = "2010-10-01",
    gateway: str = "local",
    project_path: Optional[Path] = None,
    auto_apply: bool = True,
    verbose: bool = False
) -> bool:
    """
    Run SQLMesh backfill for a specific model.
    
    Args:
        model_name: Full model name (e.g., "marts.mart_game_features")
        start_date: Start date for backfill (YYYY-MM-DD)
        gateway: SQLMesh gateway/environment name
        project_path: Path to SQLMesh project (defaults to transformation/sqlmesh)
        auto_apply: Whether to auto-apply the plan
        verbose: Print verbose output
        
    Returns:
        True if successful, False otherwise
    """
    if project_path is None:
        # Default to transformation/sqlmesh relative to script location
        script_dir = Path(__file__).parent.parent
        project_path = script_dir / "transformation" / "sqlmesh"
    
    project_path = project_path.absolute()
    
    if not project_path.exists():
        print(f"Error: SQLMesh project path does not exist: {project_path}", file=sys.stderr)
        return False
    
    sqlmesh_exe = get_sqlmesh_executable()
    
    # Build command
    cmd = [
        sqlmesh_exe,
        "plan",
        gateway,
        "--backfill",
        "--start", start_date,
        "--select-model", model_name,
    ]
    
    if auto_apply:
        cmd.append("--auto-apply")
    
    print(f"Running SQLMesh backfill for model: {model_name}")
    print(f"Start date: {start_date}")
    print(f"Command: {' '.join(cmd)}")
    print(f"Working directory: {project_path}")
    print("-" * 80)
    
    # Change to project directory
    original_dir = os.getcwd()
    os.chdir(str(project_path))
    
    try:
        # Ensure logs directory exists
        logs_dir = project_path / "logs"
        logs_dir.mkdir(exist_ok=True)
        
        # Run command
        result = subprocess.run(
            cmd,
            capture_output=not verbose,  # Show output if verbose
            text=True,
            timeout=3600,  # 1 hour timeout
        )
        
        if result.returncode != 0:
            print(f"Error: SQLMesh backfill failed with return code {result.returncode}", file=sys.stderr)
            if not verbose and result.stderr:
                print("STDERR:", result.stderr, file=sys.stderr)
            if not verbose and result.stdout:
                print("STDOUT:", result.stdout, file=sys.stderr)
            return False
        
        if verbose and result.stdout:
            print(result.stdout)
        
        print(f"\n✓ Successfully backfilled model: {model_name}")
        return True
        
    except subprocess.TimeoutExpired:
        print(f"Error: SQLMesh backfill timed out after 1 hour", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return False
    finally:
        os.chdir(original_dir)


def backfill_model_chain(
    models: List[str],
    start_date: str = "2010-10-01",
    gateway: str = "local",
    project_path: Optional[Path] = None,
    auto_apply: bool = True,
    verbose: bool = False
) -> bool:
    """
    Backfill multiple models in sequence (for dependency chains).
    
    Args:
        models: List of model names in dependency order
        start_date: Start date for backfill
        gateway: SQLMesh gateway/environment name
        project_path: Path to SQLMesh project
        auto_apply: Whether to auto-apply the plan
        verbose: Print verbose output
        
    Returns:
        True if all successful, False otherwise
    """
    print(f"Backfilling {len(models)} models in sequence...")
    print(f"Models: {', '.join(models)}")
    print("=" * 80)
    
    for i, model in enumerate(models, 1):
        print(f"\n[{i}/{len(models)}] Backfilling {model}...")
        success = run_sqlmesh_backfill(
            model_name=model,
            start_date=start_date,
            gateway=gateway,
            project_path=project_path,
            auto_apply=auto_apply,
            verbose=verbose
        )
        
        if not success:
            print(f"\n✗ Failed to backfill {model}. Stopping chain.", file=sys.stderr)
            return False
    
    print("\n" + "=" * 80)
    print("✓ All models backfilled successfully!")
    return True


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Force SQLMesh model rematerialization using backfill",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Backfill a single model
  python scripts/sqlmesh_backfill.py marts.mart_game_features

  # Backfill with custom start date
  python scripts/sqlmesh_backfill.py marts.mart_game_features --start 2015-01-01

  # Backfill mart (ML reads from marts.mart_game_features directly)
  python scripts/sqlmesh_backfill.py marts.mart_game_features

  # Dry run (no auto-apply)
  python scripts/sqlmesh_backfill.py marts.mart_game_features --no-auto-apply

  # Run from Docker container
  docker exec nba_analytics_dagster_webserver python /app/scripts/sqlmesh_backfill.py marts.mart_game_features

  # Chunked backfill (year-by-year) for lookup table - use full-history start
  python scripts/sqlmesh_backfill.py intermediate.int_team_rolling_stats_lookup --start 1946-11-01
        """
    )
    
    parser.add_argument(
        "models",
        nargs="+",
        help="Model name(s) to backfill (e.g., marts.mart_game_features). If multiple, backfill in order."
    )
    
    parser.add_argument(
        "--start",
        default="2010-10-01",
        help="Start date for backfill (YYYY-MM-DD). Default: 2010-10-01"
    )
    
    parser.add_argument(
        "--gateway",
        default="local",
        help="SQLMesh gateway/environment name. Default: local"
    )
    
    parser.add_argument(
        "--project-path",
        type=Path,
        help="Path to SQLMesh project (defaults to transformation/sqlmesh)"
    )
    
    parser.add_argument(
        "--no-auto-apply",
        action="store_true",
        help="Don't auto-apply the plan (dry run)"
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print verbose output"
    )
    
    args = parser.parse_args()
    
    # Validate start date format
    try:
        datetime.strptime(args.start, "%Y-%m-%d")
    except ValueError:
        print(f"Error: Invalid date format '{args.start}'. Use YYYY-MM-DD", file=sys.stderr)
        sys.exit(1)
    
    # Run backfill
    if len(args.models) == 1:
        success = run_sqlmesh_backfill(
            model_name=args.models[0],
            start_date=args.start,
            gateway=args.gateway,
            project_path=args.project_path,
            auto_apply=not args.no_auto_apply,
            verbose=args.verbose
        )
    else:
        success = backfill_model_chain(
            models=args.models,
            start_date=args.start,
            gateway=args.gateway,
            project_path=args.project_path,
            auto_apply=not args.no_auto_apply,
            verbose=args.verbose
        )
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
