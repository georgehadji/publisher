"""
P1 — Preflight stage + Cover stage.

Registered stages for the print compliance pipeline.
"""

from __future__ import annotations
import json
from pathlib import Path

from publisher_stages import stage, StageCtx, StageResult, StageError, ErrorKind, Diagnostic, ArtifactRef as StageArtifactRef
from publisher_cas import ContentAddressedStore, CasConfig, MediaType

from publisher_prepress.preflight import run_preflight
from publisher_prepress.geometry import spine_width, cover_dimensions, TrimSize, BleedBox
from profiles import load_profile


@stage(
    name="preflight",
    version=1,
    inputs={"pdf": "pdfx/1", "profile": "profile/1"},
    outputs={"report": "preflight/1"},
    toolchain=["pdfprobe"],
    fixtures="fixtures/preflight/v1",
    memory_budget_mb=128,
    queue="q.prepress",
    description="Vendor preflight gate — blocks delivery on any error-level failure",
)
def preflight_stage(ctx: StageCtx, pdf_path: str = "", profile_name: str = "") -> StageResult:
    """
    Run preflight checks against a vendor profile.
    """
    if not pdf_path:
        raise StageError(kind=ErrorKind.BAD_INPUT, message="pdf_path is required")
    
    profile = load_profile(profile_name) if profile_name else _default_profile()
    report = run_preflight(pdf_path, profile)
    report_dict = report.to_dict()
    
    cas_root = Path(ctx.work_dir) / ".cas"
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))
    report_bytes = json.dumps(report_dict, indent=2).encode("utf-8")
    ref = cas.put(report_bytes, media_type=MediaType("application/json"))
    
    print(f"  [preflight] Status: {report.status} ({report.summary['passed']} passed, "
          f"{report.summary['failed']} failed, {report.summary['warnings']} warnings)")
    
    warnings = []
    for c in report.checks:
        if c.status == "fail":
            print(f"    FAIL {c.code}: {c.humanMessage}")
    
    if report.status == "fail":
        diagnostics = [
            Diagnostic(
                code=c.code,
                severity="error",
                human_message=c.humanMessage,
                suggested_fix=c.suggestedFix,
            )
            for c in report.checks if c.status == "fail"
        ]
        raise StageError(
            kind=ErrorKind.POLICY_VIOLATION,
            message=f"Preflight failed: {report.summary['failed']} check(s) failed",
            diagnostics=diagnostics,
        )
    
    return StageResult(
        artifacts=[StageArtifactRef(
            kind="preflight-report",
            hash=str(ref.hash),
            media_type="application/json",
            size=len(report_bytes),
        )],
        metrics={
            "preflight_passed": 1.0 if report.status == "pass" else 0.0,
            "checks_passed": report.summary["passed"],
            "checks_failed": report.summary["failed"],
            "checks_warned": report.summary.get("warnings", 0),
        },
        warnings=warnings,
    )


