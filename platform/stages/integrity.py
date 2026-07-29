#!/usr/bin/env python3
"""
DAG integrity checker — BUILD_PLAN.md §3.20.

Usage: python -m platform.stages.integrity
Returns non-zero if any error-severity violations exist.
"""

import sys
from pathlib import Path

# Ensure the repo root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from publisher_stages import get_registry
import stages.acquire_stage  # noqa: F401
import stages.extract_stage  # noqa: F401
import stages.structure_stage  # noqa: F401
import stages.design_compile_stage  # noqa: F401
import stages.paginate_stage  # noqa: F401
import stages.finish_stage  # noqa: F401
import stages.package_stage  # noqa: F401
import stages.prepress_stages  # noqa: F401


def main():
    registry = get_registry()
    violations = registry.check_integrity()
    
    if not violations:
        print("DAG integrity check PASSED — no violations")
        return 0
    
    errors = [v for v in violations if v["severity"] == "error"]
    warnings = [v for v in violations if v["severity"] == "warning"]
    
    print(f"DAG integrity check: {len(errors)} errors, {len(warnings)} warnings")
    for v in violations:
        print(f"  [{v['severity'].upper()}] {v['kind']}: {v['message']}")
    
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
