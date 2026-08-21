---
name: publisher-platform
description: Map of the `platform/` folder — the substrate every stage stands on: content-addressed store (CAS), build-graph cache, stage registry + DAG derivation, sandbox, Postgres schema, LLM routing policy, pagescan defect scanner, reproducibility and supply-chain checks. Use this whenever a task touches CAS storage, cache keys, the @stage decorator or registry, DAG integrity, sandboxing hostile input, the database schema, model routing, or the Rust crates. Read before editing anything under platform/.
---

# `platform/` — the substrate

Everything here is infrastructure that stages *use*; nothing here knows about books.
Several modules exist in two or three languages on purpose (Python for the pipeline,
TypeScript for the API, Rust for hot paths) — they are parallel implementations of one
spec, and drift between them is a bug.

## Layout

```
platform/
  cas/           content-addressed store        (Python · Rust · TypeScript)
  cache/         build-graph memoization        (Python · TypeScript)
  stages/        stage registry + DAG           (Python · TypeScript) + integrity.py
  sandbox/       hostile-input containment      (Python)
  db/            Postgres schema                (SQL)
  routing/       LLM route table                (YAML data, not code)
  pagescan/      typographic defect scanner     (Rust)
  reproducibility/  nightly cold-vs-cached diff (Python)
  supply-chain/  SBOM + CVE gate                (Python)
```

## Files

### `platform/cas/` — content-addressed store
Artifacts are sha256-keyed blobs under `PUBLISHER_CAS_ROOT` (default `./.publisher/cas`),
sharded `h[:2]/h[2:4]/h`.

| File | What it does |
|---|---|
| `py/publisher_cas/__init__.py` | `CasConfig`, `ContentAddressedStore` (streaming put/get, atomic writes), `new_local_store()`. Functional core / imperative shell. |
| `py/publisher_cas/types.py` | `Sha256` (a real type with invariants, not `str`), `MediaType`, `ArtifactRef` (hash + media_type + size). |
| `py/tests/test_cas.py` | Round-trip, atomicity, hash-validation tests. |
| `src/lib.rs` | Rust **pure core**: hashing, key derivation, path computation. No I/O — that belongs to the Python/TS layers. |
| `ts/src/index.ts` | TypeScript mirror of the same types, for the API. |
| `Cargo.toml`, `py/pyproject.toml`, `ts/package.json`, `ts/tsconfig.json` | Per-language package manifests. |

### `platform/cache/` — build-graph memoization
| File | What it does |
|---|---|
| `py/publisher_cache/__init__.py` | `compute_cache_key()`, `compute_toolchain_digest()`, `canonical_json()`, `CacheEntry`, and three backends: `CacheStore` (base), `SqliteCacheStore` (dev harness), `PostgresCacheStore` (worker — cache state visible across processes). |
| `ts/src/index.ts` | `canonicalJson` + key computation for the TS side. Must stay byte-identical to the Python canonicalization or a cache key means two different things. |

### `platform/stages/` — the registry
| File | What it does |
|---|---|
| `py/publisher_stages/__init__.py` | **The `@stage` decorator and everything derived from it.** `ErrorKind` (error taxonomy), `Diagnostic`, `StageError`, `ArtifactRef`, `StageResult`, `StageCtx`, `StageDeclaration`, `StageRegistry`, `get_registry()`, `run_stage()`. From one declaration it derives the DAG, contract tests, cache-key inputs, the dev harness, and admission control. |
| `integrity.py` | DAG integrity checker. **Run it as a script — `python platform/stages/integrity.py` — never `python -m platform.stages.integrity`**: the repo's top-level `platform/` shadows the stdlib `platform` module, so the `-m` form always fails. CI once used `-m` under `continue-on-error`, so the gate reported success without ever running. Imports the same stage set as `stages/__init__.py`; keep both in sync. |
| `py/tests/test_registry.py` | Alternative implementations (`implements=`) and DAG derivation. |
| `py/tests/test_stages.py` | Registry behaviour, declaration validation. |
| `ts/src/index.ts` | TypeScript registry mirror. |

