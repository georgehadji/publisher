#!/usr/bin/env python3
"""
Service dependency lint -- docs/ARCHITECTURE_UPLIFT_PLAN.md U3.

Every `services/*` package must declare in its own pyproject.toml any
`publisher_*` package it imports. Today those declarations are
`dependencies = []` across the board, and cross-package imports resolve only
because Dockerfile.worker puts every package on one PYTHONPATH -- so a packaging
change breaks PRODUCTION silently instead of CI loudly.

This walks each services/*/pyproject.toml, AST-parses the shipped package (not
its tests) for `import publisher_*` / `from publisher_* import ...`, and fails
if an import maps to a distribution that is not declared in `dependencies`.

Usage: python tools/lint_service_deps.py
Exit 0 = every cross-package import is declared. Exit 1 = gaps found.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVICES_DIR = REPO_ROOT / "services"


def _distribution_of(module: str) -> str:
    """publisher_structure.rules -> publisher-structure (the distribution name)."""
    top = module.split(".")[0]
    return top.replace("_", "-")


def _imports_in(path: Path) -> set[str]:
    """publisher_* distribution names imported by one Python file (AST-based)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("publisher_"):
                    found.add(_distribution_of(alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("publisher_"):
                found.add(_distribution_of(node.module))
    return found


def main() -> int:
    problems: list[str] = []
    checked = 0

    for pyproject in sorted(SERVICES_DIR.glob("*/pyproject.toml")):
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        project = data.get("project") or {}
        name: str = project.get("name", "")
        if not name:
            problems.append(f"{pyproject.relative_to(REPO_ROOT)}: no [project].name")
            continue
        declared = set(project.get("dependencies", []))

        pkg_dir = pyproject.parent / name.replace("-", "_")
        if not pkg_dir.is_dir():
            problems.append(
                f"{pyproject.relative_to(REPO_ROOT)}: package dir {pkg_dir.relative_to(REPO_ROOT)} not found"
            )
            continue

        imported: set[str] = set()
        for py in pkg_dir.rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            try:
                imported |= _imports_in(py)
            except SyntaxError as e:
                problems.append(f"{py.relative_to(REPO_ROOT)}: unparseable ({e})")

        # A package importing itself is not a dependency.
        imported.discard(name)
        missing = sorted(imported - declared)
        checked += 1
        if missing:
            problems.append(
                f"{name}: imports {missing} but declares dependencies = {sorted(declared)}"
            )

    if problems:
        print("Service dependency lint FAILED:")
        for p in problems:
            print(f"  - {p}")
        print(
            "\nFix: declare the missing distribution in the importing package's "
            "pyproject.toml [project].dependencies (the honest fix), or remove "
            "the import (if the dependency is incidental)."
        )
        return 1

    print(f"Service dependency lint PASSED -- {checked} packages declare every publisher_* import")
    return 0


if __name__ == "__main__":
    sys.exit(main())
