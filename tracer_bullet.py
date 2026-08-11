"""
Tracer Bullet -- DAG Executor.

Orchestrates stage execution using the stage registry.
Wires upstream stage outputs into downstream stage inputs
by matching declared input/output schema IDs.

In production, this will be backed by Postgres advisory locks -> Temporal.
"""

from __future__ import annotations

import hashlib
import os
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
from publisher_stages import ArtifactRef as StageArtifactRef
from publisher_cas import ContentAddressedStore, CasConfig, Sha256, ArtifactRef, MediaType
from publisher_cache import CacheStore, SqliteCacheStore, compute_cache_key, compute_toolchain_digest


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
            # Selection-blindness guard: a deselected alternative (e.g. the
            # fixture loader `acquire` when `ingest` is selected) must not be a
            # producer here -- derive_dag()/check_integrity() already exclude
            # it, and a half-updated initial_inputs key must fail loudly rather
            # than silently execute the deselected implementation.
            if not self._registry._is_active(decl.name):
                continue
            for schema_id in decl.outputs.values():
                producers.setdefault(schema_id, []).append(decl.name)

        reachable: set[str] = set()
        changed = True
        while changed:
            changed = False
            for decl in self._registry.all():
                if decl.name in reachable:
                    continue
                if not self._registry._is_active(decl.name):
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

    @staticmethod
    def _hash_input_value(value: Any) -> str:
        """Content hash of a stage input for cache-key purposes -- file
        contents when the value is a path to a real file, else its repr.
        Not a stand-in for a real params/inputs split (A4.3); it's what the
        call site actually has available today."""
        if isinstance(value, (str, Path)):
            p = Path(value)
            if p.is_file():
                return hashlib.sha256(p.read_bytes()).hexdigest()
        return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()

    def _cache_key_for(self, stage_name: str, decl, stage_inputs: dict) -> str:
        input_hashes = sorted(self._hash_input_value(v) for v in stage_inputs.values())
        # ponytail: toolchain/params tracking is a fixed placeholder until A1's
        # follow-on work records real image/font/engine versions (out of the 14
        # findings this plan closes) -- a toolchain change will not invalidate
        # this key yet. Correct for identical-input reruns, which is what the
        # detector (and this executor's only caller today) actually needs.
        toolchain = compute_toolchain_digest(
            image_digests={}, fontset_hash="", icc_hashes={},
            hyphen_dict_versions={}, engine_semvers={},
        )
        return compute_cache_key(
            stage=stage_name, version=decl.version, inputs=input_hashes,
            params={}, toolchain=toolchain,
        )

    def _register_artifacts(self, decl, stage_name: str, result: StageResult,
                             cas: ContentAddressedStore, cas_root: Path,
                             artifact_paths: dict[str, str]) -> None:
        """
        Store output CAS paths for downstream stages, keyed by schema_id.

        Matching is EXACT (art.kind == out_key), never a fuzzy prefix guess
        or a "first declared output" fallback. The prior fuzzy/fallback logic
        was a D8-banned silent fallback in practice: `finish`'s "finished-pdf"
        and "finish-report" kinds both matched neither exactly nor by prefix,
        so BOTH fell through to "first declared output schema" — meaning
        finish-report/1 was silently never stored under its own schema at
        all (the pdf's fallback claimed the only free slot first). A stage
        whose artifact `kind` doesn't match its declared output key is a bug
        in that stage, not something the executor should paper over.
        """
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
                    h = art.hash
                    candidate = cas_root / h[:2] / h[2:4] / h
                    if candidate.exists():
                        artifact_paths[matched_schema] = str(candidate)
                    else:
                        artifact_paths[matched_schema] = art.hash

    def execute(
        self,
        build_id: str,
        initial_inputs: dict[str, Any] | None = None,
        cas_root: Path | str | None = None,
        cache_store: CacheStore | None = None,
        on_stage_complete: Any = None,
        on_stage_error: Any = None,
    ) -> dict[str, StageResult]:
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
            cas_root: Durable CAS root. Defaults to $PUBLISHER_CAS_ROOT or
                      ./.publisher/cas -- ARCHITECTURE_REMEDIATION.md A1.1.
                      No artifact registered in a StageResult lives in a
                      temp dir; only genuinely scratch work does.
            cache_store: Cache index to check/populate per stage. Defaults to
                      a SqliteCacheStore colocated with cas_root -- A1.2.
                      The worker (production path) passes a PostgresCacheStore
                      instead, so the index is visible across processes.
            on_stage_complete: Optional callback
                      `(stage_name, decl, result, duration_ms) -> None`, invoked
                      as each stage finishes rather than after the whole build.
                      The worker persists build_stages rows through this: doing
                      it only at the end meant a build that failed at stage 8
                      recorded ZERO stages (losing exactly the progress needed
                      to diagnose it), and the SSE events endpoint -- which
                      polls build_stages for live progress -- never saw a row
                      until the build was already over.
            on_stage_error: Optional callback
                      `(stage_name, decl, error, duration_ms) -> None`, invoked
                      when a stage raises StageError (or crashes into one) so
                      the worker can record a FAILED build_stages row for the
                      stage that actually failed -- without it, a build failing
                      at stage N has no per-stage row at all, only the build's
                      terminal error_message.

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

        cas_root_path = Path(cas_root) if cas_root is not None else Path(
            os.environ.get("PUBLISHER_CAS_ROOT", "./.publisher/cas")
        )
        cas_root_path.mkdir(parents=True, exist_ok=True)
        cas = ContentAddressedStore(CasConfig(local_cache_root=cas_root_path))
        if cache_store is None:
            cache_store = SqliteCacheStore(cas_root_path / "cache_index.sqlite")

        results: dict[str, StageResult] = {}
        # schema_id -> filesystem path to the artifact in CAS
        artifact_paths: dict[str, str] = {}

        with tempfile.TemporaryDirectory(prefix=f"pub-build-{build_id}-") as work_dir:
            work_dir_path = Path(work_dir)

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
                    cas_root=str(cas_root_path),
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

                cache_key = self._cache_key_for(stage_name, decl, stage_inputs)
                cached = cache_store.get(cache_key)
                if cached is not None:
                    result = StageResult(
                        artifacts=[
                            StageArtifactRef(kind=kind, hash=ref["sha256"],
                                              media_type=ref["media_type"], size=ref["size"])
                            for kind, ref in cached["output_refs"].items()
                        ],
                        cache_hit=True,
                    )
                    results[stage_name] = result
                    self._register_artifacts(decl, stage_name, result, cas, cas_root_path, artifact_paths)
                    elapsed = time.monotonic() - start
                    if on_stage_complete:
                        on_stage_complete(stage_name, decl, result, int(elapsed * 1000))
                    print(f"  HIT {stage_name} cache hit in {elapsed:.2f}s -> {len(result.artifacts)} artifacts")
                    print()
                    continue

                try:
                    result = decl.fn(ctx, **stage_inputs)
                    elapsed = time.monotonic() - start
                    results[stage_name] = result

                    self._register_artifacts(decl, stage_name, result, cas, cas_root_path, artifact_paths)
                    cache_store.put(
                        cache_key, stage_name, decl.version,
                        {art.kind: {"sha256": art.hash, "media_type": art.media_type, "size": art.size}
                         for art in result.artifacts},
                    )

                    if on_stage_complete:
                        on_stage_complete(stage_name, decl, result, int(elapsed * 1000))

                    n_artifacts = len(result.artifacts)
                    metrics_str = ", ".join(f"{k}={v}" for k, v in result.metrics.items())
                    print(f"  OK {stage_name} done in {elapsed:.2f}s -> {n_artifacts} artifacts, {metrics_str}")

                except StageError as e:
                    elapsed = time.monotonic() - start
                    print(f"  FAIL {stage_name} FAILED after {elapsed:.2f}s: [{e.kind}] {e.message}")
                    for d in e.diagnostics:
                        print(f"     {d.severity}: {d.human_message}")
                    if on_stage_error:
                        on_stage_error(stage_name, decl, e, int(elapsed * 1000))
                    raise
                except Exception as e:
                    elapsed = time.monotonic() - start
                    print(f"  FAIL {stage_name} CRASHED after {elapsed:.2f}s: {type(e).__name__}: {e}")
                    wrapped = StageError(
                        kind=ErrorKind.ENGINE_BUG,
                        message=f"Unexpected error in {stage_name}: {e}",
                    )
                    if on_stage_error:
                        on_stage_error(stage_name, decl, wrapped, int(elapsed * 1000))
                    raise wrapped

                print()

        print(f"[executor] Build complete: {len(results)} stages")
        return results


