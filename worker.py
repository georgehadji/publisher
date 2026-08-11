#!/usr/bin/env python3
"""
Publisher worker -- DB-backed queue, real execution.
ARCHITECTURE_REMEDIATION.md A1.4 · ARCHITECTURE_UPLIFT_PLAN.md U1/U2.

Claims a queued build with `FOR UPDATE SKIP LOCKED` (correct multi-worker
claim without double-processing), runs the real DAG via DagExecutor against
a durable CAS root and a PostgresCacheStore (so cache state is visible to
every worker and, eventually, the API), and records build_stages/artifacts
rows as the source of truth GET /v1/builds/:id will read from once A2 wires
the routes to it.

U1 (worker durability): every claimed build reaches a terminal state. Claims
carry a lease (attempt / worker_id / lease_expires_at); a row left at
'running' by a killed worker is reclaimed once its lease expires, and a
poison build dead-letters after MAX_ATTEMPTS instead of looping forever. A
build is marked 'failed' before an unexpected exception is re-raised, so the
process dies loudly but the row is not left stuck at 'running'.

U2 (real manuscripts): `_initial_inputs_for` maps the build's document_id to
the manuscript bytes the tenant actually uploaded (manuscripts.source_sha256
-> CAS) and feeds them to the `ingest` stage. A document with no stored
source is a BAD_INPUT failure -- never a silent fixture substitution.

tracer_bullet.py is the local dev harness (single build_id, prints to
stdout, no DB). This is the production path: many builds, many workers,
state that outlives the process. `allow_stub_engines` is NOT set here --
see the comment at its call site below.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

REPO_ROOT = Path(__file__).resolve().parent
for sub in ("platform/stages/py", "platform/cas/py", "platform/cache/py"):
    sys.path.insert(0, str(REPO_ROOT / sub))

import stages  # noqa: F401 -- registration side effect
from publisher_cache import PostgresCacheStore
from publisher_stages import ErrorKind, StageError, get_registry
from tracer_bullet import DagExecutor

POLL_INTERVAL_S = 2
CAS_ROOT = Path(os.environ.get("PUBLISHER_CAS_ROOT", "./.publisher/cas"))
# U1 lease: a healthy build renews its lease after every stage (on_stage_complete),
# so a long build never expires while a dead one always does within one lease period.
LEASE_SECONDS = int(os.environ.get("PUBLISHER_WORKER_LEASE_SECONDS", "600"))
MAX_ATTEMPTS = int(os.environ.get("PUBLISHER_WORKER_MAX_ATTEMPTS", "3"))
WORKER_ID = os.environ.get("PUBLISHER_WORKER_ID") or f"worker-{os.getpid()}"

_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")

# ── U7 observability: one JSON line per event, build_id + correlation_id on
# every build-scoped record, so an HTTP request can be joined to a container's
# stdout by a single id. Replaces print() -- structured logs are queryable.
_CURRENT_BUILD: dict = {}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "worker_id": WORKER_ID,
        }
        if _CURRENT_BUILD:
            payload.update(_CURRENT_BUILD)
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        return json.dumps(payload, default=str)


_LOGGER = logging.getLogger("publisher.worker")
if not _LOGGER.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(_JsonFormatter())
    _LOGGER.addHandler(_handler)
    _LOGGER.setLevel(logging.INFO)
    _LOGGER.propagate = False


def _log(level: int, msg: str, **fields) -> None:
    _LOGGER.log(level, msg, extra={"extra_fields": fields} if fields else None)


def _log_info(msg: str, **fields) -> None:
    _log(logging.INFO, msg, **fields)


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required to run the worker")
    return dsn


def _claim_build(conn) -> dict | None:
    """FOR UPDATE SKIP LOCKED -- the mechanism that makes `builds` a correct
    multi-worker queue: a second worker's concurrent claim query skips a row
    this transaction already holds, rather than blocking or double-claiming.

    U1: the claim is reclaim-aware. A row at 'running' whose lease has expired
    is a build whose worker died -- reclaim it exactly like a queued build. A
    row that has already been reclaimed MAX_ATTEMPTS times is poison (bad
    input, or a bug that crashes every attempt): dead-letter it to 'dead'
    instead of looping it forever.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT * FROM builds
            WHERE status = 'queued'
               OR (status = 'running' AND (lease_expires_at IS NULL OR lease_expires_at < now()))
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        )
        build = cur.fetchone()
        if build is None:
            return None

        if build["attempt"] >= MAX_ATTEMPTS:
            # Poison build: it has already been claimed MAX_ATTEMPTS times --
            # bad input, or a bug that crashes every attempt. Dead-letter it
            # whether it is stuck at 'running' or was requeued, instead of
            # looping it forever.
            cur.execute(
                "UPDATE builds SET status = 'dead', completed_at = now(), "
                "error_kind = 'exhausted', error_message = %s WHERE id = %s",
                (f"build exceeded {MAX_ATTEMPTS} attempts -- moved to dead letter", build["id"]),
            )
            _notify(conn, build["id"], {"status": "dead"})
            conn.commit()
            return None

        cur.execute(
            """
            UPDATE builds
            SET status = 'running',
                started_at = COALESCE(started_at, now()),
                attempt = attempt + 1,
                worker_id = %s,
                lease_expires_at = now() + %s * interval '1 second'
            WHERE id = %s
            """,
            (WORKER_ID, LEASE_SECONDS, build["id"]),
        )
        conn.commit()
        return dict(build)


