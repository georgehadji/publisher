# Architecture Roadmap — deferred, not built

Companion to [ARCHITECTURE.md](ARCHITECTURE.md), which is normative: it describes the
system as built, and states the rules the code must hold to. This document is the opposite
— every item here is a real part of Publisher's original target design that is **not**
built, has no current committed timeline, and must not be read as a description of the
running system.

**Why this file exists (E7.1, [ARCHITECTURE_SCORE_10_PLAN.md](ARCHITECTURE_SCORE_10_PLAN.md)
§0.1).** `ARCHITECTURE.md` used to describe these as if they existed — a 21-stage Temporal
pipeline, three DesignSpec emitters with a cross-emitter agreement gate, S3/R2 storage,
`packages/orchestrator`, `packages/worker-render`, `services/design`, a k8s/terraform
`infra/`. None of that was ever built; the audit that produced the score-10 plan flagged it
as false-baseline drift (`L18`, `L19`). Moving it here — dated, with a trigger — is what
keeps it visible instead of either lying about it (leaving it in `ARCHITECTURE.md`) or
erasing it (deleting it outright). `ARCHITECTURE_SCORE_10_PLAN.md` §6 already reasoned
through why each of these is deliberately deferred rather than missed; this file is that
reasoning turned into a document readers land on from `ARCHITECTURE.md` itself.

Each item is logged as of **2026-09-16**. None had an owner or a committed date before
that; "logged" means "written down with a trigger," not "scheduled."

**R1 is a partial exception as of 2026-09-22** — its dispatch mechanism was built as an
opt-in path that leaves the default one unchanged. Its section states exactly what landed
and what did not; the rest of this file remains unbuilt."

---

## R1 — Temporal orchestration + per-capability worker pools

**Status: partially built as of 2026-09-22** (`c88e668`) — the one item on this list that
is no longer wholly aspirational. An opt-in Temporal path exists; the default path is
untouched. Read the "genuinely missing" list below, not this section's title, for what is
still absent.

**What `ARCHITECTURE.md` used to imply:** §2.3's physical topology (a Temporal server,
separate `q.ingest` / `q.render.html` / `q.prepress` / `q.external` queues, autoscaled
independently per capability).

**What's built — two paths, and the default is still the Postgres one:**

