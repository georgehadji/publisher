# Publisher — Cost & Stability Plan

Two problems, fixed together because they share a cause: **work that was done but never verified, and context that is paid for but never read.**

Companion to [ARCHITECTURE_REMEDIATION.md](ARCHITECTURE_REMEDIATION.md) (A0–A2 landed; A3–A5 outstanding).

---

## 1. Outstanding correctness problems

| # | Problem | Evidence | Fix |
|---|---|---|---|
| P1 | 30 files changed, **zero commits** | `git status` | S4 |
| P2 | Ghostscript wiring never verified end-to-end | Docker daemon died mid-run twice | S3 |
| P3 | Test results unreadable — a "Publisher" run reported failures from a **sibling repo** | `rtk` tees every project's pytest output into one global dir (`~/AppData/Local/rtk/tee/`); `ls -t` picked up a concurrent *Leggie* run | S1 |
| P4 | `.publisher/`, `.test-cas-cache/` untracked and ungitignored | `git status` | S2 |
| P5 | Bare `pytest` costs ~160 s, ~100 s of it third-party plugin autoload | `pytest --version` alone = 2m30s | S1 |

**P3 is the serious one.** It is not a cosmetic annoyance: it caused a wrong conclusion to be drawn and reported ("3 failed") from a repo that was never under test. Any fix verified only through that channel is unverified.

---

## 2. Where the tokens go

Measured, not estimated. Injected into **every request**:

| Source | Bytes | ~Tokens | Keep? |
|---|---|---|---|
| `~/.claude/rules/zh/` | 17,634 | ~4,400 | **No** — Chinese translation of `rules/common/`, same content twice |
| `~/.claude/rules/common/` | 16,642 | ~4,200 | Yes |
| `~/.claude/rules/web/` | 14,380 | ~3,600 | **No** — CSS/React/Core-Web-Vitals rules; Publisher is a backend pipeline |
| `~/.claude/rules/README.md` | 4,688 | ~1,200 | **No** — install docs for the rules system; inert at runtime |
| `E:\Documents\Vibe-Coding\CLAUDE.md` | 3,713 | ~900 | **No** — describes *weebot*, which has its own `weebot/CLAUDE.md`. Injected into all ~45 sibling projects |
| `~/.claude/CLAUDE.md` + `RTK.md` | 1,202 | ~300 | Yes |

**~10,400 of ~14,600 tokens per request carry no information for this project.**
`rules/python/` (3,231 B) and `rules/typescript/` (7,124 B) are re-injected by PostToolUse hooks after *every* matching edit — dozens of times per session.

### What breaks caching

A stable prefix is billed at ~10% on cache hit, so bloat is survivable *if it never changes*. These make it change:

1. **Model switches void the whole cache.** This session ran `/model` four times; each forced a full re-write of a large context at Opus write rates (+25% over base input).
2. **Per-turn-varying hook output.** `COST CRITICAL: session total ~$X` and `SCOPE WARNING: N files modified` change on every single tool call. The cost-warning hook measurably costs money.
3. **Unbounded tool output.** A single `docker compose pull` log — 1,219 lines of `Downloading 83.89MB` — was read into context in full.

---

## 3. Fixes

### S1 — Make test results trustworthy and fast *(P3, P5)*

Root cause of P3 is reading results out of a **global, cross-project** log directory. Fix: never read results from `rtk`'s tee dir. Write them to a **project-local, git-ignored** path and read only that.

- `scripts/test.ps1` / `scripts/test.sh`: run the suite with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, explicit Publisher-only paths, output to `.publisher/test-output.txt`.
- Autoload off is safe *and* honest here: verified 338 tests collect with zero errors, 13.8 s vs ~160 s. No test depends on a third-party plugin.
- Add `norecursedirs` to `pyproject.toml` so collection never wanders.

### S2 — Gitignore generated state *(P4)*

`.publisher/` (durable CAS + sqlite cache index) and `.test-cas-cache/` are build output, not source.

### S3 — Verify Ghostscript end-to-end *(P2)*

Bring the stack up, run one build to `completed`, download the artifact, assert the bytes are a real PDF. This is A0.2 detector 1 — the one that has never passed. **Until it does, the Ghostscript work is written, not proven.**

**Closed 2026-08-07** — the detector passes, and the first real run found five defects that unit tests and review had both missed. See "S3 is CLOSED" below.

### S4 — Commit *(P1)*

Coherent commits, not one 30-file blob: Ghostscript/finish; durable state + worker + API; test infrastructure; docs.

### S5 — Cut static context

Reversible, in this order:

