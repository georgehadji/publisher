"""
Every fact that must be stated in more than one place, pinned to its source.

Where a second copy is unavoidable -- another language, a SQL constraint, a
compose file, a package that may not import the owner -- this is the test that
fails when the copy drifts. Each one here did drift before it existed.
"""

from __future__ import annotations

import json
import re
from fractions import Fraction
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _error_kinds() -> set[str]:
    from publisher_stages import ErrorKind
    return {k.value for k in ErrorKind}


def test_builds_error_kind_check_accepts_every_kind_the_worker_writes():
    """001's CHECK predated `timeout`/`resource_exhausted`: the worker's
    failure write broke it and the build sat `running` until its lease ran out."""
    import worker
    latest = sorted((ROOT / "platform/db/migrations").glob("*.sql"))
    check = next(m for m in reversed(latest) if "builds_error_kind_check" in m.read_text(encoding="utf-8"))
    body = check.read_text(encoding="utf-8").split("ADD CONSTRAINT builds_error_kind_check", 1)[1]
    allowed = set(re.findall(r"'([^']+)'", body))
    assert allowed == _error_kinds() | {worker.INTERNAL, worker.EXHAUSTED}


def test_manifest_and_typescript_error_kinds_are_the_registrys():
    manifest = json.loads(_read("schemas/manifest/manifest.schema.json"))
    assert set(manifest["$defs"]["stageError"]["properties"]["kind"]["enum"]) == _error_kinds()
    ts = _read("platform/stages/ts/src/index.ts")
    enum = ts.split("export enum ErrorKind {", 1)[1].split("}", 1)[0]
    assert set(re.findall(r'"([^"]+)"', enum)) == _error_kinds()


def test_confidence_threshold_is_one_number_in_three_languages():
    from publisher_structure.rules import ESCALATE_BELOW
    for rel in ("packages/api/src/contract.ts", "packages/web/src/types.ts"):
        value = re.search(r"export const LOW_CONFIDENCE_BELOW = ([0-9.]+);", _read(rel))
        assert value and float(value.group(1)) == ESCALATE_BELOW, rel


def test_cover_aspect_is_the_default_profiles_trim():
    from profiles import get_default_profile
    from publisher_cover.art_policy import COVER_ASPECT
    trim = get_default_profile()["trimSize"]
    w, h = (int(x) for x in COVER_ASPECT.split(":"))
    assert Fraction(round(trim["width"] * 10), round(trim["height"] * 10)) == Fraction(w, h)


def test_compose_temporal_pools_host_every_declared_queue_once():
    """A queue no pool polls leaves its stages' activities waiting forever."""
    import stages
    from publisher_orchestration.queues import discover_queues
    from publisher_stages import get_registry
    stages.import_idml_if_requested(True)   # the render pool hosts idml's queue
    compose = yaml.safe_load(_read("docker-compose.yml"))
    hosted = [q for svc in compose["services"].values()
              for q in str((svc.get("environment") or {}).get("PUBLISHER_TEMPORAL_QUEUES", "")).split(",")
              if q]
    assert len(hosted) == len(set(hosted)), "a queue is hosted by two pools"
    assert set(hosted) == set(discover_queues(get_registry()))


def _restated_defaults(doc: dict, node: dict, root: dict, path: str = "") -> list[str]:
    found = []
    for key, value in doc.items():
        prop = (node.get("properties") or {}).get(key)
        if prop is None:
            continue
        while "$ref" in prop:
            prop = root["$defs"][prop["$ref"].rsplit("/", 1)[-1]]
        if "default" in prop and prop["default"] == value and not isinstance(value, dict):
            found.append(f"{path}{key}")
        if isinstance(value, dict):
            found += _restated_defaults(value, prop, root, f"{path}{key}.")
    return found


def test_house_designspec_restates_no_schema_default():
    """A default stated twice is two defaults the day one changes."""
    from templates import HOUSE_DESIGNSPEC
    schema = json.loads(_read("schemas/designspec/designspec.schema.json"))
    assert _restated_defaults(HOUSE_DESIGNSPEC, schema, schema) == []


def test_every_profile_validates_against_its_schema():
    from profiles import list_profiles
    assert list_profiles()   # loading validates each one; an invalid file raises


def test_proposal_ops_are_one_mapping_in_two_languages():
    """The API builds the op a proposal becomes; the stage decides which
    proposals exist. A type one knows and the other does not is a proposal
    nobody can accept, or an accept that logs the wrong op."""
    from stages.propose_stage import PROPOSAL_OPS
    body = _read("packages/api/src/contract.ts").split("export const PROPOSAL_OPS = {", 1)[1].split("}", 1)[0]
    assert dict(re.findall(r'(\w+): "(\w+)"', body)) == PROPOSAL_OPS


def test_the_override_route_and_contract_accept_every_schema_op():
    """The API restates the op enum twice (the PATCH route's JSON schema and
    contract.ts's union). An op missing there is one no reviewer can log."""
    ops = set(json.loads(_read("schemas/overrides/overrides.schema.json"))
              ["$defs"]["overrideOp"]["properties"]["op"]["enum"])
    route = _read("packages/api/src/routes/manuscripts.ts").split("enum: [", 1)[1].split("]", 1)[0]
    union = re.search(r"\n\s*op:\s*\n(.*?);", _read("packages/api/src/contract.ts"), re.S).group(1)
    assert set(re.findall(r"'(\w+)'", route)) == ops
    assert set(re.findall(r'"(\w+)"', union)) == ops