- *Default, unchanged:* `worker.py` — one process type, N horizontally-scaled copies,
  claiming from a single Postgres queue with `FOR UPDATE SKIP LOCKED`, running every stage
  in-process. §2.4 already documents this as a deliberate, sanctioned substitute ("a
  Postgres-backed DAG executor + advisory locks is an acceptable 300-line substitute"), not
  drift — it is the one aspirational section `ARCHITECTURE.md` keeps in place rather than
  moving here, because the substitute is itself documented right next to the ambition. A
  plain `docker compose up -d` starts exactly this and nothing Temporal-related.
- *Opt-in, added:* `worker_temporal.py` + `platform/orchestration/`, behind
  `docker compose --profile temporal`. Each stage is dispatched as an activity onto the
  `queue` its own `@stage(...)` **already declared** — that taxonomy existed on every stage
  and went unread until now — so the `worker-temporal-render` service hosts only
  `q.composition` and `q.render.html` and can be scaled with
  `--scale worker-temporal-render=3` without touching any other queue's capacity, which is
  precisely what `worker.py`'s one-process-runs-every-stage model cannot do. It reuses
  `worker.py`'s claim/lease/record helpers by import rather than reimplementing them, so
  the two paths cannot drift on lease semantics, error recording, or the terminal-`package`
  hard gate; only stage execution differs. `publisher_exec.run_single_stage()` is the seam:
  one stage through the same cache/memory/deadline middleware chain, for a caller that
  sequences the DAG itself across processes. `run()` is unchanged.

**What's genuinely missing:** an autoscaling *policy* — the profile proves a stage lands on
the queue it declares and that only a worker polling that queue ever runs it, but nothing
decides *when* to add a render replica; a production Temporal deployment — the profile runs
`server start-dev`, in-memory, zero external persistence, a dev server and not a cluster;
and workflow versioning for indefinite human-gate pauses — today a `builds` row just sits
`running` until an override is applied (durable, but changing stage logic mid-flight is
still "the next poll picks up the new code").

**Trigger:** real load that makes per-stage autoscaling worth a policy rather than a manual
`--scale`, or a workflow-versioning requirement the current model can't satisfy. The
dispatch mechanism itself is no longer the blocker.

---

## R2 — Three-emitter design compilation + Chrome/Playwright render path

**What `ARCHITECTURE.md` used to imply:** §2.7's "One DesignSpec → three style compilers"
(`emit_css`, `emit_idml`, `emit_typst`) with a cross-emitter agreement contract test (page
count ±2%, ≥95% identical line breaks); §2.3's `chrome` / `Playwright` / `Paged.js` render
pod; `packages/orchestrator` (Temporal workflows) and `packages/worker-render`
(Playwright/CDP + Paged.js, + a Prince adapter); `services/design` as its own package.

**What's built instead:** two real, independently-selectable render paths behind one
`PUBLISHER_RENDER_ENGINE` switch — `design-compile` + `paginate` (weasyprint, CSS) and
`design-compile-typst` + `paginate-typst` (pandoc → Typst). Both are genuine, tested,
production paths (see `stages/typst_stages.py`'s module docstring for the bleed-geometry
difference between them). Neither is Chrome/Playwright-based, and there is no Prince
adapter, no `packages/orchestrator`, no `packages/worker-render`, no standalone
`services/design` (design compilation lives in `stages/design_compile_stage.py` +
`stages/typst_stages.py` + `services/prepress`'s geometry/fontvault).

**What's genuinely missing:** a THIRD emitter (a Chrome/Playwright-based path was the
original design's reference implementation) and the cross-emitter agreement gate — with
two emitters instead of the intended three, "agreement" has never been tested as a
three-way contract, only as "each path individually produces a valid, gated PDF."

**Trigger:** a second emitter has a customer — i.e. a real requirement (a vendor, a
template, a customer) that neither weasyprint nor Typst can satisfy, or a concrete need to
verify cross-engine agreement as its own contract rather than trusting each path's own
gates independently.

---

## R3 — S3/R2 object storage + per-tenant upload/export layout

**What `ARCHITECTURE.md` used to imply:** §2.13's `s3://pub-artifacts/` layout —
per-tenant encrypted (`SSE-KMS`) upload storage, a presigned-URL `exports/` prefix doubling
as the Adobe InDesign API hand-off surface, fixtures under their own S3 prefix.

**What's built instead:** everything — uploads, deliverables, fixtures, every stage's
output — is one blob in one local content-addressed store (`platform/cas/`, rooted at
`PUBLISHER_CAS_ROOT`), sharded by hash. No tenant-prefixed path exists; a manuscript's or a
build's artifact is found via a Postgres row (`manuscripts.source_sha256`,
`artifacts.schema_id` → hash), never a storage path. There is no encryption-at-rest layer
beyond whatever the host filesystem/volume provides, and no Adobe API integration of any
kind (no adapter, no queue, no hand-off surface).

**What's genuinely missing:** a backend that survives past one host's disk (multi-worker
deployments today all read/write the same mounted CAS volume — fine for one machine,
not for a real multi-region fleet), per-tenant encryption keys, and the Adobe hand-off
surface entirely (nothing in this repo calls an Adobe API today, per T4/T5 in the score
plan's threat model — there is no egress path that would even let it).

**Trigger:** multi-host deployment (the shared local volume becomes a real bottleneck or a
real single point of failure), or a per-tenant-encryption / compliance requirement the
filesystem-level posture can't satisfy. The CAS abstraction (`ContentAddressedStore`) is
already the right seam for this — swapping the backend is meant to be an adapter
(implement the same put/get interface against S3/R2), not a rewrite of anything that calls
it.

---

## Not on this list, and why

- **Temporal itself** — covered by R1, but flagged separately: `ARCHITECTURE.md` §2.4
  keeps this discussion in place (not moved here) because it already documents its own
  substitute inline. Read §2.4, not this file, for that reasoning.
- **God modules, an ADR log, `packages/web` behavioural tests** —
  `ARCHITECTURE_SCORE_10_PLAN.md` §6 already covers these as accepted debt; they were never
  claimed as built in `ARCHITECTURE.md` in the first place, so there is no drift to log
  here.
