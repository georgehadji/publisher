"""
Structure inference stage -- `structure-infer` (E6.2, docs/ARCHITECTURE_SCORE_10_PLAN.md).

Wires InferenceGateway onto an executing, testable path instead of leaving it
"constructed nowhere outside its own tests" (L8). Guarded-reachable, the same
mechanism that keeps the whole cover-art pipeline absent from a plain
interior-book build: `api_key` is a required ROOT input with no producer, so
a build whose initial_inputs never supplies one is simply never reachable --
`worker.py`'s `_initial_inputs_for` only supplies it when OPENROUTER_API_KEY
is configured. No env-var-gated escape hatch inside this stage decides
anything; the DAG's own reachability fixpoint does.

Consumes `ast/1` (W3, docs/WIRING_PLAN.md) and sends the model exactly the
chapters ingest scored below the review line (`rules.ESCALATE_BELOW`), each
under its `sourceRef.docxId` -- the id an override op targets, so whatever the
model says about a node can become a proposal about that node. It used to
re-run the rules pass over typescript HTML and label nodes `block-<i>`, ids no
op can aim at, from a second classifier whose scores nothing else used.

Runs in parallel with `resolve`, never feeding it or `paginate`: an LLM
classification is advisory input for a human decision, never a live value a
deterministic stage trusts (D9). Terminal output, `classification/1`.
"""

from __future__ import annotations
import json
from pathlib import Path

from publisher_stages import stage, StageCtx, StageResult, StageError, ErrorKind, ArtifactRef as StageArtifactRef
from publisher_cas import ContentAddressedStore, CasConfig, MediaType

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "structure"))
from publisher_structure.rules import ESCALATE_BELOW
from publisher_structure.inference import InferenceGateway, InferenceRequest, OpenRouterProvider

# What the model sees of a node: its text, and the opening of what follows it.
CONTEXT_CHARS = 200


def _text(node) -> str:
    if isinstance(node, list):
        return "".join(_text(n) for n in node)
    if not isinstance(node, dict):
        return ""
    own = node.get("text", "") if node.get("type") == "text" else ""
    return own + _text(node.get("content"))


def _doubtful_chapters(ast: dict) -> list[dict]:
    """The chapters ingest was unsure of, as the model's input nodes. A chapter
    with no score was never measured: it is not sent as if it were doubtful,
    and not counted as certain either -- the review view shows it as unscored."""
    chapters = [c for node in ast.get("body") or []
                for c in (node.get("content") or [] if node.get("type") == "part" else [node])
                if c.get("type") == "chapter"]
    nodes = []
    for chapter in chapters:
        ref = (chapter.get("sourceRef") or {}).get("docxId")
        score = chapter.get("confidence")
        if ref and isinstance(score, (int, float)) and score < ESCALATE_BELOW:
            following = " ".join(_text(b).strip() for b in chapter.get("content") or [])
            nodes.append({"sourceRef": ref, "current": "chapter-title",
                          "text": (chapter.get("attrs") or {}).get("title", ""),
                          "context": " ".join(following.split())[:CONTEXT_CHARS]})
    return nodes


@stage(
    name="structure-infer",
    version=2,   # v2: reads ast/1, sends ingest's low-confidence chapters by docxId
                 # (was: rules over typescript-html/1, `block-<i>` ids); strict
                 # structured output, pinned provider, real prompt (W3).
    inputs={"ast": "ast/1", "api_key": "openrouter-credential/1"},
    root_inputs=["api_key"],
    outputs={"classification": "classification/1"},
    terminal_outputs=["classification"],
    toolchain=[],
    fixtures=None,
    memory_budget_mb=128,
    timeout_s=60,
    queue="q.structure",
    description="Route the chapters ingest was unsure of through the real "
                "inference cascade -- reachable only when OpenRouter credentials "
                "are supplied as this stage's api_key root input.",
)
def structure_infer(ctx: StageCtx, ast: str | None = None, api_key: str | None = None) -> StageResult:
    if ast is None:
        raise StageError(kind=ErrorKind.BAD_INPUT, message="structure-infer requires 'ast' (from ast-assemble)")
    if not api_key:
        # Reachability already keeps this stage out of any build that never
        # supplied the root input at all; a build that DOES supply the key
        # as an empty string is exactly as much a bad input as a missing file.
        raise StageError(kind=ErrorKind.BAD_INPUT, message="structure-infer requires a non-empty api_key")

    ast_path = Path(ast)
    if not ast_path.exists():
        raise StageError(kind=ErrorKind.BAD_INPUT, message=f"AST input not found: {ast}")

    nodes = _doubtful_chapters(json.loads(ast_path.read_bytes()))
    if nodes:
        gateway = InferenceGateway(provider=OpenRouterProvider(api_key=api_key))
        route = gateway.route("structure-classify")
        result = gateway.classify(InferenceRequest(
            request_id=ctx.build_id,
            route=route.route,
            inputs={"nodes": nodes},
            prompt_version=route.prompt_version,
            schema_version=route.schema_version,
        ))
        if "error" in result.output:
            raise StageError(kind=ErrorKind.INFRA, message=f"structure-infer: {result.output['error']}")
        output, tier, cost, confidence = result.output, result.tier_used.value, result.cost_usd, result.confidence
    else:
        # Nothing doubtful, so no model is asked: say so rather than name one.
        output = {"schema": "classification/1", "nodes": [],
                  "modelInfo": {"modelId": "none", "cacheHit": False, "costUsd": 0.0}}
        tier, cost, confidence = "none", 0.0, 1.0

    cas = ContentAddressedStore(CasConfig(local_cache_root=Path(ctx.cas_root)))
    output_bytes = json.dumps(output, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ref = cas.put(output_bytes, media_type=MediaType("application/json"))

    print(f"  [structure-infer] Classified {len(nodes)} low-confidence node(s) "
          f"via {tier} -> {ref.hash}")

    return StageResult(
        artifacts=[
            StageArtifactRef(
                kind="classification",
                hash=str(ref.hash),
                media_type="application/json",
                size=len(output_bytes),
            ),
        ],
        metrics={
            "low_confidence_nodes": len(nodes),
            "confidence": confidence,
            "cost_usd": cost,
        },
    )
