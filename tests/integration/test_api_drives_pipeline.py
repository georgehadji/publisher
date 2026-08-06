"""
A0.2 -- the seam detectors (docs/ARCHITECTURE_REMEDIATION.md).

Both findings the audit rated CRITICAL trace to one root cause: no state in
this system outlives a single DagExecutor.execute() call. These two tests
assert the behaviour that fix implies. Both are currently impossible against
today's code -- that impossibility is the finding, not a bug in the test.

Working rule (ARCHITECTURE_REMEDIATION.md §2): land each detector in the same
PR as its fix, detector first in the diff. Do not soften these to pass early.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from contextlib import closing
from pathlib import Path

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "packages" / "api"
TOKEN = "detector-token"
TENANT = "detector-tenant"
# Port 55432 is the compose stack's published port -- see docker-compose.yml.
# It is deliberately NOT 5432: a locally-installed PostgreSQL owns that port on
# many dev machines, and these tests would then assert against a database the
# worker never writes to, which is precisely the kind of result that looks like
# a pass and means nothing.
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://publisher:publisher@localhost:55432/publisher"
)
# Idempotency-Key is scoped (tenant, key) and rows persist in Postgres across
# test runs -- a literal key reused run-to-run would replay a PREVIOUS run's
# cached response instead of exercising the route again. Unique per process.
RUN_ID = uuid.uuid4().hex[:8]


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _headers(idem: str) -> dict:
    return {"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": f"{RUN_ID}-{idem}"}


@pytest.fixture(scope="module")
def api_server():
    """
    Boots the real Fastify server (not a mock) so these tests exercise the
    actual routes in packages/api/src/index.ts, not a stand-in for them.
    """
    tsx = API_DIR / "node_modules" / ".bin" / ("tsx.cmd" if os.name == "nt" else "tsx")
    if not tsx.exists():
        pytest.fail(
            f"tsx not found at {tsx} -- run `npm install` in packages/api "
            f"before this detector can even attempt to run."
        )

    port = _free_port()
    env = {
        **os.environ,
        "PORT": str(port),
        "HOST": "127.0.0.1",
        "PUBLISHER_API_TOKENS": f"{TOKEN}:{TENANT}",
        "PUBLISHER_CORS_ORIGINS": "http://localhost",
        "DATABASE_URL": DATABASE_URL,
        "PUBLISHER_CAS_ROOT": str(REPO_ROOT / ".test-cas-cache"),
    }
    proc = subprocess.Popen(
        [str(tsx), "src/index.ts"],
        cwd=str(API_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        healthy = False
        for _ in range(100):
            try:
                if requests.get(f"{base_url}/v1/health", timeout=1).status_code == 200:
                    healthy = True
                    break
            except requests.RequestException:
                pass
            time.sleep(0.2)
        if not healthy:
            proc.terminate()
            out = proc.stdout.read() if proc.stdout else ""
            pytest.fail(f"API server never became healthy.\n{out}")
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def worker_process():
    """
    A real worker.py, so a queued build actually runs. Without this, detector 1
    would wait for a completion that nothing produces -- the same fabrication
    problem from a different angle.

    The worker runs with allow_stub_engines=False, so it needs the real
    toolchain. On a host without Ghostscript (Windows dev boxes, typically)
    a locally-spawned worker would fail every build in `finish` -- so there,
    the compose worker is expected to be servicing the queue instead, and this
    fixture yields None rather than starting a second worker that could claim
    the build and fail it. The test still asserts a real artifact either way:
    nothing here is softened, the work just happens in the container.
    """
    import shutil

    if shutil.which("gs") is None:
        yield None
        return

    env = {
        **os.environ,
        "DATABASE_URL": DATABASE_URL,
        "PUBLISHER_CAS_ROOT": str(REPO_ROOT / ".test-cas-cache"),
    }
    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "worker.py")],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _make_document(base_url: str) -> str:
    title = requests.post(
        f"{base_url}/v1/titles", json={"title": "Detector fixture"}, headers=_headers("t1")
    ).json()
    manuscript = requests.post(
        f"{base_url}/v1/titles/{title['id']}/manuscripts", json={}, headers=_headers("m1")
    ).json()
    return manuscript["manuscriptId"]


def test_build_request_produces_a_real_artifact(api_server, worker_process):
    """
    POST a build, poll to completion, fetch the artifact record, and assert it
    identifies bytes the pipeline actually produced -- a content hash -- rather
    than a fabricated URL to a host that has never existed.

    Currently impossible: GET /v1/builds/:id/artifacts/:kind returns a literal
    `downloadUrl` pointing at `https://artifacts.publisher.internal/...` and
    carries no `sha256` (or any content-identifying field). There is nothing
    behind the response to verify against the pipeline's real output, because
    no worker ever ran the pipeline for this build -- POST /v1/builds only
    ever wrote to an in-process Map.
    """
    document_id = _make_document(api_server)
    build = requests.post(
        f"{api_server}/v1/builds",
        json={"documentId": document_id, "designId": "d1", "profileIds": ["p1"]},
        headers=_headers("b1"),
    ).json()
    build_id = build["buildId"]

    status = {}
    for i in range(200):
        status = requests.get(
            f"{api_server}/v1/builds/{build_id}", headers=_headers(f"s1-{i}")
        ).json()
        if status.get("status") in ("completed", "failed"):
            break
        time.sleep(0.5)

    assert status.get("status") != "queued", (
        "the build never left 'queued' -- no worker is servicing the queue. "
        "Start one (`docker compose up -d worker`) or install Ghostscript "
        f"locally so this test can spawn its own: {status}"
    )
    assert status.get("status") == "completed", f"build did not complete: {status}"

    artifact = requests.get(
        f"{api_server}/v1/builds/{build_id}/artifacts/pdf", headers=_headers("a1")
    ).json()

    assert "sha256" in artifact, (
        "artifact response carries no content hash -- it cannot be verified "
        f"against anything the pipeline produced, because nothing produced it: {artifact}"
    )
    # `pdf` must resolve to the CONVERTED press file, not paginate's raw render.
    # Both stages emit kind='pdf'; keying artifacts on `kind` served the raw one.
    assert artifact["schemaId"] == "pdfx/1", (
        "GET artifacts/pdf did not return the PDF/X press artifact -- it is "
        f"serving some other stage's output as press-ready: {artifact}"
    )


def test_build_status_is_not_a_hardcoded_literal(api_server):
    """
    A0.3 -- anti-fabrication regression guard (findings 1, 14).

    A build that has just been queued, with no worker ever having run it, must
    report status='queued' and zero completed stages. It must NOT report the
    same seven-stage completed payload every build reports today regardless of
    whether anything executed.
    """
    document_id = _make_document(api_server)
    build = requests.post(
        f"{api_server}/v1/builds",
        json={"documentId": document_id, "designId": "d1", "profileIds": ["p1"]},
        headers=_headers("b2"),
    ).json()

    # No worker exists yet, so there has been no time window in which real work
    # could have happened. This assertion must hold immediately.
    status = requests.get(
        f"{api_server}/v1/builds/{build['buildId']}", headers=_headers("s2")
    ).json()

    assert status["status"] == "queued", (
        f"build reported '{status['status']}' immediately after being queued, "
        f"with no worker having run -- this is a fabricated response, not a "
        f"real build status: {status}"
    )
    assert status.get("stages", []) == [], (
        f"build reported stage results with no worker having run: {status}"
    )


def test_api_deliverable_schemas_all_exist_in_the_stage_registry():
    """
    The API resolves a public artifact name ('pdf') to a schema ID ('pdfx/1'),
    because `kind` is unique only within one stage: paginate and finish BOTH
    emit kind='pdf', and selecting on kind served paginate's unconverted
    weasyprint render as the press-ready file.

    That map is hand-written in TypeScript, so it can drift from the stage
    declarations it points at. This asserts every schema it names is really
    some registered stage's declared output -- a rename in a @stage(...) breaks
    here instead of 404ing in production.
    """
    import re

    sys.path.insert(0, str(REPO_ROOT))
    import stages  # noqa: F401 -- registration side effect
    from publisher_stages import get_registry

    source = (API_DIR / "src" / "index.ts").read_text(encoding="utf-8")
    block = re.search(
        r"DELIVERABLE_SCHEMAS[^=]*=\s*Object\.freeze\(\{(.*?)\}\)", source, re.S
    )
    assert block, "DELIVERABLE_SCHEMAS not found in packages/api/src/index.ts"
    mapped = dict(re.findall(r"'?([\w-]+)'?\s*:\s*'([^']+)'", block.group(1)))
    assert mapped, "DELIVERABLE_SCHEMAS parsed as empty"

    declared = {
        schema_id
        for decl in get_registry().all()
        for schema_id in decl.outputs.values()
    }
    unknown = {name: sid for name, sid in mapped.items() if sid not in declared}
    assert not unknown, (
        f"API offers artifact kinds whose schema no stage produces: {unknown}. "
        f"Declared output schemas: {sorted(declared)}"
    )

    # The two that matter most: the press file must be the converted one.
    assert mapped["pdf"] == "pdfx/1"
    assert mapped["raw-pdf"] == "raw-pdf/1"


def test_second_build_of_same_input_hits_cache():
    """
    Run the same stage inputs through the real executor twice. The second run
    must reuse the first run's CAS/cache and report cache_hit=True for the
    unchanged stage instead of re-executing it.

    Currently impossible: DagExecutor.execute() has no parameter for a durable,
    caller-supplied CAS root -- it allocates a fresh tempfile.TemporaryDirectory
    on every call (tracer_bullet.py), so a second run shares no bytes with the
    first and StageResult carries no cache_hit field to report on regardless.
    """
    sys.path.insert(0, str(REPO_ROOT))
    import stages  # noqa: F401 -- registration side effect
    from publisher_stages import get_registry
    from tracer_bullet import DagExecutor

    persistent_root = REPO_ROOT / ".test-cas-cache"
    executor = DagExecutor(get_registry(), allow_stub_engines=True)
    initial_inputs = {
        "acquire": {"manifest_path": "corpus/manuscripts/minimal-novel.ast.json"},
        "design-compile": {"designspec_path": None},
        "preflight": {"profile_name": "Generic 6x9"},
    }

    # The interface a durable cache requires -- a reusable, caller-supplied CAS
    # root -- does not exist on execute() yet.
    executor.execute("cache-probe-1", initial_inputs, cas_root=persistent_root)
    second = executor.execute("cache-probe-2", initial_inputs, cas_root=persistent_root)

    assert second["acquire"].cache_hit is True
