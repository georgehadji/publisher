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
the last back-matter one becomes `end_body` (its mirror). A label naming
another section type its list may hold becomes `reclassify` (`from`/`to`);
any other label -- a type only the other end holds, or no section at all --
becomes `flag_ambiguity`. `section_decision` is that table (B5).

A doubted heading or paragraph directly in a chapter goes through
`block_decision` (B8): read as a `chapter-title` mid-chapter it is a
`split_chapter` (at either end, a flag -- split cannot open a chapter there);
a heading read one level up or down is `promote_heading` / `adjust_heading_level`
(one level a build); a paragraph read as `heading-m` is `reclassify` to a
heading with `value` m, a heading read as prose is `reclassify` to a paragraph,
and a paragraph read as a blockquote, epigraph, dialogue or sidebar is
`reclassify` into one. Agreement is with the label structure-infer sent
(`block_label`); a paragraph's prose roles agree. Everything else is a flag. A
block inside a front/back-matter section gets nothing: the section's own
proposal (end_body) moves it into a chapter first.

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
from publisher_structure.classify_contract import SECTION_LABELS, block_label
from publisher_structure.overrides import section_types

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
                "end_body": "end_body", "reclassify": "reclassify",
                "split_chapter": "split", "promote_heading": "promote"}
# The parameters each proposal type may carry into its op (contract.ts
# PROPOSAL_PARAMS, pinned the same way). The API copies these: one the type
# does not declare is a 422 (B2), and so is one the op's schema requires that
# the proposal lacks. reclassify's `value` (a heading level) is optional (B8).
PROPOSAL_PARAMS = {"merge_chapters": (), "adjust_heading_level": (),
                   "flag_ambiguity": (), "start_body": (), "end_body": (),
                   "reclassify": ("from", "to", "value"),
                   "split_chapter": (), "promote_heading": ()}


