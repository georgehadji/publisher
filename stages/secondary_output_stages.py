"""
Secondary output stages -- EPUB and ONIX (E7.2, docs/ARCHITECTURE_SCORE_10_PLAN.md).

`services/epub` and `services/onix` were fully built writers with no stage, no
caller, and no way to reach a real build (L9). ARCHITECTURE.md's own
documented delivery step (§1.1 step 9) already promises "EPUB · ONIX 3.0"
alongside the interior PDF -- leaving them unwired left that promise false.

Wired here as two ordinary, always-on terminal stages consuming
`doc-effective/1` -- the same resolved document `design-compile`/`paginate`/
`idml` consume. EPUB and ONIX are alternative PROJECTIONS of that document,
not renders that depend on page geometry, so neither needs `pagemap/1` or a
vendor `profile/1` the way `idml` optionally does.

Unlike `idml` (opt-in because it needs pandoc), neither writer shells out to
an external binary -- stdlib `zipfile`/`xml.etree` only -- so both register
unconditionally in `stages/__init__.py` alongside every other always-on
stage. That also means every ordinary build now produces them: intentional,
since that is what makes ARCHITECTURE.md's delivery list true rather than
aspirational.

The EPUB is checked like the print path: its spine's text must equal the
resolved document's text stream (`stages/text_stream.ast_text`) or the stage
fails. It went unchecked for as long as it had a renderer of its own, and on
the first real book it silently shipped 76% of the text.
"""

from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path

from publisher_stages import (
    stage, StageCtx, StageResult, StageError, ErrorKind, Diagnostic,
    ArtifactRef as StageArtifactRef,
)
from publisher_cas import ContentAddressedStore, CasConfig, MediaType
from publisher_epub import (CORE_IMAGE_TYPES, STYLESHEET, EPUB3Writer, EPUBError, Media,
                            spine_text)
from publisher_onix import ONIXWriter
from publisher_prepress.fontvault import (FACE_CSS, FontLicenseViolation, font_files,
                                          fonts_in_spec, fontset_hash, validate_font_use)
from publisher_structure.rules import normalize_text

from stages.media import MEDIA_REF
from stages.rendering import MEDIA_EXTENSIONS, ast_to_epub_sections
from stages.text_stream import ast_text, divergence, excerpt

# Raster types Word embeds that EPUB readers are not required to show. They are
# converted to PNG, losslessly, rather than shipped as foreign resources.
CONVERTIBLE_TO_PNG = {"image/tiff", "image/bmp"}


def _load_doc(doc_path: str | None) -> dict:
    if doc_path is None:
        raise StageError(kind=ErrorKind.BAD_INPUT, message="requires 'doc_path' (from resolve)")
    path = Path(doc_path)
    if not path.exists():
        raise StageError(kind=ErrorKind.BAD_INPUT, message=f"Document input not found: {doc_path}")
    return json.loads(path.read_bytes())


def _epub_media(sections: list[dict], cas_root: str) -> dict[str, Media]:
    """The images the sections show, out of CAS, as the package will hold them.

    A type EPUB does not guarantee is converted to PNG and every `src` naming it
    is rewritten in place. A referenced blob missing from CAS fails the stage:
    an EPUB with an empty frame where a plate should be looks finished.
    """
    types = {ext: media_type for media_type, ext in MEDIA_EXTENSIONS.items()}
    media: dict[str, Media] = {}
    for digest, ext in sorted({ref for s in sections for ref in MEDIA_REF.findall(s["body"])}):
        href = f"media/{digest}.{ext}"
        blob = Path(cas_root) / digest[:2] / digest[2:4] / digest
        if not blob.is_file():
            raise StageError(kind=ErrorKind.INFRA,
                             message=f"figure {digest[:12]}... is not in CAS at {blob}")
        media_type, data = types.get(ext, ""), blob.read_bytes()
        if media_type in CONVERTIBLE_TO_PNG:
            from PIL import Image

            out = io.BytesIO()
            with Image.open(io.BytesIO(data)) as image:
                image.save(out, format="PNG")
            data, media_type = out.getvalue(), "image/png"
            png = f"media/{digest}.png"
            for section in sections:
                section["body"] = section["body"].replace(href, png)
            href = png
        if media_type not in CORE_IMAGE_TYPES:
            raise StageError(kind=ErrorKind.BAD_INPUT,
                             message=f"figure {digest[:12]}... is {media_type or ext}, which "
                                     "an EPUB cannot carry; re-save it as PNG or JPEG")
        media[href] = Media(media_type, data)
    return media


