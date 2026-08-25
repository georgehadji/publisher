"""
Typst render path -- the second workflow: DOCX/DOC -> pandoc -> Typst -> PDF.

Two stages live here, and both are ALTERNATIVE IMPLEMENTATIONS of steps the CSS
path already implements (StageDeclaration.implements):

    design-compile-typst   implements "design-compile"   DesignSpec -> styles.typ
    paginate-typst         implements "paginate"         doc + styles.typ -> PDF

Nothing else in the pipeline changes. `finish-gs` still converts the raw PDF to
PDF/X-1a, `preflight` still gates it, `package` still needs preflight's verdict,
and the text-integrity gate still runs upstream -- because `paginate-typst`
consumes `doc-effective/1`, exactly like `paginate`. There is no path from a
manuscript to a Typst PDF that skips `ast-assemble` (ARCHITECTURE.md §1.2,
CLAUDE.md "two hard gates"). Select the path with:

    PUBLISHER_RENDER_ENGINE=typst   (default: css)

WHY PANDOC. The AST is already rendered to HTML by `extract._ast_to_html` -- the
same function whose output ast-assemble proved text-complete. Pandoc converts
that HTML to Typst markup, so this path reuses the verified renderer instead of
introducing a second AST walker that could drift from what the gate checked.
Chapter *structure* (headings, page marks) is emitted here rather than handed to
pandoc, because the pagemap needs labelled anchors to measure chapter start
pages from.

WHY BLEED IS ADDED TO THE PAGE BOX HERE, unlike the CSS emitter. weasyprint
writes TrimBox == MediaBox from its page box, so bleed there has to be declared
with the CSS `bleed` property (see design_compile_stage._emit_css). Typst emits
a MediaBox and no TrimBox at all, so `finish-gs`'s PDFXTrimBoxToMediaBoxOffset
-- which only applies to boxes the input lacks -- is what insets the TrimBox.
The renderer's job is therefore to lay out at trim + 2*bleed and let Ghostscript
cut the trim box back in.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from publisher_stages import (
    stage, StageCtx, StageResult, StageError, ErrorKind,
    ArtifactRef as StageArtifactRef,
)
from publisher_cas import ContentAddressedStore, CasConfig, MediaType
from publisher_prepress.fontvault import FontLicenseViolation, validate_font_use
from profiles import load_profile
# The fallbacks for the page-furniture blocks, shared with the CSS emitter so
# both engines cannot drift apart on what a running head is.
from stages.design_compile_stage import (
    DEFAULT_LEADING_PT, _FOLIO_DEFAULTS, _RUNNING_HEAD_DEFAULTS,
)

TYPST_SCHEMA = "text/x-typst"

PANDOC_TIMEOUT_S = int(os.environ.get("PUBLISHER_PANDOC_TIMEOUT_S", "60"))
TYPST_TIMEOUT_S = int(os.environ.get("PUBLISHER_TYPST_TIMEOUT_S", "300"))


# ─────────────────────────── design-compile-typst ───────────────────────────


def _typ_str(text: str) -> str:
    r"""A Typst string literal for arbitrary text.

    Typst's string escapes are a subset of JSON's (`\"` and `\\`), and
    `ensure_ascii=False` keeps non-ASCII as literal UTF-8 rather than emitting
    `\uXXXX`, which Typst spells `\u{XXXX}` and would otherwise render verbatim
    in a book.
    """
    return json.dumps(text, ensure_ascii=False)


def _emit_typst(designspec: dict, bleed_mm: float = 0.0) -> str:
    """Emit a Typst preamble from a DesignSpec -- the `emit_typst()` sibling of
    `design_compile_stage._emit_css` (ARCHITECTURE.md §2.7: one spec, several
    emitters).

    Page box is trim + 2*bleed on every side and margins grow by the same
    amount, so the type area sits in the same place on the sheet as it does in
    the CSS path; see this module's docstring for why the arithmetic is done
    here rather than declared.
    """
    typography = designspec.get("typography") or {}
    margins = designspec.get("margins") or {}
    folio = designspec.get("folio") or {}
    running_heads = designspec.get("runningHeads") or {}
    chapter_openings = designspec.get("chapterOpenings") or {}
    colors = designspec.get("colors") or {}
    trim_size = designspec.get("trimSize") or {}
    metadata = designspec.get("metadata") or {}

    w_mm = float(trim_size.get("width", 152.4)) + 2 * bleed_mm
    h_mm = float(trim_size.get("height", 228.6)) + 2 * bleed_mm

    body_size = float(typography.get("bodySize", 10.5))
    leading = float(typography.get("leading", DEFAULT_LEADING_PT))
    paragraph_indent = float(typography.get("paragraphIndent", 1.5))
    justified = typography.get("bodyAlignment", "justified") == "justified"

    body_font = (typography.get("bodyFont") or {}).get("family", "EB Garamond")
    heading_font = (typography.get("headingFont") or typography.get("displayFont")
                    or {}).get("family") or body_font

    top = float(margins.get("top", 18)) + bleed_mm
    bottom = float(margins.get("bottom", 20)) + bleed_mm
    inside = float(margins.get("inside", 15)) + float(margins.get("gutter", 0)) + bleed_mm
    outside = float(margins.get("outside", 20)) + bleed_mm

    text_color = colors.get("text", "#000000")
    paper_color = colors.get("paper", "#FFFFFF")

    folio_position = folio.get("position", "bottom-center")
    numbering = {"roman-lower": "i", "roman-upper": "I",
                 "arabic": "1"}.get(folio.get("style", "arabic"), "1")
    # `outside` alternates by parity. The previous map sent both outside
    # positions to `center`, so a spec asking for outside folios silently got
    # centred ones -- and centred folios on a book with outer running heads is a
    # different design from the one that was asked for.
    folio_align = ("center" if folio_position.endswith("center")
                   else "if calc.odd(here().page()) { right } else { left }")

    # Typst's `leading` is the gap BETWEEN lines, not the baseline-to-baseline
    # distance a DesignSpec calls leading -- subtract the type size.
    # ponytail: linear approximation; a strict baseline grid needs per-face
    # tuning of `#set par(spacing:)`, which is a calibration job, not a formula.
    typst_leading = max(0.0, leading - body_size)

    book_title = metadata.get("title") or designspec.get("name") or ""

    lines = [
        "// Auto-generated from DesignSpec -- emit_typst()",
        f"// Page box is trim + 2 x {bleed_mm:g}mm bleed; finish-gs insets the TrimBox.",
        "",
        "#set page(",
        f"  width: {w_mm:g}mm,",
        f"  height: {h_mm:g}mm,",
        f"  margin: (top: {top:g}mm, bottom: {bottom:g}mm, "
        f"inside: {inside:g}mm, outside: {outside:g}mm),",
        "  binding: left,",
        # No `numbering:` here on purpose -- Typst's built-in folio renders in
        # the body text style, so the DesignSpec's folio size and weight could
        # not be applied to it. An explicit footer below carries the same number
        # and takes the spec's typography.
        f"  fill: rgb({_typ_str(paper_color)}),",
        ")",
        f"#set text(font: ({_typ_str(body_font)}, \"Liberation Serif\"), "
        f"size: {body_size:g}pt, fill: rgb({_typ_str(text_color)}))",
        f"#set par(justify: {'true' if justified else 'false'}, "
        f"leading: {typst_leading:g}pt, first-line-indent: {paragraph_indent:g}em)",
        "#set heading(numbering: none)",
        "",
    ]

    if running_heads.get("versoSource") is not None:
        # Suppressed on chapter-opening pages, per DesignSpec.folio.suppressOn --
        # a running head on a chapter opening is the commonest amateur tell in a
        # typeset book.
        head_args, head_wrap = _furniture_typst(running_heads, body_size,
                                                _RUNNING_HEAD_DEFAULTS)
        lines += [
            "// Running head, suppressed on chapter-opening pages.",
            "#set page(header: context {",
            "  let openings = query(heading.where(level: 1))"
            ".map(h => h.location().page())",
            "  if here().page() in openings { return }",
            f"  align(center, text({head_args}, "
            f"{head_wrap % _typ_str(book_title)}))",
            "})",
            "",
        ]

    if folio_position != "none":
        folio_args, folio_wrap = _furniture_typst(folio, body_size, _FOLIO_DEFAULTS)
        folio_number = folio_wrap % f"numbering({_typ_str(numbering)}, here().page())"
        suppressed = folio.get("suppressOn") or ["chapter-opening"]
        lines += [
            "// Folio. An explicit footer rather than page(numbering:) so the",
            "// DesignSpec's size, weight and tracking actually apply to it.",
            "#set page(footer: context {",
            *(["  let openings = query(heading.where(level: 1))"
               ".map(h => h.location().page())",
               "  if here().page() in openings { return }"]
              if "chapter-opening" in suppressed else []),
            f"  align({folio_align}, text({folio_args}, {folio_number}))",
            "})",
            "",
        ]

    pagebreak_to = {"recto": '"odd"', "verso": '"even"'}.get(
        chapter_openings.get("startsOn", "recto"))
    opening_break = (
        f"  pagebreak(to: {pagebreak_to}, weak: true)" if pagebreak_to
        else "  pagebreak(weak: true)"
    )
    title_align = {"centered": "center", "left": "left", "right": "right"}.get(
        chapter_openings.get("titleTreatment", "centered"), "center")

    lines += [
        "// Chapter opening: break to the right sheet, then set the title.",
        "#show heading.where(level: 1): it => {",
        opening_break,
        "  v(6%)",
        f"  align({title_align}, text(font: {_typ_str(heading_font)}, "
        f"size: {body_size * 1.6:g}pt, weight: \"regular\", it.body))",
        "  v(4%)",
        "}",
        "",
        "// ponytail: dropCap and opticalMargins are declared by the DesignSpec but",
        "// not emitted -- both need a Typst package (droplet / cetz) that is not",
        "// vendored yet. Everything else in the spec is honoured.",
    ]

    return "\n".join(lines) + "\n"


"""Case transforms available to page furniture, keyed by DesignSpec `case`."""
TYPST_CASE = {
    "none": "%s",
    "uppercase": "upper(%s)",
    "lowercase": "lower(%s)",
    "small-caps": "smallcaps(%s)",
}


def _furniture_typst(block: dict, body_size: float, defaults: dict) -> tuple[str, str]:
    """`(text(...) arguments, content wrapper)` for a running head or folio.

    The Typst counterpart of `_furniture_css`, reading the same DesignSpec
    fields with the same fallbacks -- imported from the CSS emitter rather than
    restated, because two copies of "running heads are body minus three" is how
    the two engines came to disagree in the first place (CSS said 9pt flat,
    Typst said body minus 1.5).
    """
    size = body_size + float(block.get("sizeDelta", defaults["sizeDelta"]))
    weight = block.get("weight", defaults["weight"])
    tracking = float(block.get("tracking", defaults["tracking"])) / 1000.0

    args = [f"size: {size:g}pt", f'weight: {_typ_str(weight)}']
    if tracking:
        args.append(f"tracking: {tracking:g}em")
    wrapper = TYPST_CASE.get(block.get("case", defaults["case"]), "%s")
    return ", ".join(args), wrapper


def _fonts_in_spec(spec: dict) -> list[tuple[str, str]]:
    """(family, style) pairs referenced by a DesignSpec's typography block."""
    typography = spec.get("typography") or {}
    out = []
    for key in ("bodyFont", "displayFont", "monoFont", "headingFont"):
        family = (typography.get(key) or {}).get("family")
        if family:
            out.append((family, (typography.get(key) or {}).get("style", "regular")))
    return out


