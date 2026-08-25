"""
The suite must never hang, and must never leak the servers it spawns.

This is the detector for a real failure: `./scripts/test.ps1` -- the documented
whole-suite command -- sat at 84% forever on any machine without a live
Postgres. A py-spy dump of the wedged interpreter put it in the `api_server`
fixture on this line:

    out = proc.stdout.read() if proc.stdout else ""

The 20s health poll above it worked correctly. The hang was the *diagnostic*
that ran after it gave up. `proc` is `tsx src/index.ts`, which on Windows is
`tsx.cmd` -- cmd.exe spawning node.exe. `proc.terminate()` killed cmd.exe and
left node.exe running, still holding the write end of the inherited stdout
pipe, so the pipe never reached EOF and `.read()` never returned. Confirmed
empirically: killing the wedged pytest left four orphaned `node.exe src/index.ts`
processes that had to be killed by hand.

These tests deliberately build that exact shape -- a parent whose grandchild
inherits the stdout pipe -- because it is the only way to prove the fix. They
run WITHOUT Postgres on purpose: they live outside `tests/integration/`, which
is skipped when the database is unreachable. A hang detector that only runs on
a fully provisioned machine would not have caught this.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from proc_control import (
    drain_output,
    kill_tree,
    new_session_kwargs,
    pid_alive,
    postgres_reachable,
)

# A parent that spawns a grandchild and then idles. The grandchild inherits the
# parent's stdout, which is the pipe under test -- this is the `tsx -> node`
# shape in miniature. The pid is written to a file rather than to stdout,
# because stdout is precisely the thing we must not depend on being readable.
PARENT_SRC = """
import pathlib, subprocess, sys, time
grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
pathlib.Path(sys.argv[1]).write_text(str(grandchild.pid))
time.sleep(120)
"""

# Generous: these bound "returned at all" vs "hung forever", not performance.
DRAIN_BUDGET_S = 20.0
REAP_GRACE_S = 10.0


def _spawn_parent_with_grandchild(tmp_path: Path) -> tuple[subprocess.Popen, int]:
    """Start the parent/grandchild pair and return (parent proc, grandchild pid)."""
    pid_file = tmp_path / "grandchild.pid"
    proc = subprocess.Popen(
        [sys.executable, "-c", PARENT_SRC, str(pid_file)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        **new_session_kwargs(),
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if pid_file.exists() and pid_file.read_text().strip():
            break
        if proc.poll() is not None:
            kill_tree(proc)
            pytest.fail("helper parent exited before spawning its grandchild")
        time.sleep(0.05)
    else:
        kill_tree(proc)
        pytest.fail("helper parent never reported a grandchild pid")

    grandchild_pid = int(pid_file.read_text().strip())
    assert pid_alive(grandchild_pid), "grandchild should be running before we kill anything"
    return proc, grandchild_pid


def test_kill_tree_reaps_the_grandchild_not_just_the_child(tmp_path):
    """
    `terminate()` signals one process. The orphan that survives it is what held
    the pipe open and wedged the suite, so the tree -- not the child -- is the
    unit that has to die.
    """
    proc, grandchild_pid = _spawn_parent_with_grandchild(tmp_path)
    try:
        kill_tree(proc)

        deadline = time.monotonic() + REAP_GRACE_S
        while time.monotonic() < deadline and pid_alive(grandchild_pid):
            time.sleep(0.1)

        assert not pid_alive(grandchild_pid), (
            f"grandchild {grandchild_pid} survived kill_tree -- this is exactly the "
            f"orphaned node.exe that kept the stdout pipe open and hung the suite"
        )
        assert proc.poll() is not None, "parent should be dead after kill_tree"
    finally:
        kill_tree(proc)


def test_drain_output_returns_even_while_a_grandchild_holds_the_pipe(tmp_path):
    """
    The regression itself, reproduced.

    We deliberately do the OLD thing -- terminate only the direct child -- so the
    grandchild is still holding the write end. Under the old code the next line
    was `proc.stdout.read()`, which blocked forever. `drain_output` must come
    back within its deadline instead.
    """
    proc, grandchild_pid = _spawn_parent_with_grandchild(tmp_path)
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

        started = time.monotonic()
        out = drain_output(proc, timeout_s=2)
        elapsed = time.monotonic() - started

        assert elapsed < DRAIN_BUDGET_S, (
            f"drain_output blocked {elapsed:.1f}s with a grandchild holding the pipe; "
            f"a bare proc.stdout.read() here is what hung ./scripts/test.ps1 forever"
        )
        assert isinstance(out, str), "callers interpolate this into a failure message"
    finally:
        kill_tree(proc)
        deadline = time.monotonic() + REAP_GRACE_S
        while time.monotonic() < deadline and pid_alive(grandchild_pid):
            time.sleep(0.1)


def test_drain_output_still_returns_real_output_when_nothing_holds_the_pipe(tmp_path):
    """The bounded read must not cost us the diagnostic it exists to produce."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "print('server said hello')"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        **new_session_kwargs(),
    )
    try:
        assert "server said hello" in drain_output(proc, timeout_s=30)
    finally:
        kill_tree(proc)


def test_postgres_reachable_is_false_for_a_dead_port():
    """
    Drives the skip that keeps the documented one-liner usable on a clean
    checkout. A port nothing listens on must read as unreachable quickly.
    """
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]  # released on exit -- nothing listens now

    url = f"postgresql://publisher:publisher@127.0.0.1:{dead_port}/publisher"
    started = time.monotonic()
    assert postgres_reachable(url, timeout_s=1.0) is False
    assert time.monotonic() - started < 10, "the precheck must be fast enough to run always"


def test_postgres_reachable_is_true_for_a_listening_port():
    """The other half: it must not skip the integration suite when Postgres IS up."""
    import socket

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        url = f"postgresql://publisher:publisher@127.0.0.1:{port}/publisher"
        assert postgres_reachable(url, timeout_s=2.0) is True
    finally:
        listener.close()


def test_postgres_reachable_handles_a_url_without_an_explicit_port():
    """Must not raise on a URL that omits the port -- it falls back to 5432."""
    assert postgres_reachable("postgresql://u:p@127.0.0.1/publisher", timeout_s=0.5) in (True, False)


def test_kill_tree_is_safe_on_an_already_dead_process():
    """Fixture teardown calls this unconditionally; a dead process is not an error."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"], **new_session_kwargs())
    proc.wait(timeout=30)
    kill_tree(proc)  # must not raise
    assert proc.poll() is not None


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group semantics")
def test_new_session_kwargs_requests_a_process_group_on_posix():
    """`os.killpg` needs the child to lead its own session, or kill_tree can only
    reach the direct child again."""
    assert new_session_kwargs().get("start_new_session") is True
