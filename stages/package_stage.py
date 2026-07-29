"""
Tracer Bullet -- package stage.

Assembles all artifacts into a build manifest.
"""

from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

from publisher_stages import stage, StageCtx, StageResult, StageError, ErrorKind, ArtifactRef as StageArtifactRef
from publisher_cas import ContentAddressedStore, CasConfig, MediaType


@stage(
    name="package",
    version=1,
    inputs={"manifest": "manifest/1"},
    outputs={"build-report": "build-report/1"},
    toolchain=[],
    fixtures=None,
    terminal=True,
    memory_budget_mb=64,
    queue="q.default",
    description="Assemble build manifest and produce final report",
)
def package(ctx: StageCtx, **kwargs) -> StageResult:
    """
    Package stage -- produce the final build report.
    """
    manifest = {
        "schema": "manifest/1",
        "buildId": ctx.build_id,
        "toolchain": {
            "images": {},
            "engines": {"pipeline": "tracer-bullet-v1"},
        },
        "stageVersions": {"acquire": 1, "extract": 1, "structure": 1, "design-compile": 1, "paginate": 1, "finish": 1, "package": 1},
        "stages": [],
        "reproductionKey": "tracer-bullet-v1",
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "completedAt": datetime.now(timezone.utc).isoformat(),
    }
    
    manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
    
    cas_root = Path(ctx.work_dir) / ".cas"
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))
    ref = cas.put(manifest_bytes, media_type=MediaType("application/json"))
    
    print(f"  [package] Build manifest produced -> {ref.hash}")
    print(f"  [package] Tracer bullet complete!")
    
    return StageResult(
        artifacts=[StageArtifactRef(
            kind="build-manifest",
            hash=str(ref.hash),
            media_type="application/json",
            size=len(manifest_bytes),
        )],
        metrics={"manifest_size": len(manifest_bytes)},
    )