@stage(
    name="design-compile-typst",
    version=1,
    inputs={"designspec_path": "designspec/1", "profile_name": "profile/1"},
    outputs={"typ": TYPST_SCHEMA},
    root_inputs=["designspec_path", "profile_name"],
    optional_root_inputs=["designspec_path", "profile_name"],
    implements="design-compile",   # alternative to the CSS emitter
    toolchain=[],
    fixtures=None,
    memory_budget_mb=64,
    queue="q.composition",
    description="Compile DesignSpec -> Typst preamble for the Typst renderer",
)
def design_compile_typst(ctx: StageCtx, designspec_path: str | None = None,
                         profile_name: str | None = None) -> StageResult:
    """
    Emit a Typst stylesheet from a DesignSpec.

    Mirrors `design_compile`'s contract exactly, including both of its refusals:
    a DesignSpec naming an engine this emitter does not implement is
    `bad_input` (never silently rendered by the wrong engine), and a font that
    is not licensed for print is a `policy_violation`.
    """
    IMPLEMENTED_ENGINES = {"typst"}

    from stages.design_compile_stage import _default_designspec
    # The built-in default spec says preferredEngine="chrome-pagedjs" because
    # CSS is the default path. Selecting THIS stage is the declaration that the
    # build renders with Typst, so its default follows the selection. An
    # explicitly supplied spec is still checked below.
    spec = {**_default_designspec(), "preferredEngine": "typst"}

    if designspec_path:
        p = Path(designspec_path)
        if p.exists():
            spec = json.loads(p.read_bytes())

    engine = spec.get("preferredEngine")
    if engine not in IMPLEMENTED_ENGINES:
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"DesignSpec declares preferredEngine={engine!r}, but the Typst "
                    f"render path is selected (PUBLISHER_RENDER_ENGINE=typst) and this "
                    f"emitter only implements {sorted(IMPLEMENTED_ENGINES)}. Either set "
                    f"preferredEngine to 'typst', or unset PUBLISHER_RENDER_ENGINE to "
                    f"render with CSS/Paged.js.",
        )

    for family, style in _fonts_in_spec(spec):
        try:
            validate_font_use(family, style, "PRINT_PDF")
        except FontLicenseViolation as exc:
            raise StageError(kind=ErrorKind.POLICY_VIOLATION, message=str(exc))

    # Trim and bleed belong to the vendor profile, never the DesignSpec -- the
    # same split design_compile enforces, for the same reason: the renderer and
    # preflight must measure one geometry, not two.
    bleed_mm = 0.0
    if profile_name:
        profile = load_profile(profile_name)
        if profile is None:
            raise StageError(
                kind=ErrorKind.BAD_INPUT,
                message=f"Unknown vendor profile: {profile_name!r}. Profiles are "
                        f"loaded from profiles/*/*.yaml by their `name:` field.",
            )
        trim = profile.get("trimSize") or {}
        if trim.get("width") and trim.get("height"):
            spec = {**spec, "trimSize": trim}
        bleed_mm = float((profile.get("bleed") or {}).get("all", 0.0))

    styles = _emit_typst(spec, bleed_mm=bleed_mm)
    styles_bytes = styles.encode("utf-8")

    cas = ContentAddressedStore(CasConfig(local_cache_root=Path(ctx.cas_root)))
    ref = cas.put(styles_bytes, media_type=MediaType(TYPST_SCHEMA))

    print(f"  [design-compile-typst] Generated styles.typ -> {ref.hash} "
          f"({len(styles_bytes)} bytes, bleed {bleed_mm:g}mm)")

    return StageResult(
        artifacts=[StageArtifactRef(
            kind="typ",
            hash=str(ref.hash),
            media_type=TYPST_SCHEMA,
            size=len(styles_bytes),
        )],
        metrics={"typ_size_bytes": float(len(styles_bytes)), "bleed_mm": bleed_mm},
    )


