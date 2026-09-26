"""
Tracer Bullet -- design-compile stage.

Emits CSS from a DesignSpec. This is the "emit_css()" path from ARCHITECTURE.md §2.7.
In the tracer bullet, this produces a CSS stylesheet for Paged.js / Playwright.
"""

from __future__ import annotations
import json
from pathlib import Path

from publisher_stages import stage, StageCtx, StageResult, StageError, ErrorKind, ArtifactRef as StageArtifactRef
from publisher_cas import ContentAddressedStore, CasConfig, MediaType
from publisher_prepress.fontvault import FontLicenseViolation, validate_font_use
from profiles import load_profile

# Defaults for `_default_designspec()` below.
from templates import DEFAULT_LEADING_PT   # noqa: E402 -- the house 5.00mm baseline

# E1.3: emit_css moved to stages/rendering.py -- it is a public shared module
# now, not a private cross-sibling import (paginate_stage.py's CSS fallback
# calls this SAME function object; see rendering.py's module docstring for
# why that identity matters).
from stages.rendering import emit_css


def _fonts_in_spec(spec: dict) -> list[tuple[str, str]]:
    """(family, style) pairs referenced by a DesignSpec's typography block."""
    typ = spec.get("typography") or {}
    out = []
    for key in ("bodyFont", "displayFont", "monoFont"):
        fam = (typ.get(key) or {}).get("family")
        if fam:
            out.append((fam, (typ.get(key) or {}).get("style", "regular")))
    return out


@stage(
    name="design-compile",
    # v2: page geometry now comes from the vendor profile, and the emitted
    # @page box carries the profile's bleed. Every v1 stylesheet was laid out at
    # exactly trim, so none may be replayed for a profile that requires bleed.
    # v3: bleed is declared with the CSS `bleed` property instead of being added
    # to `size`. A v2 stylesheet renders a trim+2*bleed page whose TrimBox sits
    # on its MediaBox -- geometrically plausible, and rejected by Ghostscript.
    # v4: recto starts use `break-before`; v3's `page-break-before: recto` was
    # silently ignored, so no chapter or front-matter section started on a recto.
    # v5: the built-in spec is set in GFS Didot. EB Garamond was never installed
    # anywhere, so every v4 render was in a substitute face.
    version=9,   # v9: notes carry over in document order (data-seq, paginate_stage.notes_in_document_order); no footnote-policy: line (it stranded lines: 27 widows, 23 one-line pages).
                 # v8: .keep { white-space: nowrap } (keep span: a paragraph's last two words never split in print (no runts).) v7: footnote-policy keeps each note on its call's page (weasyprint 70).
                 # v6: long footnotes set in pieces, own note numbers (rendering.PrintNotes), footnote area capped.
    inputs={"designspec_path": "designspec/1", "profile_name": "profile/1"},
    outputs={"css": "text/css"},
    # `profile_name` is optional so that a build which omits it still renders --
    # at trim, with no bleed. That is not a silent downgrade: preflight measures
    # the bleed and fails the build against any profile that requires some.
    root_inputs=["designspec_path", "profile_name"],
    optional_root_inputs=["profile_name"],
    # Alternative impl of one step: `design-compile-typst` emits text/x-typst
    # from the same DesignSpec. stages/__init__.py selects one per process.
    implements="design-compile",
    toolchain=[],
    fixtures="fixtures/design-compile/v1",
    memory_budget_mb=64,
    queue="q.composition",
    description="Compile DesignSpec -> CSS for Paged.js rendering",
)
def design_compile(ctx: StageCtx, designspec_path: str | None = None,
                   profile_name: str | None = None) -> StageResult:
    """
    Emit CSS from a DesignSpec.
    Uses a built-in default DesignSpec for the tracer bullet.

    Only `preferredEngine: "chrome-pagedjs"` is implemented (this stage's only
    emitter is `stages.rendering.emit_css`). A DesignSpec naming an engine this stage cannot
    actually emit for -- most notably "typst", declared by every packaged
    template before the O1 renderer decision was run -- is `bad_input`, not a
    silently ignored field (BUILD_PLAN.md D8, F4.1). §3.8's cross-emitter
    agreement gate only stays meaningful if a declared engine is one that ran.
    """
    IMPLEMENTED_ENGINES = {"chrome-pagedjs"}

    spec = _default_designspec()

    if designspec_path:
        p = Path(designspec_path)
        if p.exists():
            spec = json.loads(p.read_bytes())

    engine = spec.get("preferredEngine")
    if engine not in IMPLEMENTED_ENGINES:
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"DesignSpec declares preferredEngine={engine!r}, but only "
                    f"{sorted(IMPLEMENTED_ENGINES)} {'is' if len(IMPLEMENTED_ENGINES) == 1 else 'are'} "
                    f"implemented. Run the O1 renderer decision (BUILD_PLAN.md §5.1 "
                    f"P0) before declaring an engine with no emitter.",
        )

    # §2.10: refuse to emit a spec naming a font that is not licensed for print.
    # Enforced in the domain layer, not the UI.
    for family, style in _fonts_in_spec(spec):
        try:
            validate_font_use(family, style, "PRINT_PDF")
        except FontLicenseViolation as exc:
            raise StageError(kind=ErrorKind.POLICY_VIOLATION, message=str(exc))

    # Page geometry is the vendor profile's to decide, not the DesignSpec's.
    # They used to be declared independently and never reconciled: the built-in
    # spec said 152x229mm while "Generic 6x9" says 152.4x228.6mm, so the renderer
    # laid out one page size and preflight measured it against another. That
    # passed only because the disagreement (0.4mm) happened to sit inside
    # check_trim_size's 0.5mm tolerance. The DesignSpec keeps typography; the
    # profile owns trim and bleed.
    bleed_mm = 0.0
    if profile_name:
        profile = load_profile(profile_name)
        if profile is None:
            raise StageError(
                kind=ErrorKind.BAD_INPUT,
                message=f"Unknown vendor profile: {profile_name!r}. "
                        f"Profiles are loaded from profiles/*/*.yaml by their "
                        f"`name:` field.",
            )
        trim = profile.get("trimSize") or {}
        if trim.get("width") and trim.get("height"):
            spec = {**spec, "trimSize": trim}
        bleed_mm = float((profile.get("bleed") or {}).get("all", 0.0))

    css = emit_css(spec, bleed_mm=bleed_mm)
    css_bytes = css.encode("utf-8")
    
    cas_root = Path(ctx.cas_root)
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))
    ref = cas.put(css_bytes, media_type=MediaType("text/css"))
    
    print(f"  [design-compile] Generated CSS -> {ref.hash} ({len(css)} bytes)")
    
    return StageResult(
        artifacts=[StageArtifactRef(
            kind="css",   # must exactly equal the declared output key "css"
            hash=str(ref.hash),
            media_type="text/css",
            size=len(css_bytes),
        )],
        metrics={"css_size_bytes": len(css_bytes), "rule_count": css.count(" {")},
    )


