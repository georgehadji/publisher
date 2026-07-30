"""
Package stage.

Assembles all artifacts into a build manifest. Terminal: this is the last stage
in the pipeline, consumed by delivery/download, not by another stage.
"""

from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

from publisher_stages import stage, StageCtx, StageResult, StageError, ErrorKind, ArtifactRef as StageArtifactRef, get_registry
from publisher_cas import ContentAddressedStore, CasConfig, MediaType


@stage(
    name="package",
    version=3,
    # `preflight_report` is a required, non-root input: its schema (preflight/1) is
    # produced only by the `preflight` stage, so the DAG makes it structurally
    # impossible to reach `package` without a preflight verdict having run first
    # (BUILD_PLAN.md F2.3). Previously `package` also declared a `manifest` root
    # input, but the function body never actually read it (it built its own manifest
    # dict internally and swallowed everything else via **kwargs) -- a vestigial
    # declared input that, once the local executor started refusing to run stages
    # whose declared root inputs are unsupplied, would have wrongly excluded
    # `package` from every build. Removed rather than faked a value for it.
    inputs={"preflight_report": "preflight/1"},
    outputs={"build-report": "build-report/1"},
    toolchain=[],
    fixtures=None,
    terminal=True,
    memory_budget_mb=64,
    queue="q.default",
    description="Assemble build manifest and produce final report",
)
def package(ctx: StageCtx, preflight_report: str | None = None, **kwargs) -> StageResult:
    """
    Package stage -- produce the final build report.

    Reads the preflight verdict and surfaces it in the manifest. The mere PRESENCE
    of `preflight_report` as a required input is what makes the gate unbypassable
    (a StageError raised by `preflight` halts the executor before `package` ever
    runs); reading its content here is belt-and-suspenders, not the actual
    enforcement mechanism.
    """
    if preflight_report is None:
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message="package requires 'preflight_report' (from preflight) -- a "
                    "build cannot be packaged without a preflight verdict.",
        )
    preflight_path = Path(preflight_report)
    if not preflight_path.exists():
        raise StageError(kind=ErrorKind.BAD_INPUT, message=f"Preflight report not found: {preflight_report}")
    preflight_data = json.loads(preflight_path.read_bytes())

    now = datetime.now(timezone.utc).isoformat()

    # Derived from the live registry rather than hand-copied, so this can't silently
    # drift out of date the way the previous hardcoded dict did (it still listed
    # stage names -- "structure" -- that no longer exist after F2.1's rewire).
    stage_versions = {decl.name: decl.version for decl in get_registry().all()}

    manifest = {
        "schema": "manifest/1",
        "buildId": ctx.build_id,
        "toolchain": {
            "images": {},
            "engines": {"pipeline": "tracer-bullet-v1"},
        },
        "stageVersions": stage_versions,
        "preflightVerdict": {
            "status": preflight_data.get("status"),
            "summary": preflight_data.get("summary"),
        },
        "stages": [],
        "reproductionKey": "tracer-bullet-v1",
        "startedAt": now,
        "completedAt": now,
    }

    manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")

    cas_root = Path(ctx.work_dir) / ".cas"
    cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))
    ref = cas.put(manifest_bytes, media_type=MediaType("application/json"))

    print(f"  [package] Build manifest produced -> {ref.hash} "
          f"(preflight: {preflight_data.get('status', '?')})")
    print(f"  [package] Tracer bullet complete!")

    return StageResult(
        artifacts=[StageArtifactRef(
            kind="build-report",   # must exactly equal the declared output key
            hash=str(ref.hash),
            media_type="application/json",
            size=len(manifest_bytes),
        )],
        metrics={"manifest_size": len(manifest_bytes)},
    )
