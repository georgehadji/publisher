"""
Put the repo root on sys.path for the whole test run.

Several test modules import top-level repo packages -- `profiles`, `stages`,
`templates`, `tracer_bullet` -- which are plain directories at the repo root
rather than installed distributions. Under `pytest`, sys.path[0] is the test
file's own rootdir-relative package parent, not the repo root, so a bare
`pytest` from the repo root failed to collect three modules with
ModuleNotFoundError while the same imports worked fine from a script run
(where cwd is on sys.path).

The `publisher_*` packages under platform/ and services/ are already importable
(installed), so only the repo root itself is missing.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
