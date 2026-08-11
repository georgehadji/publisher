# Blocking-Fix Plan — Audit §7 (D1, D2, D3)

**Derived from:** `implementation_audit_report.md` §7, verified against live code.  
**Date:** 2026-08-11  
**Status:** draft — awaiting implementation.

---

## Context

The independent audit of `7a4401b..beeff69` (4-axis review, 48 files, +3,997/−1,998 lines)
found the uplift implementation architecturally sound with two hard gates intact.
It identified **three blocking defects** that must be resolved before Stage 2 (U5–U7)
can be marked complete. All three are product-code defects (not test flakiness or
environment): one API crash, one API error response corruption, one missing stage output.

This plan describes the minimal, architecture-respecting fix for each.

### Architecture constraints (from `CLAUDE.md`, `docs/ARCHITECTURE_UPLIFT_PLAN.md` §2)

- **The DAG is derived, never hand-wired.** Adding an output schema ID to a stage
  declaration automatically exposes it to the downstream DAG — zero executor or
  worker changes.
- **Content-addressed storage.** Every artifact is a sha256 in the CAS. The API's
  `DELIVERABLE_SCHEMAS` map is the only place the API resolves an artifact kind to
  a schema ID; it must match what the stage declares in `outputs={}`.
- **Stage versions.** Any stage whose behaviour or declaration changes gets a
  `@stage(version=…)` bump.
- **No DI framework, no Python-side repository, no async rewrite, no Temporal.**
- **Two hard gates** must survive intact: text integrity (ast-assemble) and
  preflight→package (DAG-enforced).

---

## D1 — Overrides route crashes on missing `ops` (HIGH)

- **File:** `packages/api/src/routes/manuscripts.ts`
- **Lines:** 193–208
- **Symptom:** `PATCH /v1/documents/:id/overrides` destructures
  `const { ops } = request.body` with no runtime or schema validation. If the
  caller sends `{}` (or any body where `ops` is missing), `ops` is `undefined`,
  `undefined.length` throws `TypeError`, and Fastify returns a bare 500 with
  no structured error. A malformed request crashes the handler instead of
  returning 400.
- **Severity:** HIGH — observable crash on any syntactically-valid-but-semantically-wrong
  PATCH body. No auth bypass; the crash is read-only (handler dies before any
  mutation). Still a 500 where a 400 belongs.

### Fix

Add a Fastify JSON schema to the route definition. Fastify validates the body
*before* the handler runs; a validation failure returns a structured 400 without
ever entering the handler. This is the same pattern used elsewhere in the API
(the upload route uses `bodyLimit`; the webhook route uses inline validation in
the handler; a schema is the most direct and maintainable option here).

```typescript
// BEFORE (line ~194):
server.patch<{ Params: { id: string }; Body: { ops: unknown[] } }>(
  '/v1/documents/:id/overrides',
  async (request, reply) => {
    // ...

// AFTER:
server.patch<{ Params: { id: string }; Body: { ops: unknown[] } }>(
  '/v1/documents/:id/overrides',
  {
    schema: {
      body: {
        type: 'object',
        required: ['ops'],
        properties: { ops: { type: 'array' } },
      },
    },
  },
  async (request, reply) => {
    // ...
```

**Why this, not a runtime guard:**
- Fastify natively supports JSON Schema validation in the route options (the
  third argument is an options object; the second argument is the schema
  when using the overloaded signature — but here the route already uses the
  options-object form with no schema key). Adding `schema.body` fits the
  existing pattern.
- A runtime guard (`Array.isArray(request.body?.ops)`) also works but means
  the handler has to produce its own 400 response. A schema offloads validation
  to the framework and produces consistent error shapes.
- The upload route already validates `Content-Type` in-handler; this route
  validates shape; both are appropriate for their context.

### Verification

- **Unit:** `PATCH` the route with `{}`, `{"ops": "not-an-array"}`, and
  `{"ops": []}`. Assert 400 for the first two (structured error from Fastify),
  200 for the third.
