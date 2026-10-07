"""
Structure proposals -- `structure-propose` (W4, docs/WIRING_PLAN.md).

Turns what the model said about the chapters ingest was unsure of
(`classification/1`, from `structure-infer`) into proposals a reviewer can
accept (`agent-proposal/1`), against the same `doc-effective/1` the model was
shown -- so a proposal always names a node in the book as already corrected. Nothing here calls a model: the model's decision
is already frozen in its CAS artifact, and this is a fixed translation of it,
so the same classification always gives the same proposals.

Each proposal is one implemented override op, named by its type:

| model's label for a doubtful chapter      | proposal          | op it becomes |
|-------------------------------------------|-------------------|---------------|
| paragraph / chapter-opening / first-paragraph | merge_chapters | merge         |
| heading-1 / heading-2 / heading-3         | adjust_heading_level | demote     |
| uncertain, or any other label             | flag_ambiguity    | flag_ambiguity |
| chapter-title                             | (none -- it agrees with ingest) |  |

A doubtful front/back-matter section's own type's label is agreement. The
earliest front-matter section read as a `chapter-title` becomes `start_body`
(the body really starts there; later ones move with it, so get nothing), and
the last back-matter one becomes `end_body` (its mirror). Any other label
becomes `flag_ambiguity`.

The API builds the op from a proposal only when a reviewer accepts it
(`POST /v1/manuscripts/:id/proposals/:pid/accept`); until then nothing reaches
`resolve`. That is D9: no LLM output enters a deterministic stage on its own.

With the optional `api_key` root input (W5), the Structure Wrangler then
reviews the proposals with read-only tools and may drop or re-rank them -- its
answer schema lists only the ids it was given, so it cannot add or change one.
A failed review keeps every proposal and says so as a warning: the review is
advisory, and the human gate sees each proposal either way.

Terminal output. Reachable exactly when `structure-infer` is, since it
consumes that stage's output: a build without an API key is unchanged.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from publisher_stages import (
    stage, StageCtx, StageResult, StageError, ErrorKind, Diagnostic, ArtifactRef as StageArtifactRef,
)
from publisher_cas import ContentAddressedStore, CasConfig, MediaType
from publisher_structure.classify_contract import SECTION_LABELS

AGENT_ID = "structure-propose"
AGENT_VERSION = "1"
WRANGLER_ROUTE = "structure-wrangle"

_AS_PROSE = {"paragraph", "chapter-opening", "first-paragraph"}
_AS_HEADING = {"heading-1", "heading-2", "heading-3"}
_AGREES = {"chapter-title"}
# The override op each proposal type becomes when accepted. The API builds the
# op (packages/api/src/contract.ts PROPOSAL_OPS, pinned to this by
# tests/test_single_source.py); every value is an implemented op.
PROPOSAL_OPS = {"merge_chapters": "merge", "adjust_heading_level": "demote",
                "flag_ambiguity": "flag_ambiguity", "start_body": "start_body",
                "end_body": "end_body", "reclassify": "reclassify"}
# The parameters each proposal type carries into its op (contract.ts
# PROPOSAL_PARAMS, pinned the same way). The API copies exactly these: one the
# type does not declare, or one it declares and the proposal lacks, is a 422 (B2).
PROPOSAL_PARAMS = {"merge_chapters": (), "adjust_heading_level": (),
                   "flag_ambiguity": (), "start_body": (), "end_body": (),
                   "reclassify": ("from", "to")}


def _proposal_id(docx_id: str, kind: str) -> str:
    # From the node and the kind of change only, not the build: the same
    # advice about the same chapter is the same proposal on every rebuild, so
    # "accepted" (its op is in the log) survives a rebuild.
    return "pr-" + hashlib.sha256(f"{docx_id}|{kind}".encode("utf-8")).hexdigest()[:24]


def _chapters_in_order(ast: dict) -> list[tuple[dict, bool]]:
    """Every chapter, and whether a chapter precedes it in the same list (so a
    merge or demote into the one before can apply)."""
    out: list[tuple[dict, bool]] = []

    def walk(items: list) -> None:
        for index, node in enumerate(items):
            if node.get("type") == "chapter":
                out.append((node, index > 0 and items[index - 1].get("type") == "chapter"))
            elif node.get("type") == "part":
                walk(node.get("content") or [])

    walk(ast.get("body") or [])
    return out


def proposals_for(classification: dict, ast: dict) -> list[dict]:
    model = (classification.get("modelInfo") or {}).get("modelId", "unknown")
    verdicts = {n["sourceRef"]: n for n in classification.get("nodes") or []}
    proposals = []
    for chapter, has_prior in _chapters_in_order(ast):
        ref = (chapter.get("sourceRef") or {}).get("docxId")
        verdict = verdicts.get(ref)
        if verdict is None or verdict["classification"] in _AGREES:
            continue
        label, score = verdict["classification"], verdict["confidence"]
        title = (chapter.get("attrs") or {}).get("title", "")
        if label in _AS_PROSE and has_prior:
            kind, why = "merge_chapters", f"reads “{title}” as ordinary prose, not a chapter title"
        elif label in _AS_HEADING and has_prior:
            kind, why = "adjust_heading_level", f"reads “{title}” as a section heading, not a chapter title"
        else:
            kind, why = "flag_ambiguity", f"reads “{title}” as {label}, not a chapter title"
        proposals.append({
            "id": _proposal_id(ref, kind),
            "type": kind,
            "sourceRef": {"docxId": ref},
            "rationale": f"The model ({model}) {why} (confidence {score:.2f}).",
            "confidence": score,
            "evidence": [f"label: {label}", f"model: {model}"],
        })
    for root in ("frontMatter", "backMatter"):
        front = root == "frontMatter"
        sections = ast.get(root) or []
        disagreeing = {}
        for index, section in enumerate(sections):
            verdict = verdicts.get((section.get("sourceRef") or {}).get("docxId"))
            own = SECTION_LABELS.get(section.get("type"))
            if verdict is not None and own is not None and verdict["classification"] != own:
                disagreeing[index] = (verdict, own)
        chapters = [i for i, (v, _) in disagreeing.items() if v["classification"] in _AGREES]
        # The body boundary: the first such front section, the last such back one.
        # The others move with it, so they get no proposal of their own.
        boundary = (min if front else max)(chapters, default=None)
        for index, (verdict, own) in disagreeing.items():
            section, ref = sections[index], sections[index]["sourceRef"]["docxId"]
            label, score = verdict["classification"], verdict["confidence"]
            opening = _opening(section)
            if label in _AGREES:
                if index != boundary:
                    continue
                if front:
                    kind, edge, others = "start_body", "starts", f"{len(sections) - index - 1} front-matter section(s) after it"
                else:
                    kind, edge, others = "end_body", "ends", f"{index} back-matter section(s) before it"
                why = (f"reads the {section['type']} section opening “{opening}” as a chapter "
                       f"title: the body {edge} here, so it and the {others} become chapters")
            else:
                kind = "flag_ambiguity"
                why = f"reads the {section['type']} section opening “{opening}” as {label}, not {own}"
            proposals.append({
                "id": _proposal_id(ref, kind),
                "type": kind,
                "sourceRef": {"docxId": ref},
                "rationale": f"The model ({model}) {why} (confidence {score:.2f}).",
                "confidence": score,
                "evidence": [f"label: {label}", f"model: {model}"],
            })
    return proposals


def _opening(section: dict, limit: int = 80) -> str:
    """A section's first block, as plain text: it has no title to quote."""
    def text(node) -> str:
        if isinstance(node, list):
            return "".join(text(n) for n in node)
        if not isinstance(node, dict):
            return ""
        return (node.get("text", "") if node.get("type") == "text" else "") + text(node.get("content"))
    return " ".join(text((section.get("content") or [])[:1]).split())[:limit]