def _default_designspec() -> dict:
    """Return a default DesignSpec for the tracer bullet."""
    return {
        "schema": "designspec/1",
        "name": "Tracer Bullet -- Literary 6x9",
        # "chrome-pagedjs", not "typst" -- see design_compile()'s docstring
        # above (F4.1a). Only a CSS/Paged.js emitter exists; O1 (BUILD_PLAN.md
        # §5.1 P0, §5.3) is the actual measured decision that would promote Typst
        # to the default, and it has not been run yet.
        "preferredEngine": "chrome-pagedjs",
        "trimSize": {"width": 152, "height": 229, "unit": "mm"},
        "typography": {
            "bodyFont": {"family": "GFS Didot"},
            "bodySize": 10.5,
            "leading": DEFAULT_LEADING_PT,
            "scaleRatio": 1.25,
            "measure": 66,
            "bodyAlignment": "justified",
            "paragraphIndent": 1.5,
            "opticalMargins": True,
        },
        "grid": {"type": "single", "baselineIncrement": DEFAULT_LEADING_PT},
        "margins": {"top": 18, "bottom": 20, "inside": 15, "outside": 20},
        "folio": {
            "position": "bottom-center",
            "style": "arabic",
            "suppressOn": ["chapter-opening"],
            "startNumber": 1,
        },
        "runningHeads": {
            "rectoSource": "chapter-title",
            "versoSource": "book-title",
            "style": "centered",
        },
        "chapterOpenings": {
            "startsOn": "recto",
            "dropCap": True,
            "dropCapLines": 3,
            "titleTreatment": "centered",
        },
        "fonts": [
            {"family": "GFS Didot", "source": "bundled_ofl"},
        ],
        "colors": {"text": "#000000", "paper": "#FFFFFF"},
    }
