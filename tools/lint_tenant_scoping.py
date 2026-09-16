#!/usr/bin/env python3
"""
Tenant-scoping lint -- docs/ARCHITECTURE_SCORE_10_PLAN.md E4.3.

E4.3's acceptance condition is that the `app.tenant_id` Postgres GUC the
Row-Level Security policies (migration 002) key on is set in exactly ONE
place -- `withTenant` in packages/api/src/db.ts, fed only by
`request.tenantId` (set exclusively by the auth hook in plugins.ts from a
verified bearer token). A handler that could set it to an arbitrary,
un-authenticated value would make RLS decorative: the database would
faithfully enforce a lie the application told it.

There is no Postgres-level way to test "was this value auth-derived" -- that
is a property of the CODE, not the data. This greps every packages/api/src
TypeScript file for the two ways to set that GUC (`set_config(` and a literal
`SET ... app.tenant_id`) and fails if either appears anywhere OTHER than
db.ts's own definition of `withTenant`.

Usage: python tools/lint_tenant_scoping.py
Exit 0 = app.tenant_id is set in exactly one place. Exit 1 = a route or hook
sets it directly instead of going through withTenant.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
API_SRC = REPO_ROOT / "packages" / "api" / "src"
ALLOWED_FILE = API_SRC / "db.ts"

# Either spelling of setting the GUC: the `set_config()` function (what
# `withTenant` itself uses, since it accepts a bind parameter) or a literal
# `SET`/`SET LOCAL` statement (not parameterizable, but a determined author
# could still write one with a string-interpolated value).
_PATTERN = re.compile(r"set_config\s*\(\s*['\"]app\.tenant_id|SET\s+(LOCAL\s+)?app\.tenant_id")


def main() -> int:
    violations: list[str] = []
    for path in sorted(API_SRC.rglob("*.ts")):
        if path.name.endswith(".test.ts") or path == ALLOWED_FILE:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _PATTERN.search(line):
                violations.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")

    if violations:
        print("app.tenant_id set outside withTenant (packages/api/src/db.ts):", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print(
            "\nEvery tenant-scoped query must go through withTenant(request.tenantId, ...) "
            "-- setting the GUC anywhere else means a handler could set it to a value "
            "that never passed through auth.",
            file=sys.stderr,
        )
        return 1

    print("app.tenant_id is set in exactly one place (withTenant)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
