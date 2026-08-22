---
name: publisher-scripts
description: Map of the `scripts/` folder — the project-local test runners (`test.ps1` for Windows, `test.sh` for POSIX) that must be used instead of bare pytest — note CI itself runs bare pytest and neither script has the exec bit. Use this whenever you are about to run the test suite, a test result looks like it came from the wrong repo, or a run is inexplicably slow. Read before running pytest in this repo.
---

# `scripts/` — the test runners

Two files, kept deliberately in sync. **Use these instead of a bare `pytest`.**

| File | Platform |
|---|---|
| `test.ps1` | Windows / PowerShell — carries the full rationale in its header comment. |
| `test.sh` | POSIX. **Not what CI runs** — see the warning below. |

```bash
bash scripts/test.sh            # POSIX — note the `bash`, see below
bash scripts/test.sh -k cache   # extra args pass straight through to pytest
./scripts/test.ps1              # Windows (needs pwsh; not installed on Linux)
```

**Neither file carries the exec bit** (both are mode 100644 in the index), so
`./scripts/test.sh` fails with `Permission denied`. Invoke it as `bash scripts/test.sh`
until someone runs `chmod +x`. Do not let that failure push you to bare `pytest` — that
fallback is exactly what these scripts exist to prevent.

## What they do, and why

Both `cd` to the repo root, create `.publisher/`, export
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, run
`python -m pytest platform services packages stages tests "$@"`, tee to
`.publisher/test-output.txt`, and exit with pytest's own code (via `PIPESTATUS`/
`$LASTEXITCODE`, so the tee does not swallow a failure).

1. **Results must be project-local.** Bare pytest output was being read back out of a
   global, cross-project tee directory shared by every repo on the machine. A concurrent
   run in a sibling repo was picked up and its failures reported as Publisher's — a wrong
   conclusion was drawn and reported from a repo that was never under test. Anything
   verified only through that channel is unverified.
2. **Plugin autoload costs ~100 s per run.** Third-party plugin discovery scans
   site-packages metadata; on Windows that is subject to real-time AV scanning. Disabling
   it took the suite from ~160 s to ~14 s, with 338 collected and zero errors — verified,
   no test here depends on a third-party plugin.
3. **Paths are listed explicitly** rather than relying on `testpaths`, so collection can
   never wander outside this repo.

## Rules that bite

- **CI does not use either script.** `.github/workflows/ci.yml` runs `python -m pytest
  --tb=short` and `python -m pytest tests/contracts -v` — bare pytest, without the explicit
  path list and without `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, collecting via `testpaths`. The
  protections below are real locally and **absent in CI**. Keep the two scripts in sync with
  each other, but do not assume editing them changes what CI does.
- **Read results from `.publisher/test-output.txt`**, not from any global log directory.
- If you add a top-level test directory, add it to the pytest path list in *both* scripts
  and to `testpaths` in the root `pyproject.toml`.

## Related

`tests/` (**publisher-tests**) · root `pyproject.toml` and `conftest.py` ·
`docs/COST_AND_STABILITY_PLAN.md` §S1 (§P3 is the incident that produced this).
