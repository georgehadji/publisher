"""
Layout review -- `layout-review` (W11, docs/WIRING_PLAN.md): the Compositor's caller.

Preflight warns, without failing, on widows, runts, hyphen stacks and rivers,
and names the pages. A warning is a scan's opinion; whether the page really
reads badly is a look. This stage hands the Compositor the build's own press
PDF and the warned pages, and records its verdict on each -- confirmed,
not-a-defect, needs-human -- as `layout-review/1`.

The pages come from the same functions preflight runs (`scan_composition` over
the pagemap, `measure_spacing` over the press PDF), not from preflight's
messages, which name at most six pages. Orphans are absent on purpose: an
orphan fails preflight, and a failed preflight ends the build before this.

At most MAX_FINDINGS pages are looked at a build (widows and runts first, the
order a reader notices them); the rest are recorded `unreviewed`, as is every
finding when the run fails. Each look is a whole-page image, so the cap is
what keeps a run inside the Compositor's budget.

Each confirmed widow or runt whose paragraph the pagemap names (K1) carries a
`fit_paragraph` proposal at the next step of a fixed rule (NEXT_FIT, K3) -- the
model only verifies; it never picks a value. A proposal reaches the book only
when a reviewer accepts it into the override log.

Advisory and terminal (D9): nothing consumes `layout-review/1`, so it cannot
change the book, the preflight verdict or the package. Reachable only with the
`api_key` root input -- a required root input nothing produces, supplied by the
worker when OPENROUTER_API_KEY is set (as for `structure-infer`). A build
without a key is unchanged.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from publisher_stages import (
    stage, StageCtx, StageResult, StageError, ErrorKind, Diagnostic, ArtifactRef as StageArtifactRef,
)
from publisher_cas import ContentAddressedStore, CasConfig, MediaType

ROUTE = "layout-review"
MAX_FINDINGS = 8   # ponytail: whole pages, severity order; crop a bbox when pagemap carries line boxes
_WARNED = {"composition": ("widow", "runt"), "hyphen-stacks": ("hyphen-stack",), "rivers": ("river",)}


def findings_for(report: dict, pagemap: dict, press_pdf: str) -> list[dict]:
    """{"id", "kind", "page"} for each page-level defect preflight warned on,
    widows and runts first; a check that did not warn is not looked at."""
    from publisher_prepress.preflight import scan_composition
    from publisher_prepress.spacing import measure_spacing

    warned = {c.get("code") for c in report.get("checks") or [] if c.get("status") == "warn"}
    pages: dict[str, list[int]] = {}
    if "composition" in warned:
        found = scan_composition(pagemap)
        pages["widow"], pages["runt"] = found["widows"], found["runts"]
    if warned & {"hyphen-stacks", "rivers"}:
        scan = measure_spacing(Path(press_pdf)).get("scan") or {}
        if "hyphen-stacks" in warned:
            pages["hyphen-stack"] = scan.get("hyphenStacks") or []
        if "rivers" in warned:
            pages["river"] = scan.get("rivers") or []
    order = [kind for kinds in _WARNED.values() for kind in kinds]
    return [{"id": f"{kind}-p{page}", "kind": kind, "page": page}
            for kind in order for page in sorted(set(pages.get(kind) or []))]


# K3: the fix for a confirmed widow or runt, picked by rule, never by the model.
# A paragraph as designed is tracked `tight`; one already `tight` goes `tighter`;
# past that (or one a human set loose) it is left to a human. Loosening is the
# follow-on if tightening proves the weaker fix on the real book.
NEXT_FIT = {None: "tight", "tight": "tighter"}
_REFS = {"widow": "widowRefs", "runt": "runtRefs"}


def paragraph_fits(doc: dict) -> dict[str, str | None]:
    """docxId -> the paragraph's current `fit` (None: as designed), across the document."""
    fits: dict[str, str | None] = {}

    def walk(value) -> None:
        if isinstance(value, dict):
            ref = (value.get("sourceRef") or {}).get("docxId") if value.get("type") == "paragraph" else None
            if ref:
                fits[ref] = (value.get("attrs") or {}).get("fit")
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(doc)
    return fits


def proposals_for(kind: str, page: int, pagemap: dict, fits: dict[str, str | None]) -> list[dict]:
    """A `fit_paragraph` proposal per paragraph the pagemap names behind this
    page's widow or runt (K1), at the next step NEXT_FIT allows. The id comes from
    the node and the change only, as structure-propose's do: the same advice is the
    same proposal on every rebuild, so an accepted one stays accepted."""
    entry = next((p for p in pagemap.get("pages") or [] if p.get("pageNumber") == page), {})
    out = []
    for ref in entry.get(_REFS.get(kind, ""), []):
        if ref not in fits or fits[ref] not in NEXT_FIT:
            continue
        value = NEXT_FIT[fits[ref]]
        digest = hashlib.sha256(f"{ref}|fit_paragraph|{value}".encode("utf-8")).hexdigest()[:24]
        out.append({"id": f"pr-{digest}", "type": "fit_paragraph", "docxId": ref, "value": value})
    return out


