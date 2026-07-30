#!/usr/bin/env python3
"""
DAG integrity checker — BUILD_PLAN.md §3.20.

Usage: python platform/stages/integrity.py
Returns non-zero if any error-severity violations exist.

NOTE ON INVOCATION: this must be run as a script, NOT as `python -m platform.stages.integrity`.
The repo has a top-level `platform/` directory, which shadows Python's stdlib `platform`
module, so the `-m` form fails with "No module named 'platform.stages'". CI used the `-m`
form under `continue-on-error: true`, so this gate reported success without ever running.
"""

import sys
from pathlib import Path

# Repo root is three levels up (platform/stages/integrity.py), not two. The previous
# `parent.parent` put `platform/` on the path, so `import stages.acquire_stage` raised
# ModuleNotFoundError and the checker could never execute.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from publisher_stages import get_registry
# stages/__init__.py owns the canonical list of stage modules to register. This used
# to be a hand-maintained duplicate of that list here, which had already drifted once
# (tracer_bullet.py's own copy omitted prepress_stages entirely — see its docstring).
# One list, imported everywhere, so a new stage module only has to be added once.
import stages  # noqa: F401 — import for its registration side effect


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
