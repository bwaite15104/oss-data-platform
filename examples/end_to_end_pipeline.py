"""
End-to-end pipeline example demonstrating contract composition workflow.

This example shows:
1. Composing contracts from schemas and quality rules
2. Generating tool configs
3. Running a simple data pipeline
"""

import subprocess
import sys
from pathlib import Path

def main():
    """Run end-to-end example."""
    project_root = Path(__file__).parent.parent
    
    print("1. Composing contracts...")
    result = subprocess.run(
        ["python", "contracts/composer.py", "--all"],
        cwd=project_root,
    )
    if result.returncode != 0:
        print("❌ Contract composition failed")
        return 1
    
    print("2. Generating tool configs...")
    result = subprocess.run(
        ["python", "tools/generate_configs.py", "--tools", "baselinr"],
        cwd=project_root,
    )
    if result.returncode != 0:
        print("❌ Config generation failed")
        return 1
    
    print("3. Validating ODCS configs...")
    result = subprocess.run(
        ["python", "tools/validate_odcs.py", "--check-contracts"],
        cwd=project_root,
    )
    if result.returncode != 0:
        print("❌ Validation failed")
        return 1
    
    print("✅ End-to-end pipeline example completed successfully!")
    print("\nNext steps:")
    print("  - Start infrastructure: make docker-up")
    print("  - Run Baselinr profiling: baselinr profile --config configs/generated/baselinr/baselinr_config.yml")
    print("  - Start Dagster: cd orchestration/dagster && dagster dev")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())