def run_tracer_bullet(manuscript: str | None = None, profile: str = "Generic 6x9"):
    """Run the full tracer bullet pipeline.

    `manuscript` is a path to an `ast/1` JSON document; it defaults to the
    synthetic corpus. Pass a real one to exercise the pipeline against an
    actual book (see services/ingest for DOCX -> AST conversion).

    `profile` names a vendor profile from profiles/*/*.yaml -- e.g.
    "Greek 17x24". It drives trim size and bleed for the whole build.
    """
    # A single `import stages` registers every stage (stages/__init__.py owns that
    # list). Previously this function and platform/stages/integrity.py each hand-
    # maintained their own import list, and the two had silently diverged: this list
    # never imported stages.prepress_stages, so `preflight` — the pipeline's one hard
    # gate — was never part of the executed DAG at all. The tracer bullet printed
    # "PASSED" for a build that never reached its gate. One list, imported everywhere.
    import stages  # noqa: F401 — import for its registration side effect

    registry = get_registry()
    # The registry default-selects `ingest` (U2: a build renders what the tenant
    # submitted, never a fixture). This local harness runs off the synthetic
    # corpus, so it explicitly selects the fixture loader `acquire` instead.
    registry.select_implementation("ingest", "acquire")
    executor = DagExecutor(registry, allow_stub_engines=True)

    print("=" * 60)
    print("  PUBLISHER -- TRACER BULLET")
    print("=" * 60)
    print()

    # Root inputs are keyed by STAGE NAME (executor resolves
    # initial_inputs.get(decl.name)); "finish" is a step whose selected
    # implementation is `finish-gs`, not the bare step name -- keying by
    # "finish" left it unreachable and dropped preflight/package (hard-gate
    # bypass; see worker.py _initial_inputs_for for the full story).
    finish_stage = registry.selected_implementation("finish")

    # Only provide root inputs for stages whose declared inputs
    # are not produced by any other stage.
    initial_inputs = {
        "acquire": {
            "manifest_path": manuscript or "corpus/manuscripts/minimal-novel.ast.json"
        },
        # One profile name, supplied to every stage that has a say in page
        # geometry. design-compile grows the page box by its bleed, finish insets
        # the TrimBox by the same amount, preflight measures the result. Give two
        # of them different profiles and the third will correctly fail the build.
        "design-compile": {"designspec_path": None, "profile_name": profile},
        finish_stage: {"profile_name": profile},
        "preflight": {"profile_name": profile},
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
    sys.exit(run_tracer_bullet(
        sys.argv[1] if len(sys.argv) > 1 else None,
        sys.argv[2] if len(sys.argv) > 2 else "Generic 6x9",
    ))
