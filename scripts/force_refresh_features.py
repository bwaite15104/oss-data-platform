#!/usr/bin/env python3
"""Force refresh game_features by running SQLMesh eval directly."""
import subprocess
import os

os.chdir('/app/transformation/sqlmesh')

# Try to force SQLMesh to evaluate and materialize
result = subprocess.run(
    ['sqlmesh', 'eval', 'local', 'features_dev.game_features', '2026-01-19', '2026-01-19'],
    capture_output=True,
    text=True
)
print("STDOUT:", result.stdout)
print("STDERR:", result.stderr)
print("Return code:", result.returncode)
