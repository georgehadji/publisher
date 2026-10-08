"""
`structure-infer` (E6.2, docs/ARCHITECTURE_SCORE_10_PLAN.md) -- guarded
reachability and the stage's own plumbing (AST -> the chapters ingest was
unsure of, by docxId -> InferenceGateway -> classification/1 CAS artifact).

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


def _chapter(n: int, title: str, confidence=None, docx_id=None) -> dict:
    node = {"type": "chapter", "attrs": {"number": n, "id": f"ch{n}", "title": title},
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": f"Prose of {title}."}]}]}
    if confidence is not None:
        node["confidence"] = confidence
    if docx_id:
        node["sourceRef"] = {"docxId": docx_id}
    return node


SAMPLE_AST = {"schema": "ast/1", "body": [
    _chapter(1, "CHAPTER ONE", 0.95, "c1"),
    _chapter(2, "NO!", 0.7, "c2"),                       # doubtful: sent
    {"type": "part", "attrs": {"title": "II", "id": "p2"}, "content": [
        _chapter(3, "WHY", 0.6, "c3")]},                  # inside a part: sent
    _chapter(4, "Unscored"),                             # not measured: not sent
    _chapter(5, "No id", 0.5),                           # nothing could target it
], "frontMatter": [                                      # the boundary's fallback: sent
    {"type": "dedication", "confidence": 0.5, "sourceRef": {"docxId": "f1"}, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "DEDICATION"}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "For mum."}]}]},
], "backMatter": [                                       # found by pattern: not sent
    {"type": "colophon", "confidence": 0.9, "sourceRef": {"docxId": "b1"}, "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "COLOPHON"}]}]},
]}


def _write_ast(tmp_dir: Path, ast: dict = SAMPLE_AST) -> str:
    import json
    path = tmp_dir / "ast.json"
    path.write_text(json.dumps(ast), encoding="utf-8")
    return str(path)


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
    """Supplying the root input -- and ONLY the root input; doc-effective/1 is
    produced by `resolve`, itself reachable from the ingested manuscript -- is
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
    assert "resolve" in reachable
    # B0: it classifies the document with accepted overrides applied, so it
    # runs after resolve, never on the raw AST.
    assert reachable.index("resolve") < reachable.index("structure-infer")


def test_structure_infer_requires_a_document():
    with tempfile.TemporaryDirectory() as td:
        with pytest.raises(StageError) as exc_info:
            structure_infer(_ctx(Path(td)), doc_path=None, api_key="sk-test-fake")
        assert exc_info.value.kind == ErrorKind.BAD_INPUT


def test_structure_infer_rejects_empty_api_key():
    """A build that supplies the root input as an empty string is exactly as
    much a bad input as one that never supplied it at all -- reachability
    keys on presence of the dict key, not truthiness of the value."""
    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        with pytest.raises(StageError) as exc_info:
            structure_infer(_ctx(tmp_dir), doc_path=_write_ast(tmp_dir), api_key="")
        assert exc_info.value.kind == ErrorKind.BAD_INPUT


def test_structure_infer_writes_a_classification_artifact(monkeypatch):
    fake = _FakeProvider()
    monkeypatch.setattr(structure_infer_stage, "OpenRouterProvider", lambda api_key: fake)

    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        result = structure_infer(_ctx(tmp_dir), doc_path=_write_ast(tmp_dir), api_key="sk-test-fake")

        assert len(result.artifacts) == 1
        artifact = result.artifacts[0]
        assert artifact.kind == "classification"
        assert artifact.media_type == "application/json"
        # The fake provider was actually invoked -- this isn't a stub result
        # this test fabricated independently of the code path under test.
        assert len(fake.requests) == 1
        assert fake.requests[0].route == "structure-classify"
        # Only what ingest was unsure of, by the id an op can target.
        sent = fake.requests[0].inputs["nodes"]
        # In book order: the front matter first (B7).
        assert [n["sourceRef"] for n in sent] == ["f1", "c2", "c3"]
        assert sent[1]["text"] == "NO!" and sent[1]["context"].startswith("Prose of NO!")
        # A section has no title: its first block stands in, under its type's label.
        assert sent[0] == {"sourceRef": "f1", "current": "front-dedication",
                           "text": "DEDICATION", "context": "For mum."}


def test_nothing_doubtful_means_no_model_is_asked(monkeypatch):
    def no_call(api_key):
        raise AssertionError("a provider was built with nothing to classify")
    monkeypatch.setattr(structure_infer_stage, "OpenRouterProvider", no_call)
    import json
    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        certain = {"schema": "ast/1", "body": [_chapter(1, "CHAPTER ONE", 0.95, "c1")]}
        result = structure_infer(_ctx(tmp_dir), doc_path=_write_ast(tmp_dir, certain), api_key="sk-test-fake")
        assert result.metrics["low_confidence_nodes"] == 0


