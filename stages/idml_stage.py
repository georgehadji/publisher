"""
IDML stage -- the InDesign deliverable (ARCHITECTURE.md §1.2 stage 19).

Produces `book.idml`: a package a designer opens in InDesign and continues
working in. It is a DELIVERABLE, not a step: `terminal=True`, and nothing
downstream consumes it.

WHY IT MUST NOT FEED ANYTHING. InDesign composes the text when the document is
opened -- its own composer decides line breaks and therefore page breaks. This
stage cannot know the resulting extent, so it must never be the thing a spine
width, a preflight verdict, or a print quote is derived from. The measured
`pagemap/1` from the render path is consumed here only as a FRAME COUNT HINT,
and Smart Text Reflow corrects it in both directions when the file is opened.

WHY IT IS OPT-IN. Registration is a side effect of importing this module, and
`stages/__init__.py` only imports it when PUBLISHER_EMIT_IDML is set. The stage
needs pandoc, and a CSS-path build on a machine without pandoc must not start
failing because an InDesign deliverable nobody asked for joined the graph.
There is no stub: a missing pandoc fails loudly.
"""

from __future__ import annotations

import json
from pathlib import Path

from publisher_stages import (
    stage, StageCtx, StageResult, StageError, ErrorKind,
    ArtifactRef as StageArtifactRef,
)
from publisher_cas import ContentAddressedStore, CasConfig, MediaType
from publisher_idml import IDMLValidationError, IDMLWriter, validate_idml
from profiles import load_profile

# Reused rather than re-implemented: same binary lookup and same subprocess
# discipline (pinned SOURCE_DATE_EPOCH, timeouts, stderr surfaced in the error)
# as the Typst render path, which is the other pandoc consumer.
from stages.typst_stages import PANDOC_TIMEOUT_S, _require_binary, _run

IDML_SCHEMA = "idml/1"


@stage(
    name="idml",
    version=1,
    inputs={
        "doc_path": "doc-effective/1",
        "pagemap_path": "pagemap/1",
        "designspec_path": "designspec/1",
        "profile_name": "profile/1",
    },
    outputs={"idml": IDML_SCHEMA},
    # Both are supplied by the caller; neither has a producing stage. Absent
    # designspec means the built-in default, absent profile means no vendor trim
    # -- the same posture design-compile takes.
    root_inputs=["designspec_path", "profile_name"],
    optional_root_inputs=["designspec_path", "profile_name"],
    # `doc_path` and `pagemap_path` are NOT root: routing around `resolve` (and
    # therefore around the text-integrity gate) by promoting them would let a
    # manuscript reach a delivered InDesign file without ever being checked.
    terminal=True,   # a delivery artifact; no stage consumes idml/1
    toolchain=["pandoc"],
    fixtures=None,
    memory_budget_mb=256,
    queue="q.composition",
    description="Emit an InDesign-openable IDML package from the resolved document",
)
def idml(ctx: StageCtx, doc_path: str | None = None, pagemap_path: str | None = None,
         designspec_path: str | None = None, profile_name: str | None = None) -> StageResult:
    """Build `book.idml` from the resolved document."""
    if doc_path is None:
        raise StageError(kind=ErrorKind.BAD_INPUT,
                         message="idml requires 'doc_path' (from resolve)")
    doc_file = Path(doc_path)
    if not doc_file.exists():
        raise StageError(kind=ErrorKind.BAD_INPUT,
                         message=f"Document input not found: {doc_path}")

    pandoc = _require_binary("pandoc", "PUBLISHER_PANDOC_BIN")
    doc = json.loads(doc_file.read_bytes())

    # The story is converted from the HTML ast-assemble proved text-complete,
    # via pandoc's ICML writer -- the markup IDML stories are made of. NOT
    # --standalone: the standalone wrapper carries pandoc's own style
    # definitions and a <Document> root, and this package defines its styles
    # from the DesignSpec instead.
    from stages.extract_stage import _ast_to_html
    story_xml = _run(
        [pandoc, "--from=html", "--to=icml", "--wrap=preserve"],
        timeout=PANDOC_TIMEOUT_S, what="pandoc html -> icml",
        stdin=_ast_to_html(doc).encode("utf-8"),
    ).decode("utf-8")

    spec = None
    if designspec_path and Path(designspec_path).exists():
        spec = json.loads(Path(designspec_path).read_bytes())
    if spec is None:
        from stages.design_compile_stage import _default_designspec
        spec = _default_designspec()

    profile = None
    if profile_name:
        profile = load_profile(profile_name)
        if profile is None:
            raise StageError(
                kind=ErrorKind.BAD_INPUT,
                message=f"Unknown vendor profile: {profile_name!r}. Profiles are "
                        f"loaded from profiles/*/*.yaml by their `name:` field.",
            )

    page_hint = _page_hint(pagemap_path)

    work = Path(ctx.work_dir) / "idml"
    work.mkdir(parents=True, exist_ok=True)
    out_path = work / "book.idml"

    title = (doc.get("metadata") or {}).get("title") or "Untitled"
    IDMLWriter(story_xml, title=title, designspec=spec, profile=profile,
               page_count=page_hint).write(out_path)

    # Validate the bytes that are about to be delivered, not a model of them.
    # A package that InDesign refuses is a failed build, not a shipped file the
    # customer discovers is broken.
    try:
        summary = validate_idml(out_path)
    except IDMLValidationError as e:
        raise StageError(
            # ENGINE_BUG, not BAD_INPUT: the manuscript is fine, this writer is
            # not. Blaming the input would send a customer to fix a file that
            # has nothing wrong with it.
            kind=ErrorKind.ENGINE_BUG,
            message=f"generated IDML failed structural validation: {e}",
        ) from e

    data = out_path.read_bytes()
    cas = ContentAddressedStore(CasConfig(local_cache_root=Path(ctx.cas_root)))
    ref = cas.put(data, media_type=MediaType(MediaType.APPLICATION_IDML))

    print(f"  [idml] {summary['parts']} parts, {summary['text_frames']} threaded "
          f"frame(s), {summary['paragraph_styles']} paragraph styles -> {ref.hash} "
          f"({len(data)} bytes)")

    return StageResult(
        artifacts=[StageArtifactRef(
            kind="idml",
            hash=str(ref.hash),
            media_type=str(MediaType.APPLICATION_IDML),
            size=len(data),
        )],
        metrics={
            "frame_count": float(summary["text_frames"]),
            # A HINT taken from the render path, not a claim about how InDesign
            # will paginate. Named so nothing mistakes it for a measured extent.
            "page_hint": float(page_hint),
            "output_size_bytes": float(len(data)),
            "paragraph_styles": float(summary["paragraph_styles"]),
        },
    )


def _page_hint(pagemap_path: str | None) -> int:
    """Frame count to lay down, from the render path's measured page count.

    Falls back to 1 when no pagemap is available: Smart Text Reflow then adds
    every page on open. Deliberately not an estimate dressed up as a count --
    one frame is visibly a starting point, whereas a guessed 300 would look like
    a measurement.
    """
    if not pagemap_path or not Path(pagemap_path).exists():
        return 1
    try:
        pagemap = json.loads(Path(pagemap_path).read_bytes())
    except (OSError, json.JSONDecodeError):
        return 1
    return max(1, len(pagemap.get("pages") or []))