def _renew_lease(conn, build_id: str) -> None:
    """U1: extend the current build's lease. Called after every completed
    stage, so a long but healthy build never expires."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE builds SET lease_expires_at = now() + %s * interval '1 second' WHERE id = %s",
            (LEASE_SECONDS, build_id),
        )
        conn.commit()


def _notify(conn, build_id: str, payload: dict) -> None:
    """U5/S11: push a build event to the SSE channel via Postgres LISTEN/NOTIFY.

    Executed inside the CALLER's transaction: NOTIFY is delivered at commit,
    so stage records and their events land atomically. The channel name embeds
    the build id (server-generated safe token); pg_notify parameterizes it so
    no SQL construction is involved."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_notify(%s, %s)",
            (f"build_{build_id}", json.dumps(payload, default=str)),
        )


def _resolve_profile_name(build: dict) -> str:
    """U2: `build['profile_ids']` -> one vendor profile name.

    The API stores profile_ids as a JSONB array; the first entry is the book
    profile. Unknown profiles are a BAD_INPUT -- never a silent fallback to
    "Generic 6x9", which would rebuild every book to a demo size.
    """
    ids = build.get("profile_ids") or []
    # Defensive: JSONB normally comes back deserialized (importing
    # psycopg2.extras registers the typecaster process-wide), but that is a
    # global side-effect -- if the cursor factory or import ever changes, the
    # raw JSON text would arrive here and `ids[0]` would silently become the
    # first CHARACTER of the JSON literal. Deserialize the string form
    # explicitly so the read does not depend on a registration side-effect.
    if isinstance(ids, str):
        try:
            ids = json.loads(ids)
        except json.JSONDecodeError as e:
            raise StageError(
                kind=ErrorKind.BAD_INPUT,
                message=f"build has malformed profile_ids {ids!r}: {e}",
            )
    if not ids:
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message="build has no profile_ids -- a book cannot be built without a profile",
        )
    name = ids[0] if isinstance(ids, list) else str(ids)
    from profiles import load_profile

    if load_profile(name) is None:
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"unknown profile {name!r} -- refusing to guess a default",
        )
    return name


def _initial_inputs_for(conn, build: dict) -> dict:
    """
    Maps a build's {documentId, designId, profileIds} to real DagExecutor
    root inputs (U2).

    Reads the manuscript the tenant actually uploaded: build['document_id'] ->
    manuscripts.source_sha256 -> the DOCX bytes in CAS, fed to the `ingest`
    stage. A document with no stored source is a BAD_INPUT failure -- this is
    the same lesson as `acquire`'s silent fixture substitution (commit
    7a4401b), applied one layer up. Never fall back to a fixture.
    """
    document_id = build["document_id"]
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT source_sha256 FROM manuscripts WHERE id = %s", (document_id,))
        row = cur.fetchone()

    source_sha = (row or {}).get("source_sha256")
    if not source_sha:
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"document {document_id} has no stored manuscript source. "
                    "Upload the manuscript first -- refusing to build a fixture "
                    "in its place.",
        )
    if not _SHA256_RE.fullmatch(source_sha):
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"document {document_id} has a malformed source_sha256 {source_sha!r}",
        )
    cas_path = CAS_ROOT / source_sha[:2] / source_sha[2:4] / source_sha
    if not cas_path.is_file():
        raise StageError(
            kind=ErrorKind.BAD_INPUT,
            message=f"manuscript bytes for {document_id} are not in CAS at "
                    f"{cas_path} -- the upload is incomplete",
        )

    profile_name = _resolve_profile_name(build)

    # Root inputs are keyed by STAGE NAME (the executor resolves
    # initial_inputs.get(decl.name)), and "finish" is a step with two
    # implementations -- the selected one is `finish-gs`. Keying by the bare
    # step name made `finish-gs` unreachable (its profile root input never
    # supplied), which silently dropped `preflight` and `package` from the
    # DAG: the build reported `completed` with no preflight verdict and no
    # package -- the second hard gate, bypassed. Resolve the selected name.
    finish_stage = get_registry().selected_implementation("finish")

    return {
        "ingest": {"docx_path": str(cas_path)},
        # The same profile drives all three: design-compile grows the page box by
        # its bleed, finish insets the TrimBox by it, preflight measures it.
        "design-compile": {"designspec_path": None, "profile_name": profile_name},
        finish_stage: {"profile_name": profile_name},
        "preflight": {"profile_name": profile_name},
    }