# ───────────────────────────── paginate-typst ──────────────────────────────


def _require_binary(name: str, env_var: str) -> str:
    """Absolute path to a required toolchain binary, or a loud INFRA failure.

    Deliberately has no stub branch, not even under `allow_stub_engines`: the
    CSS path's stub emits an HTML dump whose page count is an estimate, and the
    entire point of selecting Typst is that a real engine paginated the book.
    A missing binary is an infrastructure fault, not a rendering mode.
    """
    found = os.environ.get(env_var) or shutil.which(name)
    if not found:
        raise StageError(
            kind=ErrorKind.INFRA,
            message=f"'{name}' is not installed (searched PATH and ${env_var}). The "
                    f"Typst render path needs both pandoc >= 3.1.1 (for its Typst "
                    f"writer) and typst >= 0.12. Install them, or unset "
                    f"PUBLISHER_RENDER_ENGINE to render with CSS/Paged.js.",
        )
    return found


def _run(argv: list[str], *, timeout: int, what: str,
         stdin: bytes | None = None) -> bytes:
    """Run a toolchain binary, returning stdout; raise INFRA on any failure.

    SOURCE_DATE_EPOCH is pinned so Typst embeds a fixed creation date -- without
    it the same manuscript produces a different PDF on every run and
    reproducibility (ARCHITECTURE.md §2.1 principle 7) is a claim rather than a
    property.
    """
    env = {**os.environ, "SOURCE_DATE_EPOCH": "0"}
    try:
        proc = subprocess.run(argv, input=stdin, capture_output=True,
                              timeout=timeout, env=env)
    except OSError as e:
        raise StageError(kind=ErrorKind.INFRA, message=f"{what}: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise StageError(kind=ErrorKind.INFRA,
                         message=f"{what} timed out after {timeout}s") from e
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode("utf-8", "replace").strip()[-2000:]
        raise StageError(kind=ErrorKind.INFRA,
                         message=f"{what} failed (exit {proc.returncode}): {tail}")
    return proc.stdout


