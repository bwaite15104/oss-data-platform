#!/usr/bin/env python3
"""
Batch fix incremental models by applying date filters.
This script shows what needs to be fixed but doesn't auto-apply (for safety).
"""

from pathlib import Path
import re

project_root = Path(__file__).parent.parent
models_dir = project_root / "transformation" / "sqlmesh" / "models" / "intermediate"

def needs_fix(content):
    """Check if model needs date filter fixes."""
    if "@start_ds" in content and "@end_ds" in content:
        return False
    if "INCREMENTAL_BY_TIME_RANGE" not in content:
        return False
    return True

models_to_fix = []
for sql_file in sorted(models_dir.glob("*.sql")):
    content = sql_file.read_text(encoding='utf-8')
    if needs_fix(content):
        models_to_fix.append(sql_file.name)

print(f"Remaining models to fix: {len(models_to_fix)}")
for model in models_to_fix:
    print(f"  - {model}")
