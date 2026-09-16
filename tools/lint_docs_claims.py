#!/usr/bin/env python3
"""
Docs-claims lint -- docs/ARCHITECTURE_SCORE_10_PLAN.md E7.3.

`L20`: CLAUDE.md said "The 17 `@stage(...)` declarations" when the registry had 19 (now
24). A small, hand-written number nobody regenerates is the whole failure class this
closes -- not just that one claim. Five mechanically-checkable claims, each real drift
this repo has actually had:

  1. Every backtick-wrapped path in CLAUDE.md's folder-map table exists.
  2. ARCHITECTURE.md's §2.14 repo-layout tree: every top-level entry exists, and every
     path its own text says does NOT exist, does not.
  3. The stage count CLAUDE.md states matches the registry.
  4. Every stage name CLAUDE.md's `stages/` folder-map row cites resolves in the registry.
  5. Every work-item identifier (D8, U5, A3, E7.2, ...) referenced anywhere under docs/ or
     CLAUDE.md is DEFINED somewhere under docs/ (a heading or a bolded anchor) -- catches
     a typo'd or renamed identifier, not just a missing file.

This is deliberately NOT a general prose-checker: each check is narrow and mechanical, on
purpose (a gate that flags true positives loses trust and gets disabled -- E0.1's own
doctrine). Extend it by adding a check, not by loosening one.

Usage: python tools/lint_docs_claims.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
ARCHITECTURE_MD = REPO_ROOT / "docs" / "ARCHITECTURE.md"
DOCS_DIR = REPO_ROOT / "docs"


def _stage_names_from_registry() -> set[str]:
    sys.path.insert(0, str(REPO_ROOT))
    import stages  # noqa: F401 -- registration side effect
    from publisher_stages import get_registry
    return {decl.name for decl in get_registry().all()}


def _stage_count_from_source() -> int:
    total = 0
    for path in (REPO_ROOT / "stages").glob("*.py"):
        total += len(re.findall(r"^@stage\(", path.read_text(encoding="utf-8"), re.M))
    return total


# ── 1. CLAUDE.md folder-map paths exist ─────────────────────────────────────

def check_claude_md_folder_paths(violations: list[str]) -> None:
    text = CLAUDE_MD.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        m = re.match(r"\|\s*`([^`]+)`\s*\|", line)
        if not m:
            continue
        rel = m.group(1)
        if not (REPO_ROOT / rel).exists():
            violations.append(f"CLAUDE.md:{lineno}: folder-map cites `{rel}`, which does not exist")


# ── 2. ARCHITECTURE.md §2.14 tree ───────────────────────────────────────────

_TREE_ENTRY_RE = re.compile(r"^(?:├─|└─)\s*([A-Za-z0-9_.\-]+/?)")


def check_architecture_repo_tree(violations: list[str]) -> None:
    text = ARCHITECTURE_MD.read_text(encoding="utf-8")
    section = text.split("### 2.14 Repo layout", 1)
    if len(section) != 2:
        violations.append("ARCHITECTURE.md: '### 2.14 Repo layout' section not found")
        return
    after = section[1]

    fence = re.search(r"```\n(.*?)```", after, re.S)
    if not fence:
        violations.append("ARCHITECTURE.md §2.14: no fenced tree block found")
        return
    for line in fence.group(1).splitlines():
        m = _TREE_ENTRY_RE.match(line)
        if not m:
            continue
        entry = m.group(1)
        if not (REPO_ROOT / entry).exists():
            violations.append(
                f"ARCHITECTURE.md §2.14: repo tree cites `{entry}`, which does not exist"
            )

    # The tree's own text names packages it explicitly says do NOT exist --
    # assert that claim stays true too (someone building one of these should
    # update the roadmap, not silently leave the doc's negative claim stale).
    not_exist_line = re.search(r"^([^\n]*do not exist[^\n]*)$", after, re.M)
    if not_exist_line:
        for path in re.findall(r"`([^`]+)`", not_exist_line.group(1)):
            if (REPO_ROOT / path).exists():
                violations.append(
                    f"ARCHITECTURE.md §2.14: text claims `{path}` does not exist, but it does now -- "
                    "wire it into the normative doc or update ARCHITECTURE_ROADMAP.md"
                )


# ── 3 & 4. Stage count and stage names in CLAUDE.md's folder-map row ────────

_STAGE_TOKEN_RE = re.compile(r"`([a-z][a-z0-9-]*)`")


def check_stage_count_and_names(violations: list[str]) -> None:
    text = CLAUDE_MD.read_text(encoding="utf-8")
    m = re.search(r"The (\d+) `@stage\(\.\.\.\)` declarations", text)
    if not m:
        violations.append("CLAUDE.md: no 'The N `@stage(...)` declarations' claim found to check")
        return
    claimed = int(m.group(1))
    actual = _stage_count_from_source()
    if claimed != actual:
        violations.append(
            f"CLAUDE.md claims {claimed} `@stage(...)` declarations; stages/*.py has {actual}"
        )

    # Same row: every bare stage-name-shaped backtick token (no dots or
    # slashes -- those are file paths, checked separately) it names must be a
    # real, currently-registered stage.
    row_line = next((line for line in text.splitlines() if "@stage(...)" in line), "")
    registry_names = _stage_names_from_registry()
    for token in _STAGE_TOKEN_RE.findall(row_line):
        if token in ("stage",):
            continue
        if token not in registry_names:
            violations.append(
                f"CLAUDE.md `stages/` row names `{token}`, which is not a registered stage "
                f"(registered: {sorted(registry_names)})"
            )


# ── 5. Work-item identifiers (D8, U5, A3, E7.2, ...) are defined somewhere ──

_ID_RE = re.compile(r"\b([EDUAGSOPTR]\d{1,2}(?:\.\d{1,2})?)\b")
# Prefixes this repo actually uses as identifier schemes (docs/*.md headers,
# CLAUDE.md's own "cite sections by identifier" convention). Anything else
# matching the shape (e.g. a plain word like "S1000" won't match \d{1,2} --
# a normal English word colliding with THIS narrow a shape is rare enough
# that the check is worth the small remaining risk).
_ALL_DOC_FILES = lambda: [CLAUDE_MD] + sorted(DOCS_DIR.glob("*.md"))


def check_identifiers_are_defined(violations: list[str]) -> None:
    referenced: set[str] = set()
    defined: set[str] = set()

    for path in _ALL_DOC_FILES():
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            ids = _ID_RE.findall(line)
            if not ids:
                continue
            is_heading_or_anchor = bool(
                re.match(r"^#{1,6}\s", line)
                or re.search(r"\*\*[EDUAGSOPTR]\d", line)
                # A table's own first cell, e.g. `| T7 | Availability | ... |` --
                # ARCHITECTURE_SCORE_10_PLAN.md's threat-model and controls
                # tables define their IDs this way, not as headings or bold text.
                or re.match(r"^\|\s*[EDUAGSOPTR]\d", line)
            )
            for ident in ids:
                referenced.add(ident)
                if is_heading_or_anchor:
                    defined.add(ident)

    missing = sorted(referenced - defined)
    if missing:
        violations.append(
            "Referenced but never defined (as a heading or **bold** anchor) under docs/ "
            f"or CLAUDE.md: {missing}"
        )


def main() -> int:
    violations: list[str] = []
    check_claude_md_folder_paths(violations)
    check_architecture_repo_tree(violations)
    check_stage_count_and_names(violations)
    check_identifiers_are_defined(violations)

    if violations:
        print(f"Docs-claims lint FAILED: {len(violations)} violation(s)")
        for v in violations:
            print(f"  {v}")
        return 1

    print("Docs-claims lint PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
