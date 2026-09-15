"""
Publisher build stages.

Importing this package registers every stage in the global registry
(`publisher_stages.get_registry()`). Registration is a side effect of importing each
module, because the `@stage` decorator registers at definition time.

This file used to be empty, which made `import stages` a no-op. Anything relying on it —
most importantly the generated contract tests in tests/contracts/test_generated.py, which
enumerate `get_registry().all()` — silently saw an EMPTY registry and therefore produced
zero test cases. The contract gate reported success while testing nothing.

Keep this list in sync with platform/stages/integrity.py, which imports the same set for
the DAG integrity check.

E1.2 (docs/ARCHITECTURE_SCORE_10_PLAN.md): this module is imports only now -- no
`os.environ`, no `select_implementation()`. It used to read PUBLISHER_RENDER_ENGINE
and select `finish`/`ingest`/the render engine at IMPORT time, mutating the same
global registry that `worker.run_build` mutated again per build -- import order and
environment were part of the graph's identity. Selection now happens explicitly, per
caller, via `publisher_stages.build_registry(RegistryConfig(...))`.
"""

from . import acquire_stage  # noqa: F401
from . import ingest_stage  # noqa: F401
from . import extract_stage  # noqa: F401
from . import structure_stage  # noqa: F401 -- registers "ast-assemble"
from . import resolve_stage  # noqa: F401
from . import design_compile_stage  # noqa: F401
from . import paginate_stage  # noqa: F401
from . import finish_stage  # noqa: F401
from . import package_stage  # noqa: F401
from . import prepress_stages  # noqa: F401
from . import cover_stages  # noqa: F401
from . import typst_stages  # noqa: F401 -- registers the pandoc+Typst render path

__all__ = [
    "acquire_stage",
    "ingest_stage",
    "extract_stage",
    "structure_stage",
    "resolve_stage",
    "design_compile_stage",
    "paginate_stage",
    "finish_stage",
    "package_stage",
    "prepress_stages",
    "cover_stages",
    "typst_stages",
]

# ── IDML deliverable (opt-in) ────────────────────────────────────────────────
#
# `idml` is a terminal delivery artifact: an InDesign-openable package, consumed
# by nobody. It needs pandoc, so it is imported (and therefore registered) only
# when asked for. Registering it unconditionally would add a pandoc dependency
# to every CSS-path build on every machine -- worse, its two root inputs
# (`designspec_path`, `profile_name`) are both declared OPTIONAL (absence is a
# valid state, same as `resolve`'s `overrides_path`), so once registered it is
# unconditionally REACHABLE the moment its upstream producers are -- i.e. on
# every ordinary build. There is no root-input flag that can gate it off after
# the fact; import is the only gate.
#
# E1.2: this can no longer be an env read at this module's top level (stages/
# __init__.py is imports only now). Each entry point reads PUBLISHER_EMIT_IDML
# at ITS OWN edge and calls this function explicitly, before building a
# registry -- not gated on "is pandoc installed": that would silently drop the
# deliverable on a host missing the binary. Explicit request, loud failure.


def import_idml_if_requested(emit_idml: bool) -> None:
    if emit_idml:
        from . import idml_stage  # noqa: F401 -- registers the `idml` stage