### `platform/sandbox/` — hostile-input containment
| File | What it does |
|---|---|
| `py/publisher_sandbox/__init__.py` | Tiered isolation (`SandboxTier`: process → gVisor/Firecracker → network-isolated nodes), `SandboxConfig` with object-capability discipline (ro input dir, rw output dir, CPU/mem/time budget, nothing else), `ThreatMonitor`, `SecurityEvent`. Also ships **attack fixtures**: `create_zip_bomb`, `create_xxe_fixture`, `create_billion_laughs`, `create_path_traversal_zip`, `create_fork_bomb_script`. |
| `py/tests/test_security.py` | Escape attempts *are* the test suite. |
| `py/tests/test_p5.py` | P5 scale-and-hardening tests. |

### `platform/db/schema.sql`
Single source of truth for the state the worker and API share. Applied automatically by
Postgres's `docker-entrypoint-initdb.d` on first container start; re-run manually with
`psql "$DATABASE_URL" -f platform/db/schema.sql`. Tables: `cache_index`, `builds`
(with attempt/lease/`error_kind` columns for worker durability), `build_stages`,
`artifacts`, `titles`, `manuscripts`, idempotency replay, webhooks. All `CREATE ... IF NOT
EXISTS` plus idempotent `ALTER`s so it is safe to re-apply. Every tenant-scoped table
carries `tenant_id` — that is what keeps the API's `assertTenant()` checks meaningful.

### `platform/routing/policy.yaml`
**The live LLM route table** — versioned data, not code. Loaded by
`load_routes_from_policy()` in `services/structure/publisher_structure/inference.py`.
Rules that are enforced, not advisory:
- `model:` must be a **concrete slug** — `-latest` suffixes and `~` aliases raise, because
  cache keys embed `model_id` and a moving alias silently changes who produced a frozen artifact.
- Two distinct reasoning keys, never conflated: `output_effort` (Anthropic-native
  `output_config.effort`, direct-SDK routes) vs `reasoning` (OpenRouter's object).
- `cache_key_adds` on each route is a checklist; a route missing a field can poison its own cache.
- Bump `policy_version` on any edit. It enters provenance records but is deliberately *not*
  part of any route's cache key.

### `platform/pagescan/src/lib.rs`
Deterministic typographic defect scanner over a pagemap: widows, orphans, runts, rivers,
hyphen stacks, short chapter ends. Pure, total, deterministic — no I/O, no clock, no RNG,
every loop bounded.

### `platform/reproducibility/publisher_reproducibility/__init__.py`
Nightly cold-vs-cached byte comparison on the golden corpus + toolchain digest assertion
(image digests, font hashes, ICC, engine versions). `ReproducibilityChecker`,
`ReproducibilityReport`, `main()`. Drift is a P1.

### `platform/supply-chain/publisher_supply_chain/__init__.py`
`generate_sbom()`, `Dependency`, `SBOM`, `CveGate`, `CveResult`. Target: zero critical CVEs
in shipped images.

## Rules that bite

- **Stages write via `ctx.cas_root`, never `ctx.work_dir`** (scratch, deleted).
- **Cross-language modules must not drift.** A cache key computed in TS and one computed in
  Python have to be the same string.
- **Run `integrity.py` as a script**, per the shadowing note above.
- Rust crates are members of the root `Cargo.toml` workspace: `cargo test`, `cargo clippy -- -D warnings`.

## Related

`stages/` (the declarations this folder consumes) · `worker.py` / `tracer_bullet.py` (the two
executors) · `docs/ARCHITECTURE.md` §2.5/§2.8/§2.9 · `docs/BUILD_PLAN.md` §3.3/§3.4/§3.13 ·
`docs/LLM_STRATEGY.md` (routing) · `docs/COVER_DESIGN.md` §11 (cache-key discipline).
