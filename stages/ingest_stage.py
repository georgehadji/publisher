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
import shutil
import subprocess
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


# Legacy binary Word (.doc) is an OLE2 compound file, not a ZIP. python-docx
# cannot read it and no pure-Python library can; LibreOffice headless is the
# converter of record (docs/ARCHITECTURE.md §1.2 stage 3, `legacy-convert`).
# Done inline rather than as its own stage on purpose: a separate stage
# producing raw-docx/1 would become a mandatory DAG edge for every build,
# including the 95% that upload a real .docx.
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
SOFFICE_TIMEOUT_S = int(os.environ.get("PUBLISHER_SOFFICE_TIMEOUT_S", "180"))


def _is_legacy_doc(source: Path) -> bool:
    with source.open("rb") as fh:
        return fh.read(8) == _OLE2_MAGIC


def _convert_legacy_doc(source: Path, work_dir: Path) -> Path:
    """Convert a legacy `.doc` to `.docx` with LibreOffice headless.

    The conversion is not lossless in the way the rest of the pipeline is: it
    is the point where a 1997 binary format becomes something the AST gate can
    reason about. Everything downstream -- including ast-assemble's text
    integrity check -- then runs against the CONVERTED document, so a
    conversion that dropped text still fails the build; it just fails at the
    gate rather than here.
    """
    soffice = (os.environ.get("PUBLISHER_SOFFICE_BIN")
               or shutil.which("soffice") or shutil.which("libreoffice"))
    if not soffice:
        raise StageError(
            kind=ErrorKind.INFRA,
            message="uploaded manuscript is a legacy binary .doc, and LibreOffice "
                    "('soffice') is not installed to convert it. Install LibreOffice "
                    "on the worker, or upload the manuscript as .docx.",
        )

    out_dir = work_dir / "legacy-convert"
    out_dir.mkdir(parents=True, exist_ok=True)
    # LibreOffice needs a WRITABLE user profile; the worker's rootfs is
    # read-only, so point it at the build's scratch dir instead of $HOME.
    user_profile = (work_dir / "lo-profile")
    user_profile.mkdir(parents=True, exist_ok=True)

    try:
        proc = subprocess.run(
            [soffice, "--headless", "--norestore", "--nolockcheck",
             f"-env:UserInstallation={user_profile.as_uri()}",
             "--convert-to", "docx", "--outdir", str(out_dir), str(source)],
            capture_output=True, timeout=SOFFICE_TIMEOUT_S,
        )
    except OSError as e:
        raise StageError(kind=ErrorKind.INFRA,
                         message=f"LibreOffice could not be run: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise StageError(
            kind=ErrorKind.INFRA,
            message=f"LibreOffice .doc conversion timed out after "
                    f"{SOFFICE_TIMEOUT_S}s",
        ) from e

    converted = sorted(out_dir.glob("*.docx"))
    if proc.returncode != 0 or not converted:
        tail = (proc.stderr or b"").decode("utf-8", "replace").strip()[-1000:]
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"LibreOffice could not convert the uploaded .doc to .docx "
                    f"(exit {proc.returncode}): {tail or 'no output produced'}",
        )
    print(f"  [ingest] legacy .doc -> {converted[0].name} via LibreOffice")
    return converted[0]


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
    # v2: a legacy binary .doc is converted to .docx with LibreOffice before
    # parsing. v1 rejected those bytes outright, so no v1 result may be replayed
    # for an upload this version now accepts.
    version=2,
    inputs={"docx_path": "raw-docx/1"},
    outputs={"source": "raw-source/1"},
    root_inputs=["docx_path"],
    implements="ingest",  # alternative to `acquire`; see module docstring
    toolchain=[],
    fixtures=None,
    memory_budget_mb=128,
    queue="q.ingest",
    description="Convert an uploaded DOCX manuscript into the ast/1 source",
)
def ingest(ctx: StageCtx, docx_path: str | None = None) -> StageResult:
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

    # A legacy binary .doc becomes a .docx first; everything after this point
    # sees one format. The zip-bomb guard then runs on the CONVERTED file,
    # which is the archive python-docx is about to decompress.
    if _is_legacy_doc(source):
        source = _convert_legacy_doc(source, Path(ctx.work_dir))

    # U5/S9 -- zip-bomb and malformed-archive guard before python-docx trusts
    # the bytes.
    _check_zip_limits(source)

    def store_media(data: bytes, media_type: str, name: str) -> str:
        """Give an embedded image a home in CAS and return its sha256.

        This has to happen during ingest: the AST references figures by hash
        alone, and by the time any later stage sees that AST the DOCX is gone
        and the bytes are unrecoverable.
        """
        return str(cas.put(data, media_type=MediaType(media_type)).hash)

    try:
        ast = docx_to_ast(source, manuscript_id=ctx.build_id, store_media=store_media)
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
