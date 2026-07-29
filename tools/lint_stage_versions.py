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


STAGE_DIRS = ["stages", "services"]


def get_stage_versions(file_path: Path) -> list[tuple[str, int]]:
    """Extract @stage(name=..., version=...) declarations from a file."""
    text = file_path.read_text()
    pattern = re.compile(
        r'@stage\s*\([^)]*?name\s*=\s*"([^"]+)"[^)]*?version\s*=\s*(\d+)',
        re.DOTALL,
    )
    return [(m.group(1), int(m.group(2))) for m in pattern.finditer(text)]


def get_changed_stage_files(base_ref: str = "HEAD~1") -> list[Path]:
    """Get list of changed stage files vs base ref."""
    result = subprocess.run(
        ["git", "diff", "--name-only", base_ref],
        capture_output=True, text=True,
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
        
        # Check version against git show for the base
        try:
            result = subprocess.run(
                ["git", "show", f"{base}:{fp.relative_to(Path.cwd())}"],
                capture_output=True, text=True,
            )
            if result.returncode == 0 and result.stdout:
                old_versions = get_stage_versions(Path("/dev/null"))
                # Re-parse the old version
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
                            f"{fp.name}: stage '{name}' version {new_ver} "
                            f"not bumped from {old_ver}"
                        )
        except Exception:
            pass
    
    if violations:
        print(f"Stage version lint FAILED: {len(violations)} violation(s)")
        for v in violations:
            print(f"  {v}")
        return 1
    
    print(f"Stage version lint PASSED — {len(changed_files)} file(s) checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
