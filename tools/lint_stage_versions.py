#!/usr/bin/env python3
"""
Stage version-bump lint — BUILD_PLAN.md §3.3.

Checks that any change to a stage module's behavior also bumps its @stage(version=N).
In CI, diffs against the merge base and flags touched stage modules whose
version hasn't changed.

Usage: python tools/lint_stage_versions.py [--base <git-ref>]
"""

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


STAGE_DIRS = ["stages", "services"]


def get_stage_versions(file_path: Path) -> list[tuple[str, int]]:
    """Extract @stage(name=..., version=...) declarations from a file."""
    # Explicit UTF-8: the default locale codec (cp1253 on this dev box) raises
    # UnicodeDecodeError on the em-dashes that appear throughout these sources.
    text = file_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r'@stage\s*\([^)]*?name\s*=\s*"([^"]+)"[^)]*?version\s*=\s*(\d+)',
        re.DOTALL,
    )
    return [(m.group(1), int(m.group(2))) for m in pattern.finditer(text)]


def get_changed_stage_files(base_ref: str = "HEAD~1") -> list[Path]:
    """Get list of changed stage files vs base ref."""
    result = subprocess.run(
        ["git", "diff", "--name-only", base_ref],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        # Not a git repo or no base — skip
        return []
    
    repo_root = Path(__file__).resolve().parent.parent
    changed = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        p = repo_root / line
        for stage_dir in STAGE_DIRS:
            if p.exists() and str(p).startswith(str(repo_root / stage_dir)):
                changed.append(p)
                break
    return changed


def main():
    # In CI, --base is set to the merge base
    base = sys.argv[1] if len(sys.argv) > 1 else "HEAD~1"
    
    changed_files = get_changed_stage_files(base)
    if not changed_files:
        print(f"Stage version lint: no stage files changed since {base}")
        return 0
    
    violations = []
    for fp in changed_files:
        versions = get_stage_versions(fp)
        if not versions:
            continue  # no @stage in this file
        
        # Compare each stage's version against the same file at the base revision.
        #
        # Two bugs used to make this check structurally incapable of reporting anything:
        #   1. `get_stage_versions(Path("/dev/null"))` read a path unrelated to the file
        #      being checked — on Windows it raises FileNotFoundError, on Linux it returns
        #      []. Its result was then discarded anyway.
        #   2. The whole block sat in `try: ... except Exception: pass`, so that error
        #      (and any git failure, and the `relative_to(Path.cwd())` ValueError raised
        #      whenever CWD is not the repo root) was swallowed silently and main() fell
        #      through to printing "PASSED".
        # A lint that cannot fail is worse than no lint. Errors are now reported.
        try:
            rel = fp.resolve().relative_to(REPO_ROOT).as_posix()
        except ValueError:
            violations.append(f"{fp}: not inside the repo root {REPO_ROOT}")
            continue

        result = subprocess.run(
            ["git", "show", f"{base}:{rel}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=REPO_ROOT,
        )
        if result.returncode != 0:
            # File did not exist at the base revision — nothing to compare, not a violation.
            continue

        old_pattern = re.compile(
            r'@stage\s*\([^)]*?name\s*=\s*"([^"]+)"[^)]*?version\s*=\s*(\d+)',
            re.DOTALL,
        )
        old_parsed = {
            m.group(1): int(m.group(2))
            for m in old_pattern.finditer(result.stdout)
        }

        for name, new_ver in versions:
            old_ver = old_parsed.get(name)
            if old_ver is not None and new_ver <= old_ver:
                violations.append(
                    f"{rel}: stage '{name}' changed but version {new_ver} "
                    f"was not bumped from {old_ver}"
                )
    
    if violations:
        print(f"Stage version lint FAILED: {len(violations)} violation(s)")
        for v in violations:
            print(f"  {v}")
        return 1
    
    print(f"Stage version lint PASSED — {len(changed_files)} file(s) checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
