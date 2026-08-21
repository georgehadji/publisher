"""
Ingest stage -- real manuscript acquisition (docs/ARCHITECTURE_UPLIFT_PLAN.md U2).

`acquire` loads a fixture ast/1 JSON straight into the CAS -- the tracer-bullet
path for a machine with no real manuscript storage. This stage is the production
alternative: it takes the DOCX bytes the API's upload route stored in CAS and
converts them to the same `raw-source/1` (an ast/1 JSON document) via
services/ingest's `docx_to_ast`.

Both stages declare `implements="ingest"` -- they are alternative
implementations of one logical step. Exactly one is selected per process:
`stages/__init__.py` defaults to this stage (a build must render what the
tenant submitted, never a fixture); the local dev harness (tracer_bullet.py)
explicitly selects `acquire` so it can keep running off the synthetic corpus.

The DAG derives the new `ingest -> extract` edge automatically: `extract`
consumes `raw-source/1`, which this stage produces. No executor change was
needed -- that is the registry design (derived DAG) paying off.
"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

from publisher_ingest.docx_to_ast import IngestError, docx_to_ast
from publisher_stages import (
    stage, StageCtx, StageResult, StageError, ErrorKind,
    ArtifactRef as StageArtifactRef,
)
from publisher_cas import ContentAddressedStore, CasConfig, MediaType

# U5/S9 -- zip-bomb caps. A DOCX is a ZIP archive; a hostile file can declare
# huge uncompressed sizes or a huge entry count while being small on the wire
# (the API already caps the uploaded BYTES). python-docx then decompresses on
# parse -- cap what it is allowed to decompress here, where the bytes are about
# to be trusted. Inspecting the central directory (infolist) reads no entry
# data, so the check is cheap.
MAX_DOCX_ENTRIES = int(os.environ.get("PUBLISHER_MAX_DOCX_ENTRIES", "4096"))
MAX_DOCX_DECOMPRESSED = int(os.environ.get("PUBLISHER_MAX_DOCX_DECOMPRESSED_BYTES", str(256 * 1024 * 1024)))


def _check_zip_limits(source: Path) -> None:
    try:
        with zipfile.ZipFile(source) as zf:
            infos = zf.infolist()
    except zipfile.BadZipFile as e:
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"{source.name} is not a valid ZIP/DOCX archive: {e}",
        ) from e
    if len(infos) > MAX_DOCX_ENTRIES:
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"{source.name} has {len(infos)} entries, over the "
                    f"{MAX_DOCX_ENTRIES} cap -- refusing to decompress a zip bomb",
        )
    total = sum(i.file_size for i in infos)
    if total > MAX_DOCX_DECOMPRESSED:
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"{source.name} declares {total} bytes uncompressed, over the "
                    f"{MAX_DOCX_DECOMPRESSED} cap -- refusing to decompress a zip bomb",
        )


@stage(
    name="ingest",
    # v2: `docx_to_ast` now reads word/footnotes.xml, takes its section outline
    # from the contents page, and emits a linked `toc`. The version lint watches
    # stages/*.py and so did not ask for this bump -- the change is one directory
    # over, in services/ingest -- but the ARTIFACT is what the cache keys on, and
    # a v1 AST for a manuscript with footnotes is missing every one of them.
    # Replaying one would silently reinstate the loss this bump exists to end.
    version=2,
    inputs={"docx_path": "raw-docx/1", "blank_leading_pages": "page-count/1"},
    outputs={"source": "raw-source/1"},
    root_inputs=["docx_path", "blank_leading_pages"],
    # Optional, so a build that says nothing about front leaves stays reachable
    # and gets none. Declared rather than inferred from the parameter default --
    # the same distinction `resolve`'s `overrides_path` makes.
    optional_root_inputs=["blank_leading_pages"],
    implements="ingest",  # alternative to `acquire`; see module docstring
    toolchain=[],
    fixtures=None,
    memory_budget_mb=128,
    queue="q.ingest",
    description="Convert an uploaded DOCX manuscript into the ast/1 source",
)
def ingest(ctx: StageCtx, docx_path: str | None = None,
           blank_leading_pages: int | str | None = None) -> StageResult:
    """
    Convert a tenant's DOCX (bytes stored in CAS by the upload route) into an
    ast/1 document, and store that as `raw-source/1` for the rest of the DAG.

    Raises `StageError(BAD_INPUT)` -- never a silent fixture substitution --
    when the bytes are missing or cannot be faithfully converted. The text
    must survive the conversion verbatim: `docx_to_ast` enforces its own
    no-loss post-condition, and `ast-assemble` re-checks losslessness against
    this AST later in the build.
    """
    cas_root = Path(ctx.cas_root)
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))

    if not docx_path:
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message="ingest requires 'docx_path' (the CAS path of the uploaded "
                    "manuscript bytes). Refusing to substitute a fixture for a "
                    "manuscript that was never uploaded.",
        )
    source = Path(docx_path)
    if not source.is_file():
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"Uploaded manuscript bytes not found at {docx_path}. The "
                    "upload is incomplete or the API and worker are looking at "
                    "different CAS roots.",
        )

    # U5/S9 -- zip-bomb and malformed-archive guard before python-docx trusts
    # the bytes.
    _check_zip_limits(source)

    try:
        leaves = int(blank_leading_pages or 0)
    except (TypeError, ValueError):
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"blank_leading_pages must be an integer, got "
                    f"{blank_leading_pages!r}",
        )
    if leaves < 0:
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"blank_leading_pages cannot be negative (got {leaves})",
        )

    try:
        ast = docx_to_ast(
            source, manuscript_id=ctx.build_id, blank_leading_pages=leaves
        )
    except IngestError as e:
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"DOCX could not be ingested: {e}",
        ) from e

    data = json.dumps(ast, ensure_ascii=False).encode("utf-8")
    ref = cas.put(data, media_type=MediaType("application/json"))

    print(f"  [ingest] {source.name} -> {ref.hash} ({ref.size} bytes, "
          f"{len(ast.get('body') or [])} chapters)")

    return StageResult(
        artifacts=[StageArtifactRef(
            kind="source",   # must exactly equal the declared output key "source"
            hash=str(ref.hash),
            media_type="application/json",
            size=ref.size,
        )],
        metrics={"size_bytes": ref.size, "chapters": float(len(ast.get("body") or []))},
    )
