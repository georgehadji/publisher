"""
E0.1 (docs/ARCHITECTURE_SCORE_10_PLAN.md) -- prove every CI-blocking gate can fail.

`L1` was not "the codegen gate is misconfigured": it was "a gate was declared
blocking and nobody ever checked it could fail" (`.gitignore` excluded `*.gen.*`,
so `git diff --exit-code` after codegen never saw the generated files, no matter
what was committed). Fixing that one instance leaves the class open.

For every static-analysis gate CI runs, this file applies one known-bad mutation
to a scratch git worktree and asserts the gate's exit code is non-zero. A gate
whose test passes on the mutated tree has not been demonstrated to catch anything.

`test_every_ci_run_step_is_classified` parses `ci.yml` itself: a `run:` step
added there with no entry in `STEP_CLASSIFICATION` fails loudly instead of
silently shipping unchecked, which is exactly the failure mode this file exists
to close.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"


@dataclass(frozen=True)
class Gate:
    id: str
    cmd: list[str]
    cwd: str  # relative to the worktree root
    mutate: Callable[[Path], None]  # applies one known-bad change in place
    # Extra environment, computed from the worktree root -- only
    # `import-boundaries` needs this; see `_worktree_pythonpath`'s docstring.
    env: Callable[[Path], dict[str, str]] | None = None
    # Returns a skip reason if this gate's tool cannot run at all in the
    # current environment (vs. failing because of the mutation) -- only
    # `pip-audit` needs this; see `_pip_audit_unusable_reason`'s docstring.
    skip_if: Callable[[], str | None] | None = None


# ---- mutations: each makes ONE known-bad change, nothing else -------------

def mutate_schema_without_regen(root: Path) -> None:
    """Edit a schema source without regenerating -- the exact shape of L1."""
    schemas_dir = root / "schemas"
    # node_modules is gitignored, so a fresh worktree doesn't have it.
    shutil.copytree(REPO_ROOT / "schemas" / "node_modules", schemas_dir / "node_modules")
    path = schemas_dir / "manifest" / "manifest.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    schema["properties"]["metaGateProbe"] = {"type": "string", "const": "probe"}
    path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")


def add_unsatisfiable_stage_input(root: Path) -> None:
    """A stage input with no producing stage and no root_inputs declaration."""
    path = root / "stages" / "prepress_stages.py"
    text = path.read_text(encoding="utf-8")
    needle = 'inputs={"pdf_path": "cover-raw-pdf/1", "profile_name": "profile/1"}'
    assert needle in text, "cover-preflight's inputs= shape changed; update this mutation"
    text = text.replace(
        needle,
        needle[:-1] + ', "meta_gate_bogus": "meta-gate-nonexistent-schema/1"}',
        1,
    )
    path.write_text(text, encoding="utf-8")


def add_freetext_field_to_structure_route(root: Path) -> None:
    """An open string field with no enum/const on a structure-route schema."""
    path = root / "schemas" / "classification" / "classification.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    schema["properties"]["metaGateFreeText"] = {"type": "string"}
    path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")


def change_stage_body_without_version_bump(root: Path) -> None:
    """Touch a stage function's body; leave its @stage(version=...) untouched."""
    path = root / "stages" / "prepress_stages.py"
    text = path.read_text(encoding="utf-8")
    needle = "def preflight_stage("
    assert needle in text, "preflight stage function signature changed; update this mutation"
    text = text.replace(
        needle, "# meta-gate probe: behavior change, no version bump\ndef preflight_stage(", 1
    )
    path.write_text(text, encoding="utf-8")


def add_undeclared_cross_service_import(root: Path) -> None:
    """publisher-ingest imports publisher_prepress without declaring it."""
    path = root / "services" / "ingest" / "publisher_ingest" / "__init__.py"
    text = path.read_text(encoding="utf-8")
    needle = "from __future__ import annotations\n"
    assert needle in text, "publisher_ingest/__init__.py shape changed; update this mutation"
    text = text.replace(
        needle,
        needle + "\nimport publisher_prepress  # meta-gate probe: undeclared\n",
        1,
    )
    path.write_text(text, encoding="utf-8")


