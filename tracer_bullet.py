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
    
    def __init__(self, registry: Optional[StageRegistry] = None, allow_stub_engines: bool = False):
        self._registry = registry or get_registry()
        # Only the local dev/tracer harness sets this True. See StageCtx.allow_stub_engines.
        self._allow_stub_engines = allow_stub_engines

    def _reachable_stages(self, initial_inputs: dict[str, Any] | None) -> set[str]:
        """
        The subset of registered stages this build can actually run: every root
        input it declares is supplied in `initial_inputs`, and every non-root input
        is produced by another stage that is itself reachable (fixpoint).

        WHY THIS EXISTS
        `import stages` (F2.3) registers every stage module, including the whole
        cover-art brief/generate/judge/compose pipeline (stages/cover_stages.py) and
        the cover-preflight/finish-gs alternates -- none of which this tracer bullet
        supplies root inputs for. Without this filter, `execute()` would attempt
        EVERY registered stage regardless of whether its inputs are satisfiable, and
        one unrelated, out-of-scope stage raising `bad_input` for a missing root
        input would abort the entire interior-book build. A real orchestrator fans
        out per output profile (BUILD_PLAN.md §3.13); this is the local-executor
        equivalent -- run what's reachable from the supplied roots, not the entire
        registry.
        """
        initial_inputs = initial_inputs or {}
        producers: dict[str, list[str]] = {}
        for decl in self._registry.all():
            for schema_id in decl.outputs.values():
                producers.setdefault(schema_id, []).append(decl.name)

        reachable: set[str] = set()
        changed = True
        while changed:
            changed = False
            for decl in self._registry.all():
                if decl.name in reachable:
                    continue
                root_set = set(decl.root_inputs or [])
                supplied = set(initial_inputs.get(decl.name, {}).keys())
                optional = set(decl.optional_root_inputs or [])
                # A root input missing from initial_inputs blocks reachability
                # UNLESS the stage explicitly declared it optional (`resolve`'s
                # `overrides_path`, where absence legitimately means "zero
                # overrides"). Deliberately NOT inferred from whether the Python
                # parameter merely has a default -- `cover`'s `page_count=0` also
                # has a default, but 0 is a placeholder the function rejects
                # outright, not a valid empty state.
                if root_set - supplied - optional:
                    continue
                non_root_ok = True
                for param_name, schema_id in decl.inputs.items():
                    if param_name in root_set:
                        continue
                    prods = producers.get(schema_id, [])
                    if not any(p in reachable for p in prods):
                        non_root_ok = False
                        break
                if non_root_ok:
                    reachable.add(decl.name)
                    changed = True
        return reachable

    def execute(self, build_id: str, initial_inputs: dict[str, Any] | None = None) -> dict[str, StageResult]:
        """
        Execute the subset of registered stages reachable from `initial_inputs`, in
        topological order.

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
        reachable = self._reachable_stages(initial_inputs)
        order = [s for s in self._registry.topological_sort() if s in reachable]
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
                    allow_stub_engines=self._allow_stub_engines,
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
                    #
                    # Matching is EXACT (art.kind == out_key), never a fuzzy prefix guess
                    # or a "first declared output" fallback. The prior fuzzy/fallback logic
                    # was a D8-banned silent fallback in practice: `finish`'s "finished-pdf"
                    # and "finish-report" kinds both matched neither exactly nor by prefix,
                    # so BOTH fell through to "first declared output schema" — meaning
                    # finish-report/1 was silently never stored under its own schema at
                    # all (the pdf's fallback claimed the only free slot first). A stage
                    # whose artifact `kind` doesn't match its declared output key is a bug
                    # in that stage, not something the executor should paper over.
                    for art in result.artifacts:
                        matched_schema = decl.outputs.get(art.kind)
                        if matched_schema is None:
                            raise StageError(
                                kind=ErrorKind.ENGINE_BUG,
                                message=(
                                    f"Stage '{stage_name}' emitted an artifact with kind "
                                    f"'{art.kind}', which matches none of its declared "
                                    f"output keys {sorted(decl.outputs.keys())}. Fix the "
                                    f"stage's StageArtifactRef(kind=...) to exactly match "
                                    f"a declared output key."
                                ),
                            )

                        if matched_schema not in artifact_paths:
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
    # A single `import stages` registers every stage (stages/__init__.py owns that
    # list). Previously this function and platform/stages/integrity.py each hand-
    # maintained their own import list, and the two had silently diverged: this list
    # never imported stages.prepress_stages, so `preflight` — the pipeline's one hard
    # gate — was never part of the executed DAG at all. The tracer bullet printed
    # "PASSED" for a build that never reached its gate. One list, imported everywhere.
    import stages  # noqa: F401 — import for its registration side effect

    registry = get_registry()
    executor = DagExecutor(registry, allow_stub_engines=True)

    print("=" * 60)
    print("  PUBLISHER -- TRACER BULLET")
    print("=" * 60)
    print()

    # Only provide root inputs for stages whose declared inputs
    # are not produced by any other stage.
    initial_inputs = {
        "acquire": {"manifest_path": "corpus/manuscripts/minimal-novel.ast.json"},
        "design-compile": {"designspec_path": None},
        "preflight": {"profile_name": "Generic 6x9"},
    }

    try:
        results = executor.execute(
            build_id=f"tb-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
            initial_inputs=initial_inputs,
        )
        # "Passed" must mean the gates ran, not merely that no stage in whatever
        # subset happened to be wired raised an exception (BUILD_PLAN.md F2.3).
        if "preflight" not in results:
            print("\nFAILED: TRACER BULLET FAILED: 'preflight' never executed — "
                  "a build without a preflight verdict is not a passed build.")
            return 1
        if "package" not in results:
            print("\nFAILED: TRACER BULLET FAILED: 'package' never executed.")
            return 1
        print(f"OK TRACER BULLET PASSED -- preflight verdict: "
              f"{results['preflight'].metrics.get('checks_failed', '?')} check(s) failed, "
              f"gate held")
        return 0
    except StageError as e:
        print(f"\nFAILED: TRACER BULLET FAILED: [{e.kind}] {e.message}")
        return 1
    except Exception as e:
        print(f"\nCRASHED: TRACER BULLET CRASHED: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(run_tracer_bullet())
