"""
Tracer Bullet -- DAG Executor.

Orchestrates stage execution using the stage registry.
Wires upstream stage outputs into downstream stage inputs
by matching declared input/output schema IDs.

In production, this will be backed by Postgres advisory locks -> Temporal.
"""

from __future__ import annotations

import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from publisher_stages import (
    StageRegistry, StageCtx, StageResult, StageError,
    ErrorKind, get_registry,
)
from publisher_cas import ContentAddressedStore, CasConfig, Sha256, ArtifactRef, MediaType


class DagExecutor:
    """
    Executes stages in dependency order (topological sort).
    
    Wires upstream stage outputs into downstream stage inputs
    by resolving declared input parameter names to upstream
    artifact references stored in the shared CAS.
    """
    
    def __init__(self, registry: Optional[StageRegistry] = None):
        self._registry = registry or get_registry()
    
    def execute(self, build_id: str, initial_inputs: dict[str, Any] | None = None) -> dict[str, StageResult]:
        """
        Execute all stages in topological order.
        
        For each stage, resolves its declared input parameters:
        1. If an initial_input is provided for this stage, use it directly
        2. Otherwise, look for matching output artifacts from already-executed 
           upstream stages by matching the declared input schema ID
        
        Args:
            build_id: Unique build identifier
            initial_inputs: Root inputs keyed by stage name -> keyword args.
                           Only provide inputs for stages whose inputs are not
                           produced by any other registered stage.
            
        Returns:
            dict of stage_name -> StageResult
        """
        order = self._registry.topological_sort()
        if not order:
            print("[executor] No stages registered -- nothing to do.")
            return {}
        
        print(f"[executor] Build {build_id}: {len(order)} stages to execute")
        print(f"[executor] Order: {' -> '.join(order)}")
        print()
        
        results: dict[str, StageResult] = {}
        # schema_id -> filesystem path to the artifact in CAS
        artifact_paths: dict[str, str] = {}
        
        with tempfile.TemporaryDirectory(prefix=f"pub-build-{build_id}-") as work_dir:
            work_dir_path = Path(work_dir)
            cas_root = work_dir_path / ".cas"
            cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root))
            
            for stage_name in order:
                decl = self._registry.get(stage_name)
                if decl is None:
                    print(f"  !! Stage '{stage_name}' registered but not found -- skipping")
                    continue
                
                print(f"  -- {stage_name} (v{decl.version}) --")
                start = time.monotonic()
                
                # Build context
                ctx = StageCtx(
                    build_id=build_id,
                    cache_key=f"tb-{stage_name}-v{decl.version}",
                    deadline=datetime.now(timezone.utc),
                    memory_budget_mb=decl.memory_budget_mb,
                    work_dir=str(work_dir_path),
                )
                
                # Resolve inputs for this stage:
                # 1. Start with explicit initial_inputs (root params like fixture paths)
                stage_inputs = {}
                if initial_inputs and stage_name in initial_inputs:
                    stage_inputs.update(initial_inputs[stage_name])
                
                # 2. For each declared input param, check if an upstream stage produced it
                for param_name, schema_id in decl.inputs.items():
                    if param_name in stage_inputs:
                        continue
                    if schema_id in artifact_paths:
                        stage_inputs[param_name] = artifact_paths[schema_id]
                
                try:
                    result = decl.fn(ctx, **stage_inputs)
                    elapsed = time.monotonic() - start
                    results[stage_name] = result
                    
                    # Store output CAS paths for downstream stages.
                    # Each artifact in the result has a hash; resolve it to a
                    # filesystem path in the shared CAS and map it by schema_id.
                    for art in result.artifacts:
                        matched_schema = None
                        for out_key, out_schema in decl.outputs.items():
                            # Match artifact kind to output key (exact or prefix)
                            if art.kind == out_key or art.kind.startswith(out_key.split('/')[0]):
                                matched_schema = out_schema
                                break
                        if matched_schema is None:
                            # Fallback: use the first declared output schema
                            for out_schema in decl.outputs.values():
                                matched_schema = out_schema
                                break
                        
                        if matched_schema and matched_schema not in artifact_paths:
                            # Try to resolve the hash to a local CAS path
                            resolved = False
                            try:
                                sha = Sha256(art.hash)
                                art_ref = ArtifactRef(
                                    hash=sha,
                                    media_type=MediaType(art.media_type),
                                    size=art.size,
                                )
                                artifact_paths[matched_schema] = str(cas.get_path(art_ref))
                                resolved = True
                            except Exception:
                                pass
                            
                            if not resolved:
                                # Construct the sharded CAS path directly
                                h = art.hash
                                candidate = cas_root / h[:2] / h[2:4] / h
                                if candidate.exists():
                                    artifact_paths[matched_schema] = str(candidate)
                                else:
                                    artifact_paths[matched_schema] = art.hash
                    
                    n_artifacts = len(result.artifacts)
                    metrics_str = ", ".join(f"{k}={v}" for k, v in result.metrics.items())
                    print(f"  OK {stage_name} done in {elapsed:.2f}s -> {n_artifacts} artifacts, {metrics_str}")
                    
                except StageError as e:
                    elapsed = time.monotonic() - start
                    print(f"  FAIL {stage_name} FAILED after {elapsed:.2f}s: [{e.kind}] {e.message}")
                    for d in e.diagnostics:
                        print(f"     {d.severity}: {d.human_message}")
                    raise
                except Exception as e:
                    elapsed = time.monotonic() - start
                    print(f"  FAIL {stage_name} CRASHED after {elapsed:.2f}s: {type(e).__name__}: {e}")
                    raise StageError(
                        kind=ErrorKind.ENGINE_BUG,
                        message=f"Unexpected error in {stage_name}: {e}",
                    )
                
                print()
        
        print(f"[executor] Build complete: {len(results)} stages")
        return results


def run_tracer_bullet():
    """Run the full tracer bullet pipeline."""
    import stages.acquire_stage
    import stages.extract_stage
    import stages.structure_stage
    import stages.design_compile_stage
    import stages.paginate_stage
    import stages.finish_stage
    import stages.package_stage
    
    registry = get_registry()
    executor = DagExecutor(registry)
    
    print("=" * 60)
    print("  PUBLISHER -- TRACER BULLET")
    print("=" * 60)
    print()
    
    # Only provide root inputs for stages whose declared inputs
    # are not produced by any other stage.
    initial_inputs = {
        "acquire": {"manifest_path": "corpus/manuscripts/minimal-novel.ast.json"},
        "design-compile": {"designspec_path": None},
    }
    
    try:
        results = executor.execute(
            build_id=f"tb-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            initial_inputs=initial_inputs,
        )
        print("OK TRACER BULLET PASSED")
        return 0
    except StageError as e:
        print(f"\nFAILED: TRACER BULLET FAILED: [{e.kind}] {e.message}")
        return 1
    except Exception as e:
        print(f"\nCRASHED: TRACER BULLET CRASHED: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(run_tracer_bullet())
