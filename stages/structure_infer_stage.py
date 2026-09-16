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

Consumes `typescript-html/1` (the same input `ast-assemble` takes, NOT its
output) and runs the deterministic rules pass
(services/structure/publisher_structure/rules.py) to find which blocks
scored below the confidence threshold, then routes exactly those through the
real inference cascade. Runs in PARALLEL with `ast-assemble`, never feeding
into it, `resolve`, or `paginate`: an LLM classification is advisory input
for a future human/agent review decision, never a live value a deterministic
stage silently trusts (D9). Terminal output, `classification/1` -- the exact
schema `InferenceResult.output` already produces and
schemas/classification/classification.schema.json already defines. Wiring
its CONSUMPTION (surfacing it in the structure review UI, folding accepted
suggestions into the override log) is separate, later work this stage's
existence does not presuppose.
"""

from __future__ import annotations
import json
from pathlib import Path

from publisher_stages import stage, StageCtx, StageResult, StageError, ErrorKind, ArtifactRef as StageArtifactRef
from publisher_cas import ContentAddressedStore, CasConfig, MediaType

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "structure"))
from publisher_structure.rules import parse_html, find_low_confidence
from publisher_structure.inference import InferenceGateway, InferenceRequest, OpenRouterProvider


@stage(
    name="structure-infer",
    version=1,
    inputs={"html": "typescript-html/1", "api_key": "openrouter-credential/1"},
    root_inputs=["api_key"],
    outputs={"classification": "classification/1"},
    terminal_outputs=["classification"],
    toolchain=[],
    fixtures=None,
    memory_budget_mb=128,
    timeout_s=60,
    queue="q.structure",
    description="Route typescript-html/1's low-confidence blocks through the real "
                "inference cascade -- reachable only when OpenRouter credentials "
                "are supplied as this stage's api_key root input.",
)
def structure_infer(ctx: StageCtx, html: str | None = None, api_key: str | None = None) -> StageResult:
    if html is None:
        raise StageError(kind=ErrorKind.BAD_INPUT, message="structure-infer requires 'html' (from extract)")
    if not api_key:
        # Reachability already keeps this stage out of any build that never
        # supplied the root input at all; a build that DOES supply the key
        # as an empty string is exactly as much a bad input as a missing file.
        raise StageError(kind=ErrorKind.BAD_INPUT, message="structure-infer requires a non-empty api_key")

    html_path = Path(html)
    if not html_path.exists():
        raise StageError(kind=ErrorKind.BAD_INPUT, message=f"HTML input not found: {html}")

    blocks = parse_html(html_path.read_text(encoding="utf-8"))
    low_confidence_indices = find_low_confidence(blocks)
    nodes = [
        {"sourceRef": blocks[i].source_id or f"block-{i}", "text": blocks[i].text}
        for i in low_confidence_indices
    ]

    gateway = InferenceGateway(provider=OpenRouterProvider(api_key=api_key))
    request = InferenceRequest(
        request_id=ctx.build_id,
        route="structure-classify",
        inputs={"nodes": nodes},
        prompt_version="1.0",
        schema_version="classification/1",
    )
    result = gateway.classify(request)

    cas = ContentAddressedStore(CasConfig(local_cache_root=Path(ctx.cas_root)))
    output_bytes = json.dumps(result.output, indent=2).encode("utf-8")
    ref = cas.put(output_bytes, media_type=MediaType("application/json"))

    print(f"  [structure-infer] Classified {len(nodes)} low-confidence node(s) "
          f"via {result.tier_used.value} -> {ref.hash}")

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
            "confidence": result.confidence,
            "cost_usd": result.cost_usd,
            "cache_hit": float(result.cache_hit),
        },
    )