@stage(
    name="cover",
    version=1,
    inputs={"page_count": "integer", "profile": "profile/1", "cover_art": "image/*"},
    outputs={"cover-pdf": "pdfx/1"},
    toolchain=["ghostscript"],
    fixtures=None,
    memory_budget_mb=256,
    queue="q.prepress",
    description="Generate print-ready cover PDF from page count + cover art",
)
def cover_stage(ctx: StageCtx, page_count: int = 0, profile_name: str = "", 
                cover_art: str = "") -> StageResult:
    """
    Generate cover geometry and produce cover PDF.
    
    In production, this renders the cover art + spine + back cover as a PDF.
    In the tracer bullet, it computes and records the geometry.
    """
    if not page_count:
        raise StageError(kind=ErrorKind.BAD_INPUT, message="page_count is required")
    
    profile = load_profile(profile_name) if profile_name else _default_profile()
    ts = profile.get("trimSize", {})
    bl = profile.get("bleed", {})
    
    trim = TrimSize(width=ts.get("width", 152.4), height=ts.get("height", 228.6))
    bleed = BleedBox.uniform(bl.get("all", 3.0))
    spine = spine_width(page_count)
    cover_w, cover_h = cover_dimensions(trim, spine, bleed)
    
    cover_geom = {
        "schema": "cover-geometry/1",
        "pageCount": page_count,
        "trimWidthMm": trim.width,
        "trimHeightMm": trim.height,
        "spineWidthMm": spine,
        "coverWidthMm": round(cover_w, 2),
        "coverHeightMm": round(cover_h, 2),
    }
    
    print(f"  [cover] Spine: {spine} mm | Cover: {cover_w:.1f} x {cover_h:.1f} mm "
          f"(for {page_count} pages, trim {trim.width}x{trim.height} mm)")
    
    cas_root = Path(ctx.work_dir) / ".cas"
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))
    geom_bytes = json.dumps(cover_geom, indent=2).encode("utf-8")
    ref = cas.put(geom_bytes, media_type=MediaType("application/json"))
    
    return StageResult(
        artifacts=[StageArtifactRef(
            kind="cover-geometry",
            hash=str(ref.hash),
            media_type="application/json",
            size=len(geom_bytes),
        )],
        metrics={
            "spine_width_mm": spine,
            "cover_width_mm": round(cover_w, 2),
            "cover_height_mm": round(cover_h, 2),
        },
    )


@stage(
    name="finish-gs",
    version=1,
    inputs={"pdf": "raw-pdf/1", "profile": "profile/1"},
    outputs={"pdf": "pdfx/1", "report": "finish-report/1"},
    toolchain=["ghostscript", "icc"],
    fixtures="fixtures/finish/v1",
    memory_budget_mb=512,
    queue="q.prepress",
    description="Apply CMYK conversion, bleed, marks, OutputIntent via Ghostscript",
)
def finish_gs(ctx: StageCtx, pdf_path: str = "", profile_name: str = "") -> StageResult:
    """
    Finish stage — Ghostscript PDF/X production path.
    
    In production: gs -dPDFX -dNOPAUSE -dBATCH -sOutputFile=output.pdf input.pdf
    Tracer bullet: records intent and produces geometry metadata.
    """
    profile = load_profile(profile_name) if profile_name else _default_profile()
    
    geometry = {
        "trimSize": profile.get("trimSize"),
        "bleed": profile.get("bleed"),
        "pdfSpec": profile.get("pdfSpec"),
    }
    
    # Check if Ghostscript is available
    import shutil
    gs_path = shutil.which("gs") or shutil.which("gswin64c") or shutil.which("gswin32c")
    
    result = {
        "schema": "finish-report/1",
        "status": "prepared",
        "geometry": geometry,
        "gs_available": gs_path is not None,
        "gs_command": f"{gs_path} -dPDFX -sOutputFile=output.pdf input.pdf" if gs_path else "not available",
    }
    
    cas_root = Path(ctx.work_dir) / ".cas"
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))
    result_bytes = json.dumps(result, indent=2).encode("utf-8")
    ref = cas.put(result_bytes, media_type=MediaType("application/json"))
    
    print(f"  [finish-gs] GS={'available' if gs_path else 'NOT available'} | "
          f"Profile: {profile.get('name', 'unknown')}")
    
    return StageResult(
        artifacts=[
            StageArtifactRef(
                kind="finish-report",
                hash=str(ref.hash),
                media_type="application/json",
                size=len(result_bytes),
            ),
        ],
        metrics={"gs_available": 1.0 if gs_path else 0.0},
    )


def _default_profile() -> dict:
    p = load_profile("Generic 6x9")
    if p:
        return p
    return {
        "name": "Default",
        "trimSize": {"width": 152.4, "height": 228.6, "unit": "mm"},
        "bleed": {"all": 3.0},
        "pdfSpec": {"version": "1.7", "standard": "pdfx-1a", "colorSpace": "cmyk"},
        "proofSpec": {"dpi": 150, "sizeBudgetBytes": 100000000},
        "minPages": 1, "maxPages": 2000, "pageSizeMultiple": 1,
    }
