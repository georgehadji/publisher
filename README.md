# Publisher

Turn a DOCX manuscript into a press-ready book PDF — plus EPUB, IDML, and ONIX metadata —
through a content-addressed, cache-aware build pipeline with a real multi-tenant API and
worker fleet behind it.

Publisher is not a converter. It is a build system: every stage is a pure function of its
inputs, keyed by hash, checked against two gates that cannot be bypassed, and reproducible
from a manifest months later when a vendor rejects a file and someone has to find out why.

---

## What it does

- **Ingests** a real DOCX manuscript (not a fixture) into a structured, immutable AST
- **Infers structure** (chapters, front/back matter, scene breaks) with human and agent
  overrides tracked as separate, replayable operations — never mutating the source
- **Composes** pages against a vendor trim-size profile (KDP, IngramSpark, Lulu, and Greek
  formats today) with real bleed, gutter, and margin geometry
- **Prepresses** to PDF/X-1a: font embedding, CMYK conversion, ink coverage, and a
  **preflight gate that a build cannot be packaged without passing** — structurally, not by
  convention
- **Never alters the author's words.** A text-integrity check compares normalized text
  before and after composition on every build, with no override flag. This is the one
  invariant the whole project is built around.

## What it doesn't (yet)

EPUB, IDML export, and ONIX metadata generation exist as working emitters but are earlier
and less battle-tested than the core PDF path — treat them as beta. Non-Latin scripts,
InDesign parity, and real-time collaborative editing are explicitly out of scope; see
[docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md) §10 for the full, deliberate
non-goals list.

---

## Architecture — the load-bearing ideas

**The build DAG is derived, never hand-wired.** Stages declare their input/output schemas;
the executor matches one stage's output to another's input to build the graph. Adding an
edge is a declaration change, not a wiring change.

**Reachability is a fixpoint.** A build runs only the stages whose inputs are actually
satisfiable — this is why importing the full stage registry doesn't accidentally attempt
every pipeline at once.

**Content-addressed storage.** Every artifact is a sha256-keyed blob. Two tenants who
submit byte-identical input get byte-identical output, verifiably, without re-deriving it.

**Two hard gates, and they cannot be softened:**
- *Text integrity* — fails the build outright if a single character of prose changed. No
  override flag exists, on purpose.
- *Preflight* — the packaging stage structurally requires a preflight verdict as an input.
  There is no code path that produces a deliverable without one.

**Two execution tiers.** A local dev harness for one-off builds and stub engines during
development, and a production worker that claims real work from Postgres
(`FOR UPDATE SKIP LOCKED`), runs real rendering engines only, and never takes the stub
shortcut — by default and by design.

Full detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) ·
[docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) ·
[docs/ARCHITECTURE_UPLIFT_PLAN.md](docs/ARCHITECTURE_UPLIFT_PLAN.md)

### Stack

| Tier | Language | Where |
|---|---|---|
| API, orchestration, worker fleet | TypeScript (Node 22+) | `packages/api`, `worker.py`'s callers, the review UI |
| Document processing, rendering | Python 3.12+ | `services/*`, `stages/*` — ingest, structure, design, prepress, EPUB, IDML, ONIX |
| Hot paths | Rust | `platform/cas` hashing, doc streaming |

---

## Running it

**Production-shaped stack** (Postgres + worker + API, the path real builds take):

```bash
docker compose up -d
```

API listens on `:4000`; Postgres on `:55432` (host) to avoid colliding with a local
Postgres install. `docker-compose.yml` sets per-service memory/CPU limits — the worker is
the one to raise if you're rendering large manuscripts.

**Local dev harness** (single build, stdout, no Postgres — stub engines allowed):

```bash
python tracer_bullet.py
```

**CLI:**

```bash
python cli.py schema validate <file>
```

### Render paths

Two workflows produce the same press PDF from the same manuscript. They differ only in
which pair of stages the derived DAG binds — both sit downstream of the text-integrity
gate, and both feed the same Ghostscript → preflight → package tail.

| `PUBLISHER_RENDER_ENGINE` | Styles | Renderer | Needs |
|---|---|---|---|
| `css` (default) | `design-compile` → CSS | weasyprint / Paged.js | weasyprint |
| `typst` | `design-compile-typst` → `.typ` | pandoc → Typst | pandoc ≥ 3.1.1, typst ≥ 0.12 |

```bash
PUBLISHER_RENDER_ENGINE=typst python tracer_bullet.py
```