def add_platform_to_services_import(root: Path) -> None:
    """publisher_stages imports a services package -- exactly what
    no-platform-to-services forbids (E1.4)."""
    path = root / "platform" / "stages" / "py" / "publisher_stages" / "__init__.py"
    text = path.read_text(encoding="utf-8")
    text += "\nimport publisher_ingest  # meta-gate probe: platform importing a service\n"
    path.write_text(text, encoding="utf-8")


def add_tenant_id_set_outside_with_tenant(root: Path) -> None:
    """A route sets the RLS GUC directly instead of going through
    withTenant (E4.3) -- exactly the shape that would let a handler set it
    to a value that never passed through auth."""
    path = root / "packages" / "api" / "src" / "routes" / "titles.ts"
    text = path.read_text(encoding="utf-8")
    needle = "export async function registerTitles(server: FastifyInstance): Promise<void> {"
    assert needle in text, "titles.ts's registerTitles signature changed; update this mutation"
    text = text.replace(
        needle,
        needle + "\n  // meta-gate probe: bypasses withTenant\n"
        "  void \"set_config('app.tenant_id', 'meta-gate-probe', true)\";\n",
        1,
    )
    path.write_text(text, encoding="utf-8")


# Every package `.importlinter`'s root_packages names, mapped to its source
# directory relative to the repo root -- mirrors ci.yml's PUBLISHER_PKGS.
_IMPORT_LINTER_PACKAGE_DIRS = [
    "platform/cas/py", "platform/cache/py", "platform/stages/py", "platform/exec/py",
    "platform/sandbox/py", "services/ingest", "services/structure", "services/prepress",
    "services/cover", "services/epub", "services/onix", "services/alttext", "services/agents",
]


def _worktree_pythonpath(root: Path) -> dict[str, str]:
    """PYTHONPATH pointing `lint-imports` at THIS worktree's own copies of
    every package it analyzes -- required for the mutation above to be
    visible at all.

    Every one of these packages is `pip install -e`'d exactly once against
    the MAIN working tree. Pip's PEP 660 editable finder resolves
    `import publisher_stages` (etc.) to that fixed absolute path forever,
    regardless of which process's cwd asks for it -- so mutating a scratch
    worktree's copy of `publisher_stages/__init__.py` and running
    `lint-imports` with `cwd=worktree` silently analyzes the UNMODIFIED main
    tree; the gate could never fail no matter what the mutation was.
    Prepending the worktree's own source directories to PYTHONPATH fixes
    this: the interpreter's standard sys.path-based PathFinder resolves a
    plain top-level package name before the editable finder's meta-path
    entry gets a chance (confirmed empirically against this repo's actual
    pip/Python; there is no public grimp/import-linter option for this,
    since it is really a pip editable-install behavior, not an import-linter
    one). Every OTHER gate in this file mutates a file reached by a
    cwd-relative or script-relative path, not a `pip install -e`'d package
    name, so none of them need this.
    """
    paths = [str(root / d) for d in _IMPORT_LINTER_PACKAGE_DIRS]
    return {"PYTHONPATH": os.pathsep.join(paths)}


def add_known_vulnerable_pinned_dependency(root: Path) -> None:
    """A real PyPI release with public CVEs (urllib3 1.24.1 -- e.g.
    CVE-2019-11324), added with ITS OWN correct hashes so the gate's failure
    is pip-audit's vulnerability finding, not a hash mismatch. It has no
    runtime dependencies of its own, so this is a self-contained addition
    under `--require-hashes` -- no other line in the file needs to change."""
    path = root / "requirements.lock"
    text = path.read_text(encoding="utf-8")
    text += (
        "\nurllib3==1.24.1 \\\n"
        "    --hash=sha256:61bf29cada3fc2fbefad4fdf059ea4bd1b4a86d2b6d15e1c7c0b582b9752fe39 \\\n"
        "    --hash=sha256:de9529817c93f27c8ccbfead6985011db27bd0ddfcdb2d86f3f663385c6a9c22\n"
        "    # meta-gate probe: known-vulnerable pin (CVE-2019-11324 et al.)\n"
    )
    path.write_text(text, encoding="utf-8")


