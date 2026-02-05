"""
SQLMesh Table Diff Script - Investigate State Issues

This script uses SQLMesh's table_diff feature to compare expected vs actual state.
Use this to diagnose SQLMesh state management issues.

Based on recommendations from sqlmesh-change-detection-investigation.md
"""

import os
import subprocess
import sys
import argparse
from pathlib import Path
from typing import Optional


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


def run_table_diff(
    model_name: str,
    gateway: str = "local",
    project_path: Optional[Path] = None,
    environment: Optional[str] = None
) -> bool:
    """
    Run SQLMesh table_diff for a specific model.
    
    Args:
        model_name: Full model name (e.g., "marts.mart_game_features")
        gateway: SQLMesh gateway/environment name
        project_path: Path to SQLMesh project (defaults to transformation/sqlmesh)
        environment: Optional environment to compare (defaults to gateway)
        
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
    # SQLMesh table_diff syntax: sqlmesh table_diff ENV MODEL [OPTIONS]
    cmd = [
        sqlmesh_exe,
        "table_diff",
        environment or gateway,
        model_name,
    ]
    
    print(f"Running SQLMesh table_diff for model: {model_name}")
    print(f"Environment: {environment or gateway}")
    print(f"Command: {' '.join(cmd)}")
    print(f"Working directory: {project_path}")
    print("-" * 80)
    
    # Change to project directory
    original_dir = os.getcwd()
    os.chdir(str(project_path))
    
    try:
        # Run command (table_diff outputs to stdout)
        result = subprocess.run(
            cmd,
            capture_output=False,  # Show output directly
            text=True,
            timeout=300,  # 5 minute timeout
        )
        
        if result.returncode != 0:
            print(f"\nError: SQLMesh table_diff failed with return code {result.returncode}", file=sys.stderr)
            return False
        
        return True
        
    except subprocess.TimeoutExpired:
        print(f"\nError: SQLMesh table_diff timed out after 5 minutes", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return False
    finally:
        os.chdir(original_dir)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Compare SQLMesh expected vs actual table state",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Compare state for a model
  python scripts/sqlmesh_table_diff.py marts.mart_game_features

  # Compare with specific environment
  python scripts/sqlmesh_table_diff.py marts.mart_game_features --environment prod

  # Run from Docker container
  docker exec nba_analytics_dagster_webserver python /app/scripts/sqlmesh_table_diff.py marts.mart_game_features
        """
    )
    
    parser.add_argument(
        "model",
        help="Model name to compare (e.g., marts.mart_game_features)"
    )
    
    parser.add_argument(
        "--gateway",
        default="local",
        help="SQLMesh gateway/environment name. Default: local"
    )
    
    parser.add_argument(
        "--environment",
        help="Specific environment to compare (defaults to gateway)"
    )
    
    parser.add_argument(
        "--project-path",
        type=Path,
        help="Path to SQLMesh project (defaults to transformation/sqlmesh)"
    )
    
    args = parser.parse_args()
    
    # Run table_diff
    success = run_table_diff(
        model_name=args.model,
        gateway=args.gateway,
        project_path=args.project_path,
        environment=args.environment
    )
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