- **Integration:** no new test needed — the route is not covered by integration
  tests (it's a minor route). The fix is a few lines of a validation schema;
  the risk of regression is negligible.

### Architecture impact: NONE

No stage, CAS, DAG, or worker changes. API-only, additive validation.

---

## D2 — SSE teardown sends empty response before hijack (HIGH)

- **File:** `packages/api/src/routes/builds.ts`
- **Lines:** 96–130 (setup), 146–183 (try/catch)
- **Symptom:** The SSE handler's `close()` function calls `reply.raw.end()`
  unconditionally. If the handler's `try` block throws *before* reaching
  `reply.hijack()` (e.g., `pool.connect()` times out, `LISTEN` fails), the
  `catch` calls `close()` → `reply.raw.end()` on a Fastify-managed socket.
  Fastify was about to send its own structured error (500), but `raw.end()`
  terminates the socket — the client gets a truncated response or empty stream
  instead of a proper error body.
- **Severity:** HIGH — client gets no error signal; failed SSE connections are
  silent. No data corruption or auth bypass. Degrades diagnostics for every
  SSE setup failure (pool exhaustion, Postgres network blip).

### Fix

Track whether `reply.hijack()` has been called. In `close()`, only call
`reply.raw.end()` when the response is hijacked. If not hijacked, let Fastify
handle the error (or explicitly call `reply.code(500).send(...)` in the catch
path).

```typescript
// BEFORE (simplified):
let closed = false;
const close = async () => {
  if (closed) return;
  closed = true;
  // ... release client ...
  reply.raw.end();
};
try {
  client = await pool.connect();
  // ... LISTEN ...
  reply.hijack();
  reply.raw.writeHead(200, { 'Content-Type': 'text/event-stream', ... });
  reply.raw.write('event: connected\ndata: {}\n\n');
  // ...
} catch (err) {
  request.log.error({ err, buildId: id }, 'SSE setup failed');
  await close();   // <-- calls raw.end() before hijack → corrupts error response
}

// AFTER:
let closed = false;
let hijacked = false;                              // ← NEW
const close = async () => {
  if (closed) return;
  closed = true;
  clearTimeout(timer);
  const held = client;
  client = null;
  if (held) {
    try { await held.query('UNLISTEN *'); } catch {}
    held.release();
  }
  if (hijacked) {                                  // ← GUARD
    reply.raw.end();
  }
};
try {
  client = await pool.connect();
  // ... LISTEN ...
  reply.hijack();
  hijacked = true;                                 // ← SET AFTER hijack
  reply.raw.writeHead(200, { 'Content-Type': 'text/event-stream', ... });
  reply.raw.write('event: connected\ndata: {}\n\n');
  // ...
} catch (err) {
  request.log.error({ err, buildId: id }, 'SSE setup failed');
  if (!hijacked) {
    return reply.code(500).send({ error: 'SSE setup failed' });
  }
  await close();
}
```

**Why a flag, not `reply.sent`:**
- `reply.hijack()` hands raw control to the caller. Fastify's internal `reply.sent`
  is not a reliable indicator post-hijack. A simple boolean set immediately after
  `reply.hijack()` is explicit and local — the least-surprising mechanism for the
  next reader.

**Why `return reply.code(500)` in the catch instead of letting Fastify handle it:**
- The catch runs inside an `async` handler. If the error is unreachable by the
  next `await`, the handler resolves and Fastify sends 200 with empty body (or a
  default 500 if the unhandled rejection surfaces). An explicit `reply.code(500)`
  in the catch before hijack gives the client a clear error. This is consistent
  with the existing pattern in the idempotency and auth hooks.

### Verification

- **Manual:** Simulate a pool-connection failure (stop Postgres, attempt SSE
  connection). Assert the client receives a proper JSON `{"error":"SSE setup failed"}`
  with HTTP 500, not an empty stream.
- **Integration:** The existing `test_sse_pushes_notified_events` covers the
  happy path. A new targeted test is low priority (the failure path is
  infrastructure-level); the fix is a ~10 line local change at the error boundary.

### Architecture impact: NONE

API-only. No stage, CAS, DAG, or worker changes. Adds an explicit error message
for an edge case that previously corrupted the response.

---

## D3 — `finish-gs` does not produce `proof-pdf/1` (MEDIUM)

- **Files:** `stages/prepress_stages.py` (finish-gs), potentially
  `stages/__init__.py`, `Dockerfile.worker` (if proof spec deps change)
- **Lines:** `prepress_stages.py:259-275` (declaration), `276-385` (body),
  `386-409` (StageResult construction)
- **Symptom:** The `finish` step has two implementations: `finish` and
  `finish-gs`. `stages/__init__.py:47` selects `finish-gs`. The deselected
  `finish` (stages/finish_stage.py:52) produces `proof-pdf/1` — but the
  active `finish-gs` (prepress_stages.py:268) does NOT: its `outputs=` only
  declares `pdfx/1` and `finish-report/1`. The API's `DELIVERABLE_SCHEMAS`
  (db.ts:98) maps `proof` → `proof-pdf/1`, but `proof-pdf/1` has no active
  producer. A client requesting `/v1/builds/:id/artifacts/proof` gets 404
  because no artifact row exists for schema `proof-pdf/1`.

  **This is a DAG-level gap**: `stages/__init__.py:47` selects `finish-gs` as
  the `finish` implementation, but the deselected `finish` produces an output
  the selected one doesn't — and the API maps a deliverable to that missing
  schema.

- **Severity:** MEDIUM — the proof PDF is a convenience artifact (lower-res
  version of the press PDF), not a hard gate. The build still completes
  correctly with `pdfx/1` (press) and `finish-report/1`. Customers asking
  for the proof PDF get 404. No security impact.

### Fix

Three changes in `stages/prepress_stages.py`, all localized to `finish-gs`:

1. **Declaration change** (`@stage` decorator, lines 268–269):
   - Add `"proof": "proof-pdf/1"` to `outputs=`
   - Add `"proof"` to `terminal_outputs=`
   - Bump `version` from 7 → 8 (output schema change — new artifact kind
     changes the stage's contract; downstream consumers must be aware)

```python
@stage(
    name="finish-gs",
    version=8,   # v8: produce proof-pdf/1 (D3 fix); v7 = report terminal output (U6)
    implements="finish",
    inputs={"pdf_path": "raw-pdf/1", "profile_name": "profile/1"},
    root_inputs=["profile_name"],
    outputs={"pdf": "pdfx/1", "proof": "proof-pdf/1", "report": "finish-report/1"},
    terminal_outputs=["proof", "report"],
    toolchain=["ghostscript", "icc"],
    ...
)
```

2. **Body change** — generate a proof PDF and CAS-put it:
   - After the press PDF is CAS-put (`pdf_ref = cas.put(...)` around line 372),
     add proof generation mirroring `finish`'s pattern (lines 98–153 in
     finish_stage.py):
     - Read `proofSpec` from the profile for DPI/size budget
     - Call `to_proof(pdf_path_p, proof_path, gs_binary=gs_binary)` — this
       function already exists in `publisher_prepress.ghostscript` and is
       already imported by the package
     - CAS-put the bytes as `application/pdf`
   - In the stub path (no `gs_binary`), the proof bytes = press bytes (same
     as `finish`'s no-ghostscript fallback at lines 123–125)

```python
    # ... after press_path.write_bytes and pdf_ref = cas.put(...) ...

    # ── Proof PDF (D3 fix) ──────────────────────────────────────
    proof_spec = profile.get("proofSpec", {})
    if gs_binary is not None:
        proof_file = work / "proof.pdf"
        try:
            to_proof(pdf_path_p, proof_file, gs_binary=gs_binary)
        except GhostscriptError as e:
            raise StageError(
                kind=ErrorKind.ENGINE_BUG,
                message=f"Ghostscript proof conversion failed: {e}",
            )
        proof_bytes = proof_file.read_bytes()
    else:
        proof_bytes = data   # stub: re-use the same bytes as the press PDF
    proof_ref = cas.put(proof_bytes, media_type=MediaType("application/pdf"))
    # ─────────────────────────────────────────────────────────────
```

3. **StageResult change** — add the `proof` artifact reference:

```python
    return StageResult(
        artifacts=[
            StageArtifactRef(
                kind="pdf", hash=str(pdf_ref.hash),
                media_type="application/pdf", size=len(data),
            ),
            StageArtifactRef(                                    # ← NEW
                kind="proof", hash=str(proof_ref.hash),
                media_type="application/pdf", size=len(proof_bytes),
            ),
            StageArtifactRef(
                kind="report", hash=str(report_ref.hash),
                media_type="application/json", size=len(result_bytes),
            ),
        ],
        metrics={"gs_available": 1.0 if gs_binary else 0.0, "stub_engine": stub},
    )
```

**Why not a separate `finish-proof` stage:**
- Proof generation shares the same input (`raw-pdf/1`) and the same
  Ghostscript binary as the press PDF. A separate stage would duplicate the
  Ghostscript binary lookup, profile loading, and stub-engine gating — exactly
  the kind of duplication the DRY principle and the plan's anti-doctrine
  (`paginate_stage imports private _ast_to_html/_emit_css from sibling stages`)
  exist to prevent. The `finish` stage already couples press+proof; `finish-gs`
  should match.
- The DAG does not need a separate edge for proof — proof is a terminal
  deliverable, never consumed by another stage. Adding it as a separate stage
  would add reachability complexity for zero benefit.

**Why `proof_bytes = data` in the stub path:**
- Mirrors `finish.py:123-125`: when Ghostscript isn't available, there's no
  meaningful low-res derivative to produce, and the press bytes are the best
  available artifact. The API still serves it under the `proof` deliverable key.
  The preflight gate already refuses stub output (`file-format: not a PDF`), so
  no stub artifact evades the hard gate.

### Verification

- **Unit:** A new test in `services/prepress/tests/` (or a contract test) that
  calls `finish_gs` with a real 1-page PDF and a profile containing `proofSpec`,
  and asserts:
  - `StageResult.artifacts` contains a `proof` artifact with schema `proof-pdf/1`
  - The proof bytes are a valid PDF (starts with `%PDF-`)
  - The proof file has lower resolution than the press file (check DPI via
    Ghostscript's `-dPDFSETTINGS=/ebook` or similar — `to_proof` chooses the
    resolution; a simpler check is `proof_size < press_size` for anything
    non-trivial).
- **Integration:** the existing `test_build_request_produces_a_real_artifact`
  already asserts `schemaId == "pdfx/1"` on the press artifact. After the fix,
  the same test can also fetch `/v1/builds/:id/artifacts/proof` and assert
  `schemaId == "proof-pdf/1"`. However, this test is host-conditional (needs
  `gs` on PATH or a CAS-aligned compose worker — audit §5.2). The proof artifact
  assertion should be added as a separate, lighter gate (e.g., verify the API's
  `DELIVERABLE_SCHEMAS` map includes `proof-pdf/1` and that the schema exists in
  the registry's output set).
- **E2E:** Rebuild the worker image, run the compose E2E upload→build→completed
  flow, and verify the proof artifact is served at
  `/v1/builds/:id/artifacts/proof/download` with real PDF bytes.

### Architecture impact

- **DAG:** Adding `proof-pdf/1` to `finish-gs` outputs does NOT add a new
  downstream edge — `proof-pdf/1` is a terminal deliverable, consumed by nothing.
  `check_integrity()` will be clean because `terminal_outputs=["proof"]` marks
  it as an intended sink (the `terminal_outputs` mechanism, added in U6,
  suppresses the "output not consumed" warning for terminal deliverables).
- **Stage version bump:** v7 → v8. All existing `build_stages` rows for `finish-gs`
  are at v7; new builds will record v8. The cache key derivation includes
  version, so v8 builds will not hit v7's cache — this is correct (different
  outputs).
- **`check_integrity()`:** Was clean before; stays clean after (terminal_outputs
  handles the new unreferenced output).
- **`DELIVERABLE_SCHEMAS`:** Already maps `proof → proof-pdf/1`. No change needed.

---

## Fix order & dependency

```
D1 (overrides crash) ── independent ──┐
D2 (SSE teardown)    ── independent ──┤
D3 (finish-gs proof) ── independent ──┘
```

All three are orthogonal — they touch different files with no shared code paths.
They can be implemented and verified in any order, or in parallel.

### Recommended order
1. **D2 first** — the SSE teardown fix is the smallest diff (a flag + a guard)
   and improves diagnostics for every future SSE investigation.
2. **D1 second** — the overrides route fix is one line (Fastify schema) and
   removes a known crash vector.
3. **D3 third** — the finish-gs proof fix is the largest change (~40 lines,
   stage version bump, new test) and benefits from a clean environment
   (D1/D2 landed) for E2E verification.

---

## Acceptance criteria for all three

- [ ] D1: `PATCH /v1/documents/:id/overrides` returns 400 for `{}` and
  `{"ops": "not-an-array"}`, 200 for `{"ops": []}`.
- [ ] D2: An SSE request where the handler throws before `hijack()` returns a
  JSON `{"error":"..."}` with HTTP 500, not an empty stream.
- [ ] D3: `stages/prepress_stages.py`: `finish-gs` declares `proof-pdf/1` in
  `outputs=`, version is 8, and the body generates a proof PDF artifact.
  `finish-report/1` schema still produced; `pdfx/1` unchanged.
- [ ] D3: `check_integrity()` clean (zero warnings).
- [ ] D3: E2E compose build produces a `proof-pdf/1` artifact accessible via
  the API `/v1/builds/:id/artifacts/proof` route.
- [ ] No regression in the existing 369-test suite (348 unit + 21 integration;
  the documented pre-existing host-conditional failure in
  `test_build_request_produces_a_real_artifact` unchanged).
- [ ] No regression in `tracer_bullet.py` (full 9-stage DAG, preflight refuses
  stub output).

---

## Files summary

| File | Change | Lines |
|---|---|---|
| `packages/api/src/routes/manuscripts.ts` | Add `schema.body` to PATCH `/v1/documents/:id/overrides` | +5 |
| `packages/api/src/routes/builds.ts` | Add `hijacked` flag, guard `close()`, explicit error in catch | +8 |
| `stages/prepress_stages.py` | `finish-gs` v7→v8, add `proof-pdf/1` output, proof generation, StageResult | +35, ~−2 |
| `stages/__init__.py` | No change needed (`finish-gs` already selected) | 0 |
| `packages/api/src/db.ts` | No change needed (`DELIVERABLE_SCHEMAS` already maps `proof→proof-pdf/1`) | 0 |
| `Dockerfile.worker` | No change needed (`publisher_prepress.ghostscript.to_proof` already installed) | 0 |
