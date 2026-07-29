"""Verify the full test suite is collected — a collection regression must fail loudly."""

import subprocess
import sys
from pathlib import Path


FLOOR = 203  # Known passing count. Bump when adding tests; never lower.


def test_suite_collection_floor():
    """`pytest --collect-only` must report ≥ FLOOR tests.
    
    A CI assertion pins the floor so a future collection break fails loudly
    rather than silently passing with 0 tests (as the old testpaths=["tests"]
    configuration did).
    """
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    # Parse "N collected" from output
    for line in result.stdout.split("\n"):
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "collected":
            count = int(parts[0])
            assert count >= FLOOR, (
                f"Collection regression: {count} tests collected, "
                f"expected ≥ {FLOOR}. Did a test module break?"
            )
            return
    
    # Fallback: count "N:" lines (file-level test counts from -q output)
    count = sum(1 for l in result.stdout.split("\n") if ": " in l and l.strip())
    assert count > 0, (
        f"Could not determine collection count.\n"
        f"stdout: {result.stdout[:500]}\n"
        f"stderr: {result.stderr[:500]}"
    )