# ── B7: doubted blocks, chapter-aligned batches, a per-build cap ──

def _para(text, ref=None, confidence=None):
    node = {"type": "paragraph", "content": [{"type": "text", "text": text}]}
    if ref:
        node["sourceRef"] = {"docxId": ref}
    if confidence is not None:
        node["confidence"] = confidence
    return node


def _book(*doubted_per_chapter, word="x"):
    """One chapter per count, holding that many doubted paragraphs."""
    return {"schema": "ast/1", "body": [
        {"type": "chapter", "attrs": {"number": n, "id": f"ch{n}", "title": f"C{n}"},
         "sourceRef": {"docxId": f"c{n}"}, "confidence": 0.95,
         "content": [_para(f"{word} {n}.{i}", f"p{n}.{i}", 0.5) for i in range(count)] or [_para("prose")]}
        for n, count in enumerate(doubted_per_chapter, start=1)]}


def _run(tmp_dir, ast, fake):
    ctx = StageCtx(build_id="b", deterministic_seed="t", deadline=datetime.now(timezone.utc),
                   memory_budget_mb=128, work_dir=str(tmp_dir), cas_root=str(tmp_dir / "cas"))
    return structure_infer(ctx, doc_path=_write_ast(tmp_dir, ast), api_key="sk-test-fake")


def test_doubted_blocks_are_sent_with_what_ingest_made_them(monkeypatch, tmp_path):
    fake = _FakeProvider()
    monkeypatch.setattr(structure_infer_stage, "OpenRouterProvider", lambda api_key: fake)
    chapter = {"type": "chapter", "attrs": {"number": 1, "id": "ch1", "title": "One"},
               "sourceRef": {"docxId": "c1"}, "confidence": 0.95, "content": [
                   _para("Chapter 3", "p1", 0.5), _para("Then it rained.", "p2"),
                   {"type": "heading", "attrs": {"level": 2}, "sourceRef": {"docxId": "h1"},
                    "confidence": 0.85, "content": [{"type": "text", "text": "1.2 Method"}]},
                   {"type": "heading", "attrs": {"level": 5}, "sourceRef": {"docxId": "h2"},
                    "confidence": 0.5, "content": [{"type": "text", "text": "Deep"}]}]}
    _run(tmp_path, {"schema": "ast/1", "body": [chapter]}, fake)
    sent = fake.requests[0].inputs["nodes"]
    assert [(n["sourceRef"], n["current"]) for n in sent] == [("p1", "paragraph"), ("h2", "heading-3")]
    assert sent[0]["text"] == "Chapter 3" and sent[0]["context"].startswith("Then it rained.")


def test_batches_follow_chapters_and_split_only_an_oversized_one(monkeypatch, tmp_path):
    monkeypatch.setattr(structure_infer_stage, "MAX_NODES_PER_CALL", 2)
    fake = _FakeProvider()
    monkeypatch.setattr(structure_infer_stage, "OpenRouterProvider", lambda api_key: fake)
    result = _run(tmp_path, _book(1, 2, 1, 3), fake)
    sizes = [[n["sourceRef"] for n in r.inputs["nodes"]] for r in fake.requests]
    assert sizes == [["p1.0"], ["p2.0", "p2.1"], ["p3.0"], ["p4.0", "p4.1"], ["p4.2"]]
    assert result.metrics["batches"] == 5 and result.warnings == []


def test_an_edit_in_one_chapter_re_buys_only_its_batch(monkeypatch, tmp_path):
    monkeypatch.setattr(structure_infer_stage, "MAX_NODES_PER_CALL", 2)
    fake = _FakeProvider()
    monkeypatch.setattr(structure_infer_stage, "OpenRouterProvider", lambda api_key: fake)
    _run(tmp_path, _book(2, 2, 2), fake)
    first = len(fake.requests)
    edited = _book(2, 2, 2)
    edited["body"][1]["content"][0]["content"][0]["text"] = "changed"
    _run(tmp_path, edited, fake)
    assert first == 3 and len(fake.requests) == 4
    assert fake.requests[-1].inputs["nodes"][0]["text"] == "changed"


def test_the_build_cap_warns_with_what_it_left_out(monkeypatch, tmp_path):
    monkeypatch.setattr(structure_infer_stage, "MAX_NODES_PER_BUILD", 3)
    fake = _FakeProvider()
    monkeypatch.setattr(structure_infer_stage, "OpenRouterProvider", lambda api_key: fake)
    result = _run(tmp_path, _book(2, 3), fake)
    assert [n["sourceRef"] for r in fake.requests for n in r.inputs["nodes"]] == ["p1.0", "p1.1", "p2.0"]
    assert [w.code for w in result.warnings] == ["classification-truncated"]
    assert result.warnings[0].human_message.startswith("2 doubtful node(s)")
    assert result.metrics["nodes_truncated"] == 2