def _pip_audit_unusable_reason() -> str | None:
    """pip-audit's CLI hard-imports the stdlib `venv` module on every code
    path -- including a plain `-r requirements.lock` audit that never
    creates one -- and some minimal Python installs omit it (found on this
    project's own dev machine while wiring this gate up). Skip rather than
    report a false gate failure that has nothing to do with the mutation;
    CI's ubuntu-latest runner ships the full stdlib and runs this for real."""
    if importlib.util.find_spec("venv") is None:
        return "pip-audit requires the stdlib `venv` module, missing from this Python install"
    if shutil.which("pip-audit") is None:
        return "pip-audit is not installed in this environment (pip install pip-audit)"
    return None


def add_nonexistent_path_to_architecture_md(root: Path) -> None:
    """E7.3's own shape: cite a path in ARCHITECTURE.md's §2.14 repo tree that
    does not exist -- exactly the class `L18`/`L20` were (a hand-written
    claim nothing regenerates or checks)."""
    path = root / "docs" / "ARCHITECTURE.md"
    text = path.read_text(encoding="utf-8")
    needle = "### 2.14 Repo layout"
    assert needle in text, "ARCHITECTURE.md's §2.14 heading changed; update this mutation"
    text = text.replace(
        "publisher/\n",
        "publisher/\n├─ meta-gate-probe/           # does not exist -- meta-gate probe\n",
        1,
    )
    path.write_text(text, encoding="utf-8")


def add_known_vulnerable_npm_dependency(root: Path) -> None:
    """minimatch 3.0.4 carries a published ReDoS advisory (GHSA-f8q6-p94x-37v3,
    severity high). Resolved with a real `npm install`, not a hand-edited
    lockfile, so package.json and package-lock.json stay consistent with
    each other exactly as a real dependency bump would leave them."""
    api_dir = root / "packages" / "api"
    # bash -c, not a raw ["npm", ...] argv: on Windows, `npm` resolves to
    # `npm.cmd`, and CreateProcess (what subprocess uses without shell=True)
    # cannot exec a .cmd directly -- the same reason `codegen-sync`'s Gate
    # above wraps its command in bash -c.
    subprocess.run(
        ["bash", "-c", "npm install minimatch@3.0.4 --save --package-lock-only --no-audit"],
        cwd=api_dir, check=True, capture_output=True, text=True, timeout=120,
    )


GATES: list[Gate] = [
    Gate(
        id="codegen-sync",
        cmd=["bash", "-c", "node codegen/generate.mjs && git diff --exit-code"],
        cwd="schemas",
        mutate=mutate_schema_without_regen,
    ),
    Gate(
        id="dag-integrity",
        cmd=[sys.executable, "platform/stages/integrity.py"],
        cwd=".",
        mutate=add_unsatisfiable_stage_input,
    ),
    Gate(
        id="schema-lint",
        cmd=[sys.executable, "tools/lint_schemas.py"],
        cwd=".",
        mutate=add_freetext_field_to_structure_route,
    ),
    Gate(
        id="stage-versions",
        cmd=[sys.executable, "tools/lint_stage_versions.py", "HEAD"],
        cwd=".",
        mutate=change_stage_body_without_version_bump,
    ),
    Gate(
        id="service-deps",
        cmd=[sys.executable, "tools/lint_service_deps.py"],
        cwd=".",
        mutate=add_undeclared_cross_service_import,
    ),
    Gate(
        id="import-boundaries",
        cmd=["lint-imports", "--config", ".importlinter"],
        cwd=".",
        mutate=add_platform_to_services_import,
        env=_worktree_pythonpath,
    ),
    Gate(
        id="pip-audit",
        cmd=["pip-audit", "-r", "requirements.lock", "--require-hashes"],
        cwd=".",
        mutate=add_known_vulnerable_pinned_dependency,
        skip_if=_pip_audit_unusable_reason,
    ),
    Gate(
        id="npm-audit",
        cmd=["bash", "-c", "npm ci --no-audit && npm audit --audit-level=high"],
        cwd="packages/api",
        mutate=add_known_vulnerable_npm_dependency,
    ),
    Gate(
        id="tenant-scoping",
        cmd=[sys.executable, "tools/lint_tenant_scoping.py"],
        cwd=".",
        mutate=add_tenant_id_set_outside_with_tenant,
    ),
    Gate(
        id="docs-claims",
        cmd=[sys.executable, "tools/lint_docs_claims.py"],
        cwd=".",
        mutate=add_nonexistent_path_to_architecture_md,
    ),
]

