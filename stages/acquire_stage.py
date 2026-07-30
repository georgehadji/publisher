"""
Tracer Bullet -- acquire stage.

Simulates manuscript acquisition: copies a fixture into CAS.
In production this would accept a multipart upload.
"""

from __future__ import annotations
import json
from pathlib import Path

from publisher_stages import stage, StageCtx, StageResult, ArtifactRef as StageArtifactRef
from publisher_cas import ContentAddressedStore, CasConfig, Sha256, MediaType, ArtifactRef


@stage(
    name="acquire",
    version=1,
    inputs={"manifest_path": "fixture-manifest/1"},
    outputs={"source": "raw-source/1"},
    root_inputs=["manifest_path"],
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
    cas_root = Path(ctx.work_dir) / ".cas"
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))
    
    # Determine fixture path
    if manifest_path and Path(manifest_path).exists():
        fixture_path = Path(manifest_path)
    else:
        # Use the synthetic corpus as default fixture source
        fixture_path = Path("corpus/manuscripts/minimal-novel.ast.json")
    
    if not fixture_path.exists():
        raise FileNotFoundError(f"Fixture not found: {fixture_path}")
    
    data = fixture_path.read_bytes()
    ref = cas.put(data, media_type=MediaType("application/json"))
    
    print(f"  [acquire] Loaded {fixture_path.name} -> {ref.hash} ({ref.size} bytes)")
    
    return StageResult(
        artifacts=[StageArtifactRef(
            kind="source",   # must exactly equal the declared output key "source"
            hash=str(ref.hash),
            media_type="application/json",
            size=ref.size,
        )],
        metrics={"size_bytes": ref.size, "load_time_ms": 0},
    )
