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
"""

import os

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

# The `finish` step has two implementations: `finish` (P0 pass-through scaffolding) and
# `finish-gs` (P1 Ghostscript PDF/X). Select the real one so the derived DAG binds a
# single producer of pdfx/1 instead of whichever stage happened to run first.
from publisher_stages import get_registry as _get_registry  # noqa: E402
_get_registry().select_implementation("finish", "finish-gs")

# The `ingest` step has two implementations: `ingest` (U2 -- real DOCX the tenant
# uploaded) and `acquire` (fixture ast/1 loader). Default to the REAL one: a build
# must render what the tenant submitted, and the only way to reintroduce the
# fixture-substitution CRITICAL (ARCHITECTURE_UPLIFT_PLAN.md N1) is to explicitly
# select `acquire`. The local dev harness (tracer_bullet.py) does exactly that.
_get_registry().select_implementation("ingest", "ingest")

# ── Render path selection ────────────────────────────────────────────────────
#
# Two workflows produce the same raw-pdf/1 from the same doc-effective/1:
#
#   css    (default)  design-compile      -> text/css        -> paginate
#   typst             design-compile-typst -> text/x-typst   -> paginate-typst
#
# Both then feed the SAME finish-gs -> preflight -> package tail, and both sit
# downstream of the text-integrity gate. Selecting an engine changes which pair
# of stages the derived DAG binds; it never changes which gates run.
#
# This is a selection, not a fallback: an unknown value fails at import rather
# than silently rendering with the other engine (BUILD_PLAN.md D8).
_RENDER_ENGINES = {
    "css": (("design-compile", "design-compile"), ("paginate", "paginate")),
    "typst": (("design-compile", "design-compile-typst"), ("paginate", "paginate-typst")),
}
_ENGINE = os.environ.get("PUBLISHER_RENDER_ENGINE", "css").strip().lower()
if _ENGINE not in _RENDER_ENGINES:
    raise ValueError(
        f"PUBLISHER_RENDER_ENGINE={_ENGINE!r} is not a render path. "
        f"Choose one of {sorted(_RENDER_ENGINES)}."
    )
for _step, _impl in _RENDER_ENGINES[_ENGINE]:
    _get_registry().select_implementation(_step, _impl)

# ── IDML deliverable (opt-in) ────────────────────────────────────────────────
#
# `idml` is a terminal delivery artifact: an InDesign-openable package, consumed
# by nobody. It needs pandoc, so it is imported (and therefore registered) only
# when asked for. Registering it unconditionally would add a pandoc dependency
# to every CSS-path build on every machine, and a build would start failing for
# want of a file the customer never ordered.
#
# Not gated on "is pandoc installed" on purpose: that would silently drop the
# deliverable on a host missing the binary. Explicit request, loud failure.
if os.environ.get("PUBLISHER_EMIT_IDML", "").strip().lower() in ("1", "true", "yes"):
    from . import idml_stage  # noqa: F401 -- registers the `idml` stage
