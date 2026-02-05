"""Force rebuild of marts.mart_game_features (game_features view was removed)."""

import subprocess
import sys
from pathlib import Path

# Project root
project_root = Path(__file__).resolve().parent.parent

def main():
    print("The features_dev.game_features view was removed.")
    print("Use marts.mart_game_features directly. To force rebuild the mart:")
    print("  sqlmesh plan local --auto-apply --select-model marts.mart_game_features")
    print("\nOr materialize via Dagster: mart_game_features asset.")
    # Optional: run sqlmesh plan
    if len(sys.argv) > 1 and sys.argv[1] == "--run":
        cmd = ["sqlmesh", "plan", "local", "--auto-apply", "--select-model", "marts.mart_game_features"]
        return subprocess.call(cmd, cwd=str(project_root))
    return 0

if __name__ == "__main__":
    sys.exit(main())
