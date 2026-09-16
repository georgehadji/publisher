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
"""

from __future__ import annotations
import json
from pathlib import Path

from publisher_stages import (
    stage, StageCtx, StageResult, StageError, ErrorKind,
    ArtifactRef as StageArtifactRef,
)
from publisher_cas import ContentAddressedStore, CasConfig, MediaType
from publisher_epub import EPUB3Writer
from publisher_onix import ONIXWriter


def _load_doc(doc_path: str | None) -> dict:
    if doc_path is None:
        raise StageError(kind=ErrorKind.BAD_INPUT, message="requires 'doc_path' (from resolve)")
    path = Path(doc_path)
    if not path.exists():
        raise StageError(kind=ErrorKind.BAD_INPUT, message=f"Document input not found: {doc_path}")
    return json.loads(path.read_bytes())


@stage(
    name="epub",
    version=1,
    inputs={"doc_path": "doc-effective/1"},
    outputs={"epub": "epub/1"},
    # `doc_path` is NOT root: it comes from `resolve`, so an EPUB can only be
    # produced for a document that already passed ast-assemble's text-
    # integrity gate. Same posture as `idml`'s `doc_path`.
    terminal=True,
    toolchain=[],
    fixtures=None,
    memory_budget_mb=128,
    queue="q.composition",
    description="Emit an EPUB 3 package from the resolved document",
)
def epub(ctx: StageCtx, doc_path: str | None = None) -> StageResult:
    doc = _load_doc(doc_path)
    work = Path(ctx.work_dir) / "epub"
    work.mkdir(parents=True, exist_ok=True)
    out_path = EPUB3Writer(doc).write(work / "book.epub")
    data = out_path.read_bytes()

    cas = ContentAddressedStore(CasConfig(local_cache_root=Path(ctx.cas_root)))
    ref = cas.put(data, media_type=MediaType(MediaType.APPLICATION_EPUB))

    chapters = len(doc.get("body") or [])
    print(f"  [epub] {chapters} chapter(s) -> {ref.hash} ({len(data)} bytes)")

    return StageResult(
        artifacts=[StageArtifactRef(
            kind="epub",   # must exactly equal the declared output key "epub"
            hash=str(ref.hash),
            media_type=str(MediaType.APPLICATION_EPUB),
            size=len(data),
        )],
        metrics={"output_size_bytes": float(len(data)), "chapters": float(chapters)},
    )


@stage(
    name="onix",
    version=1,
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
