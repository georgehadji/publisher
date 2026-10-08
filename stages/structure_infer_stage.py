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
about a node can become a proposal about that node -- and, inside them, the
headings and paragraphs ingest doubted (B6). They go in book order, in batches
that follow chapters, with a per-build cap that warns when it bites (B7). Reading the effective
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

from publisher_stages import (
    stage, StageCtx, StageResult, StageError, ErrorKind, Diagnostic, ArtifactRef as StageArtifactRef,
)
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


# Batching (B7, docs/STRUCTURE_REPAIR_PLAN.md). Each batch is one gateway call and
# one cache entry; batches follow chapters, so an edit in one chapter re-buys
# only the batch holding it.
MAX_NODES_PER_CALL = 40     # one answer stays well inside the fast tier's output
MAX_NODES_PER_BUILD = 400   # the cost bound: ~10 calls; what is left over is never silent
PER_CALL_TIMEOUT_S = 60
# Greedy packing leaves no two neighbouring batches that would fit together, so
# a capped build makes at most this many calls.
MAX_CALLS = 2 * MAX_NODES_PER_BUILD // MAX_NODES_PER_CALL + 1


def _doubted(node: dict) -> bool:
    """Ingest scored it below the review line, and an op could target it. A node
    with no score was never measured (or a person made it): it is not sent."""
    score = node.get("confidence")
    return bool((node.get("sourceRef") or {}).get("docxId")) \
        and isinstance(score, (int, float)) and score < ESCALATE_BELOW


def _input_node(ref: str, current: str, text: str, following: list) -> dict:
    context = " ".join(" ".join(_text(b).strip() for b in following).split())
    return {"sourceRef": ref, "current": current, "text": text, "context": context[:CONTEXT_CHARS]}


def _doubtful_units(ast: dict) -> list[list[dict]]:
    """What ingest was unsure of, as the model's input nodes, one list per
    front-matter section, chapter and back-matter section, in book order: the
    container itself when doubted, then its doubted headings and paragraphs (B6).
    A section has no title, so its first block stands in for it."""
    chapters = [c for node in ast.get("body") or []
                for c in (node.get("content") or [] if node.get("type") == "part" else [node])
                if c.get("type") == "chapter"]
    containers = [*(s for s in ast.get("frontMatter") or [] if s.get("type") in SECTION_LABELS),
                  *chapters,
                  *(s for s in ast.get("backMatter") or [] if s.get("type") in SECTION_LABELS)]
    units = []
    for node in containers:
        blocks, unit = node.get("content") or [], []
        if _doubted(node):
            ref = node["sourceRef"]["docxId"]
            if node["type"] == "chapter":
                unit.append(_input_node(ref, "chapter-title", (node.get("attrs") or {}).get("title", ""), blocks))
            else:
                unit.append(_input_node(ref, SECTION_LABELS[node["type"]], _text(blocks[:1]).strip(), blocks[1:]))
        for index, block in enumerate(blocks):
            if block.get("type") in ("heading", "paragraph") and _doubted(block):
                level = (block.get("attrs") or {}).get("level") or 1
                current = f"heading-{min(level, 3)}" if block["type"] == "heading" else "paragraph"
                unit.append(_input_node(block["sourceRef"]["docxId"], current, _text(block).strip(),
                                        blocks[index + 1:]))
        if unit:
            units.append(unit)
    return units


def _capped(units: list[list[dict]]) -> tuple[list[list[dict]], int]:
    """The units cut to MAX_NODES_PER_BUILD nodes in book order, and how many were left out."""
    kept, room = [], MAX_NODES_PER_BUILD
    for unit in units:
        if room <= 0:
            break
        kept.append(unit[:room])
        room -= len(kept[-1])
    return kept, sum(map(len, units)) - sum(map(len, kept))


def _batches(units: list[list[dict]]) -> list[list[dict]]:
    """Units packed into batches of at most MAX_NODES_PER_CALL, in order. A unit
    is split only when it alone is over the limit."""
    batches: list[list[dict]] = []
    current: list[dict] = []
    for unit in units:
        for start in range(0, len(unit), MAX_NODES_PER_CALL):
            piece = unit[start:start + MAX_NODES_PER_CALL]
            if current and len(current) + len(piece) > MAX_NODES_PER_CALL:
                batches.append(current)
                current = []
            current = current + piece
    return batches + [current] if current else batches


@stage(
    name="structure-infer",
    version=6,   # v6: doubted headings and paragraphs are sent too, in chapter-aligned
                 # batches, one call and cache entry each; past MAX_NODES_PER_BUILD a
                 # classification-truncated warning (B7). Nodes go in book order.
                 # v5: reads doc-effective/1, so accepted overrides shape what is
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
    timeout_s=PER_CALL_TIMEOUT_S * MAX_CALLS,
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

    units, truncated = _capped(_doubtful_units(json.loads(path.read_bytes())))
    batches = _batches(units)
    nodes = [n for batch in batches for n in batch]
    warnings = [Diagnostic(
        code="classification-truncated", severity="warning",
        human_message=f"{truncated} doubtful node(s) past the first {MAX_NODES_PER_BUILD} "
                      "were not sent to the model; review them by hand.")] if truncated else []
    if batches:
        # Inside cas_root: the only path the worker's read-only container can write
        # that outlives the build. Shard dirs are two hex chars, so no collision.
        gateway = InferenceGateway(provider=OpenRouterProvider(api_key=api_key),
                                   cache_dir=Path(ctx.cas_root) / INFERENCE_CACHE_DIR)
        route = gateway.route("structure-classify")
        results = []
        for index, batch in enumerate(batches):
            result = gateway.classify(InferenceRequest(
                request_id=f"{ctx.build_id}-{index}",
                route=route.route,
                inputs={"nodes": batch},
                prompt_version=route.prompt_version,
                schema_version=route.schema_version,
            ))
            if "error" in result.output:
                raise StageError(kind=ErrorKind.INFRA,
                                 message=f"structure-infer, batch {index + 1}/{len(batches)}: {result.output['error']}")
            results.append(result)
        infos = [r.output.get("modelInfo") or {} for r in results]
        output = {**results[0].output,
                  "nodes": [n for r in results for n in r.output.get("nodes") or []],
                  "modelInfo": {**infos[0], "cacheHit": all(i.get("cacheHit") for i in infos),
                                "costUsd": sum(i.get("costUsd") or 0.0 for i in infos)}}
        tier = results[0].tier_used.value
        cost = sum(r.cost_usd for r in results)
        confidence = min(r.confidence for r in results)
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
            "batches": len(batches),
            "nodes_truncated": truncated,
            "confidence": confidence,
            "cost_usd": cost,
        },
        warnings=warnings,
    )
