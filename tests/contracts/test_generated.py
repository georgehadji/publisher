"""
Generated contract tests — BUILD_PLAN.md §3.20.

Every stage with a declared fixture set appears as a parametrized case. The fixture
manifest is validated against the stage's OWN declaration: the outputs it promises, the
inputs it consumes, and the schema IDs it names must agree with the registry.

WHAT THIS FILE USED TO DO, AND WHY IT CHANGED
The previous version ended `test_stage_output_matches_declared_schema` with a literal
`assert True`, and called `pytest.skip()` the moment a declared output had no expected
file — so a missing contract became a pass. It also relied on `import stages` to populate
the registry, but `stages/__init__.py` was empty, so the registry was EMPTY, `CASES` was
empty, and the parametrized test produced zero cases. The gate that ARCHITECTURE.md §2.15
calls "CI blocking, zero hand-written" was verifying nothing at all.

WHAT IT DOES NOT DO YET
It does not execute stages against fixture inputs and diff the artifacts. The fixture
sets currently contain only `manifest.json` — no `inputs/` or `expected/` payloads exist
(P0 state). Asserting on data that isn't there would just be `assert True` in a longer
form. What IS checkable today is the contract between each stage declaration and its
fixture manifest, and that is checked strictly. When fixture payloads land, extend
`test_fixture_declares_every_output` into a real execute-and-compare.
"""

import json
from pathlib import Path

import pytest

from publisher_stages import get_registry
import stages  # noqa: F401 — importing registers every stage

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _fixture_cases():
    """(stage_name, fixture_dir) for every stage declaring a fixture set."""
    return [
        (decl.name, REPO_ROOT / decl.fixtures)
        for decl in get_registry().all()
        if decl.fixtures
    ]


CASES = _fixture_cases()


def test_registry_is_populated():
    """
    Guard against the failure that silently disabled this whole module: an empty
    registry makes every parametrized test below vacuous.
    """
    assert get_registry().all(), (
        "stage registry is empty — `import stages` no longer registers stages, so every "
        "contract test in this file would silently become a no-op"
    )


def test_at_least_one_stage_declares_fixtures():
    assert CASES, "no stage declares a fixture set; contract tests would cover nothing"


@pytest.mark.parametrize("stage_name,fixture_dir", CASES, ids=[c[0] for c in CASES])
def test_declared_fixture_set_exists(stage_name, fixture_dir):
    """A stage that declares a fixture path must have that fixture set on disk."""
    assert fixture_dir.is_dir(), (
        f"stage '{stage_name}' declares fixtures at '{fixture_dir}' but no such "
        f"directory exists"
    )
    manifest = fixture_dir / "manifest.json"
    assert manifest.is_file(), f"stage '{stage_name}': missing {manifest}"


@pytest.mark.parametrize("stage_name,fixture_dir", CASES, ids=[c[0] for c in CASES])
def test_fixture_manifest_is_wellformed(stage_name, fixture_dir):
    """The manifest must parse and carry the fields the fixture contract defines."""
    manifest = json.loads((fixture_dir / "manifest.json").read_text(encoding="utf-8"))

    assert manifest.get("schema") == "fixture-manifest/1", (
        f"stage '{stage_name}': manifest schema is {manifest.get('schema')!r}, "
        f"expected 'fixture-manifest/1'"
    )
    for field in ("fixtureSet", "description", "inputs", "expectedOutputs", "fixtures"):
        assert field in manifest, f"stage '{stage_name}': manifest missing '{field}'"

    assert manifest["fixtures"], f"stage '{stage_name}': manifest declares no fixture cases"
    for case in manifest["fixtures"]:
        for field in ("name", "inputFiles", "expectedFiles"):
            assert field in case, (
                f"stage '{stage_name}': fixture case {case.get('name', '?')!r} "
                f"missing '{field}'"
            )


@pytest.mark.parametrize("stage_name,fixture_dir", CASES, ids=[c[0] for c in CASES])
def test_fixture_declares_every_output(stage_name, fixture_dir):
    """
    The manifest's expectedOutputs must cover every schema the stage declares as output.

    This is the actual contract: a stage cannot promise an artifact its fixture set never
    checks. Compared by SCHEMA ID rather than by artifact-kind name, because the kind is
    a local label while the schema is the interface.
    """
    decl = get_registry().get(stage_name)
    assert decl is not None, f"stage '{stage_name}' vanished from the registry"

    manifest = json.loads((fixture_dir / "manifest.json").read_text(encoding="utf-8"))
    declared_schemas = set(decl.outputs.values())
    covered_schemas = {o.get("schema") for o in manifest.get("expectedOutputs", [])}

    uncovered = declared_schemas - covered_schemas
    assert not uncovered, (
        f"stage '{stage_name}' declares output schema(s) {sorted(uncovered)} that its "
        f"fixture manifest never checks (manifest covers {sorted(covered_schemas)})"
    )


@pytest.mark.parametrize("stage_name,fixture_dir", CASES, ids=[c[0] for c in CASES])
def test_fixture_inputs_match_declaration(stage_name, fixture_dir):
    """Every input schema named by the manifest must be one the stage actually declares."""
    decl = get_registry().get(stage_name)
    manifest = json.loads((fixture_dir / "manifest.json").read_text(encoding="utf-8"))

    declared_schemas = set(decl.inputs.values())
    manifest_schemas = {i.get("schema") for i in manifest.get("inputs", [])}

    unknown = manifest_schemas - declared_schemas
    assert not unknown, (
        f"stage '{stage_name}' fixture manifest names input schema(s) {sorted(unknown)} "
        f"that the stage does not declare (declares {sorted(declared_schemas)})"
    )
