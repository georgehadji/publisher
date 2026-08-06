"""Verify the full test suite is collected — a collection regression must fail loudly."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# Known-good collected count. Bump when adding tests; never lower without justification.
FLOOR = 290

REPO_ROOT = Path(__file__).resolve().parent.parent

# Third-party pytest plugin autoload costs ~100s of wall time per interpreter
# start on some dev machines (entry-point metadata scanning across site-packages,
# which on Windows is subject to real-time AV scanning). Both tests below spawn a
# subprocess, so they paid it twice and intermittently blew their own timeout --
# reporting a "collection regression" that was really just machine latency.
#
# Neither test needs third-party plugins: they assert that pytest core can import
# and collect this repo's modules. Disabling autoload cuts collection from ~160s
# to ~14s and makes the signal honest. Verified to still collect all 338 tests
# with no ERROR section.
_SUBPROCESS_ENV = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}


def test_suite_collection_floor():
    """
    `pytest --collect-only` must report >= FLOOR tests.

    Pins the floor so a future collection break (a bad import, a renamed package, a
    testpaths regression) fails loudly instead of silently passing with 0 tests.

    The previous version could not fail. Its fallback counted any stdout line containing
    ": ", and `pytest --collect-only -q` prints one such line per collected FILE before
    any interruption banner — so during a genuine collection failure the count was still
    > 0 and the assertion passed. Now an unparseable count is itself a failure.
    """
    result = subprocess.run(
        # `-p no:cacheprovider` avoids fighting the parent run over .pytest_cache.
        # `--ignore` this file: without it the subprocess re-collects the floor test,
        # which is harmless but doubles the work for no signal.
        # No extra `-q`: pyproject's addopts already sets it, and a second -q suppresses
        # the "N tests collected" summary line entirely.
        [sys.executable, "-m", "pytest", "--collect-only",
         "-p", "no:cacheprovider", "--ignore", str(Path(__file__).resolve())],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
        env=_SUBPROCESS_ENV,
        timeout=300,
    )

    match = re.search(r"^(\d+)\s+tests? collected", result.stdout, re.MULTILINE) \
        or re.search(r"^(\d+)\s+collected", result.stdout, re.MULTILINE)

    # Fallback: sum the per-file counts pytest prints in quiet mode
    # ("services/structure/tests/test_rules.py: 13"). This is a real count, unlike the
    # previous fallback which merely counted lines containing ": " and so stayed
    # positive even while collection was failing.
    if match is None:
        per_file = re.findall(r"^\S+\.py: (\d+)\s*$", result.stdout, re.MULTILINE)
        if per_file:
            count = sum(int(n) for n in per_file)
            assert count >= FLOOR, (
                f"Collection regression: {count} tests collected across "
                f"{len(per_file)} files, expected >= {FLOOR}."
            )
            return

    if match is None:
        pytest.fail(
            "Could not parse a collected-test count from `pytest --collect-only`. "
            "That usually means collection itself errored — which is exactly the "
            "regression this test exists to catch.\n"
            f"exit code: {result.returncode}\n"
            f"stdout tail:\n{result.stdout[-2000:]}\n"
            f"stderr tail:\n{result.stderr[-2000:]}"
        )

    count = int(match.group(1))
    assert count >= FLOOR, (
        f"Collection regression: {count} tests collected, expected >= {FLOOR}. "
        f"Did a test module fail to import, or did testpaths change?"
    )


def test_collection_reports_no_errors():
    """Collection must be clean — a module that fails to import must fail this suite."""
    result = subprocess.run(
        # No extra `-q`: pyproject's addopts already sets it, and a second -q suppresses
        # the "N tests collected" summary line entirely.
        [sys.executable, "-m", "pytest", "--collect-only",
         "-p", "no:cacheprovider", "--ignore", str(Path(__file__).resolve())],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
        env=_SUBPROCESS_ENV,
        timeout=300,
    )
    # Use the exit code, not a substring scan: `--collect-only` prints every collected
    # node id, and plenty of legitimate test names contain the word "error"
    # (test_build_error_resolver, test_agent_error_handling, ...). pytest exits non-zero
    # when collection itself fails, which is the signal we actually want.
    assert result.returncode == 0, (
        f"pytest --collect-only exited {result.returncode}, indicating collection "
        f"errors:\n{result.stdout[-2000:]}\n{result.stderr[-1000:]}"
    )
    assert not re.search(r"^ERRORS?\b", result.stdout, re.MULTILINE), (
        f"pytest reported a collection ERROR section:\n{result.stdout[-2000:]}"
    )
