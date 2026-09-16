"""
Manuscript advisory stage -- pre-ingest, advisory-only (E7.2,
docs/ARCHITECTURE_SCORE_10_PLAN.md).

Wires `services/alttext`'s `ManuscriptDoctor` (L9: fully built, zero callers)
onto an executing path, exposed through the SAME generic artifact-download
route every other deliverable uses (`GET /v1/builds/:id/artifacts/advisory-
report`) rather than a bespoke endpoint -- "surfaced through the API"
without inventing new API surface.

Runs in PARALLEL with `ingest`, off the SAME uploaded bytes (`raw-docx/1`)
-- never feeds `ingest`, `ast-assemble`, or any downstream stage. That is
the actual mechanism behind "advisory only": there is no edge from this
stage's output to anything that produces a deliverable, so nothing it says
can change what a build produces (services/alttext's own docstring: "it
cannot change a build"). Same parallel-branch shape as `structure-infer`
(E6.2), which runs alongside `ast-assemble` off `typescript-html/1` for the
identical reason.
"""

from __future__ import annotations
import json
from pathlib import Path

from publisher_stages import (
    stage, StageCtx, StageResult, StageError, ErrorKind,
    ArtifactRef as StageArtifactRef,
)
from publisher_cas import ContentAddressedStore, CasConfig, MediaType
from publisher_alttext.doctor import ManuscriptDoctor
from stages.ingest_stage import _is_legacy_doc


@stage(
    name="manuscript-advisory",
    version=1,
    inputs={"docx_path": "raw-docx/1"},
    root_inputs=["docx_path"],
    outputs={"report": "advisory-report/1"},
    terminal=True,
    toolchain=[],
    fixtures=None,
    memory_budget_mb=64,
    queue="q.ingest",
    description="Pre-ingest advisory analysis of the uploaded manuscript -- "
                "format, size, and structural warnings. Read-only; cannot "
                "change a build.",
)
def manuscript_advisory(ctx: StageCtx, docx_path: str | None = None) -> StageResult:
    if not docx_path:
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message="manuscript-advisory requires 'docx_path' (the CAS path of "
                    "the uploaded manuscript bytes)",
        )
    source = Path(docx_path)
    if not source.is_file():
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"Uploaded manuscript bytes not found at {docx_path}",
        )

    # CAS blobs are content-addressed and extension-free; ManuscriptDoctor's
    # format check needs a real suffix to mean anything, so give it one via
    # the same magic-byte sniff `ingest` uses to pick the LibreOffice path --
    # never trust a client-claimed extension for that decision.
    work = Path(ctx.work_dir) / "advisory"
    work.mkdir(parents=True, exist_ok=True)
    named = work / ("manuscript.doc" if _is_legacy_doc(source) else "manuscript.docx")
    named.write_bytes(source.read_bytes())

    report = ManuscriptDoctor().analyze(named)

    data = json.dumps(report, indent=2).encode("utf-8")
    cas = ContentAddressedStore(CasConfig(local_cache_root=Path(ctx.cas_root)))
    ref = cas.put(data, media_type=MediaType(MediaType.APPLICATION_JSON))

    findings = report.get("findings") or []
    print(f"  [manuscript-advisory] status={report.get('status')}, "
          f"{len(findings)} finding(s) -> {ref.hash}")

    return StageResult(
        artifacts=[StageArtifactRef(
            kind="report",   # must exactly equal the declared output key "report"
            hash=str(ref.hash),
            media_type=str(MediaType.APPLICATION_JSON),
            size=len(data),
        )],
        metrics={"finding_count": float(len(findings))},
    )
