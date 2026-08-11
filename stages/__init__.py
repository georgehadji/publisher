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