FONT_MEDIA_TYPES = {".otf": "font/otf", ".ttf": "font/ttf"}


def _design(designspec_path: str | None) -> dict:
    """The DesignSpec the book was set in: the supplied one, or the house design."""
    if designspec_path and Path(designspec_path).exists():
        return json.loads(Path(designspec_path).read_bytes())
    from templates import house_designspec
    return house_designspec()


def _epub_fonts(spec: dict) -> tuple[str, dict[str, Media], list[str]]:
    """The design's faces for the EPUB: `@font-face` rules and the rules that
    use them, the font files, and the families left to the reading system.

    A family is embedded when its regular face is on this worker and the vault
    licenses every face found for EPUB_EMBED -- so the commercial faces, which
    are licensed for print only, are never put in a package a reader can unzip.
    """
    rules: list[str] = []
    files: dict[str, Media] = {}
    left: list[str] = []
    for family in dict.fromkeys(f for f, _ in fonts_in_spec(spec)):
        faces = font_files([family])
        try:
            for _, style in faces:
                validate_font_use(family, style, "EPUB_EMBED")
        except FontLicenseViolation:
            faces = {}
        if (family, "regular") not in faces:
            left.append(family)
            continue
        for (_, style), path in sorted(faces.items()):
            data = path.read_bytes()
            href = f"fonts/{hashlib.sha256(data).hexdigest()[:16]}{path.suffix.lower()}"
            files[href] = Media(FONT_MEDIA_TYPES[path.suffix.lower()], data)
            weight, css_style = FACE_CSS[style]
            rules.append(f"@font-face {{ font-family: {json.dumps(family)}; "
                         f"src: url({json.dumps(href)}); font-weight: {weight}; "
                         f"font-style: {css_style}; }}")
    typography = spec.get("typography") or {}
    body = (typography.get("bodyFont") or {}).get("family")
    heading = (typography.get("headingFont") or {}).get("family") or body
    for selector, family in (("body", body), (".chapter-title", heading)):
        if family and family not in left:
            rules.append(f"{selector} {{ font-family: {json.dumps(family)}, serif; }}")
    return "\n".join(rules), files, left


def _epub_font_salt(inputs: dict) -> str:
    """Cache-key salt, as `paginate`'s: the bytes of the faces the EPUB may embed."""
    return fontset_hash([f for f, _ in fonts_in_spec(_design(inputs.get("designspec_path")))])


