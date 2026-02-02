#!/usr/bin/env python3
"""
Helper script to fix incremental models missing date filters.
This script identifies the patterns and suggests fixes, but doesn't auto-apply them.
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

def find_patterns(content):
    """Find common patterns that need fixing."""
    patterns = []
    
    # Pattern 1: FROM raw_dev.games or staging tables without date filter
    if re.search(r'FROM\s+(raw_dev\.games|staging\.stg_\w+)\s+g\s+WHERE', content, re.IGNORECASE):
        if '@end_ds' not in content.split('FROM')[1].split('WHERE')[0]:
            patterns.append("CTE needs @end_ds filter")
    
    # Pattern 2: Final SELECT without date filter
    if re.search(r'FROM\s+(raw_dev\.games|staging\.stg_\w+)\s+g\s*;', content, re.IGNORECASE):
        patterns.append("Final SELECT needs date filter")
    
    return patterns

models_to_fix = []
for sql_file in sorted(models_dir.glob("*.sql")):
    content = sql_file.read_text(encoding='utf-8')
    if needs_fix(content):
        patterns = find_patterns(content)
        models_to_fix.append((sql_file.name, patterns))

print(f"Found {len(models_to_fix)} models that need fixes:\n")
for model, patterns in models_to_fix:
    print(f"  - {model}")
    if patterns:
        for p in patterns:
            print(f"    {p}")