# Maps each CI `run:` step (by its `name:`, or by the run command itself for
# the rust job's unnamed steps) to the Gate id that proves it can fail, or to
# None with a reason in the comment for a step that needs no synthetic
# mutation because its own pass/fail IS the signal under test.
STEP_CLASSIFICATION: dict[str, str | None] = {
    "pip install ${{ env.PUBLISHER_PKGS }} import-linter": None,  # dependency install, not a gate
    "pip install ${{ env.PUBLISHER_PKGS }} psycopg2-binary requests": None,  # ditto
    "Load schema": None,  # CI setup -- no initdb mount for services: postgres
    "Full test suite": None,  # pytest failing is itself the signal
    "npm ci": None,  # dependency install, not a gate
    "Type-check (tsc --noEmit)": None,  # tsc failing is itself the signal (plan §5.2: "--")
    "Vitest -- auth matrix, route coverage, CAS hardening": None,  # ditto
    # packages/web: next build's TypeScript pass failing is the signal, as with
    # tsc above. Shown able to fail by hand (TS2339 on the panel's old read of
    # ChapterReview.id); not a Gate here -- npm ci + next build take minutes.
    "Type-check and build (next build)": None,
    "Lint schemas (no free-text in structure routes)": "schema-lint",
    "cargo test": None,
    "cargo clippy -- -D warnings": None,
    "Codegen in sync (§3.1 Done)": "codegen-sync",
    "Generated types are valid (§3.1)": None,  # codegen's own test suite
    "DAG integrity (§3.20)": "dag-integrity",
    "Generated contract tests (§3.20)": None,  # generated tests failing is the signal
    "Version-bump rule (§3.3, §7)": "stage-versions",
    "Service deps declared (U3)": "service-deps",
    "Import boundaries (E1.4)": "import-boundaries",
    "pip-audit (requirements.lock)": "pip-audit",
    "npm ci (api)": None,  # dependency install, not a gate
    "npm audit --audit-level=high (api)": "npm-audit",
    "npm ci (web)": None,  # dependency install, not a gate
    "npm audit --audit-level=high (web)": "npm-audit",  # same mechanism as (api), one Gate covers both
    "Tenant scoping (E4.3)": "tenant-scoping",
    "Docs claims (E7.3)": "docs-claims",
}


def _ci_steps() -> list[str]:
    doc = yaml.safe_load(CI_YML.read_text(encoding="utf-8"))
    steps = []
    for job in doc["jobs"].values():
        for step in job["steps"]:
            if "run" not in step:
                continue  # setup actions (checkout, setup-python, ...) -- nothing to mutate
            steps.append(step.get("name") or step["run"].strip().splitlines()[0])
    return steps


def _worktree(tmp_path: Path) -> Path:
    """A real git worktree at the current commit: cheap, and shares full history
    so a gate that diffs against a base ref (stage-versions) works unmodified."""
    dest = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(dest), "HEAD"],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    )
    return dest


@pytest.fixture
def worktree(tmp_path):
    wt = _worktree(tmp_path)
    try:
        yield wt
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt)],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )


@pytest.mark.parametrize("gate", GATES, ids=[g.id for g in GATES])
def test_gate_can_fail(gate: Gate, worktree: Path):
    if gate.skip_if is not None:
        reason = gate.skip_if()
        if reason:
            pytest.skip(reason)
    gate.mutate(worktree)
    run_env = {**os.environ, **gate.env(worktree)} if gate.env is not None else None
    result = subprocess.run(
        gate.cmd, cwd=worktree / gate.cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180, env=run_env,
    )
    assert result.returncode != 0, (
        f"gate '{gate.id}' did not fail on a known-bad mutation\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_every_ci_run_step_is_classified():
    unclassified = sorted(set(_ci_steps()) - set(STEP_CLASSIFICATION))
    assert not unclassified, (
        f"ci.yml step(s) not classified in test_gates_can_fail.py: {unclassified}. "
        "Add a GATES entry (and mutation) or an explicit None with a reason."
    )


def test_every_classified_gate_has_a_can_fail_test():
    gate_ids = {g.id for g in GATES}
    referenced = {v for v in STEP_CLASSIFICATION.values() if v is not None}
    missing = referenced - gate_ids
    assert not missing, (
        f"STEP_CLASSIFICATION references gate id(s) with no GATES entry: {missing}"
    )