def _chapters_of(doc: dict) -> list[dict]:
    """Chapter id/number/title/content, using the SAME id rule as
    `extract._ast_to_html`, so a pagemap from this path is comparable with one
    from the CSS path for the same book.
    """
    chapters = []
    for i, chapter in enumerate(doc.get("body") or []):
        attrs = chapter.get("attrs") or {}
        ctype = chapter.get("type", "chapter")
        cid = attrs.get("id") or f"{ctype}-{attrs.get('number', '?')}"
        try:
            number = int(attrs.get("number", i + 1))
        except (TypeError, ValueError):
            number = i + 1
        chapters.append({
            "chapterId": cid,
            "number": number,
            "title": attrs.get("title") or f"Chapter {number}",
            "content": chapter.get("content") or [],
        })
    return chapters


def _mark(fields: str) -> str:
    """A queryable page mark. `typst query` reads these back AFTER layout, which
    is how chapter start pages and the page count are measured rather than
    apportioned by text volume."""
    return f"#context [#metadata(({fields}))<pubmeta>]"


# pandoc's HTML reader has no notion of a footnote element -- HTML has none --
# so `<span class="footnote">` arrives as a plain Span and the Typst writer
# prints the note inline, mid-sentence, in the body text. Promoting it to a Note
# makes the writer emit `#footnote[...]`, which numbers the call and sets the
# note at the foot of the page it actually landed on.
#
# A filter rather than a regex over pandoc's output: a note body is arbitrary
# markup, and matching balanced brackets in generated Typst is the kind of
# parsing that works until a manuscript contains a bracket.
FOOTNOTE_FILTER_LUA = """
function Span(el)
  if el.classes:includes("footnote") then
    return pandoc.Note({pandoc.Plain(el.content)})
  end
end
"""


