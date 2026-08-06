"""
Finish stage.

Applies CMYK conversion and PDF/X-1a OutputIntent via Ghostscript, and emits two
artifacts (BUILD_PLAN.md §3.10, O4): a full-fidelity PRESS PDF for vendor upload,
and a smaller, linearized PROOF PDF for the human reviewer. Never one compromise
file for both audiences.

REAL GHOSTSCRIPT IS NOW WIRED
An earlier version detected whether `gs` was on PATH but never invoked it -- it
reported `"profile_applied": "pdfx-1a"` when GS was merely *present*, and
`"status": "passed"` unconditionally either way. That was corrected to refuse
outright without `allow_stub_engines`, which was honest but meant no real build
could ever finish (the worker runs with allow_stub_engines=False by design).
Conversion now actually happens, in publisher_prepress.ghostscript, shared with
`finish-gs` so there is one implementation rather than two that can drift.

The stub path survives for exactly one case: a local dev harness on a machine
with no Ghostscript installed, which must explicitly opt in. It reports
`"status": "stub"` and never "passed" (BUILD_PLAN.md D8: no silent fallback that
downgrades quality without telling the user).
"""

from __future__ import annotations
import json
from pathlib import Path

from publisher_stages import stage, StageCtx, StageResult, StageError, ErrorKind, ArtifactRef as StageArtifactRef
from publisher_cas import ContentAddressedStore, CasConfig, MediaType
from publisher_prepress.ghostscript import (
    GhostscriptError, find_binary, to_pdfx, to_proof,
)


@stage(
    name="finish",
    # v5: to_pdfx now verifies its own output (gs's "reverting to normal PDF
    # output" notice, and the presence of an OutputIntent in the bytes). v4
    # could return a non-PDF/X file reported as pdfx-1a, so every v4 press
    # artifact in the cache is suspect and must not be replayed.
    version=5,
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
    """Finish stage -- prepare press and proof PDFs for delivery."""
    if pdf_path is None:
        raise StageError(kind=ErrorKind.BAD_INPUT, message="finish requires 'pdf_path' (from paginate)")

    pdf_path_p = Path(pdf_path)
    if not pdf_path_p.exists():
        raise StageError(kind=ErrorKind.BAD_INPUT, message=f"PDF input not found: {pdf_path}")

    gs_binary = find_binary()
    if gs_binary is None and not ctx.allow_stub_engines:
        raise StageError(
            kind=ErrorKind.INFRA,
            message="Ghostscript is not installed and allow_stub_engines is not set. "
                    "A build cannot silently certify an unconverted PDF as press-ready.",
        )

    cas_root = Path(ctx.cas_root)
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))
    work = Path(ctx.work_dir) / "finish"
    work.mkdir(parents=True, exist_ok=True)

    if gs_binary is not None:
        press_path = work / "press.pdf"
        proof_path = work / "proof.pdf"
        try:
            to_pdfx(pdf_path_p, press_path, work, title=ctx.build_id, gs_binary=gs_binary)
            to_proof(pdf_path_p, proof_path, gs_binary=gs_binary)
        except GhostscriptError as e:
            # A failed conversion is an engine failure, not a reason to fall back
            # to passing the input through -- that is precisely the silent
            # quality downgrade D8 forbids.
            raise StageError(
                kind=ErrorKind.ENGINE_BUG,
                message=f"Ghostscript PDF/X conversion failed: {e}",
            )

        press_bytes = press_path.read_bytes()
        proof_bytes = proof_path.read_bytes()
        press_ref = cas.put(press_bytes, media_type=MediaType("application/pdf"))
        proof_ref = cas.put(proof_bytes, media_type=MediaType("application/pdf"))
        status, profile_applied, stub = "passed", "pdfx-1a", 0.0
        print(f"  [finish] PDF/X-1a via ghostscript -- press={len(press_bytes)}B, "
              f"proof={len(proof_bytes)}B")
    else:
        # Stub mode: both artifacts are the same unconverted bytes. Press and
        # proof stay DISTINCT CAS artifacts (distinct schema IDs) so nothing
        # downstream changes shape between the stub and real paths.
        press_bytes = proof_bytes = pdf_path_p.read_bytes()
        press_ref = cas.put(press_bytes, media_type=MediaType("application/pdf"))
        proof_ref = cas.put(proof_bytes, media_type=MediaType("application/pdf"))
        status, profile_applied, stub = "stub", "none (stub mode -- ghostscript not installed)", 1.0
        print("  [finish] STUB MODE (allow_stub_engines=True) -- no ghostscript installed, "
              "PDFs passed through unconverted")

    report = {
        "schema": "finish-report/1",
        "status": status,
        "profileApplied": profile_applied,
        "pressHash": str(press_ref.hash),
        "proofHash": str(proof_ref.hash),
        "outputSizeBytes": len(press_bytes),
    }
    report_bytes = json.dumps(report, indent=2).encode("utf-8")
    report_ref = cas.put(report_bytes, media_type=MediaType("application/json"))

    return StageResult(
        artifacts=[
            StageArtifactRef(
                kind="pdf",
                hash=str(press_ref.hash),
                media_type="application/pdf",
                size=len(press_bytes),
            ),
            StageArtifactRef(
                kind="proof",
                hash=str(proof_ref.hash),
                media_type="application/pdf",
                size=len(proof_bytes),
            ),
            StageArtifactRef(
                kind="report",
                hash=str(report_ref.hash),
                media_type="application/json",
                size=len(report_bytes),
            ),
        ],
        metrics={
            "output_size": len(press_bytes),
            "proof_size": len(proof_bytes),
            "stub_engine": stub,
        },
    )
