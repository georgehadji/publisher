"""
Tracer Bullet -- acquire stage.

Simulates manuscript acquisition: copies a fixture into CAS.
In production this would accept a multipart upload.
"""

from __future__ import annotations
import json
from pathlib import Path

from publisher_stages import (
    stage, StageCtx, StageResult, StageError, ErrorKind,
    ArtifactRef as StageArtifactRef,
)
from publisher_cas import ContentAddressedStore, CasConfig, Sha256, MediaType, ArtifactRef


@stage(
    name="acquire",
    version=3,
    inputs={"manifest_path": "fixture-manifest/1"},
    outputs={"source": "raw-source/1"},
    root_inputs=["manifest_path"],
    implements="ingest",  # fixture alternative to `ingest` (U2); default for the local dev harness
    toolchain=[],
    fixtures="fixtures/manuscripts/v1",
    memory_budget_mb=64,
    queue="q.ingest",
    description="Acquire a manuscript fixture into the CAS",
)
def acquire(ctx: StageCtx, manifest_path: str | None = None) -> StageResult:
    """
    Load a fixture manuscript into the content-addressed store.
    
    In the tracer bullet, this takes a fixture path and loads it.
    """
    # Use a local CAS
    cas_root = Path(ctx.cas_root)
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))

    # A requested manuscript that isn't there is a hard error. This used to fall
    # back to the synthetic corpus whenever the path didn't resolve, so a typo, a
    # bad mount or a mangled path silently built a DIFFERENT BOOK and the run
    # still reported success -- the failure mode D8 exists to forbid. Only an
    # absent request (manifest_path=None) may default.
    if manifest_path is None:
        fixture_path = Path("corpus/manuscripts/minimal-novel.ast.json")
        if not fixture_path.exists():
            raise StageError(
                kind=ErrorKind.BAD_INPUT,
                message=f"Default corpus manuscript missing: {fixture_path}",
            )
    else:
        fixture_path = Path(manifest_path)
        if not fixture_path.is_file():
            raise StageError(
                kind=ErrorKind.BAD_INPUT,
                message=f"Manuscript not found: {manifest_path}. Refusing to "
                        "substitute the default corpus for a manuscript that was "
                        "explicitly requested.",
            )

    data = fixture_path.read_bytes()

    # `raw-source/1` is an `ast/1` JSON document despite the schema's name. Check
    # it here, where the offending path can still be named, rather than letting a
    # DOCX or a stray file surface three stages later as a JSON decode error.
    try:
        parsed = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"{fixture_path.name} is not JSON ({exc}). The pipeline ingests "
                    "an ast/1 document; convert a DOCX first (services/ingest).",
        ) from exc

    if not isinstance(parsed, dict) or "body" not in parsed:
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"{fixture_path.name} is not an ast/1 document (no 'body' key).",
        )

    ref = cas.put(data, media_type=MediaType("application/json"))

    print(f"  [acquire] Loaded {fixture_path.name} -> {ref.hash} ({ref.size} bytes, "
          f"{len(parsed.get('body') or [])} chapters)")

    return StageResult(
        artifacts=[StageArtifactRef(
            kind="source",   # must exactly equal the declared output key "source"
            hash=str(ref.hash),
            media_type="application/json",
            size=ref.size,
        )],
        metrics={"size_bytes": ref.size, "load_time_ms": 0},
    )