@stage(
    name="layout-review",
    version=2,   # v2: a confirmed widow or runt carries a fit_paragraph proposal (K3); reads doc-effective/1
    inputs={"report": "preflight/1", "pdf_path": "pdfx/1", "pagemap_path": "pagemap/1",
            "doc_path": "doc-effective/1", "api_key": "openrouter-credential/1"},
    root_inputs=["api_key"],   # required and unproduced: no key, not reachable
    outputs={"review": "layout-review/1"},
    terminal_outputs=["review"],
    toolchain=["ghostscript", "inference-gateway"],
    # The verdicts depend on the model and its prompt, which no input carries.
    cache_salt=lambda inputs: _salt(),
    fixtures=None,
    memory_budget_mb=256,
    timeout_s=300,
    queue="q.prepress",
    description="The Compositor looks at preflight's page-level warnings on the press PDF "
                "and records a closed verdict on each; advisory, changes nothing.",
)
def layout_review(ctx: StageCtx, report: str | None = None, pdf_path: str | None = None,
                  pagemap_path: str | None = None, doc_path: str | None = None,
                  api_key: str | None = None) -> StageResult:
    for name, value in (("report", report), ("pdf_path", pdf_path), ("pagemap_path", pagemap_path),
                        ("doc_path", doc_path)):
        if value is None or not Path(value).exists():
            raise StageError(kind=ErrorKind.BAD_INPUT, message=f"layout-review requires '{name}'")
    if not api_key:
        raise StageError(kind=ErrorKind.BAD_INPUT, message="layout-review requires 'api_key'")
    pagemap = json.loads(Path(pagemap_path).read_bytes())
    findings = findings_for(json.loads(Path(report).read_bytes()), pagemap, pdf_path)
    looked_at = findings[:MAX_FINDINGS]
    verdicts, result = _compositor(api_key).verify(looked_at, pdf_path) if looked_at else ({}, None)
    warnings: list[Diagnostic] = []
    if result is not None and result.failed:
        warnings.append(Diagnostic(
            code="layout-review-failed", severity="warning",
            human_message=f"The Compositor's review failed, so all {len(findings)} finding(s) "
                          f"are unreviewed: {result.call.error}"))
    elif len(findings) > MAX_FINDINGS:
        warnings.append(Diagnostic(
            code="layout-review-capped", severity="warning",
            human_message=f"{len(findings) - MAX_FINDINGS} of {len(findings)} finding(s) are "
                          f"unreviewed: a build looks at {MAX_FINDINGS} pages at most."))
    fits = paragraph_fits(json.loads(Path(doc_path).read_bytes()))

    def recorded(finding: dict) -> dict:
        verdict = verdicts.get(finding["id"], "unreviewed")
        out = {"kind": finding["kind"], "page": finding["page"], "verdict": verdict}
        proposals = proposals_for(finding["kind"], finding["page"], pagemap, fits) if verdict == "confirmed" else []
        return {**out, "proposals": proposals} if proposals else out

    document = {"schema": "layout-review/1", "agentId": "compositor", "findings": [recorded(f) for f in findings]}
    if result is not None:
        from publisher_agents.compositor import PROMPT_VERSION
        document.update(promptVersion=PROMPT_VERSION, modelId=_route().model_id,
                        costUsd=round(result.call.cost_usd, 6))
    data = json.dumps(document, indent=2, sort_keys=True).encode("utf-8")
    ref = ContentAddressedStore(CasConfig(local_cache_root=Path(ctx.cas_root))).put(
        data, media_type=MediaType("application/json"))
    print(f"  [layout-review] {len(verdicts)} of {len(findings)} finding(s) reviewed -> {ref.hash}")
    return StageResult(
        artifacts=[StageArtifactRef(kind="review", hash=str(ref.hash),
                                    media_type="application/json", size=len(data))],
        metrics={"findings": len(findings), "reviewed": len(verdicts),
                 "confirmed": sum(v == "confirmed" for v in verdicts.values()),
                 "proposals": sum(len(f.get("proposals", [])) for f in document["findings"])},
        warnings=warnings,
    )


def _salt() -> str:
    from publisher_agents.compositor import PROMPT_VERSION
    return f"{_route().model_id}|{PROMPT_VERSION}|{MAX_FINDINGS}"


def _route():
    from publisher_structure.inference import load_routes_from_policy
    return load_routes_from_policy(service="agents")[ROUTE]


def _compositor(api_key: str):
    """The production Compositor. A seam so tests drive it with a scripted provider."""
    from publisher_agents.compositor import Compositor
    from publisher_agents.openrouter import OpenRouterAgentProvider
    from publisher_agents.runtime import AgentRuntime

    return Compositor(AgentRuntime(OpenRouterAgentProvider(api_key, _route())))
