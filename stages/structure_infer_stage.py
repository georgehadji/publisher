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

Consumes `doc-effective/1` -- the book with every accepted override applied --
and sends the model exactly the chapters and front/back-matter sections ingest
scored below the review line (`rules.ESCALATE_BELOW`), each under its
`sourceRef.docxId` -- the id an override op targets, so whatever the model says
about a node can become a proposal about that node. Reading the effective
document, not `ast/1` (B0, docs/STRUCTURE_REPAIR_PLAN.md), is what lets a fix
that takes two steps surface its second once the first is accepted: a section
`start_body` turned into a chapter is no longer asked about as a section, and a
node an op created can be asked about at all. A node an op made carries no
score, so it is never sent: a person already decided it.

Runs after `resolve`, never feeding it or `paginate`: an LLM classification is
advisory input for a human decision, never a live value a deterministic stage
trusts (D9). Terminal output, `classification/1`.
"""

from __future__ import annotations
import json
from pathlib import Path

from publisher_stages import stage, StageCtx, StageResult, StageError, ErrorKind, ArtifactRef as StageArtifactRef
from publisher_cas import ContentAddressedStore, CasConfig, MediaType

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "structure"))
from publisher_structure.rules import ESCALATE_BELOW
from publisher_structure.classify_contract import SECTION_LABELS
from publisher_structure.inference import InferenceGateway, InferenceRequest, OpenRouterProvider

# What the model sees of a node: its text, and the opening of what follows it.
CONTEXT_CHARS = 200
INFERENCE_CACHE_DIR = "inference-cache"


def _text(node) -> str:
    if isinstance(node, list):
        return "".join(_text(n) for n in node)
    if not isinstance(node, dict):
        return ""
    own = node.get("text", "") if node.get("type") == "text" else ""
    return own + _text(node.get("content"))


def _doubtful_nodes(ast: dict) -> list[dict]:
    """The chapters and front/back-matter sections ingest was unsure of, as the
    model's input nodes. A node with no score was never measured: it is not sent
    as if it were doubtful, and not counted as certain either -- the review view
    shows it as unscored. A section has no title, so its first block stands in."""
    chapters = [c for node in ast.get("body") or []
                for c in (node.get("content") or [] if node.get("type") == "part" else [node])
                if c.get("type") == "chapter"]
    sections = [s for root in ("frontMatter", "backMatter") for s in ast.get(root) or []
                if s.get("type") in SECTION_LABELS]
    nodes = []
    for node in chapters + sections:
        ref = (node.get("sourceRef") or {}).get("docxId")
        score = node.get("confidence")
        if not (ref and isinstance(score, (int, float)) and score < ESCALATE_BELOW):
            continue
        blocks = node.get("content") or []
        if node["type"] == "chapter":
            current, text = "chapter-title", (node.get("attrs") or {}).get("title", "")
        else:
            current, text, blocks = SECTION_LABELS[node["type"]], _text(blocks[:1]).strip(), blocks[1:]
        following = " ".join(_text(b).strip() for b in blocks)
        nodes.append({"sourceRef": ref, "current": current, "text": text,
                      "context": " ".join(following.split())[:CONTEXT_CHARS]})
    return nodes


@stage(
    name="structure-infer",
    version=5,   # v5: reads doc-effective/1, so accepted overrides shape what is
                 # asked next (B0, docs/STRUCTURE_REPAIR_PLAN.md).
                 # v4: front/back-matter sections ingest was unsure of are sent too,
                 # under their own docxId, with their type's label as `current`.
                 # v3: answers cached under cas_root/inference-cache, keyed on what
                 # is sent -- an edit elsewhere in the book no longer buys the
                 # same classification again.
                 # v2: reads ast/1, sends ingest's low-confidence chapters by docxId
                 # (was: rules over typescript-html/1, `block-<i>` ids); strict
                 # structured output, pinned provider, real prompt (W3).
    inputs={"doc_path": "doc-effective/1", "api_key": "openrouter-credential/1"},
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
def structure_infer(ctx: StageCtx, doc_path: str | None = None, api_key: str | None = None) -> StageResult:
    if doc_path is None:
        raise StageError(kind=ErrorKind.BAD_INPUT, message="structure-infer requires 'doc_path' (from resolve)")
    if not api_key:
        # Reachability already keeps this stage out of any build that never
        # supplied the root input at all; a build that DOES supply the key
        # as an empty string is exactly as much a bad input as a missing file.
        raise StageError(kind=ErrorKind.BAD_INPUT, message="structure-infer requires a non-empty api_key")

    path = Path(doc_path)
    if not path.exists():
        raise StageError(kind=ErrorKind.BAD_INPUT, message=f"effective document not found: {doc_path}")

    nodes = _doubtful_nodes(json.loads(path.read_bytes()))
    if nodes:
        # Inside cas_root: the only path the worker's read-only container can write
        # that outlives the build. Shard dirs are two hex chars, so no collision.
        gateway = InferenceGateway(provider=OpenRouterProvider(api_key=api_key),
                                   cache_dir=Path(ctx.cas_root) / INFERENCE_CACHE_DIR)
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
