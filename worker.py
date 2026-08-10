#!/usr/bin/env python3
"""
Publisher worker -- DB-backed queue, real execution.
ARCHITECTURE_REMEDIATION.md A1.4.

Claims a queued build with `FOR UPDATE SKIP LOCKED` (correct multi-worker
claim without double-processing), runs the real DAG via DagExecutor against
a durable CAS root and a PostgresCacheStore (so cache state is visible to
every worker and, eventually, the API), and records build_stages/artifacts
rows as the source of truth GET /v1/builds/:id will read from once A2 wires
the routes to it.

tracer_bullet.py is the local dev harness (single build_id, prints to
stdout, no DB). This is the production path: many builds, many workers,
state that outlives the process. `allow_stub_engines` is NOT set here --
see the comment at its call site below.
"""

from __future__ import annotations

import os
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
from publisher_stages import StageError, get_registry
from tracer_bullet import DagExecutor

POLL_INTERVAL_S = 2
CAS_ROOT = Path(os.environ.get("PUBLISHER_CAS_ROOT", "./.publisher/cas"))


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required to run the worker")
    return dsn


def _claim_build(conn) -> dict | None:
    """FOR UPDATE SKIP LOCKED -- the mechanism that makes `builds` a correct
    multi-worker queue: a second worker's concurrent claim query skips a row
    this transaction already holds, rather than blocking or double-claiming."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT * FROM builds
            WHERE status = 'queued'
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        )
        build = cur.fetchone()
        if build is None:
            return None
        cur.execute(
            "UPDATE builds SET status = 'running', started_at = now() WHERE id = %s",
            (build["id"],),
        )
        conn.commit()
        return dict(build)


def _initial_inputs_for(build: dict) -> dict:
    """
    Maps a build's {documentId, designId, profileIds} to real DagExecutor
    root inputs.

    Placeholder: A2 has not yet wired manuscript upload to real stored
    content, so there is no real manuscript behind `documentId` yet to read.
    Runs the same fixture tracer_bullet.py uses so the queue/cache/artifact
    mechanism this worker owns is exercised end-to-end today. Replace this
    function's body -- not its callers -- once A2 lands real manuscript
    storage; nothing else here needs to change.
    """
    return {
        "acquire": {"manifest_path": "corpus/manuscripts/minimal-novel.ast.json"},
        # The same profile drives all three: design-compile grows the page box by
        # its bleed, finish insets the TrimBox by it, preflight measures it.
        "design-compile": {"designspec_path": None, "profile_name": "Generic 6x9"},
        "finish": {"profile_name": "Generic 6x9"},
        "preflight": {"profile_name": "Generic 6x9"},
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


def run_build(conn, build: dict) -> None:
    build_id = build["id"]
    print(f"[worker] claimed build {build_id}")
    registry = get_registry()
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

    try:
        results = executor.execute(
            build_id=build_id,
            initial_inputs=_initial_inputs_for(build),
            cas_root=CAS_ROOT,
            cache_store=cache_store,
            on_stage_complete=_on_stage,
        )
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE builds SET status = 'completed', completed_at = now() WHERE id = %s",
                (build_id,),
            )
            conn.commit()
        print(f"[worker] build {build_id} completed -- {len(results)} stages")

    except StageError as e:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE builds SET status = 'failed', error_kind = %s, error_message = %s, "
                "completed_at = now() WHERE id = %s",
                (e.kind.value, e.message, build_id),
            )
            conn.commit()
        print(f"[worker] build {build_id} failed: [{e.kind}] {e.message}")


def main() -> int:
    dsn = _dsn()
    print(f"[worker] starting, polling every {POLL_INTERVAL_S}s")
    while True:
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
            print(f"[worker] database unavailable, retrying in {POLL_INTERVAL_S}s: {e}")
            time.sleep(POLL_INTERVAL_S)
            continue
        try:
            build = _claim_build(conn)
            if build is not None:
                run_build(conn, build)
                continue
        except psycopg2.OperationalError as e:
            print(f"[worker] lost the database mid-poll, reconnecting: {e}")
        finally:
            conn.close()
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    sys.exit(main())