@stage(
    name="structure-propose",
    version=6,  # v6: PROPOSAL_PARAMS, and reclassify is acceptable (B2)
                # v5: proposes against doc-effective/1, the document structure-infer
                # classified, so ids match and accepted ops shape what comes next (B0)
                # v4: a back-matter section read as a chapter proposes end_body
                # v3: a front-matter section read as a chapter proposes start_body
                # v2: a verdict on a doubtful front/back-matter section is flagged
    inputs={"classification": "classification/1", "doc_path": "doc-effective/1",
            "api_key": "openrouter-credential/1"},
    root_inputs=["api_key"],
    optional_root_inputs=["api_key"],  # absent: proposals unreviewed, never unreachable
    outputs={"proposals": "agent-proposal/1"},
    terminal_outputs=["proposals"],
    toolchain=[],
    fixtures=None,
    memory_budget_mb=128,
    timeout_s=60,
    queue="q.structure",
    description="Turn structure-infer's classification of doubtful chapters into "
                "proposals a reviewer can accept as override ops.",
)
def structure_propose(ctx: StageCtx, classification: str | None = None,
                      doc_path: str | None = None, api_key: str | None = None) -> StageResult:
    for name, value in (("classification", classification), ("doc_path", doc_path)):
        if value is None or not Path(value).exists():
            raise StageError(kind=ErrorKind.BAD_INPUT, message=f"structure-propose requires '{name}'")
    verdicts = json.loads(Path(classification).read_bytes())
    document_ast = json.loads(Path(doc_path).read_bytes())
    proposals = proposals_for(verdicts, document_ast)
    warnings: list[Diagnostic] = []
    made = len(proposals)
    if api_key and proposals:
        proposals, failure = _reviewed(proposals, document_ast, api_key)
        if failure:
            warnings.append(Diagnostic(
                code="proposal-review-failed", severity="warning",
                human_message=f"The Structure Wrangler's review failed, so all {made} "
                              f"proposals are shown unreviewed: {failure}"))
    document = {
        "schema": "agent-proposal/1",
        "agentId": AGENT_ID,
        "agentVersion": AGENT_VERSION,
        "modelId": (verdicts.get("modelInfo") or {}).get("modelId", "unknown"),
        "proposals": proposals,
    }
    data = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ref = ContentAddressedStore(CasConfig(local_cache_root=Path(ctx.cas_root))).put(
        data, media_type=MediaType("application/json"))
    print(f"  [structure-propose] {len(proposals)} proposal(s) -> {ref.hash}")
    return StageResult(
        artifacts=[StageArtifactRef(kind="proposals", hash=str(ref.hash),
                                    media_type="application/json", size=len(data))],
        metrics={"proposals": len(proposals), "proposals_dropped": made - len(proposals)},
        warnings=warnings,
    )


def _wrangler(api_key: str):
    """The production Wrangler. A seam so tests drive the review with a scripted provider."""
    from publisher_agents.openrouter import OpenRouterAgentProvider
    from publisher_agents.runtime import AgentRuntime
    from publisher_agents.structure_wrangler import StructureWrangler
    from publisher_structure.inference import load_routes_from_policy

    route = load_routes_from_policy(service="agents")[WRANGLER_ROUTE]
    return StructureWrangler(AgentRuntime(OpenRouterAgentProvider(api_key, route)))


def _reviewed(proposals: list[dict], ast: dict, api_key: str) -> tuple[list[dict], str | None]:
    kept, result = _wrangler(api_key).review(proposals, ast)
    return kept, (result.error if result is not None and result.failed else None)
