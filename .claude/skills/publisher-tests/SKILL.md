---
name: publisher-tests
description: Map of the `tests/` folder and the whole testing layout — generated contract tests, the collection floor, and the Postgres-backed integration suite (worker durability, real manuscripts, API hardening, API-drives-pipeline). Also says where the co-located unit tests live and how to run the suite correctly. Use this whenever a task adds or debugs a test, a test fails, CI is red, or asks "how do I run the tests here". Read before running pytest or editing anything under tests/.
---

# `tests/` — the cross-cutting suite

## Run it correctly — this matters

```bash
./scripts/test.ps1          # Windows / PowerShell
bash scripts/test.sh        # POSIX (no exec bit -- use `bash`). NOT what CI runs.
./scripts/test.ps1 -k cache # extra args pass through to pytest
```

**Never run bare `pytest`.** Two reasons, both learned the hard way:

1. Results were being read out of a machine-global, cross-project tee directory. A
   concurrent run in a sibling repo was picked up and *its* failures reported as
   Publisher's. The wrapper writes to `.publisher/test-output.txt`, which only this repo
   writes.
2. Third-party pytest plugin autoload costs ~100 s per run (`pytest --version` alone took
   2m30s on Windows under AV). The wrapper sets `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`:
   ~160 s → ~14 s. No test here depends on a third-party plugin.

Both scripts run `python -m pytest platform services packages stages tests` — paths listed
explicitly so collection can never wander outside the repo.

## Where tests live

| Location | What |
|---|---|
| `tests/contracts/` | Generated contract tests (cross-cutting). |
| `tests/integration/` | Postgres-backed end-to-end tests. |
| `tests/test_collection_floor.py` | Guards the suite itself. |
| `platform/*/py/tests/` | Unit tests co-located with each platform package — see **publisher-platform**. |
| `services/*/tests/` | Unit tests co-located with each service — see **publisher-services**. |
| `stages/tests/` | Stage-level tests (integrity mutation, bleed geometry, ingest security) — see **publisher-stages**. |
| `conftest.py` (repo root) | Puts the repo root on `sys.path` so `profiles`, `stages`, `templates`, `tracer_bullet` import under pytest. The `publisher_*` packages are installed, so only the root was missing. |
| `pyproject.toml` (repo root) | `testpaths`, `norecursedirs`. `tests` was once omitted from `testpaths`, so a bare pytest never ran the contract tests or the floor. |

## Files in `tests/`

| File | What it asserts |
|---|---|
| `contracts/test_generated.py` | Every stage with a declared fixture set becomes a parametrized case; the fixture manifest is validated against the stage's **own** declaration (outputs promised, inputs consumed, schema IDs named). The previous version ended with a literal `assert True` and `pytest.skip()`-ed on a missing expected file, so a missing contract was a pass — and it relied on an empty `stages/__init__.py`, so it produced zero cases. Do not soften it back. |
| `test_collection_floor.py` | Asserts the suite is actually collected. `FLOOR = 290` — **bump when adding tests, never lower without justification.** A collection regression must fail loudly rather than quietly shrink the suite. |
| `integration/conftest.py` | Shared fixtures for the DB-backed tests: a real Postgres and a writable CAS root. `DATABASE_URL` defaults to the compose-published port **55432** and is env-overridable. Worker subprocess fixtures share the same `DATABASE_URL` so the queue the test writes to is the queue the worker drains. Rows are namespaced `test-` and cleaned at session start, so reruns and parallel sessions do not fight. |
| `integration/test_worker_durability.py` | U1 — an expired lease on a `running` build is reclaimed; a build that exhausts attempts dead-letters to `dead`; a non-`StageError` exception leaves `failed` + `error_kind='INTERNAL'` before re-raising. |
| `integration/test_real_manuscripts.py` | U2 — upload streams the DOCX into CAS with `source_sha256`; two tenants' builds produce **different** artifacts each containing their own text; a document with no stored source fails `BAD_INPUT` instead of silently building the fixture. |
| `integration/test_api_hardening.py` | U5/U7 — S3 concurrent identical `POST /v1/builds` with one Idempotency-Key creates exactly one build; S2 `casPath` rejects path traversal; S4 webhook `http://` and private/loopback/metadata hosts refused at creation; `/v1/health` returns 503 with Postgres or CAS down. |
| `integration/test_api_drives_pipeline.py` | **Four** tests. Two are the A0.2 seam detectors, and they assert the *fixed* behaviour — that state **does** survive a `DagExecutor.execute()` call (a real artifact hash; `cache_hit=True` on a second run against a persistent CAS root). "No state outlives one execute()" was the diagnosed defect, not the assertion. The other two are unrelated guards: `test_build_status_is_not_a_hardcoded_literal` (A0.3 anti-fabrication) and a check that every schema in the API's `DELIVERABLE_SCHEMAS` map is some registered stage's declared output. |

## Rules that bite

- **Detector-first.** The working rule from `docs/ARCHITECTURE_REMEDIATION.md` §2: land each
  detector in the same PR as its fix, detector first in the diff. **Do not soften a test to
  make it pass early.**
- **The integration tests need Postgres.** `docker compose up -d` first; the host-side port
  is 55432, not 5432, precisely so a locally installed Postgres cannot silently steal the
  connection.
- **A gate that cannot fail is worse than no gate.** Several tests here exist because an
  earlier version of the same test passed while verifying nothing.

## Related

`scripts/` (the runners) · `tools/` (the CI lints) · `.github/workflows/ci.yml` ·
`docs/COST_AND_STABILITY_PLAN.md` §S1 · `docs/ARCHITECTURE_UPLIFT_PLAN.md` §3.