def _change(kind: str, params: dict) -> str:
    """What a proposal changes, for its id: the type and every parameter but
    `from` (which the node already is). A different target is a different proposal."""
    return ":".join([kind, *(str(v) for k, v in params.items() if k != "from")])


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
    for chapter, _ in _chapters_in_order(ast):
        blocks = chapter.get("content") or []
        for index, block in enumerate(blocks):
            ref = (block.get("sourceRef") or {}).get("docxId")
            verdict = verdicts.get(ref) if block.get("type") in ("heading", "paragraph") else None
            if verdict is None:
                continue
            label, score = verdict["classification"], verdict["confidence"]
            position = "first" if index == 0 else "last" if index == len(blocks) - 1 else "middle"
            decided = block_decision(label, block, position)
            if decided is None:
                continue
            kind, params = decided
            text = _opening({"content": [block]})   # the whole block, not its first run
            why = {"split_chapter": f"reads “{text}” as a chapter title: a new chapter starts there",
                   "promote_heading": f"reads the {block_label(block)} “{text}” as {label}: one level up",
                   "adjust_heading_level": f"reads the {block_label(block)} “{text}” as {label}: one level down",
                   "reclassify": f"reads the {block_label(block)} “{text}” as {label}"}.get(
                kind, f"reads the {block_label(block)} “{text}” as {label}")
            proposals.append({
                "id": _proposal_id(ref, _change(kind, params)),
                "type": kind,
                "sourceRef": {"docxId": ref},
                **params,
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
            decided = section_decision(label, section["type"], root, index == boundary)
            if decided is None:
                continue
            kind, params = decided
            opening = _opening(section)
            if kind in ("start_body", "end_body"):
                if front:
                    edge, others = "starts", f"{len(sections) - index - 1} front-matter section(s) after it"
                else:
                    edge, others = "ends", f"{index} back-matter section(s) before it"
                why = (f"reads the {section['type']} section opening “{opening}” as a chapter "
                       f"title: the body {edge} here, so it and the {others} become chapters")
            elif kind == "reclassify":
                why = f"reads the {section['type']} section opening “{opening}” as {label}: it is a {params['to']}"
            else:
                why = f"reads the {section['type']} section opening “{opening}” as {label}, not {own}"
            proposals.append({
                "id": _proposal_id(ref, _change(kind, params)),
                "type": kind,
                "sourceRef": {"docxId": ref},
                **params,
                "rationale": f"The model ({model}) {why} (confidence {score:.2f}).",
                "confidence": score,
                "evidence": [f"label: {label}", f"model: {model}"],
            })
        # A section's own blocks: a chapter ingest folded into it (F1) comes back
        # as a chapter-title verdict on a paragraph. No op lifts a block out of
        # a section yet, so it is flagged -- a correct verdict is never dropped.
        for section in sections:
            for block in section.get("content") or []:
                ref = (block.get("sourceRef") or {}).get("docxId")
                verdict = verdicts.get(ref) if block.get("type") in ("heading", "paragraph") else None
                if verdict is None or block_decision(verdict["classification"], block, "middle") is None:
                    continue
                label, score = verdict["classification"], verdict["confidence"]
                text = _opening({"content": [block]})
                where = f"inside the {section['type']} section"
                why = (f"reads “{text}” {where} as a chapter title: a chapter may have been folded into it"
                       if label in _AGREES else f"reads the {block_label(block)} “{text}” {where} as {label}")
                proposals.append({
                    "id": _proposal_id(ref, "flag_ambiguity"),
                    "type": "flag_ambiguity",
                    "sourceRef": {"docxId": ref},
                    "rationale": f"The model ({model}) {why} (confidence {score:.2f}).",
                    "confidence": score,
                    "evidence": [f"label: {label}", f"model: {model}"],
                })
    return proposals


_WRAPPERS = {"blockquote", "epigraph", "dialogue", "sidebar"}   # override_ops._reshape wraps these


def block_decision(label: str, block: dict, position: str):
    """The block decision table (B8): what the model reading a chapter's
    heading or paragraph as `label` proposes -- `(proposal type, its
    parameters)`, or None. `position` is "first", "middle" or "last" in the
    chapter: `split` can open a chapter at neither end."""
    kind, current = block.get("type"), block_label(block)
    # Agreement is with what was SENT: a level-5 heading went as heading-3. A
    # paragraph's prose roles are still a paragraph.
    if label == current or (kind == "paragraph" and label in _AS_PROSE):
        return None
    if label in _AGREES:
        return ("split_chapter", {}) if position == "middle" else ("flag_ambiguity", {})
    if label in _AS_HEADING:
        level = int(label[-1])
        if kind == "paragraph":
            return "reclassify", {"from": "paragraph", "to": "heading", "value": level}
        # One level at a time; the next build (B0) proposes the next step.
        own = (block.get("attrs") or {}).get("level") or 1
        return ("promote_heading", {}) if level < own else ("adjust_heading_level", {})
    if kind == "heading" and label in _AS_PROSE:
        return "reclassify", {"from": "heading", "to": "paragraph"}
    if kind == "paragraph" and label in _WRAPPERS:
        return "reclassify", {"from": "paragraph", "to": label}
    # verse (no _reshape case yet), breaks (the op would drop the words),
    # front/back-matter labels (no op fits a body block), uncertain, the rest.
    return "flag_ambiguity", {}


# The section type each section label names (the inverse of SECTION_LABELS).
_LABELLED_TYPE = {label: kind for kind, label in SECTION_LABELS.items()}


def section_decision(label: str, kind: str, root: str, at_boundary: bool):
    """The section decision table (B5): what the model reading a `kind` section
    in `root` as `label` proposes -- `(proposal type, its parameters)`, or None.
    `at_boundary`: it is the earliest front / last back section read as a chapter."""
    if label == SECTION_LABELS.get(kind):
        return None                                          # agrees
    if label in _AGREES:
        if not at_boundary:
            return None                                      # moves with the boundary
        return ("start_body" if root == "frontMatter" else "end_body"), {}
    target = _LABELLED_TYPE.get(label)
    if target in section_types(root):
        return "reclassify", {"from": kind, "to": target}
    return "flag_ambiguity", {}                              # another end's type, or not a section


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
    version=9,  # v9: a disagreeing verdict on a block inside a front/back section is flagged (F1)
                # v8: a chapter's doubted headings and paragraphs get proposals --
                # split_chapter, promote_heading (new), demote, reclassify (B8)
                # v7: a section read as another type its list holds is a reclassify proposal (B5)
                # v6: PROPOSAL_PARAMS, and reclassify is acceptable (B2)
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
