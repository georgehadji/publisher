"""
`structure-infer` (E6.2, docs/ARCHITECTURE_SCORE_10_PLAN.md) -- guarded
reachability and the stage's own plumbing (HTML -> low-confidence blocks ->
InferenceGateway -> classification/1 CAS artifact).

Does NOT make a real OpenRouter call: `test_structure_infer_writes_a_
classification_artifact` monkeypatches OpenRouterProvider with a fake that
implements the same InferenceProvider protocol, the same substitution
services/structure/tests/fabricating_provider.py exists for -- this file
just does it inline rather than importing that test-only module across a
different service's test directory. What IS real: the DAG reachability
fixpoint (publisher_exec.plan), proving the guard is the actual mechanism
this stage becomes unreachable through, not merely documented intent.
"""

from __future__ import annotations
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

import stages  # noqa: F401 -- registers every stage, including structure-infer
from publisher_stages import StageCtx, StageError, ErrorKind, RegistryConfig, build_registry
from publisher_exec import plan
from stages.structure_infer_stage import structure_infer
import stages.structure_infer_stage as structure_infer_stage
from publisher_structure.inference import InferenceRequest, InferenceResult, ModelTier, RouteConfig


SAMPLE_HTML = (
    '<p class="paragraph">CHAPTER ONE</p>'
    '<p class="paragraph">An ordinary sentence with nothing distinctive about it.</p>'
)


def _ctx(tmp_dir: Path) -> StageCtx:
    return StageCtx(
        build_id="test-build",
        deterministic_seed="test",
        deadline=datetime.now(timezone.utc),
        memory_budget_mb=128,
        work_dir=str(tmp_dir),
        allow_stub_engines=True,
    )


class _FakeProvider:
    """Stands in for OpenRouterProvider -- implements InferenceProvider
    without a network call, so this test proves the STAGE's plumbing
    (parses HTML, calls the gateway, writes classification/1 to CAS)
    without asserting anything about what a real model would answer."""

    def __init__(self):
        self.requests: list[InferenceRequest] = []

    def complete(self, request: InferenceRequest, route: RouteConfig, tier: ModelTier) -> InferenceResult:
        self.requests.append(request)
        nodes = [
            {"sourceRef": n.get("sourceRef", f"node-{i}"), "classification": "paragraph", "confidence": 0.9}
            for i, n in enumerate(request.inputs.get("nodes", []))
        ]
        return InferenceResult(
            request_id=request.request_id,
            route=request.route,
            tier_used=tier,
            output={
                "schema": "classification/1",
                "nodes": nodes,
                "modelInfo": {
                    "modelId": route.model_id,
                    "promptVersion": route.prompt_version,
                    "schemaVersion": route.schema_version,
                    "cacheHit": False,
                    "costUsd": route.cost_per_call,
                },
            },
            confidence=0.9,
            model_id=route.model_id,
            prompt_version=route.prompt_version,
            cost_usd=route.cost_per_call,
        )


def _selected_registry():
    """A properly SELECTED registry (worker.py's own production shape) --
    the raw, unselected get_registry() leaves `ingest`/`acquire`,
    `design-compile`/`design-compile-typst` etc. genuinely ambiguous
    (multiple active producers of one schema), which makes the reachability
    fixpoint answer questions this test isn't asking."""
    return build_registry(RegistryConfig(ingest_impl="ingest"))


def test_structure_infer_is_unreachable_without_credentials():
    """The DAG's own reachability fixpoint keeps structure-infer out of a
    build that never supplies its api_key root input -- not a runtime
    env-var check inside the stage, the same mechanism that keeps the whole
    cover pipeline absent from a plain interior-book build."""
    reachable = plan(_selected_registry(), {}).order
    assert "structure-infer" not in reachable


def test_structure_infer_is_reachable_once_a_key_is_supplied():
    """Supplying the root input -- and ONLY the root input; typescript-html/1
    is produced by `extract`, itself reachable given no other roots -- is
    what flips reachability. Worker.py does this precisely when
    OPENROUTER_API_KEY is configured."""
    # `extract` needs `ingest` reachable first (raw-source/1), which needs
    # its OWN root input supplied -- plan() is pure (dict in, dict out; no
    # filesystem check), so a fake path is enough to prove the chain.
    reachable = plan(_selected_registry(), {
        "ingest": {"docx_path": "/fake/manuscript.docx"},
        "structure-infer": {"api_key": "sk-test-fake"},
    }).order
    assert "structure-infer" in reachable
    # Its declared non-root input's producer must also be reachable, or this
    # assertion would be vacuous -- confirms the fixpoint pulled in the
    # dependency, not just the stage that happened to have a root input.
    assert "extract" in reachable


def test_structure_infer_requires_html():
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(StageError) as exc_info:
            structure_infer(_ctx(Path(td)), html=None, api_key="sk-test-fake")
        assert exc_info.value.kind == ErrorKind.BAD_INPUT


def test_structure_infer_rejects_empty_api_key():
    """A build that supplies the root input as an empty string is exactly as
    much a bad input as one that never supplied it at all -- reachability
    keys on presence of the dict key, not truthiness of the value."""
    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        html_path = tmp_dir / "extract.html"
        html_path.write_text(SAMPLE_HTML, encoding="utf-8")
        with pytest.raises(StageError) as exc_info:
            structure_infer(_ctx(tmp_dir), html=str(html_path), api_key="")
        assert exc_info.value.kind == ErrorKind.BAD_INPUT


def test_structure_infer_writes_a_classification_artifact(monkeypatch):
    fake = _FakeProvider()
    monkeypatch.setattr(structure_infer_stage, "OpenRouterProvider", lambda api_key: fake)

    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        html_path = tmp_dir / "extract.html"
        html_path.write_text(SAMPLE_HTML, encoding="utf-8")

        result = structure_infer(_ctx(tmp_dir), html=str(html_path), api_key="sk-test-fake")

        assert len(result.artifacts) == 1
        artifact = result.artifacts[0]
        assert artifact.kind == "classification"
        assert artifact.media_type == "application/json"
        # The fake provider was actually invoked -- this isn't a stub result
        # this test fabricated independently of the code path under test.
        assert len(fake.requests) == 1
        assert fake.requests[0].route == "structure-classify"