1. Move `~/.claude/rules/zh/` → `~/.claude/rules-disabled/zh/` (~4,400 tok/req)
2. Move `~/.claude/rules/web/` → `~/.claude/rules-disabled/web/` (~3,600 tok/req)
3. Move `~/.claude/rules/README.md` → `~/.claude/rules-disabled/` (~1,200 tok/req)
4. Delete `E:\Documents\Vibe-Coding\CLAUDE.md` — stale duplicate; `weebot/CLAUDE.md` already holds the real copy (~900 tok/req **× 45 projects**)

Moved, not deleted, so any of it comes back with one `mv`. Restore instructions live in `rules-disabled/README.md`.

### S6 — Add a Publisher `CLAUDE.md`

Currently absent, so every session re-derives the architecture from source — or worse, inherits weebot's. A ~60-line project file is net-negative in tokens by the second turn and removes a standing source of wrong context.

### S7 — Habits (documented in the new `CLAUDE.md`)

- Pipe build/install/pull output through `tail`; never `Read` a log unbounded.
- `/clear` at task boundaries; `/compact` only mid-task.
- Pick a model at a boundary and stay on it. Mechanical work → Sonnet; architecture/audit → Opus.

---

## 4. Order

```
S1  test harness        ← everything downstream is verified through it
S2  gitignore
S5  context cuts        ← independent, immediate savings
S6  CLAUDE.md
S3  verify ghostscript  ← needs S1 to be believable
S4  commit              ← needs S3 green
```

S1 is first because it is the instrument every other claim is measured with. Verifying S3 through the broken channel that produced P3 would just manufacture another false result.

## 5. Definition of done

- [x] `scripts/test.*` writes Publisher-only results to `.publisher/test-output.txt`; suite green — **332 passed, 5 skipped, 0 failed**
- [x] Collection ≥ 338, zero errors, under ~30 s — 338 in 13.8 s
- [x] `.publisher/`, `.test-cas-cache/` gitignored
- [x] **A build reaches `completed`; downloaded artifact begins `%PDF`** — closed 2026-08-07, see below
- [x] Working tree committed in coherent commits — 4 commits on `fix/durable-state-and-ghostscript`
- [x] `rules/zh`, `rules/web`, `rules/README.md` moved; workspace `CLAUDE.md` disabled
- [x] `Publisher/CLAUDE.md` exists

### S3 is CLOSED — 2026-08-07

`test_build_request_produces_a_real_artifact` passes. A build posted to the API is
claimed by the containerised worker, runs all nine stages with `allow_stub_engines=False`,
and `GET /v1/builds/:id/artifacts/pdf/download` returns 154,889 bytes beginning `%PDF-1.3`
and carrying `/GTS_PDFX`, `/OutputIntent` and `/DeviceCMYK`. Suite: **350 passed, 5 skipped.**

Of the three risks flagged as unexercised, the prologue and the ICC discovery worked on
first contact and `--permit-file-read` was sufficient with SAFER on. First contact did,
however, expose five defects that only a real run could have surfaced — every one of them
a case of something reporting success it had not earned:

1. **Ghostscript declined PDF/X and exited 0.** It printed `TrimBox does not fit inside
   BleedBox … reverting to normal PDF output`, wrote an ordinary PDF, and returned 0.
   `_run` discarded its output on success and `_assert_pdf` checked only the `%PDF` magic,
   so `finish` reported `"profileApplied": "pdfx-1a"` over a file that was not PDF/X.
   `to_pdfx` now reads gs's output for downgrade notices and asserts an OutputIntent is
   really in the bytes. `finish`/`finish-gs` went to v5 so no v4 artifact is replayed.
2. **The boxes were identical and gs rejected them anyway** — its own comparison of
   `0 0 430.866142 649.133858` against itself. `-dUseTrimBox` (applied only when there is
   no bleed allowance, where it is lossless) makes the page exactly the TrimBox.
3. **The API served the unconverted render as the press file.** `artifacts` was keyed
   `(build_id, kind)`, but `kind` is stage-local: paginate emits `kind='pdf'` (`raw-pdf/1`)
   and finish emits `kind='pdf'` (`pdfx/1`); with `ON CONFLICT DO NOTHING` the first writer
   won. Artifacts are now keyed by `schema_id`, and the API maps a public name to a schema
   (`pdf` → `pdfx/1`). Preflight's report had been losing to finish's the same way, so
   `GET /v1/builds/:id/preflight` was returning the wrong document entirely.
4. **The API could not read the CAS at all** — no volume mount, so every download 500'd
   with an ENOENT quoting the server's filesystem path back to the caller.
5. **Host-side tests were talking to a different database.** A locally-installed
   PostgreSQL owns port 5432, so `DATABASE_URL=localhost:5432` never reached the compose
   stack. Published on 55432 now. Separately, the worker exited for good whenever Postgres
   restarted; it reconnects, and has `restart: unless-stopped`.

The lesson generalises: every one of these passed unit tests and code review. What caught
them was one real build, inspected byte by byte at the end.