def _record_stage(conn, build_id: str, stage_name: str, decl_version: int,
                   status: str, cache_hit: bool, duration_ms: int, metrics: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO build_stages
                (build_id, stage_name, stage_version, status, cache_hit, duration_ms, metrics, started_at, completed_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now())
            ON CONFLICT (build_id, stage_name) DO UPDATE SET
                status = EXCLUDED.status, cache_hit = EXCLUDED.cache_hit,
                duration_ms = EXCLUDED.duration_ms, metrics = EXCLUDED.metrics,
                completed_at = EXCLUDED.completed_at
            """,
            (build_id, stage_name, decl_version, status, cache_hit, duration_ms,
             psycopg2.extras.Json(metrics)),
        )
        _notify(conn, build_id, {"stage": stage_name, "status": status})
        conn.commit()


def _record_artifact(conn, build_id: str, kind: str, schema_id: str, sha256: str,
                      media_type: str, size: int) -> None:
    """
    Upsert keyed on schema_id -- see the comment on the artifacts table.

    `kind` is unique only within one stage, so keying on it made two stages'
    artifacts collide; DO NOTHING then kept whichever finished first, which
    meant the press PDF/X from `finish` lost to paginate's raw PDF. DO UPDATE
    (not DO NOTHING) so a re-run of a build refreshes its artifact rows rather
    than leaving a previous run's hashes in place.
    """
    if not schema_id:
        raise RuntimeError(
            f"stage emitted artifact kind '{kind}' that its declaration does not "
            f"list in outputs={{}} -- it has no schema ID to be identified by"
        )
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO artifacts (build_id, kind, schema_id, sha256, media_type, size)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (build_id, schema_id) DO UPDATE SET
                kind = EXCLUDED.kind, sha256 = EXCLUDED.sha256,
                media_type = EXCLUDED.media_type, size = EXCLUDED.size
            """,
            (build_id, kind, schema_id, sha256, media_type, size),
        )
        conn.commit()


