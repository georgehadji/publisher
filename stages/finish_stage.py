"""
Finish stage.

In production, applies CMYK conversion, bleed, marks, PDF/X and OutputIntent via
Ghostscript, and emits two artifacts (BUILD_PLAN.md §3.10, O4): a full-fidelity
PRESS PDF for vendor upload, and a smaller, linearized PROOF PDF for the human
reviewer. Never one compromise file for both audiences.

WHY THIS NOW REQUIRES `allow_stub_engines`
The previous version detected whether `gs`/`gswin64c` was on PATH, but never
actually invoked Ghostscript in either branch -- it reported
`"profile_applied": "pdfx-1a"` when GS was merely *present*, and
`"status": "passed"` unconditionally either way. A build could report success
having never been converted to PDF/X at all (BUILD_PLAN.md D8: no silent fallback
that downgrades quality without telling the user). Until real Ghostscript
invocation is wired (a P1-scale feature needing the PDFX_def.ps template and a
per-vendor ICC profile, not a remediation-scope fix), this stage now refuses to
run at all unless the caller explicitly opts into stub mode -- the same
`allow_stub_engines` gate as `paginate`.
"""

from __future__ import annotations
import json
from pathlib import Path

from publisher_stages import stage, StageCtx, StageResult, StageError, ErrorKind, ArtifactRef as StageArtifactRef
from publisher_cas import ContentAddressedStore, CasConfig, MediaType


@stage(
    name="finish",
    version=3,
    implements="finish",   # alternative impl of one step; see StageDeclaration.implements
    inputs={"pdf_path": "raw-pdf/1"},
    outputs={"pdf": "pdfx/1", "proof": "proof-pdf/1", "report": "finish-report/1"},
    toolchain=["ghostscript"],
    fixtures="fixtures/finish/v1",
    memory_budget_mb=256,
    queue="q.prepress",
    description="Apply CMYK, bleed, marks, OutputIntent; emit press + proof PDFs",
)
def finish(ctx: StageCtx, pdf_path: str | None = None) -> StageResult:
    """
    Finish stage -- prepare press and proof PDFs for delivery.

    Real Ghostscript invocation is not wired yet (see module docstring). This
    stage either refuses to run (default) or, with `ctx.allow_stub_engines`,
    passes the input through unconverted -- honestly labeled as a stub, never
    reported as "passed".
    """
    if pdf_path is None:
        raise StageError(kind=ErrorKind.BAD_INPUT, message="finish requires 'pdf_path' (from paginate)")

    pdf_path_p = Path(pdf_path)
    if not pdf_path_p.exists():
        raise StageError(kind=ErrorKind.BAD_INPUT, message=f"PDF input not found: {pdf_path}")

    if not ctx.allow_stub_engines:
        raise StageError(
            kind=ErrorKind.INFRA,
            message="Ghostscript PDF/X conversion is not wired yet, and "
                    "allow_stub_engines is not set. A build cannot silently certify "
                    "an unconverted PDF as press-ready.",
        )

    data = pdf_path_p.read_bytes()

    cas_root = Path(ctx.work_dir) / ".cas"
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))

    # Stub mode: both artifacts are currently the same unconverted bytes. Press and
    # proof are DISTINCT CAS artifacts (distinct hashes, distinct schema IDs) even
    # though their content is identical right now, so nothing downstream has to
    # change shape when real Ghostscript downsampling/linearization/watermarking
    # lands -- only this stage's body does.
    press_ref = cas.put(data, media_type=MediaType("application/pdf"))
    proof_ref = cas.put(data, media_type=MediaType("application/pdf"))

    report = {
        "schema": "finish-report/1",
        "status": "stub",   # never "passed" for a stub path -- see module docstring
        "profileApplied": "none (stub mode -- ghostscript not invoked)",
        "pressHash": str(press_ref.hash),
        "proofHash": str(proof_ref.hash),
        "outputSizeBytes": len(data),
    }
    report_bytes = json.dumps(report, indent=2).encode("utf-8")
    report_ref = cas.put(report_bytes, media_type=MediaType("application/json"))

    print(f"  [finish] STUB MODE (allow_stub_engines=True) -- press={press_ref.hash}, "
          f"proof={proof_ref.hash}, no ghostscript invocation")

    return StageResult(
        artifacts=[
            StageArtifactRef(
                kind="pdf",
                hash=str(press_ref.hash),
                media_type="application/pdf",
                size=len(data),
            ),
            StageArtifactRef(
                kind="proof",
                hash=str(proof_ref.hash),
                media_type="application/pdf",
                size=len(data),
            ),
            StageArtifactRef(
                kind="report",
                hash=str(report_ref.hash),
                media_type="application/json",
                size=len(report_bytes),
            ),
        ],
        metrics={"output_size": len(data), "stub_engine": 1.0},
    )
