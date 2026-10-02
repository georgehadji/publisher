"""
Tracer Bullet -- local dev harness CLI.

A thin wrapper over `publisher_exec` (E1.1): sets `allow_stub_engines=True`
and supplies the synthetic-corpus root inputs, then delegates the real
planning/execution work to `publisher_exec.DagExecutor`. The executor itself
-- reachability, topological order, cache-key derivation, CAS writes -- moved
to `platform/exec/py/publisher_exec/` so `worker.py` (production) no longer
imports from the module whose own docstring called it a dev harness (L4).

In production, this will be backed by Postgres advisory locks -> Temporal.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from publisher_exec import DagExecutor
from publisher_stages import StageError, RegistryConfig, RenderEngine, build_registry


def _harness(manuscript: str | None, profile: str, designspec: str | None):
    """The executor and root inputs for one local build.

    `manuscript` is a path to an `ast/1` JSON document; it defaults to the
    synthetic corpus. A `.docx` (or legacy `.doc`) path runs the real `ingest`
    stage on it instead, as the worker does for an upload -- the way to put an
    actual book through the whole pipeline locally.

    `profile` names a vendor profile from profiles/*/*.yaml -- e.g.
    "Greek 17x24". It drives trim size and bleed for the whole build.

    `designspec` is a path to a `designspec/1` JSON document (margins, type,
    leading ...), completed over the house spec; omitted, the house spec is used.
    """
    # A single `import stages` registers every stage (stages/__init__.py owns that
    # list). Previously this function and platform/stages/integrity.py each hand-
    # maintained their own import list, and the two had silently diverged: this list
    # never imported stages.prepress_stages, so `preflight` — the pipeline's one hard
    # gate — was never part of the executed DAG at all. The tracer bullet printed
    # "PASSED" for a build that never reached its gate. One list, imported everywhere.
    import stages  # noqa: F401 — import for its registration side effect

    # E1.2: this entry point parses env at its own edge -- PUBLISHER_RENDER_ENGINE
    # and PUBLISHER_EMIT_IDML, documented in README.md as tracer_bullet.py flags --
    # stages/__init__.py no longer reads either.
    stages.import_idml_if_requested(
        os.environ.get("PUBLISHER_EMIT_IDML", "").strip().lower() in ("1", "true", "yes")
    )
    engine = RenderEngine.from_env(os.environ.get("PUBLISHER_RENDER_ENGINE"))
    # A build renders what the tenant submitted by default (U2); this local
    # harness runs off the synthetic corpus unless handed a Word file, so it
    # selects the fixture loader `acquire` for anything else.
    is_word = bool(manuscript) and manuscript.lower().endswith((".docx", ".doc"))
    registry = build_registry(RegistryConfig(
        render_engine=engine, ingest_impl="ingest" if is_word else "acquire"))
    executor = DagExecutor(registry, allow_stub_engines=True)

    # Root inputs are keyed by STAGE NAME (the executor resolves
    # initial_inputs.get(decl.name)); "finish" is a step whose selected
    # implementation is `finish-gs`, not the bare step name -- keying by
    # "finish" left it unreachable and dropped preflight/package (hard-gate
    # bypass; see worker.py _initial_inputs_for for the full story).
    finish_stage = registry.selected_implementation("finish")
    # Same trap, one step earlier: "design-compile" is now a STEP with two
    # implementations (CSS and Typst). Keying its root inputs by the step name
    # would leave the selected emitter without a profile -- and an emitter with
    # no profile lays out at trim with no bleed, which preflight then fails.
    design_stage = registry.selected_implementation("design-compile")

    # Only provide root inputs for stages whose declared inputs
    # are not produced by any other stage.
    source = ({"ingest": {"docx_path": manuscript}} if is_word else
              {"acquire": {"manifest_path": manuscript or "corpus/manuscripts/minimal-novel.ast.json"}})
    initial_inputs = {
        **source,
        # One profile name, supplied to every stage that has a say in page
        # geometry. design-compile grows the page box by its bleed, finish insets
        # the TrimBox by the same amount, preflight measures the result. Give two
        # of them different profiles and the third will correctly fail the build.
        design_stage: {"designspec_path": designspec, "profile_name": profile},
        # The EPUB embeds the same design's faces (where their licence allows).
        "epub": {"designspec_path": designspec},
        finish_stage: {"profile_name": profile},
        "preflight": {"profile_name": profile},
    }
    return executor, initial_inputs


def run_tracer_bullet(manuscript: str | None = None, profile: str = "Generic 6x9",
                      designspec: str | None = None):
    """Run the full tracer bullet pipeline (arguments: see `_harness`)."""
    executor, initial_inputs = _harness(manuscript, profile, designspec)

    print("=" * 60)
    print("  PUBLISHER -- TRACER BULLET")
    print("=" * 60)
    print()

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


def run_reproducibility(manuscripts: list[str] | None = None,
                        profile: str = "Generic 6x9") -> int:
    """The nightly reproducibility check (W10): each manuscript is built cold,
    cold again in a separate store, and from the first build's cache. Every
    artifact must have the same bytes in all three, and the cached build must
    run nothing.

    A build that stops (as `finish-gs` does on Ghostscript 10.07.1) stops at the
    same stage every run; what ran before it is still compared, and the stop is
    reported as a warning -- a failing stage is the test suite's to catch.
    """
    import tempfile
    from pathlib import Path
    from publisher_reproducibility import ReproducibilityReport, compare_runs

    corpus = manuscripts or sorted(str(p) for p in Path("corpus/manuscripts").glob("*.ast.json"))
    results: list[dict] = []
    for manuscript in corpus:
        label = Path(manuscript).name
        executor, initial_inputs = _harness(manuscript, profile, None)
        runs: dict[str, dict] = {}
        # ignore_cleanup_errors: on Windows the cache index (sqlite) is still open.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as first, \
                tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as second:
            for name, root in (("cold", first), ("cold-again", second), ("cached", first)):
                done: dict = {}
                try:
                    executor.execute(
                        build_id="reproducibility", initial_inputs=initial_inputs, cas_root=root,
                        on_stage_complete=lambda stage, _decl, result, _ms, done=done:
                            done.__setitem__(stage, result))
                except StageError as e:
                    results.append({"check": f"{label}: {name} build", "status": "warn",
                                    "message": f"stopped: [{e.kind}] {e.message[:160]}"})
                runs[name] = {stage: {a.kind: a.hash for a in r.artifacts}
                              for stage, r in done.items()}
                if name == "cached":
                    reran = sorted(stage for stage, r in done.items() if not r.cache_hit)
                    results.append({"check": f"{label}: cached build replays",
                                    "status": "fail" if reran else "pass",
                                    "message": f"re-ran {reran}" if reran
                                    else f"{len(done)} stage(s) served from the cache"})
        results.extend({**r, "check": f"{label}: {r['check']}"} for r in compare_runs(runs))

    report = ReproducibilityReport(datetime.now(timezone.utc).isoformat(), results)
    report.print_report()
    return 0 if report.passed else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--reproducibility"]:
        sys.exit(run_reproducibility(sys.argv[2:] or None))
    sys.exit(run_tracer_bullet(
        sys.argv[1] if len(sys.argv) > 1 else None,
        sys.argv[2] if len(sys.argv) > 2 else "Generic 6x9",
        sys.argv[3] if len(sys.argv) > 3 else None,
    ))