def _fail(conn, build_id: str, error_kind: str, error_message: str) -> None:
    """U1: record a terminal 'failed' state. Called for BOTH expected
    StageError and unexpected exceptions -- the build must never be left at
    'running' with nobody holding its lease."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE builds SET status = 'failed', error_kind = %s, error_message = %s, "
            "completed_at = now() WHERE id = %s",
            (error_kind, error_message, build_id),
        )
        _notify(conn, build_id, {"status": "failed", "error_kind": error_kind})
        conn.commit()


def run_build(conn, build: dict) -> None:
    build_id = build["id"]
    _CURRENT_BUILD.clear()
    _CURRENT_BUILD["build_id"] = build_id
    if build.get("correlation_id"):
        _CURRENT_BUILD["correlation_id"] = build["correlation_id"]
    _log_info("claimed build",
              # _claim_build returns the row BEFORE its own UPDATE incremented
              # attempt; the attempt this run is ON is pre-value + 1.
              attempt=(build.get("attempt") or 0) + 1,
              lease_s=LEASE_SECONDS)
    registry = get_registry()
    # U2: this worker renders what the tenant uploaded. Select `ingest` (the real
    # DOCX path) -- idempotent, and it forces the requirement in-process even if
    # something else flipped the registry to the fixture loader `acquire`.
    registry.select_implementation("ingest", "ingest")
    executor = DagExecutor(registry, allow_stub_engines=False)
    cache_store = PostgresCacheStore(_dsn())

    # Persist each stage the moment it finishes, not after the whole build.
    # Recording only at the end meant a build failing at stage 8 stored ZERO
    # stages -- throwing away exactly the trail needed to diagnose it -- and
    # GET /v1/builds/:id/events, which polls build_stages for live progress,
    # had nothing to stream until the build was already over.
    def _on_stage(stage_name, decl, result, duration_ms):
        _record_stage(
            conn, build_id, stage_name, decl.version, "completed",
            result.cache_hit, duration_ms, result.metrics,
        )
        for art in result.artifacts:
            _record_artifact(
                conn, build_id, art.kind, decl.outputs.get(art.kind, ""),
                art.hash, art.media_type, art.size,
            )
        # U1: a stage just finished -- the build is alive, extend the lease.
        # A build that takes longer than one lease period between stages is
        # pathological and will be reclaimed; every healthy build renews here.
        _renew_lease(conn, build_id)

    try:
        results = executor.execute(
            build_id=build_id,
            initial_inputs=_initial_inputs_for(conn, build),
            cas_root=CAS_ROOT,
            cache_store=cache_store,
            on_stage_complete=_on_stage,
        )
        # HARD GATE (BUILD_PLAN.md F2.3): `package` declares `preflight_report`
        # as a required input, so the DAG makes it structurally impossible to
        # reach `package` without a preflight verdict. But reachability is a
        # derived property -- a caller bug (e.g. a root-input keyed by step
        # name instead of the selected stage name) can silently drop the tail
        # of the DAG. The tracer bullet guards this; the worker must too: a
        # build without the terminal `package` stage is NOT completed, and
        # certifying it would be the exact silent-gate-bypass the plan bans.
        if "package" not in results:
            raise StageError(
                kind=ErrorKind.ENGINE_BUG,
                message=(
                    f"terminal stage 'package' did not execute "
                    f"({len(results)} stages ran) -- refusing to certify "
                    "a build without a preflight verdict and package report"
                ),
            )
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE builds SET status = 'completed', completed_at = now() WHERE id = %s",
                (build_id,),
            )
            _notify(conn, build_id, {"status": "completed"})
            conn.commit()
        _log_info("build completed", stages=len(results))

    except StageError as e:
        # Expected: a gate refused the build, or the input was bad. The build
        # reaches a terminal state and the worker keeps going.
        _fail(conn, build_id, e.kind.value, e.message)
        _log_info("build failed", error_kind=e.kind.value, error=e.message)

    except Exception as e:
        # Unexpected: a bug, OOM, disk full. Record 'failed' FIRST so the build
        # reaches a terminal state, THEN let the process die loudly -- the
        # caller (main) propagates this out of the poll loop and exits non-zero.
        # U1: recording before re-raising is the whole point; without it the row
        # would sit at 'running' with an expired lease until another worker
        # reclaimed it, indistinguishable from a build still in flight.
        _fail(conn, build_id, "INTERNAL", f"{type(e).__name__}: {e}")
        _log_info("build crashed -- recorded as failed, process exiting",
                  error_type=type(e).__name__, error=str(e))
        raise


_stop_requested = False


def _handle_sigterm(signum, frame):
    """U1: graceful shutdown. Finish the current build, do not claim another,
    exit 0. Without this every `docker compose up -d --build` orphans an
    in-flight build for a full lease period."""
    global _stop_requested
    _stop_requested = True


def main() -> int:
    dsn = _dsn()
    signal.signal(signal.SIGTERM, _handle_sigterm)
    _log_info("starting", poll_interval_s=POLL_INTERVAL_S, lease_s=LEASE_SECONDS,
              max_attempts=MAX_ATTEMPTS)
    while True:
        if _stop_requested:
            _log_info("SIGTERM received -- exiting cleanly, no new claims")
            return 0
        # A worker that dies on a transient database blip is a worker that
        # silently stops draining the queue. Restarting Postgres killed this
        # process outright ("the database system is shutting down") and every
        # build after that sat in 'queued' forever with nothing reporting why.
        # Connection loss is an expected condition for a long-lived poller, not
        # a reason to exit; a genuinely bad DSN still surfaces, as a repeated
        # and visible error rather than a swallowed one.
        try:
            conn = psycopg2.connect(dsn)
        except psycopg2.OperationalError as e:
            _log_info("database unavailable, retrying", error=str(e))
            time.sleep(POLL_INTERVAL_S)
            continue
        try:
            build = _claim_build(conn)
            if build is not None:
                run_build(conn, build)
                _CURRENT_BUILD.clear()
                continue
        except psycopg2.OperationalError as e:
            _log_info("lost the database mid-poll, reconnecting", error=str(e))
        finally:
            conn.close()
        if _stop_requested:
            _log_info("SIGTERM received after build -- exiting cleanly")
            return 0
        # U1: jitter the poll. Ten workers polling on a fixed tick are a
        # synchronised thundering herd against one row.
        time.sleep(POLL_INTERVAL_S * (0.5 + random.random()))


if __name__ == "__main__":
    sys.exit(main())
