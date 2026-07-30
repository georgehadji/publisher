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