@stage(
    name="epub",
    # v4: the package passes DAISY ACE (W10) -- OPF xml:lang, an
    # accessibilitySummary, and role="doc-toc" on the nav (publisher_epub).
    # v3: embeds the design's faces that are licensed for it (W8), from an
    # optional designspec/1 like idml's; the cache key includes their bytes.
    # v2: rendered by stages/rendering.py (what the print gate verifies) instead
    # of the writer's own lossy walk, with figures, linked footnotes, all
    # front/back matter, and a text-integrity check on the stored package.
    version=4,
    inputs={"doc_path": "doc-effective/1", "designspec_path": "designspec/1"},
    outputs={"epub": "epub/1"},
    # `doc_path` is NOT root: it comes from `resolve`, so an EPUB can only be
    # produced for a document that already passed ast-assemble's text-
    # integrity gate. Same posture as `idml`'s `doc_path`. An absent
    # designspec means the house design, as for `idml`.
    root_inputs=["designspec_path"],
    optional_root_inputs=["designspec_path"],
    cache_salt=_epub_font_salt,
    terminal=True,
    toolchain=[],
    fixtures=None,
    memory_budget_mb=128,
    queue="q.composition",
    description="Emit an EPUB 3 package from the resolved document",
)
def epub(ctx: StageCtx, doc_path: str | None = None,
         designspec_path: str | None = None) -> StageResult:
    doc = _load_doc(doc_path)
    work = Path(ctx.work_dir) / "epub"
    work.mkdir(parents=True, exist_ok=True)

    sections = ast_to_epub_sections(doc)
    media = _epub_media(sections, ctx.cas_root)
    font_css, fonts, fonts_left = _epub_fonts(_design(designspec_path))
    try:
        out_path = EPUB3Writer(sections, metadata=doc.get("metadata") or {},
                               media=media, fonts=fonts,
                               stylesheet=STYLESHEET + font_css).write(work / "book.epub")
    except EPUBError as exc:
        raise StageError(kind=ErrorKind.ENGINE_BUG, message=f"EPUB not written: {exc}") from exc

    # Read back from the written package, not from `sections`: what is checked
    # is what gets stored.
    epub_side = normalize_text(spine_text(out_path))
    source_side = normalize_text(ast_text(doc))
    if epub_side != source_side:
        i = divergence(epub_side, source_side)
        raise StageError(
            kind=ErrorKind.ENGINE_BUG,
            message="Text integrity violation -- the EPUB's text does not match the "
                    "resolved document's text after normalization.",
            diagnostics=[Diagnostic(
                code="integrity_mismatch",
                severity="error",
                human_message=(f"Text streams diverge at normalized offset {i} "
                               f"({len(epub_side)} EPUB vs {len(source_side)} source chars). "
                               f"EPUB side: ...{excerpt(epub_side, i)}... "
                               f"Source side: ...{excerpt(source_side, i)}..."),
                suggested_fix="Look for a node type stages/rendering.py renders differently "
                              "in its EPUB flavour, or markup spine_text reads differently.",
            )],
        )
    data = out_path.read_bytes()

    cas = ContentAddressedStore(CasConfig(local_cache_root=Path(ctx.cas_root)))
    ref = cas.put(data, media_type=MediaType(MediaType.APPLICATION_EPUB))

    chapters = len(doc.get("body") or [])
    notes = sum(s["body"].count('epub:type="noteref"') for s in sections)
    print(f"  [epub] {len(sections)} section(s), {chapters} chapter(s), {notes} note(s), "
          f"{len(media)} image(s); text verified ({len(source_side)} chars) "
          f"-> {ref.hash} ({len(data)} bytes)")

    return StageResult(
        artifacts=[StageArtifactRef(
            kind="epub",   # must exactly equal the declared output key "epub"
            hash=str(ref.hash),
            media_type=str(MediaType.APPLICATION_EPUB),
            size=len(data),
        )],
        metrics={"output_size_bytes": float(len(data)), "chapters": float(chapters),
                 "sections": float(len(sections)), "footnotes": float(notes),
                 "images": float(len(media)), "text_length": float(len(source_side)),
                 "fonts_embedded": float(len(fonts)), "integrity_ok": 1.0},
        warnings=[Diagnostic(
            code="epub-font-not-embedded", severity="warning",
            human_message=f"{fonts_left} not embedded in the EPUB: not licensed for EPUB "
                          "embedding, or not installed on this worker. Readers will "
                          "use a face of their own.",
            suggested_fix="Expected for a print-only commercial face. For an OFL face, "
                          "install it or point PUBLISHER_FONT_DIRS at it.",
        )] if fonts_left else [],
    )


@stage(
    name="onix",
    # v4, v3, v2: no change to its output; its module changed (the epub stage), and
    # the version lint bumps every stage in a touched module.
    version=4,
    inputs={"doc_path": "doc-effective/1"},
    outputs={"onix": "onix/1"},
    terminal=True,
    toolchain=[],
    fixtures=None,
    memory_budget_mb=64,
    queue="q.default",
    description="Emit ONIX 3.0 metadata XML from the resolved document",
)
def onix(ctx: StageCtx, doc_path: str | None = None) -> StageResult:
    doc = _load_doc(doc_path)
    work = Path(ctx.work_dir) / "onix"
    work.mkdir(parents=True, exist_ok=True)
    out_path = ONIXWriter(doc).write(work / "book")
    data = out_path.read_bytes()

    cas = ContentAddressedStore(CasConfig(local_cache_root=Path(ctx.cas_root)))
    ref = cas.put(data, media_type=MediaType(MediaType.APPLICATION_XML))

    print(f"  [onix] -> {ref.hash} ({len(data)} bytes)")

    return StageResult(
        artifacts=[StageArtifactRef(
            kind="onix",   # must exactly equal the declared output key "onix"
            hash=str(ref.hash),
            media_type=str(MediaType.APPLICATION_XML),
            size=len(data),
        )],
        metrics={"output_size_bytes": float(len(data))},
    )