def _build_main_typ(doc: dict, styles: str, chapters: list[dict],
                    pandoc: str, filter_path: Path | None = None) -> str:
    """Assemble the Typst document: preamble, front matter, chapters, back
    matter -- with pandoc converting each HTML fragment into Typst markup."""
    from stages.extract_stage import _render_content

    filter_args = ["--lua-filter", str(filter_path)] if filter_path else []

    def to_typst(content: list) -> str:
        html = _render_content(content)
        if not html.strip():
            return ""
        out = _run([pandoc, "--from=html", "--to=typst", "--wrap=preserve",
                    *filter_args],
                   timeout=PANDOC_TIMEOUT_S, stdin=html.encode("utf-8"),
                   what="pandoc html -> typst")
        return out.decode("utf-8")

    parts = [styles, ""]

    for item in doc.get("frontMatter") or []:
        parts.append(to_typst(item.get("content") or []))

    for chapter in chapters:
        parts.append(f"#heading(level: 1)[{_typ_str(chapter['title'])}]")
        # After the heading, so `here().page()` reports the page the chapter
        # opening actually landed on (the heading's show rule breaks pages).
        parts.append(_mark(
            f'kind: "chapter", id: {_typ_str(chapter["chapterId"])}, '
            f'page: here().page()'
        ))
        parts.append(to_typst(chapter["content"]))

    for item in doc.get("backMatter") or []:
        parts.append(to_typst(item.get("content") or []))

    # Last element in the document: the page it lands on IS the page count.
    # Read that way rather than via `counter(page).final()`, whose spelling has
    # moved between Typst releases.
    parts.append(_mark(
        'kind: "doc", page: here().page(), '
        'w: page.width.pt(), h: page.height.pt()'
    ))

    return "\n\n".join(p for p in parts if p)


