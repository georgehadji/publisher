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
from publisher_prepress.ghostscript import GhostscriptError, find_binary, to_pdfx
from profiles import load_profile


def _deterministic_timestamp(ctx: StageCtx) -> str:
    """
    A timestamp derived from the build's cache key rather than the wall clock.

    ARCHITECTURE.md §2.5 requires nondeterministic bytes to be "set to fixed values
    derived from the cache key" so artifacts stay byte-identical across rebuilds.
    """
    import hashlib
    from datetime import datetime, timezone
    seed = int(hashlib.sha256(ctx.cache_key.encode("utf-8")).hexdigest()[:8], 16)
    return datetime.fromtimestamp(seed, tz=timezone.utc).isoformat()


@stage(
    name="preflight",
    version=5,   # v4 made the probe real; v5 fixes its page count, which read 1
                 # for every Ghostscript-produced file (see preflight._PAGE_RE).
    # Input dict KEYS are bound to the stage function's parameter names by the
    # executor (`decl.fn(ctx, **stage_inputs)`), not just documentation. The
    # previous keys ("pdf", "profile") didn't match this function's actual
    # parameters (`pdf_path`, `profile_name`) -- a TypeError waiting to happen the
    # first time this stage actually ran with resolved inputs. It never had, because
    # nothing imported stages.prepress_stages before F2.3 (see tracer_bullet.py).
    inputs={"pdf_path": "pdfx/1", "profile_name": "profile/1"},
    root_inputs=["profile_name"],   # vendor profile is loaded from profiles/, not produced
    outputs={"report": "preflight/1"},
    toolchain=["pdfprobe"],
    fixtures="fixtures/preflight/v1",
    # No longer terminal: `package` now declares `preflight_report: "preflight/1"` as
    # a required (non-root) input, so a build cannot be packaged without a preflight
    # verdict having actually run (BUILD_PLAN.md F2.3). Marking this terminal would
    # have suppressed the orphan-output check that should otherwise have caught
    # `package` NOT consuming it, back when that was the case.
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
    report = run_preflight(pdf_path, profile, created_at=_deterministic_timestamp(ctx))
    report_dict = report.to_dict()
    
    cas_root = Path(ctx.cas_root)
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))
    report_bytes = json.dumps(report_dict, indent=2).encode("utf-8")
    ref = cas.put(report_bytes, media_type=MediaType("application/json"))
    
    print(f"  [preflight] Status: {report.status} ({report.summary['passed']} passed, "
          f"{report.summary['failed']} failed, {report.summary['warnings']} warnings)")
    
    # Warn-severity findings must reach StageResult.warnings, not just stdout — the
    # stage contract (§2.8) carries them as Diagnostics, and §3.15 requires every
    # user-facing finding to carry a sourceRef. This list was initialised and returned
    # but never appended to, so warnings vanished.
    warnings = [
        Diagnostic(code=c.code, severity="warning", human_message=c.humanMessage,
                   suggested_fix=c.suggestedFix, source_ref=c.sourceRef)
        for c in report.checks if c.status == "warn"
    ]
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
            kind="report",   # must exactly equal the declared output key "report"
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
    version=2,   # v1 declared a `cover_art: image/*` root input this function never
                 # read. Bumped to v2 dropping it: art generation is now the separate
                 # page-count-FREE cover-brief/cover-art/cover-judge fan-out in
                 # stages/cover_stages.py — see COVER_DESIGN.md §0/§1. This stage
                 # computes geometry only; cover-compose (also cover_stages.py) is what
                 # joins geometry with the human-selected art.
    # "profile" -> "profile_name" to match this function's actual parameter name;
    # see the note on the preflight stage above about why this key must be exact.
    inputs={"page_count": "integer", "profile_name": "profile/1"},
    # Both are caller-supplied: page_count comes from the paginated interior, profile
    # from profiles/. Neither is the declared output of any registered stage.
    root_inputs=["page_count", "profile_name"],
    outputs={"cover-geometry": "cover-geometry/1"},
    toolchain=["ghostscript"],
    fixtures=None,
    memory_budget_mb=256,
    queue="q.prepress",
    description="Compute cover geometry (spine/trim/bleed) from final interior page count",
)
def cover_stage(ctx: StageCtx, page_count: int = 0, profile_name: str = "") -> StageResult:
    """
    Generate cover geometry from the interior's final page count.

    Deliberately does NOT take cover art as an input. Art is generated in parallel with
    the whole interior build (COVER_DESIGN.md §0); only geometry is bound to the final
    page count. cover-compose joins the two once both are ready.
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
    
    cas_root = Path(ctx.cas_root)
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
    name="cover-preflight",
    version=1,
    inputs={"pdf_path": "cover-raw-pdf/1", "profile_name": "profile/1"},
    root_inputs=["profile_name"],   # vendor profile is loaded from profiles/, not produced
    # Distinct output kind from `preflight`'s -- both stages emit content that
    # internally declares "schema": "preflight/1" (same report shape, same
    # schemas/preflight/preflight.schema.json), but the STAGE REGISTRY's DAG wiring
    # needs a distinct lineage tag so a downstream consumer (or the integrity
    # checker) can't ambiguously match the interior's report and the cover's report
    # to the same producer.
    outputs={"report": "cover-preflight/1"},
    toolchain=["pdfprobe"],
    fixtures=None,
    terminal=True,   # report is consumed by the review UI, not another stage
    memory_budget_mb=128,
    queue="q.prepress",
    description=(
        "Vendor preflight gate for the composed cover PDF (bleed, spine-safe, TAC, "
        "barcode quiet zone). Reuses the interior's preflight rule engine — "
        "COVER_DESIGN.md §1/§8."
    ),
)
def cover_preflight_stage(ctx: StageCtx, pdf_path: str = "", profile_name: str = "") -> StageResult:
    """Run the same preflight rule engine used for the interior against the composed
    cover PDF from cover-compose. A separate stage name (not a reuse of `preflight`)
    keeps the DAG-integrity checker's producer/consumer matching unambiguous — the
    interior and the cover are two distinct pdfx/1 lineages."""
    if not pdf_path:
        raise StageError(kind=ErrorKind.BAD_INPUT, message="pdf_path is required")

    profile = load_profile(profile_name) if profile_name else _default_profile()
    report = run_preflight(pdf_path, profile, created_at=_deterministic_timestamp(ctx))
    report_dict = report.to_dict()

    cas_root = Path(ctx.cas_root)
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))
    report_bytes = json.dumps(report_dict, indent=2).encode("utf-8")
    ref = cas.put(report_bytes, media_type=MediaType("application/json"))

    if report.status == "fail":
        diagnostics = [
            Diagnostic(code=c.code, severity="error", human_message=c.humanMessage,
                       suggested_fix=c.suggestedFix)
            for c in report.checks if c.status == "fail"
        ]
        raise StageError(
            kind=ErrorKind.POLICY_VIOLATION,
            message=f"Cover preflight failed: {report.summary['failed']} check(s) failed",
            diagnostics=diagnostics,
        )

    return StageResult(
        artifacts=[StageArtifactRef(
            kind="report",   # must exactly equal the declared output key "report"
            hash=str(ref.hash),
            media_type="application/json", size=len(report_bytes),
        )],
        metrics={
            "preflight_passed": 1.0 if report.status == "pass" else 0.0,
            "checks_passed": report.summary["passed"],
            "checks_failed": report.summary["failed"],
        },
    )


@stage(
    name="finish-gs",
    version=6,   # see the note on `finish` v6 -- same TrimBox/bleed change
    implements="finish",   # alternative impl of one step; see StageDeclaration.implements
    # "pdf" -> "pdf_path", "profile" -> "profile_name": see the note on preflight
    # above for why the input dict's KEYS must exactly match this function's
    # actual parameter names.
    inputs={"pdf_path": "raw-pdf/1", "profile_name": "profile/1"},
    root_inputs=["profile_name"],   # vendor profile is loaded from profiles/, not produced
    outputs={"pdf": "pdfx/1", "report": "finish-report/1"},
    toolchain=["ghostscript", "icc"],
    fixtures="fixtures/finish-gs/v1",
    memory_budget_mb=512,
    queue="q.prepress",
    description="Apply CMYK conversion, bleed, marks, OutputIntent via Ghostscript",
)
def finish_gs(ctx: StageCtx, pdf_path: str = "", profile_name: str = "") -> StageResult:
    """
    Finish stage — Ghostscript PDF/X production path.

    The profile-aware variant of `finish`: same real Ghostscript conversion
    (shared via publisher_prepress.ghostscript, so the two cannot drift), but
    driven by a vendor profile's declared pdfSpec rather than defaults.

    This used to report `"status": "prepared"` and return only a `finish-report`
    artifact -- never the `pdf` artifact its own `outputs={}` declared, and never
    actually running Ghostscript even when `gs_path` was detected. A caller reading
    "prepared" could reasonably assume conversion happened. It now actually
    converts; the stub path survives only for a Ghostscript-less dev machine that
    explicitly opts in, and says "stub", never "prepared"/"passed"
    (BUILD_PLAN.md D8, F2.4).
    """
    if not pdf_path:
        raise StageError(kind=ErrorKind.BAD_INPUT, message="pdf_path is required")

    profile = load_profile(profile_name) if profile_name else _default_profile()

    geometry = {
        "trimSize": profile.get("trimSize"),
        "bleed": profile.get("bleed"),
        "pdfSpec": profile.get("pdfSpec"),
    }

    gs_binary = find_binary()
    if gs_binary is None and not ctx.allow_stub_engines:
        raise StageError(
            kind=ErrorKind.INFRA,
            message="Ghostscript is not installed and allow_stub_engines is not set. "
                    "A build cannot silently certify an unconverted PDF as press-ready.",
        )

    pdf_path_p = Path(pdf_path)
    if not pdf_path_p.exists():
        raise StageError(kind=ErrorKind.BAD_INPUT, message=f"PDF input not found: {pdf_path}")

    cas_root = Path(ctx.cas_root)
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))
    work = Path(ctx.work_dir) / "finish-gs"
    work.mkdir(parents=True, exist_ok=True)

    if gs_binary is not None:
        press_path = work / "press.pdf"
        try:
            to_pdfx(
                pdf_path_p, press_path, work,
                title=ctx.build_id,
                output_condition=str(profile.get("name", "Commercial printing")),
                # Same figure design-compile grew the page box by; see finish v6.
                bleed_pt=float((profile.get("bleed") or {}).get("all", 0.0)) * 72.0 / 25.4,
                gs_binary=gs_binary,
            )
        except GhostscriptError as e:
            raise StageError(
                kind=ErrorKind.ENGINE_BUG,
                message=f"Ghostscript PDF/X conversion failed: {e}",
            )
        data = press_path.read_bytes()
        status, stub = "passed", 0.0
        print(f"  [finish-gs] PDF/X-1a via ghostscript ({len(data)}B) | "
              f"Profile: {profile.get('name', 'unknown')}")
    else:
        data = pdf_path_p.read_bytes()
        status, stub = "stub", 1.0
        print(f"  [finish-gs] STUB MODE (allow_stub_engines=True) -- ghostscript not "
              f"installed | Profile: {profile.get('name', 'unknown')}")

    pdf_ref = cas.put(data, media_type=MediaType("application/pdf"))

    result = {
        "schema": "finish-report/1",
        "status": status,
        "geometry": geometry,
        "gs_available": gs_binary is not None,
        # Command shape only — never the absolute path. `find_binary` returns a
        # machine-specific location, and this dict is written to the CAS, so embedding
        # it made the artifact hash depend on the host's Ghostscript install prefix
        # (ARCHITECTURE.md §2.11 pins the toolchain; it does not discover it).
        "gs_command": "gs -dPDFX -sOutputFile=output.pdf input.pdf" if gs_binary else "not available",
    }
    result_bytes = json.dumps(result, indent=2).encode("utf-8")
    report_ref = cas.put(result_bytes, media_type=MediaType("application/json"))

    return StageResult(
        artifacts=[
            StageArtifactRef(
                kind="pdf",
                hash=str(pdf_ref.hash),
                media_type="application/pdf",
                size=len(data),
            ),
            StageArtifactRef(
                kind="report",
                hash=str(report_ref.hash),
                media_type="application/json",
                size=len(result_bytes),
            ),
        ],
        metrics={"gs_available": 1.0 if gs_binary else 0.0, "stub_engine": stub},
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
