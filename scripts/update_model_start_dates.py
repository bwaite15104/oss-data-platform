#!/usr/bin/env python3
"""Update all SQLMesh model start dates from 2010-10-01 to 1946-11-01 for full history backfill."""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = PROJECT_ROOT / "transformation" / "sqlmesh" / "models" / "intermediate"

OLD_START = "start '2010-10-01'"
NEW_START = "start '1946-11-01',  -- Updated for full history backfill"

def update_file(file_path: Path) -> bool:
    """Update start date in a single file. Returns True if changed."""
    content = file_path.read_text(encoding="utf-8")
    if OLD_START in content:
        new_content = content.replace(OLD_START, NEW_START)
        file_path.write_text(new_content, encoding="utf-8")
        return True
    return False

def main():
    updated_count = 0
    for sql_file in MODELS_DIR.glob("*.sql"):
        if update_file(sql_file):
            updated_count += 1
            print(f"Updated: {sql_file.name}")
    
    print(f"\nUpdated {updated_count} files")

if __name__ == "__main__":
    main()