class _MeasuredPage:
    """The shape `paginate._build_pagemap` reads off a laid-out page.

    Reusing that builder rather than writing a second pagemap assembler keeps
    both render paths emitting comparable `pagemap/1` for the same book, which
    is the only reason a layout diff between engines means anything.
    """

    __slots__ = ("anchors", "width", "height")

    def __init__(self, anchors: dict, width: float, height: float):
        self.anchors = anchors
        self.width = width
        self.height = height


@stage(
    name="paginate-typst",
    version=1,
    inputs={"doc_path": "doc-effective/1", "typ_path": TYPST_SCHEMA},
    outputs={"pdf": "raw-pdf/1", "pagemap": "pagemap/1"},
    terminal_outputs=["pagemap"],
    implements="paginate",   # alternative to the weasyprint/CSS renderer
    toolchain=["typst", "pandoc"],
    fixtures=None,
    memory_budget_mb=512,
    queue="q.composition",
    description="Render the resolved document into a PDF via pandoc + Typst",
)
def paginate_typst(ctx: StageCtx, doc_path: str | None = None,
                   typ_path: str | None = None) -> StageResult:
    """Paginate with Typst: doc-effective -> HTML -> (pandoc) -> Typst -> PDF."""
    if doc_path is None:
        raise StageError(kind=ErrorKind.BAD_INPUT,
                         message="paginate-typst requires 'doc_path' (from resolve)")
    doc_file = Path(doc_path)
    if not doc_file.exists():
        raise StageError(kind=ErrorKind.BAD_INPUT,
                         message=f"Document input not found: {doc_path}")

    pandoc = _require_binary("pandoc", "PUBLISHER_PANDOC_BIN")
    typst = _require_binary("typst", "PUBLISHER_TYPST_BIN")

    doc = json.loads(doc_file.read_bytes())

    if typ_path and Path(typ_path).exists():
        styles = Path(typ_path).read_text(encoding="utf-8")
    else:
        # Same posture as `paginate`: a missing stylesheet falls back to the
        # built-in default rather than failing, because preflight -- not this
        # stage -- is the authority on whether the resulting geometry is legal.
        from stages.design_compile_stage import _default_designspec
        styles = _emit_typst({**_default_designspec(), "preferredEngine": "typst"})

    chapters = _chapters_of(doc)
    work = Path(ctx.work_dir) / "typst"
    work.mkdir(parents=True, exist_ok=True)
    main_typ = work / "main.typ"
    out_pdf = work / "out.pdf"

    # Figures are `media/<sha256>.<ext>` in the HTML and become `#image(...)` in
    # the Typst source, resolved relative to --root -- so the bytes have to be
    # under `work` before typst compiles, not merely somewhere in CAS.
    from stages.extract_stage import _ast_to_html
    from stages.media import materialize_media
    images = materialize_media(_ast_to_html(doc), ctx.cas_root, work)

    footnote_filter = work / "footnotes.lua"
    footnote_filter.write_text(FOOTNOTE_FILTER_LUA, encoding="utf-8")

    main_typ.write_text(
        _build_main_typ(doc, styles, chapters, pandoc, footnote_filter),
        encoding="utf-8",
    )
    if images:
        print(f"  [paginate-typst] materialized {len(images)} figure(s)")

    # --root confines Typst's file access to the scratch dir; the document is
    # generated here, but its text is ultimately tenant-supplied.
    _run([typst, "compile", "--root", str(work), str(main_typ), str(out_pdf)],
         timeout=TYPST_TIMEOUT_S, what="typst compile")

    if not out_pdf.is_file():
        raise StageError(kind=ErrorKind.INFRA,
                         message="typst compile reported success but wrote no PDF")
    pdf_bytes = out_pdf.read_bytes()

    # A second compile, so the marks can be read back after layout. Worth the
    # duplicate cost: it is the difference between a MEASURED page count (which
    # the spine width is priced off) and an estimate.
    # ponytail: `typst query` recompiles; if render time ever dominates, emit
    # the marks to a sidecar during the first pass instead.
    raw = _run([typst, "query", "--root", str(work), "--field", "value",
                str(main_typ), "<pubmeta>"],
               timeout=TYPST_TIMEOUT_S, what="typst query <pubmeta>")
    try:
        marks = json.loads(raw.decode("utf-8") or "[]")
    except json.JSONDecodeError as e:
        raise StageError(kind=ErrorKind.INFRA,
                         message=f"typst query returned non-JSON: {e}") from e

    doc_marks = [m for m in marks if isinstance(m, dict) and m.get("kind") == "doc"]
    if not doc_marks:
        raise StageError(
            kind=ErrorKind.INFRA,
            message="typst query found no document mark -- the page count could not "
                    "be measured, and an estimated page count must never price a "
                    "spine (ARCHITECTURE.md §1.3).",
        )
    page_count = int(doc_marks[-1]["page"])
    width_pt = float(doc_marks[-1].get("w") or 432.0)
    height_pt = float(doc_marks[-1].get("h") or 648.0)

    starts = {m["id"]: int(m["page"]) for m in marks
              if isinstance(m, dict) and m.get("kind") == "chapter" and m.get("id")}

    # Rebuild the per-page anchor view `_build_pagemap` expects from a laid-out
    # document, then hand it the same builder the CSS path uses.
    from stages.paginate_stage import _build_pagemap
    anchors_by_page: dict[int, dict] = {}
    for chapter_id, page in starts.items():
        anchors_by_page.setdefault(page, {})[chapter_id] = True
    rendered_pages = [
        _MeasuredPage(anchors_by_page.get(p, {}), width_pt, height_pt)
        for p in range(1, page_count + 1)
    ]

    pagemap = _build_pagemap(
        [{"chapterId": c["chapterId"], "number": c["number"], "textLength": 1}
         for c in chapters],
        page_count,
        rendered_pages,
    )
    pagemap_bytes = json.dumps(pagemap, indent=2).encode("utf-8")

    cas = ContentAddressedStore(CasConfig(local_cache_root=Path(ctx.cas_root)))
    pdf_ref = cas.put(pdf_bytes, media_type=MediaType.APPLICATION_PDF)
    pm_ref = cas.put(pagemap_bytes, media_type=MediaType("application/json"))

    print(f"  [paginate-typst] Rendered {page_count} page(s) via pandoc + typst "
          f"({len(pdf_bytes)} bytes): PDF={pdf_ref.hash}, pagemap={pm_ref.hash}")

    return StageResult(
        artifacts=[
            StageArtifactRef(kind="pdf", hash=str(pdf_ref.hash),
                             media_type=str(MediaType.APPLICATION_PDF),
                             size=len(pdf_bytes)),
            StageArtifactRef(kind="pagemap", hash=str(pm_ref.hash),
                             media_type="application/json",
                             size=len(pagemap_bytes)),
        ],
        metrics={
            "page_count": float(page_count),
            # Always measured here -- this stage has no stub branch.
            "page_count_measured": 1.0,
            "chapter_count": float(len(chapters)),
            "chapters_located": float(len(starts)),
            "output_size_bytes": float(len(pdf_bytes)),
            "renderer_type": 1.0,
            "stub_engine": 0.0,
        },
    )
