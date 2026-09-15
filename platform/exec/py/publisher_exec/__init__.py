"""
publisher_exec -- the DAG executor, split into a pure planning half and an
effectful running half (E1.1, docs/ARCHITECTURE_SCORE_10_PLAN.md).

Previously this all lived in `tracer_bullet.py` as one `DagExecutor.execute()`
method, which entangled reachability/ordering (pure) with CAS writes, cache
lookups and printing (effectful). That made every scheduling decision an
integration test. Splitting it:

    plan(registry, root_inputs) -> ExecutionPlan   # pure -- dict in, dict out
    run(plan, registry, ...)    -> BuildResult      # effectful -- the only
                                                     # half that touches CAS,
                                                     # the cache store, or a
                                                     # clock

`tracer_bullet.py` is now a thin CLI over this package (sets
`allow_stub_engines=True`); `worker.py` is the production caller (does not).

`DagExecutor` is kept as a facade over `plan()` + `run()` for existing call
sites (`worker.py`, `tracer_bullet.py`, integration tests) -- new code should
call `plan()`/`run()` directly, since that is what makes scheduling decisions
unit-testable without a CAS or a socket.

`run()`'s per-stage call is itself a small middleware chain (E2.1,
docs/ARCHITECTURE_SCORE_10_PLAN.md): `cache_mw`, `memory_mw` and
`deadline_mw`, composed once per build and applied to every stage. Before
this, three unrelated defects (an always-already-expired deadline nothing
read, an unenforced memory budget, and a cache key that was really a
placeholder string) all traced back to the same cause -- there was no single
place where a cross-cutting per-stage concern lived, so each was a special
case bolted onto the stage-invocation code inline. `sandbox_mw` (E3.2) and
`admission_mw` (U8) are deliberately NOT here yet: both need capabilities (a
real sandboxed child process; a shared admission-control port) this plan has
not built. Adding them is a matter of extending the list `run()` passes to
`_compose()`, not restructuring it.

`memory_mw` wraps OUTSIDE `deadline_mw`, not inside it as the plan's own
illustrative `CHAIN` example orders them -- deliberately, not an oversight.
`deadline_mw`'s watchdog abandons a timed-out call without waiting for its
background thread; if `memory_mw` were the one running IN that thread (inside
`deadline_mw`), a genuinely hung stage would leave the process's RLIMIT_AS
lowered for as long as the leaked thread keeps running, which for an infinite
loop is forever -- silently starving every later stage in the same
long-lived worker process. With `memory_mw` outside, its own `finally`
restore runs on the caller's thread as soon as `deadline_mw` gives up and
returns, bounding the poisoned window to at most that one stage's
`timeout_s`.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from publisher_stages import (
    StageRegistry, StageDeclaration, StageCtx, StageResult, StageError,
    ErrorKind, get_registry,
)
from publisher_stages import ArtifactRef as StageArtifactRef
from publisher_cas import ContentAddressedStore, CasConfig, Sha256, ArtifactRef, MediaType
from publisher_cache import CacheStore, SqliteCacheStore, compute_cache_key, compute_toolchain_digest

BuildResult = dict[str, StageResult]


@dataclass(frozen=True)
class ExecutionPlan:
    """The pure output of `plan()`: which stages run, in what order.

    Deliberately carries nothing effectful -- no CAS handle, no cache store,
    no clock reading. `run()` re-derives everything else it needs (stage
    declarations, inputs) from the registry it is handed alongside this plan.
    """

    order: tuple[str, ...]


def _reachable_stages(
    registry: StageRegistry, initial_inputs: Mapping[str, Mapping[str, Any]] | None
) -> set[str]:
    """
    The subset of registered stages a build can actually run: every root
    input it declares is supplied in `initial_inputs`, and every non-root
    input is produced by another stage that is itself reachable (fixpoint).

    WHY THIS EXISTS
    `import stages` (F2.3) registers every stage module, including the whole
    cover-art brief/generate/judge/compose pipeline (stages/cover_stages.py)
    and the cover-preflight/finish-gs alternates -- none of which a tracer
    bullet or a single-book worker build supplies root inputs for. Without
    this filter, `run()` would attempt EVERY registered stage regardless of
    whether its inputs are satisfiable, and one unrelated, out-of-scope stage
    raising `bad_input` for a missing root input would abort the entire
    interior-book build. A real orchestrator fans out per output profile
    (BUILD_PLAN.md §3.13); this is the local-executor equivalent -- run
    what's reachable from the supplied roots, not the entire registry.
    """
    initial_inputs = initial_inputs or {}
    producers: dict[str, list[str]] = {}
    for decl in registry.all():
        # Selection-blindness guard: a deselected alternative (e.g. the
        # fixture loader `acquire` when `ingest` is selected) must not be a
        # producer here -- derive_dag()/check_integrity() already exclude
        # it, and a half-updated initial_inputs key must fail loudly rather
        # than silently execute the deselected implementation.
        if not registry._is_active(decl.name):
            continue
        for schema_id in decl.outputs.values():
            producers.setdefault(schema_id, []).append(decl.name)

    reachable: set[str] = set()
    changed = True
    while changed:
        changed = False
        for decl in registry.all():
            if decl.name in reachable:
                continue
            if not registry._is_active(decl.name):
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


def plan(
    registry: StageRegistry, root_inputs: Mapping[str, Mapping[str, Any]] | None = None
) -> ExecutionPlan:
    """Pure: resolve the reachable stage subset and its topological order.

    No CAS is opened, no cache store touched, no clock read, nothing printed
    -- unit-testable with a plain dict of root inputs and the registry the
    caller already has in memory.
    """
    reachable = _reachable_stages(registry, root_inputs)
    order = tuple(s for s in registry.topological_sort() if s in reachable)
    return ExecutionPlan(order=order)


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


def _cache_key_for(stage_name: str, decl, stage_inputs: dict) -> str:
    input_hashes = sorted(_hash_input_value(v) for v in stage_inputs.values())
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


def _register_artifacts(decl, stage_name: str, result: StageResult,
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


@dataclass(frozen=True)
class StageInvocation:
    """Everything one middleware needs to run or short-circuit a single stage
    call (E2.1). Built once per stage in `run()`'s loop; every middleware
    reads it and passes it unchanged to the next one -- none of them mutate
    it or reach into `run()`'s own state for anything but what's threaded
    through here or closed over at construction (`cache_mw`)."""

    stage_name: str
    decl: StageDeclaration
    ctx: StageCtx
    inputs: dict[str, Any]


Next = Callable[[StageInvocation], StageResult]
StageMiddleware = Callable[[StageInvocation, Next], StageResult]


def _compose(chain: list[StageMiddleware], terminal: Next) -> Next:
    """Build one callable: chain[0] outermost, `terminal` innermost."""
    handler = terminal
    for mw in reversed(chain):
        handler = (lambda inv, _mw=mw, _next=handler: _mw(inv, _next))
    return handler


def deadline_mw(invocation: StageInvocation, next_: Next) -> StageResult:
    """Enforces `invocation.ctx.deadline` around the rest of the chain.

    ponytail: a background-thread watchdog, not real preemption -- calling
    this middleware's caller stops waiting at the deadline, but a stage stuck
    in a tight CPU-bound loop keeps its thread alive after that (Python
    cannot forcibly kill a thread). That is a real ceiling, not an oversight:
    true preemption needs E3.2's `RLIMIT_CPU` on a forked, sandboxed child,
    which the plan explicitly defers this middleware to once it lands. Until
    then this at least turns a hang into a classified `TIMEOUT` failure
    instead of blocking the whole build (or worker) indefinitely.
    """
    remaining = (invocation.ctx.deadline - datetime.now(timezone.utc)).total_seconds()
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(next_, invocation)
    try:
        result = future.result(timeout=max(remaining, 0))
    except _FutureTimeoutError:
        pool.shutdown(wait=False)
        raise StageError(
            kind=ErrorKind.TIMEOUT,
            message=(
                f"Stage '{invocation.stage_name}' exceeded its "
                f"{invocation.decl.timeout_s}s deadline"
            ),
            retryable=True,
        )
    pool.shutdown(wait=False)
    return result


def _current_vsize_bytes(resource_mod) -> int:
    """Current process virtual address space, in bytes -- what `RLIMIT_AS`
    actually caps. `/proc/self/statm`'s first field (Linux-only, hence the
    fallback) rather than `RUSAGE_SELF.ru_maxrss`: RSS is resident pages
    actually touched, and undercounts by a lot against something like
    weasyprint's Cairo/Pango bindings, which mmap far more address space than
    they fault in. Padding the RSS-based fallback 2x is a deliberately crude
    stand-in for hosts with no /proc (e.g. macOS), not a claim of precision.
    """
    try:
        with open("/proc/self/statm") as f:
            pages = int(f.read().split()[0])
        return pages * resource_mod.getpagesize()
    except (OSError, ValueError, IndexError):
        return resource_mod.getrusage(resource_mod.RUSAGE_SELF).ru_maxrss * 1024 * 2


def memory_mw(invocation: StageInvocation, next_: Next) -> StageResult:
    """Applies `invocation.decl.memory_budget_mb` as a hard `RLIMIT_AS`
    around the rest of the chain, converting a resulting `MemoryError` into
    `RESOURCE_EXHAUSTED`.

    The limit is CURRENT VIRTUAL SIZE PLUS the declared budget, not the
    budget alone -- found necessary verifying E3.1 against a real 9-stage
    build in one worker process, not by inspection. `run()` executes an
    entire build's stage DAG sequentially in ONE process; `RLIMIT_AS` caps
    that process's TOTAL virtual address space, which only grows as earlier
    stages import their own libraries (ingest's python-docx/lxml, design-
    compile's/paginate's weasyprint/Cairo/Pango, ...). Treating
    `memory_budget_mb` as an absolute ceiling meant a later stage's declared
    budget had to exceed not just its OWN needs but everything every earlier
    stage in the same build had already mapped -- observed directly as
    design-compile's real, tiny CSS-generation work dying with a plain
    `MemoryError` under its declared 64 MB, because the process had already
    mapped more than that just importing weasyprint for the stage after it.
    Reinterpreting the budget as "how much MORE this stage may map, on top
    of what is already resident" is the only reading that survives a
    multi-stage single-process executor.

    POSIX-only -- `resource` does not exist on Windows -- so this is a
    best-effort no-op there, the same convention `platform/sandbox` already
    uses for the same constraint (see its `_preexec`). Only the SOFT limit is
    ever touched: an unprivileged process can lower its hard limit but never
    raise it back, so touching `hard` here would make the restore in
    `finally` unable to undo itself on a second stage in the same process.
    ponytail: process-wide, not per-child -- a budget that can't leak onto
    the *next* stage sharing this process needs E3.2's forked sandbox child.

    DEPLOYMENT REQUIREMENT (also found empirically, not by inspection): the
    process this runs in MUST set `MALLOC_ARENA_MAX=1` in its environment
    before the interpreter starts (Dockerfile.worker does). `deadline_mw`
    runs the stage on a background thread; glibc's malloc gives a new thread
    its own arena on first allocation, reserving tens of MB of virtual
    address space via mmap up front -- exactly what RLIMIT_AS exists to
    block. Under a tight budget this can make the WATCHDOG THREAD's own
    first allocation fail, which libxml2 (reached via python-docx) does not
    always surface as a clean MemoryError -- it was observed as a real,
    valid DOCX failing to parse with lxml's `unknown error (<string>, line
    0)`. Reproduced with and without `MALLOC_ARENA_MAX=1` to confirm it.
    """
    try:
        import resource
    except ImportError:
        return next_(invocation)

    budget_bytes = _current_vsize_bytes(resource) + invocation.decl.memory_budget_mb * 1024 * 1024
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    new_soft = budget_bytes if hard == resource.RLIM_INFINITY else min(budget_bytes, hard)
    try:
        resource.setrlimit(resource.RLIMIT_AS, (new_soft, hard))
    except (ValueError, OSError):
        return next_(invocation)

    try:
        return next_(invocation)
    except MemoryError as e:
        raise StageError(
            kind=ErrorKind.RESOURCE_EXHAUSTED,
            message=(
                f"Stage '{invocation.stage_name}' exceeded its "
                f"{invocation.decl.memory_budget_mb}MB memory budget"
            ),
        ) from e
    finally:
        resource.setrlimit(resource.RLIMIT_AS, (soft, hard))


def _make_cache_mw(
    cache_store: CacheStore, cas: ContentAddressedStore,
    cas_root_path: Path, artifact_paths: dict[str, str],
) -> StageMiddleware:
    """Builds `cache_mw`, closing over this build's own effectful handles so
    the real cache key stays internal to it (E2.3) -- a stage function never
    sees it, only `ctx.deterministic_seed`, which happens to be the same
    value but is understood as a seed, not a lookup key."""

    def cache_mw(invocation: StageInvocation, next_: Next) -> StageResult:
        decl = invocation.decl
        cache_key = invocation.ctx.deterministic_seed
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
            _register_artifacts(decl, invocation.stage_name, result, cas, cas_root_path, artifact_paths)
            return result

        result = next_(invocation)
        _register_artifacts(decl, invocation.stage_name, result, cas, cas_root_path, artifact_paths)
        cache_store.put(
            cache_key, invocation.stage_name, decl.version,
            {art.kind: {"sha256": art.hash, "media_type": art.media_type, "size": art.size}
             for art in result.artifacts},
        )
        return result

    return cache_mw


def run(
    execution_plan: ExecutionPlan,
    registry: StageRegistry,
    *,
    build_id: str,
    initial_inputs: dict[str, Any] | None = None,
    cas_root: Path | str | None = None,
    cache_store: CacheStore | None = None,
    allow_stub_engines: bool = False,
    on_stage_complete: Callable[..., None] | None = None,
    on_stage_error: Callable[..., None] | None = None,
) -> BuildResult:
    """
    Effectful: execute `execution_plan.order` against a real CAS. The only
    half of the executor that touches the world -- opens/creates the CAS
    root, checks and populates the cache store, calls each stage's function,
    and prints progress.

    Args:
        execution_plan: The output of `plan()` -- the stage order to run.
        registry: The same registry `plan()` was called with (stage
                  declarations are looked up here as each stage runs).
        build_id: Unique build identifier.
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
        allow_stub_engines: Dev-only escape hatch threaded into every
                  stage's StageCtx. Only tracer_bullet.py sets this True.
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
    order = execution_plan.order
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

    results: BuildResult = {}
    # schema_id -> filesystem path to the artifact in CAS
    artifact_paths: dict[str, str] = {}

    # E2.1: composed once per build, applied to every stage. cache_mw
    # outermost so a hit short-circuits before deadline/memory enforcement
    # ever runs; deadline_mw innermost (closest to the actual call) so its
    # watchdog thread never runs memory_mw's own setrlimit/restore -- see
    # the module docstring for why that ordering matters.
    handle: Next = _compose(
        [_make_cache_mw(cache_store, cas, cas_root_path, artifact_paths), memory_mw, deadline_mw],
        terminal=lambda inv: inv.decl.fn(inv.ctx, **inv.inputs),
    )

    with tempfile.TemporaryDirectory(prefix=f"pub-build-{build_id}-") as work_dir:
        work_dir_path = Path(work_dir)

        for stage_name in order:
            decl = registry.get(stage_name)
            if decl is None:
                print(f"  !! Stage '{stage_name}' registered but not found -- skipping")
                continue

            print(f"  -- {stage_name} (v{decl.version}) --")
            start = time.monotonic()
            started_at = datetime.now(timezone.utc)

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

            # The real cache key, computed once and handed to the stage as
            # `deterministic_seed` (E2.3) -- cache_mw reads it back off ctx
            # rather than recomputing it, so there is exactly one value, not
            # two things that are supposed to agree.
            seed = _cache_key_for(stage_name, decl, stage_inputs)
            ctx = StageCtx(
                build_id=build_id,
                deterministic_seed=seed,
                deadline=started_at + timedelta(seconds=decl.timeout_s),
                memory_budget_mb=decl.memory_budget_mb,
                work_dir=str(work_dir_path),
                allow_stub_engines=allow_stub_engines,
                cas_root=str(cas_root_path),
            )
            invocation = StageInvocation(stage_name=stage_name, decl=decl, ctx=ctx, inputs=stage_inputs)

            try:
                result = handle(invocation)
                elapsed = time.monotonic() - start
                results[stage_name] = result

                if on_stage_complete:
                    on_stage_complete(stage_name, decl, result, int(elapsed * 1000))

                if result.cache_hit:
                    print(f"  HIT {stage_name} cache hit in {elapsed:.2f}s -> {len(result.artifacts)} artifacts")
                else:
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


class DagExecutor:
    """Facade over `plan()` + `run()` for existing call sites.

    `worker.py`, `tracer_bullet.py` and the integration tests all predate the
    plan/run split and call `DagExecutor(registry, allow_stub_engines).execute(...)`
    as one step. This keeps that call shape working -- new code should call
    `plan()`/`run()` directly, which is what makes scheduling decisions
    unit-testable without a CAS or a socket (E1.1's acceptance criterion).
    """

    def __init__(self, registry: Optional[StageRegistry] = None, allow_stub_engines: bool = False):
        self._registry = registry or get_registry()
        # Only the local dev/tracer harness sets this True. See StageCtx.allow_stub_engines.
        self._allow_stub_engines = allow_stub_engines

    def execute(
        self,
        build_id: str,
        initial_inputs: dict[str, Any] | None = None,
        cas_root: Path | str | None = None,
        cache_store: CacheStore | None = None,
        on_stage_complete: Any = None,
        on_stage_error: Any = None,
    ) -> BuildResult:
        execution_plan = plan(self._registry, initial_inputs)
        return run(
            execution_plan,
            self._registry,
            build_id=build_id,
            initial_inputs=initial_inputs,
            cas_root=cas_root,
            cache_store=cache_store,
            allow_stub_engines=self._allow_stub_engines,
            on_stage_complete=on_stage_complete,
            on_stage_error=on_stage_error,
        )


__all__ = [
    "ExecutionPlan", "plan", "run", "DagExecutor",
    "StageInvocation", "StageMiddleware", "Next", "deadline_mw", "memory_mw",
]