The Typst path converts each chapter's already-verified HTML with pandoc, emits labelled
page marks, and reads chapter start pages and the page count back out of the laid-out
document with `typst query` — a measured pagemap, never an estimate. It has no stub mode:
a missing `pandoc` or `typst` fails the build rather than certifying an unpaginated dump.

Legacy binary `.doc` uploads are converted to `.docx` with LibreOffice headless inside
`ingest`, before anything parses them; the integrity gate then runs against the converted
document like any other manuscript.

### IDML deliverable

```bash
PUBLISHER_EMIT_IDML=1 python tracer_bullet.py
```

Adds a terminal `idml` stage producing `book.idml` — a package a designer opens in
InDesign and keeps working in. Opt-in because it needs pandoc, and a build should not
start failing for want of a file nobody ordered.

Story markup comes from pandoc's ICML writer (the same markup IDML stories are made of),
applied to the HTML the integrity gate already proved text-complete; styles and page
geometry come from the DesignSpec and vendor profile. Every package is structurally
validated before it is stored — mimetype first and STORED, `container.xml` → `designmap`,
every `idPkg` part, story, and style reference resolving, every part well-formed XML.

**It is terminal, and it must stay that way.** InDesign composes text at open time, so
this stage cannot know the resulting page count. It threads frames using the render
path's *measured* pagemap as a hint and turns on Smart Text Reflow, which adds or removes
pages on open. Nothing may price a spine off an IDML.

### Windows dev toolchain

Linux and the worker image get these from the package manager (see
`Dockerfile.worker`). On Windows, none of them need admin rights:

```bash
scoop install 7zip ghostscript && scoop bucket add extras && scoop install scribus
```

`7zip` first is not optional — scoop's built-in extractor runs out of memory on
larger archives.

**weasyprint** needs the GTK/Pango stack, which has no scoop package. MSYS2 is
gtk.org's supported route but its `fork` emulation fails on some Windows 11
hosts (`0xC0000142`, and `rebaseall` needs the same broken `fork` to repair
itself). The working route is the GTK3 runtime installer from
`tschoonj/GTK-for-Windows-Runtime-Environment-Installer` — and because its
installer demands elevation, the DLLs can simply be extracted instead:

```bash
7z x gtk3-runtime-3.24.31-2022-01-04-ts-win64.exe -oC:/Users/you/gtk3-runtime
```

Rename the extracted `$_63_` directory (NSIS's name for the `bin` payload) to
`bin`, put it on PATH, and set `FONTCONFIG_PATH` to the extracted
`etc/fonts` — without it fontconfig loads no config, finds no system fonts, and
every page renders in a substituted face.

Ghostscript's CMYK ICC profile is located from the `gs` binary's own install
tree, so no configuration is needed; `PUBLISHER_CMYK_ICC` overrides it when a
vendor supplies their own profile.

## Testing

```bash
./scripts/test.ps1          # the whole suite — ALWAYS use this, not bare pytest
./scripts/test.ps1 -k cache # extra args pass through to pytest
```

Bare `pytest` reads/writes a machine-global log directory shared with other repos on this
host and pays ~100s of avoidable plugin-autoload cost. `scripts/test.ps1` fixes both.
Integration tests under `tests/integration/` need a live Postgres — bring up
`docker compose up -d postgres` first.

## Project structure

```
schemas/          JSON Schema source of truth -- codegen'd to Pydantic + Zod
platform/         CAS, cache, stage registry/DAG derivation, sandbox, telemetry
services/         Ingest, structure, design, prepress, cover, EPUB, IDML, ONIX, agents
packages/api/     Fastify + Postgres API -- tenancy, builds, artifacts, webhooks
packages/web/     Review UI (structure review with raster preview)
stages/           Stage implementations wired into the derived DAG
fixtures/         Versioned stage fixture sets for offline/dev builds
corpus/           Golden manuscripts + raster-diff regression harness
profiles/         Vendor trim-size specs -- KDP, IngramSpark, Lulu, Greek formats
templates/        DesignSpec presets
worker.py         Production worker: claims builds, runs the real pipeline, records state
tracer_bullet.py  Local dev harness: one build, stub engines allowed
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system design
- [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) — phased build plan and decisions
- [docs/ARCHITECTURE_UPLIFT_PLAN.md](docs/ARCHITECTURE_UPLIFT_PLAN.md) — active hardening
  workstreams and what's verified vs. still open
- [docs/PRODUCTION_READINESS.md](docs/PRODUCTION_READINESS.md) — the GA bar: output
  correctness, reliability, security, legal, and what's explicitly out of scope

## License

Proprietary — all rights reserved. No license is granted for use, copying, or
distribution without express written permission.
